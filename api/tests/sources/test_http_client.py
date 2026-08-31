"""Tests for the policy-aware HTTP client.

Tests rate limiting, retry, conditional requests, robots enforcement,
User-Agent, and object size checks.
"""

import hashlib
import time
from unittest.mock import patch

import httpx
import pytest

from irc_data.sources.http_client import (
    MAX_OBJECT_SIZE,
    PolicyAwareHttpClient,
    RateLimiter,
    STANDARD_USER_AGENT,
)
from irc_data.sources.models import DataSource
from irc_data.sources.policy import (
    PolicyVersionMismatchError,
    SourceNotApprovedError,
)
from irc_data.sources.registry import get_source


def make_client(transport=None, **kwargs):
    if transport:
        inner = httpx.AsyncClient(
            transport=transport,
            follow_redirects=True,
            headers={"User-Agent": STANDARD_USER_AGENT},
        )
    else:
        inner = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": STANDARD_USER_AGENT},
        )
    return PolicyAwareHttpClient(
        client=inner,
        rate_limiter=RateLimiter(min_delay=0.0, jitter=0.0),
        **kwargs,
    )


class TestRateLimiter:
    """Tests for the RateLimiter class."""

    @pytest.mark.asyncio
    async def test_rate_limiter_enforces_delay(self):
        rl = RateLimiter(min_delay=0.1, jitter=0.0)
        start = time.monotonic()
        await rl.wait("example.com")
        await rl.wait("example.com")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.1  # at least the min_delay

    @pytest.mark.asyncio
    async def test_rate_limiter_per_domain(self):
        """Different domains should have independent rate limits."""
        rl = RateLimiter(min_delay=0.1, jitter=0.0)
        start = time.monotonic()
        await rl.wait("a.com")
        await rl.wait("b.com")  # different domain, no wait
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # should be near-instant

    @pytest.mark.asyncio
    async def test_rate_limiter_reset(self):
        rl = RateLimiter(min_delay=0.1, jitter=0.0)
        await rl.wait("example.com")
        rl.reset("example.com")
        # After reset, next call should not wait
        start = time.monotonic()
        await rl.wait("example.com")
        elapsed = time.monotonic() - start
        assert elapsed < 0.05


class TestPolicyAwareHttpClient:
    """Tests for the PolicyAwareHttpClient."""

    @pytest.mark.asyncio
    async def test_sets_standard_user_agent(self):
        captured = {}

        def handler(req):
            captured["ua"] = req.headers.get("User-Agent")
            return httpx.Response(200, content=b"ok")

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        await client.fetch("https://example.com/page")
        assert captured["ua"] == STANDARD_USER_AGENT
        await client.aclose()

    @pytest.mark.asyncio
    async def test_conditional_request_sends_etag(self):
        captured = {}

        def handler(req):
            captured["etag"] = req.headers.get("If-None-Match")
            return httpx.Response(200, content=b"ok")

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        await client.fetch("https://example.com/page", etag='"abc123"')
        assert captured["etag"] == '"abc123"'
        await client.aclose()

    @pytest.mark.asyncio
    async def test_conditional_request_sends_last_modified(self):
        captured = {}

        def handler(req):
            captured["lm"] = req.headers.get("If-Modified-Since")
            return httpx.Response(200, content=b"ok")

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        await client.fetch("https://example.com/page", last_modified="Wed, 01 Jan 2025 00:00:00 GMT")
        assert captured["lm"] == "Wed, 01 Jan 2025 00:00:00 GMT"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_304_is_clean_success(self):
        def handler(req):
            return httpx.Response(304, headers={"ETag": '"abc"'})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        response = await client.fetch("https://example.com/page", etag='"abc"')
        assert response.status_code == 304
        await client.aclose()

    @pytest.mark.asyncio
    async def test_429_honours_retry_after(self):
        call_count = {"n": 0}

        def handler(req):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, content=b"ok")

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        response = await client.fetch("https://example.com/page")
        assert response.status_code == 200
        await client.aclose()

    @pytest.mark.asyncio
    async def test_5xx_retries_with_backoff(self):
        call_count = {"n": 0}

        def handler(req):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, content=b"ok")

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        response = await client.fetch("https://example.com/page")
        assert response.status_code == 200
        assert call_count["n"] == 3
        await client.aclose()

    @pytest.mark.asyncio
    async def test_4xx_no_retry(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(404))
        client = make_client(transport)
        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch("https://example.com/missing")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_source_policy_enforced(self):
        src = get_source("clubspot")  # hold
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"ok"))
        client = make_client(transport)
        with pytest.raises(SourceNotApprovedError):
            await client.fetch("https://clubspot.com/results", source=src)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_robots_disallow_enforced(self):
        src = get_source("sailsys")
        src.robots_disallow = ["/private"]
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"ok"))
        client = make_client(transport)
        with pytest.raises(SourceNotApprovedError, match="disallowed"):
            await client.fetch("https://app.sailsys.com.au/private/data", source=src)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_irc_certs_attribution_header(self):
        captured = {}

        def handler(req):
            captured["hdr"] = req.headers.get("X-SailRatings-Source")
            return httpx.Response(200, content=b"ok")

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        src = get_source("irc-certs")
        await client.fetch("https://ircrating.org/pdfdirectory/cert.pdf", source=src)
        assert captured["hdr"] == "irc-certs"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_object_size_check(self):
        client = make_client()
        with pytest.raises(ValueError, match="exceeds"):
            client.check_object_size(MAX_OBJECT_SIZE + 1, "https://example.com/big")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_count_increments(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"ok"))
        client = make_client(transport)
        assert client.fetch_count == 0
        await client.fetch("https://example.com/a")
        assert client.fetch_count == 1
        await client.fetch("https://example.com/b")
        assert client.fetch_count == 2
        await client.aclose()

    @pytest.mark.asyncio
    async def test_redirects_followed(self):
        def handler(req):
            if req.url.path == "/old":
                return httpx.Response(301, headers={"Location": "/new"})
            return httpx.Response(200, content=b"redirected")

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        response = await client.fetch("https://example.com/old")
        assert response.status_code == 200
        assert response.content == b"redirected"
        await client.aclose()
