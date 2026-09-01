"""Policy-aware HTTP client with rate limiting, retry, and conditional requests.

Wraps ``httpx.AsyncClient`` to enforce the collection policy:
- Standard ``User-Agent``
- Rate limiting (max 1 req / 2s per domain with jitter)
- Exponential backoff on transient 5xx
- Honours ``Retry-After`` on 429
- Conditional requests (If-None-Match / If-Modified-Since)
- robots.txt disallow enforcement
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from irc_data.sources.models import DataSource
from irc_data.sources.policy import (
    SourceNotApprovedError,
    assert_policy_current,
    assert_source_approved,
)

STANDARD_USER_AGENT = (
    "SailRatings/1.0 (+https://sailratings.com; contact=stuart@sailratings.com)"
)

# Policy limits
DEFAULT_MIN_DELAY = 2.0  # seconds between requests
DEFAULT_JITTER = 1.0  # additional random delay
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 10.0
MAX_OBJECT_SIZE = 25 * 1024 * 1024  # 25 MB
MAX_FETCHES_PER_RUN = 5000


class RateLimiter:
    """Per-domain rate limiter enforcing max 1 req / 2s + jitter."""

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


class PolicyAwareHttpClient:
    """HTTP client that enforces all collection policy rules."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        rate_limiter: RateLimiter | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_object_size: int = MAX_OBJECT_SIZE,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                DEFAULT_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT
            ),
            follow_redirects=True,
            headers={"User-Agent": STANDARD_USER_AGENT},
        )
        self.rate_limiter = rate_limiter or RateLimiter()
        self.max_retries = max_retries
        self.max_object_size = max_object_size
        self.fetch_count = 0

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    def _get_domain(self, url: str) -> str:
        from urllib.parse import urlparse

        return urlparse(url).hostname or ""

    def _check_source_policy(self, source: DataSource | None) -> None:
        """Run policy checks if a source is provided."""
        if source is None:
            return
        assert_policy_current(source)
        assert_source_approved(source)

    def _check_robots(self, url: str, source: DataSource | None) -> None:
        """Raise if the URL is disallowed by robots.txt."""
        if source and source.is_disallowed(url):
            raise SourceNotApprovedError(
                source.slug,
                reason=f"URL '{url}' is disallowed by robots.txt",
            )

    def _build_headers(
        self,
        source: DataSource | None,
        etag: str | None = None,
        last_modified: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build request headers with UA, conditional, and attribution."""
        headers: dict[str, str] = {"User-Agent": STANDARD_USER_AGENT}

        # IRC cert attribution
        if source and source.slug == "irc-certs":
            headers["X-SailRatings-Source"] = "irc-certs"

        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        if extra_headers:
            headers.update(extra_headers)

        return headers

    async def fetch(
        self,
        url: str,
        source: DataSource | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        extra_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Fetch *url* with rate limiting, retry, and policy enforcement.

        Returns the ``httpx.Response`` (caller validates content).
        Raises ``SourceNotApprovedError`` if the source is blocked or
        the URL is robots-disallowed.
        Raises ``httpx.HTTPStatusError`` on non-retryable failures.
        """
        self._check_source_policy(source)
        self._check_robots(url, source)

        domain = self._get_domain(url)
        headers = self._build_headers(source, etag, last_modified, extra_headers)

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            await self.rate_limiter.wait(domain)
            try:
                self.fetch_count += 1
                response = await self._client.get(url, headers=headers, **kwargs)

                # 304 is a clean success
                if response.status_code == 304:
                    return response

                # 429: honour Retry-After
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and attempt < self.max_retries:
                        try:
                            wait = float(retry_after)
                        except ValueError:
                            wait = (2**attempt) + 1
                        await asyncio.sleep(wait)
                        continue
                    response.raise_for_status()

                # 5xx: retry with backoff
                if 500 <= response.status_code < 600:
                    if attempt < self.max_retries:
                        backoff = (2**attempt) + random.uniform(0, 1)
                        await asyncio.sleep(backoff)
                        continue
                    response.raise_for_status()

                # 4xx (non-429): don't retry
                if 400 <= response.status_code < 500:
                    response.raise_for_status()

                # Success
                return response

            except (httpx.TransportError, httpx.NetworkError) as e:
                last_exc = e
                if attempt < self.max_retries:
                    backoff = (2**attempt) + random.uniform(0, 1)
                    await asyncio.sleep(backoff)
                    continue
                raise

        if last_exc:
            raise last_exc
        # Should not reach here
        raise RuntimeError("fetch loop exhausted without result")

    def check_object_size(self, content_length: int, url: str) -> None:
        """Raise ``ValueError`` if the object exceeds the 25 MB cap."""
        if content_length > self.max_object_size:
            raise ValueError(
                f"Object at {url} is {content_length} bytes "
                f"(exceeds {self.max_object_size} byte cap)"
            )

    async def aclose(self) -> None:
        """Close the underlying client if we own it."""
        if self._owns_client:
            await self._client.aclose()
