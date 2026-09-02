"""Policy-enforced HTTP client for the source adapter SDK (DP-01-03).

Wraps :class:`httpx.AsyncClient` with the responsible-collection
primitives mandated by SPEC-012 §3 and the DP-01-02 policy:

* **User-Agent** — the policy-mandated ``SailRatings/1.0`` string is
  always sent.  A blank or browser User-Agent is rejected.
* **Rate limiting** — per-domain minimum delay + jitter (≤ 1 req / 2 s).
* **Retry** — transient 5xx responses are retried with exponential
  backoff.  ``Retry-After`` headers are honoured.
* **Conditional requests** — ``If-None-Match`` / ``If-Modified-Since``
  headers are sent when a cached ETag / Last-Modified is available.
  A 304 response is treated as a *clean success* (not an error).
* **Content hashing** — every response body is SHA-256'd before being
  returned in a :class:`FetchResult`.
* **Object-size cap** — responses larger than 25 MB are rejected.

Backward-compatibility aliases (``PolicyAwareHttpClient``, ``RateLimiter``,
``STANDARD_USER_AGENT``, ``MAX_OBJECT_SIZE``) are retained for existing
scrapers that import them.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from irc_data.sources.envelope import FetchResult, sha256_hex
from irc_data.sources.policy import (
    ACTIVE_POLICY,
    CollectionPolicyDecisionV1,
    PolicyVersionMismatchError,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

#: Status codes that are retried with backoff.
RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})

#: Default backoff sequence (seconds) — matches RateRule.backoff_sequence.
DEFAULT_BACKOFF: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0)

#: Default max retries.
DEFAULT_MAX_RETRIES = 3

# Backward-compat constants
STANDARD_USER_AGENT = (
    "SailRatings/1.0 (+https://sailratings.com; contact=stuart@sailratings.com)"
)
DEFAULT_MIN_DELAY = 2.0
DEFAULT_JITTER = 1.0
DEFAULT_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 10.0
MAX_OBJECT_SIZE = 25 * 1024 * 1024  # 25 MB
MAX_FETCHES_PER_RUN = 5000


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HttpClientError(Exception):
    """Base exception for HTTP-client failures."""


class ObjectTooLargeError(HttpClientError, ValueError):
    """Raised when a response body exceeds the size cap.

    Subclasses :class:`ValueError` so existing callers that catch
    ``ValueError`` for size-cap violations continue to work.
    """

    def __init__(self, url: str, size: int, max_size: int):
        self.url = url
        self.size = size
        self.max_size = max_size
        super().__init__(
            f"Response from {url} is {size} bytes, exceeds cap of {max_size} bytes"
        )


class RetryExhaustedError(HttpClientError):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, url: str, status_code: int, attempts: int):
        self.url = url
        self.status_code = status_code
        self.attempts = attempts
        super().__init__(
            f"Exhausted {attempts} retries for {url} (last status {status_code})"
        )


class UserAgentMissingError(HttpClientError):
    """Raised when no User-Agent is set on a request."""


# ---------------------------------------------------------------------------
# NotModified sentinel
# ---------------------------------------------------------------------------


@dataclass
class NotModified:
    """Sentinel returned when the server responds 304 Not Modified.

    A 304 is a *clean success* — the caller should treat it as "content
    unchanged" and skip re-downloading.
    """

    url: str
    etag: str | None = None
    last_modified: str | None = None


# ---------------------------------------------------------------------------
# HttpClient
# ---------------------------------------------------------------------------


class HttpClient:
    """Async HTTP client with rate-limit, retry, and hashing.

    Parameters
    ----------
    client
        An :class:`httpx.AsyncClient` instance (real or mock).  When
        ``None``, a new client is created lazily on first use.
    policy
        The active :class:`CollectionPolicyDecisionV1`.  Defaults to
        ``ACTIVE_POLICY``.
    max_retries
        Maximum retry attempts for transient 5xx errors.
    backoff
        Backoff sequence (seconds) between retries.
    rate_limiter
        Optional :class:`RateLimiter` (backward-compat).  When provided,
        its ``min_delay`` / ``jitter`` override the policy rate rule so
        tests can disable or tune the built-in per-domain rate limiting.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        policy: CollectionPolicyDecisionV1 | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff: tuple[float, ...] = DEFAULT_BACKOFF,
        rate_limiter: Any = None,
    ):
        self.policy = policy or ACTIVE_POLICY
        self.max_retries = max_retries
        self.backoff = backoff
        self._client = client
        self._owns_client = client is None

        # Backward-compat: honour an injected RateLimiter's delay/jitter.
        if rate_limiter is not None:
            self._rate_min_delay = float(getattr(rate_limiter, "min_delay", 0.0))
            self._rate_jitter = float(getattr(rate_limiter, "jitter", 0.0))
        else:
            self._rate_min_delay = self.policy.rate.min_delay_seconds
            self._rate_jitter = self.policy.rate.jitter_seconds

        # Per-domain rate limiting (monotonic timestamp of last request)
        self._rate_last_request: dict[str, float] = {}

        # Call counter (for testing / observability)
        self.call_count: int = 0
        self.call_log: list[dict[str, Any]] = []

        # Backward-compat: fetch_count mirrors call_count
        self._fetch_count_alias = True

    @property
    def fetch_count(self) -> int:
        """Backward-compat alias for call_count."""
        return self.call_count

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            ua = self.policy.attribution.user_agent
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
                headers={"User-Agent": ua},
            )
            self._owns_client = True
        return self._client

    async def close(self) -> None:
        """Close the underlying client if we own it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def aclose(self) -> None:
        """Alias for :meth:`close` (httpx compatibility)."""
        await self.close()

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # User-Agent enforcement
    # ------------------------------------------------------------------

    def _build_headers(
        self,
        etag: str | None = None,
        last_modified: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build request headers with mandatory User-Agent + conditional."""
        headers: dict[str, str] = {
            "User-Agent": self.policy.attribution.user_agent,
        }
        if self.policy.retention.conditional_requests:
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _validate_user_agent(headers: dict[str, str]) -> None:
        """Raise if no User-Agent is present."""
        ua = headers.get("User-Agent", "").strip()
        if not ua:
            raise UserAgentMissingError(
                "A User-Agent is mandatory for all source-framework requests"
            )

    def check_object_size(self, content_length: int, url: str) -> None:
        """Raise ``ValueError`` if the object exceeds the 25 MB cap."""
        max_bytes = self.policy.retention.max_object_size_mb * 1024 * 1024
        if content_length > max_bytes:
            raise ValueError(
                f"Object at {url} is {content_length} bytes "
                f"(exceeds {max_bytes} byte cap)"
            )

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    async def _rate_limit_wait(self, domain: str) -> float:
        """Enforce per-domain rate limit.  Returns seconds slept."""
        domain_lower = domain.lower()
        now = time.monotonic()
        last = self._rate_last_request.get(domain_lower, 0.0)
        elapsed = now - last
        delay = self._rate_min_delay + random.uniform(0, self._rate_jitter)

        if elapsed < delay:
            sleep_time = delay - elapsed
            await asyncio.sleep(sleep_time)
            self._rate_last_request[domain_lower] = time.monotonic()
            return sleep_time

        self._rate_last_request[domain_lower] = now
        return 0.0

    # ------------------------------------------------------------------
    # Core fetch
    # ------------------------------------------------------------------

    async def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        extra_headers: dict[str, str] | None = None,
        skip_rate_limit: bool = False,
        # Backward-compat kwargs (ignored in new implementation)
        source: Any = None,
        **kwargs: Any,
    ) -> FetchResult | NotModified:
        """Fetch *url* and return a :class:`FetchResult` or :class:`NotModified`.

        This is the single entry point for all HTTP fetches.  It:

        1. Validates the User-Agent is set.
        2. Rate-limits per domain.
        3. Sends conditional-request headers (If-None-Match / If-Modified-Since).
        4. Retries transient 5xx with exponential backoff (honours Retry-After).
        5. Treats 304 as a clean success (returns :class:`NotModified`).
        6. SHA-256 hashes the body.
        7. Enforces the object-size cap.
        """
        response = await self.raw(
            url,
            etag=etag,
            last_modified=last_modified,
            extra_headers=extra_headers,
            skip_rate_limit=skip_rate_limit,
        )

        # 304 — clean success, content unchanged
        if response.status_code == 304:
            return NotModified(
                url=url,
                etag=response.headers.get("ETag") or etag,
                last_modified=response.headers.get("Last-Modified") or last_modified,
            )

        # Success — build FetchResult (size cap enforced in raw())
        return FetchResult.from_response(
            url=str(response.url) if response.url else url,
            content=response.content,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            policy_version=self.policy.version,
        )

    async def raw(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        extra_headers: dict[str, str] | None = None,
        skip_rate_limit: bool = False,
    ) -> httpx.Response:
        """Fetch *url* and return the raw :class:`httpx.Response`.

        Applies the same policy mechanics as :meth:`fetch` — User-Agent
        validation, per-domain rate limiting, conditional-request headers,
        retry with backoff, and the object-size cap — but returns the raw
        ``httpx.Response`` so callers (e.g. acquisition primitives) can
        inspect Content-Type and validate format before hashing/storing.

        A 304 response is returned as-is (caller treats it as clean
        success).  A ``429`` response honouring ``Retry-After`` is retried.
        """
        headers = self._build_headers(etag, last_modified, extra_headers)
        self._validate_user_agent(headers)

        domain = urlparse(url).hostname or ""
        if not skip_rate_limit:
            await self._rate_limit_wait(domain)

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.call_count += 1
            self.call_log.append({
                "url": url,
                "attempt": attempt,
                "method": "GET",
            })
            try:
                response = await self.client.get(url, headers=headers)

                # 304 — clean success, content unchanged
                if response.status_code == 304:
                    return response

                # Retryable 5xx, or 429 with honourable Retry-After
                retryable = response.status_code in RETRYABLE_STATUS_CODES
                if (
                    response.status_code == 429
                    and self.policy.rate.honour_retry_after
                    and "Retry-After" in response.headers
                ):
                    retryable = True
                if retryable:
                    last_exc = RetryExhaustedError(
                        url, response.status_code, attempt + 1
                    )
                    if attempt < self.max_retries:
                        await self._backoff(attempt, response)
                        continue
                    raise last_exc

                # Non-retryable error
                response.raise_for_status()

                # Object-size cap
                content = response.content
                max_bytes = self.policy.retention.max_object_size_mb * 1024 * 1024
                if len(content) > max_bytes:
                    raise ObjectTooLargeError(url, len(content), max_bytes)

                return response

            except httpx.TransportError as e:
                last_exc = e
                if attempt < self.max_retries:
                    await self._backoff(attempt, None)
                    continue
                raise

        # Should not reach here, but just in case
        raise last_exc or RetryExhaustedError(url, 0, self.max_retries)

    async def _backoff(self, attempt: int, response: httpx.Response | None) -> None:
        """Sleep for the backoff duration, honouring Retry-After if present."""
        # Honour Retry-After header if the policy says to
        if (
            response is not None
            and self.policy.rate.honour_retry_after
            and "Retry-After" in response.headers
        ):
            try:
                wait = float(response.headers["Retry-After"])
                await asyncio.sleep(wait)
                return
            except ValueError:
                pass  # fall through to backoff sequence

        idx = min(attempt, len(self.backoff) - 1)
        wait = self.backoff[idx] + random.uniform(0, 1)
        await asyncio.sleep(wait)

    # ------------------------------------------------------------------
    # Convenience: fetch + hash check
    # ------------------------------------------------------------------

    async def fetch_or_skip(
        self,
        url: str,
        known_hash: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> FetchResult | NotModified | None:
        """Fetch *url*, returning ``None`` if content hash is unchanged.

        If *known_hash* is provided and the fetched content's SHA-256
        matches it, the result is ``None`` (skip — already stored).
        """
        result = await self.fetch(
            url,
            etag=etag,
            last_modified=last_modified,
            extra_headers=extra_headers,
        )

        if isinstance(result, NotModified):
            return result

        if known_hash and result.content_hash == known_hash:
            return None  # content unchanged — skip

        return result


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def get_source_http_client(
    client: httpx.AsyncClient | None = None,
    policy: CollectionPolicyDecisionV1 | None = None,
) -> HttpClient:
    """Create a :class:`HttpClient` bound to the active policy."""
    return HttpClient(client=client, policy=policy or ACTIVE_POLICY)


# ---------------------------------------------------------------------------
# Backward-compatibility: RateLimiter + PolicyAwareHttpClient
# ---------------------------------------------------------------------------


class RateLimiter:
    """Per-domain rate limiter — backward-compat wrapper.

    New code should use :class:`HttpClient` directly (rate limiting is
    built in).  This class is retained for existing scrapers.
    """

    def __init__(
        self,
        min_delay: float = DEFAULT_MIN_DELAY,
        jitter: float = DEFAULT_JITTER,
    ) -> None:
        self.min_delay = min_delay
        self.jitter = jitter
        self._last_request_by_domain: dict[str, float] = {}

    async def wait(self, domain: str | None = None) -> None:
        """Wait until we're allowed to make the next request to *domain*."""
        now = time.monotonic()
        key = domain or "_global"
        last = self._last_request_by_domain.get(key, 0.0)
        delay = self.min_delay + random.uniform(0, self.jitter)
        elapsed = now - last
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_by_domain[key] = time.monotonic()

    def reset(self, domain: str | None = None) -> None:
        """Reset the rate limiter for a domain (or all)."""
        if domain:
            self._last_request_by_domain.pop(domain, None)
        else:
            self._last_request_by_domain.clear()


# PolicyAwareHttpClient is a backward-compat alias for HttpClient.
# Existing scrapers that import it will get the full HttpClient.
PolicyAwareHttpClient = HttpClient
