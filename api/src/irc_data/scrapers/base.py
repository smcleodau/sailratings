"""Base scraper utilities — rate limiting, retry, session management."""

import asyncio
import random
import ssl
import time
from functools import lru_cache, wraps
from pathlib import Path

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


_EXTRA_INTERMEDIATES = Path(__file__).parent / "certs"


@lru_cache(maxsize=1)
def scraper_ssl_context() -> ssl.SSLContext:
    """certifi roots plus intermediates some source servers fail to send.

    Browsers fetch a missing intermediate via AIA, so a site with an
    incomplete chain looks fine to a person while httpx rejects it. TopYacht's
    results host (tes.topyacht.net.au) serves its leaf without Sectigo DV R36;
    that silently cost every TopYacht regatta scrape from at least Aug 2026.
    Intermediates only extend chains to roots already in certifi, so this
    does not widen what is trusted.
    """
    import certifi

    ctx = ssl.create_default_context(cafile=certifi.where())
    for pem in sorted(_EXTRA_INTERMEDIATES.glob("*.pem")):
        ctx.load_verify_locations(cafile=str(pem))
    return ctx


def get_http_client(**kwargs) -> httpx.AsyncClient:
    """Create an httpx async client with sensible defaults.

    The User-Agent is set per the responsible-collection policy
    (DP-01-02 / docs/SOURCE-POLICY.md §8).  Never override with a
    browser User-Agent or blank User-Agent.
    """
    from irc_data.sources.policy import ACTIVE_POLICY

    defaults = {
        "timeout": httpx.Timeout(30.0, connect=10.0),
        "follow_redirects": True,
        "verify": scraper_ssl_context(),
        "headers": {
            "User-Agent": ACTIVE_POLICY.attribution.user_agent,
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
