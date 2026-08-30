"""Base scraper utilities — rate limiting, retry, session management."""

import asyncio
import random
import time
from functools import wraps

import httpx

from irc_data.config import DEFAULT_DELAY_SECONDS, MAX_RETRIES


class RateLimiter:
    """Simple rate limiter with jitter."""

    def __init__(self, min_delay: float = DEFAULT_DELAY_SECONDS, jitter: float = 1.0):
        self.min_delay = min_delay
        self.jitter = jitter
        self._last_request = 0.0

    async def wait(self):
        """Wait until we're allowed to make the next request."""
        now = time.monotonic()
        elapsed = now - self._last_request
        delay = self.min_delay + random.uniform(0, self.jitter)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request = time.monotonic()

    def wait_sync(self):
        """Synchronous version of wait."""
        now = time.monotonic()
        elapsed = now - self._last_request
        delay = self.min_delay + random.uniform(0, self.jitter)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request = time.monotonic()


def get_http_client(**kwargs) -> httpx.AsyncClient:
    """Create an httpx async client with sensible defaults.

    The standard SailRatings User-Agent is set by default (INTERIM-POLICY.md §6).
    Legacy callers may still override it via ``headers`` but should migrate
    to the policy-aware client in ``irc_data.sources``.
    """
    defaults = {
        "timeout": httpx.Timeout(30.0, connect=10.0),
        "follow_redirects": True,
        "headers": {
            "User-Agent": (
                "SailRatings/1.0 "
                "(+https://sailratings.com; contact=stuart@sailratings.com)"
            ),
        },
    }
    defaults.update(kwargs)
    return httpx.AsyncClient(**defaults)


async def fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    rate_limiter: RateLimiter | None = None,
    max_retries: int = MAX_RETRIES,
    **kwargs,
) -> httpx.Response:
    """Fetch a URL with exponential backoff retry."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            if rate_limiter:
                await rate_limiter.wait()
            response = await client.get(url, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            last_exc = e
            if attempt < max_retries:
                wait = (2**attempt) + random.uniform(0, 1)
                await asyncio.sleep(wait)
    raise last_exc  # type: ignore[misc]
