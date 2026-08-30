"""Unit tests for the policy-aware HTTP client primitives.

These exercise the fetch / retry / hash / cap / robots logic in
isolation, with ``httpx.MockTransport`` so there are zero network calls.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from irc_data.sources.contracts import (
    FetchCapExceededError,
    MAX_BYTES_PER_RUN,
    MAX_FETCHES_PER_RUN,
    MAX_OBJECT_BYTES,
    STANDARD_USER_AGENT,
    UserAgentError,
)
from irc_data.sources.http import PolicyAwareHTTPClient, is_path_disallowed, sha256_hex
from irc_data.sources.rate_limit import RateLimiter


class _NoSleep:
    async def __call__(self, _s: float) -> None:
        return None


def _client(handler, **kwargs):
    return PolicyAwareHTTPClient(
        rate_limiter=RateLimiter(min_delay=0.0, jitter=0.0),
        transport=httpx.MockTransport(handler),
        sleep=_NoSleep(),
        **kwargs,
    )


def test_sha256_hex_is_deterministic():
    assert sha256_hex(b"abc") == sha256_hex(b"abc")
    assert sha256_hex(b"abc") != sha256_hex(b"abd")
    assert len(sha256_hex(b"x")) == 64


def test_user_agent_is_mandatory():
    with pytest.raises(UserAgentError):
        PolicyAwareHTTPClient(user_agent="Mozilla/5.0 ...")


def test_user_agent_sent_on_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("User-Agent")
        return httpx.Response(200, text="ok")

    client = _client(handler)
    result = asyncio.run(client.fetch("http://x.test/"))
    assert seen["ua"] == STANDARD_USER_AGENT
    assert result.status_code == 200
    assert result.content == b"ok"
    assert result.content_hash == sha256_hex(b"ok")


def test_conditional_request_304_is_clean_success():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304, headers={"ETag": '"v1"'})
        return httpx.Response(200, text="body", headers={"ETag": '"v1"'})

    client = _client(handler)
    first = asyncio.run(client.fetch("http://x.test/", etag='"v1"'))
    assert first.not_modified is True
    assert first.status_code == 304
    assert first.content == b""
    # 304 doesn't count toward bytes (nothing downloaded).
    assert client.bytes_fetched == 0


def test_retry_on_503_then_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="down")
        return httpx.Response(200, text="recovered")

    client = _client(handler, max_retries=3)
    result = asyncio.run(client.fetch("http://x.test/"))
    assert result.status_code == 200
    assert result.content == b"recovered"
    assert calls["n"] == 2  # one failure + one success


def test_retry_honours_retry_after_on_429():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, text="ok")

    client = _client(handler, max_retries=2)
    result = asyncio.run(client.fetch("http://x.test/"))
    assert result.status_code == 200
    assert calls["n"] == 2


def test_exhausted_retries_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="always down")

    client = _client(handler, max_retries=1)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(client.fetch("http://x.test/"))


def test_object_size_cap_enforced():
    big = b"x" * (MAX_OBJECT_BYTES + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big)

    client = _client(handler)
    with pytest.raises(FetchCapExceededError):
        asyncio.run(client.fetch("http://x.test/"))


def test_fetch_count_cap_enforced():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    client = _client(handler, fetch_cap=2)
    asyncio.run(client.fetch("http://x.test/a"))
    asyncio.run(client.fetch("http://x.test/b"))
    with pytest.raises(FetchCapExceededError):
        asyncio.run(client.fetch("http://x.test/c"))
    assert client.fetch_count == 2


def test_byte_cap_enforced():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100)

    # Two fetches of 100 bytes = 200 (≤ 250, ok); the third would push
    # the running total to 300 > 250 and must be rejected.
    client = _client(handler, byte_cap=250)
    asyncio.run(client.fetch("http://x.test/a"))   # 100 → total 100
    asyncio.run(client.fetch("http://x.test/b"))   # 100 → total 200
    with pytest.raises(FetchCapExceededError):
        asyncio.run(client.fetch("http://x.test/c"))  # +100 → 300 > 250


def test_robots_disallow_blocks_fetch():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="should not reach")

    client = _client(handler)
    with pytest.raises(FetchCapExceededError):
        asyncio.run(client.fetch("http://x.test/private", robots_disallow=("/private",)))


def test_is_path_disallowed():
    assert is_path_disallowed("/private/x", ("/private",)) is True
    assert is_path_disallowed("/public", ("/private",)) is False
    assert is_path_disallowed("/anything", ("/",)) is True
    assert is_path_disallowed("/anything", ("",)) is True
    assert is_path_disallowed("/ok", ()) is False


def test_rate_limiter_enforces_min_delay_per_domain():
    limiter = RateLimiter(min_delay=2.0, jitter=0.0)
    # Record waits via an awaitable that captures the delay without sleeping.
    waits: list[float] = []

    async def _rec(_s: float) -> None:
        waits.append(_s)

    limiter._sleep = _rec  # type: ignore[assignment]

    async def go():
        # First call against an unseen domain: ``last`` defaults to 0.0 so
        # ``elapsed`` is huge → delay is 0 (no wait needed).
        await limiter.wait("a.test")
        # Immediate second call to the same domain: ``last`` was just set
        # to ~now, so the limiter must impose the ~2.0s min delay.
        await limiter.wait("a.test")
        # A different domain has an independent budget.
        await limiter.wait("b.test")

    asyncio.run(go())
    # The same-domain back-to-back call must produce a non-zero wait.
    assert any(w > 0 for w in waits), waits


def test_rate_limiter_backoff_seconds():
    assert RateLimiter.backoff_seconds(0) == 2.0
    assert RateLimiter.backoff_seconds(1) == 4.0
    assert RateLimiter.backoff_seconds(2) == 8.0
    assert RateLimiter.backoff_seconds(3) == 16.0


def test_rate_limiter_retry_after_parse():
    assert RateLimiter.retry_after_seconds("5") == 5.0
    assert RateLimiter.retry_after_seconds(None) is None
    assert RateLimiter.retry_after_seconds("Tue, 01 Jan 2026") is None
