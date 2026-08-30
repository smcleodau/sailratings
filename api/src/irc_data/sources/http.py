"""Policy-aware HTTP client for source adapters.

This is the **fetch** primitive of the SDK (SPEC-012 §4 + §5).  It
centralises every politeness rule from ``INTERIM-POLICY.md`` §3 so an
adapter author never has to re-implement them:

* standard :data:`STANDARD_USER_AGENT`
* per-domain rate limiting with jitter (:class:`RateLimiter`)
* conditional requests (``If-None-Match`` / ``If-Modified-Since``) with
  304-as-success semantics
* SHA-256 content hashing of every response body
* exponential back-off on transient 5xx, ``Retry-After`` on 429
* hard caps: 25 MB per object, 5 000 fetches / run, 500 MB / run
* ``robots_disallow`` enforcement

Transport injection
-------------------
The client accepts an ``httpx.MockTransport`` (or any
``httpx.AsyncBaseTransport``) so the reference adapter and the contract
suite run with **zero real network calls**.  Production code passes
``transport=None`` and gets a real ``httpx.AsyncClient``.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

from .contracts import (
    DEFAULT_MAX_RETRIES,
    FetchCapExceededError,
    FetchResult,
    FetchTarget,
    MAX_BYTES_PER_RUN,
    MAX_FETCHES_PER_RUN,
    MAX_OBJECT_BYTES,
    STANDARD_USER_AGENT,
    UserAgentError,
)
from .rate_limit import RateLimiter

__all__ = [
    "PolicyAwareHTTPClient",
    "sha256_hex",
    "is_path_disallowed",
]


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 hex digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def is_path_disallowed(path: str, disallow: Iterable[str]) -> bool:
    """True if *path* matches any robots.txt ``Disallow`` rule.

    Matching is prefix-based (per the robots.txt spec).  An empty /
    ``/`` rule matches everything (we treat it as a total block).
    """
    for rule in disallow:
        if rule == "" or rule == "/":
            return True
        if path.startswith(rule):
            return True
    return False


class PolicyAwareHTTPClient:
    """Async HTTP client that enforces the collection policy on every call.

    Parameters
    ----------
    rate_limiter:
        Shared :class:`RateLimiter`; if ``None`` a default one is created.
    max_retries:
        Number of retries on transient failures (default 3).
    fetch_cap / byte_cap:
        Per-run hard caps; defaults to the policy values.
    transport:
        ``httpx`` transport to use.  ``None`` means a real network
        client; tests pass ``httpx.MockTransport``.
    """

    def __init__(
        self,
        *,
        rate_limiter: RateLimiter | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        fetch_cap: int = MAX_FETCHES_PER_RUN,
        byte_cap: int = MAX_BYTES_PER_RUN,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = STANDARD_USER_AGENT,
        sleep=None,
    ) -> None:
        if user_agent != STANDARD_USER_AGENT:
            raise UserAgentError(
                "the standard User-Agent is mandatory (INTERIM-POLICY.md §6)"
            )
        self.rate_limiter = rate_limiter or RateLimiter()
        self.max_retries = max_retries
        self.fetch_cap = fetch_cap
        self.byte_cap = byte_cap
        self.transport = transport
        self.user_agent = user_agent
        self._sleep = sleep
        self._fetch_count = 0
        self._bytes_fetched = 0
        self._seen_hashes: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Run-budget accounting
    # ------------------------------------------------------------------
    @property
    def fetch_count(self) -> int:
        return self._fetch_count

    @property
    def bytes_fetched(self) -> int:
        return self._bytes_fetched

    def reset_run_budget(self) -> None:
        """Reset the per-run fetch / byte counters at the start of a run."""
        self._fetch_count = 0
        self._bytes_fetched = 0

    def remember_hash(self, url: str, content_hash: str) -> bool:
        """Record *content_hash* for *url*; return True if it is new."""
        prev = self._seen_hashes.get(url)
        self._seen_hashes[url] = content_hash
        return prev != content_hash

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    async def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        robots_disallow: Iterable[str] = (),
        extra_headers: dict[str, str] | None = None,
    ) -> FetchResult:
        """Fetch *url* with full policy enforcement; return a :class:`FetchResult`.

        A 304 response is a clean success — ``content`` is empty and
        ``not_modified`` is ``True``.  Callers should treat it as
        "nothing new to store".
        """
        path = urlparse(url).path or "/"
        if is_path_disallowed(path, robots_disallow):
            raise FetchCapExceededError(
                f"robots.txt disallows {url!r} (matched disallow rule)"
            )

        self._assert_budget(0)

        headers: dict[str, str] = {"User-Agent": self.user_agent}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        if extra_headers:
            headers.update(extra_headers)

        domain = urlparse(url).netloc
        last_exc: Exception | None = None
        response: httpx.Response | None = None

        for attempt in range(self.max_retries + 1):
            await self.rate_limiter.wait(domain)
            try:
                response = await self._request(url, headers)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    await self._backoff(attempt, None)
                    continue
                raise

            assert response is not None
            # 429: honour Retry-After then retry.
            if response.status_code == 429 and attempt < self.max_retries:
                ra = self.rate_limiter.retry_after_seconds(
                    response.headers.get("Retry-After")
                )
                await self._backoff(attempt, ra)
                continue
            # 5xx: exponential back-off, then retry.
            if 500 <= response.status_code < 600 and attempt < self.max_retries:
                await self._backoff(attempt, None)
                continue
            break

        assert response is not None
        # 4xx (other than 429) and final 5xx are hard errors.
        if 400 <= response.status_code < 600:
            response.raise_for_status()

        return self._finalise(url, response)

    async def _request(self, url: str, headers: dict[str, str]) -> httpx.Response:
        kwargs: dict[str, Any] = {
            "headers": headers,
            "follow_redirects": True,
            "timeout": httpx.Timeout(30.0, connect=10.0),
        }
        if self.transport is not None:
            kwargs["transport"] = self.transport
        # Use a short-lived client per request so transport injection works
        # uniformly for both real and mock transports.
        async with httpx.AsyncClient(**kwargs) as client:
            return await client.get(url)

    async def _backoff(self, attempt: int, retry_after: float | None) -> None:
        delay = retry_after if retry_after is not None else self.rate_limiter.backoff_seconds(attempt)
        if self._sleep is not None:
            await self._sleep(delay)
        else:
            import asyncio

            await asyncio.sleep(delay)

    def _finalise(self, url: str, response: httpx.Response) -> FetchResult:
        body = response.content
        if response.status_code == 304:
            self._fetch_count += 1
            return FetchResult(
                url=url,
                content=b"",
                content_hash="",
                status_code=304,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                not_modified=True,
            )

        if len(body) > MAX_OBJECT_BYTES:
            raise FetchCapExceededError(
                f"object {url!r} is {len(body)} bytes > cap {MAX_OBJECT_BYTES}"
            )
        self._assert_budget(len(body))

        content_hash = sha256_hex(body)
        self._fetch_count += 1
        self._bytes_fetched += len(body)
        return FetchResult(
            url=url,
            content=body,
            content_hash=content_hash,
            status_code=response.status_code,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            not_modified=False,
        )

    def _assert_budget(self, incoming_bytes: int) -> None:
        if self._fetch_count >= self.fetch_cap:
            raise FetchCapExceededError(
                f"fetch cap reached: {self._fetch_count} >= {self.fetch_cap}"
            )
        if self._bytes_fetched + incoming_bytes > self.byte_cap:
            raise FetchCapExceededError(
                f"byte cap reached: {self._bytes_fetched + incoming_bytes} > {self.byte_cap}"
            )

    # ------------------------------------------------------------------
    # Conditional-request helper
    # ------------------------------------------------------------------
    async def fetch_target(self, target: FetchTarget, *, robots_disallow: Iterable[str] = ()) -> FetchResult:
        """Fetch a :class:`FetchTarget`, re-using its conditional tokens."""
        return await self.fetch(
            target.url,
            etag=target.etag,
            last_modified=target.last_modified,
            robots_disallow=robots_disallow,
        )
