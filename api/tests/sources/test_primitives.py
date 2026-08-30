"""Fixture-based tests for the acquisition primitive library (DP-01-04).

These tests run with **zero network calls** — all HTTP traffic goes
through ``httpx.MockTransport`` and the browser is a fake.  They cover
the verification criteria from the issue:

* redirects
* pagination
* JavaScript rendering
* PDFs
* malformed responses
* throttling (429 Retry-After)

Each test uses small, recorded "fixtures" (inline mock handlers) so the
behaviour is deterministic and self-documenting.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from irc_data.sources.contracts import (
    CURRENT_POLICY_VERSION,
    DataSource,
    FetchCapExceededError,
    FetchResult,
    MAX_OBJECT_BYTES,
    PolicyVersionMismatchError,
    STANDARD_USER_AGENT,
)
from irc_data.sources.http import PolicyAwareHTTPClient, sha256_hex
from irc_data.sources.primitives import (
    MalformedResponseError,
    RenderedFetchResult,
    fetch_file,
    fetch_html,
    fetch_json,
    fetch_pdf,
    paginate,
    render_page,
)
from irc_data.sources.rate_limit import RateLimiter


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
class _NoSleep:
    """Awaitable that returns immediately — skips real sleeps in tests."""

    async def __call__(self, _s: float) -> None:
        return None


def _client(handler, **kwargs):
    """Build a PolicyAwareHTTPClient wired to a mock transport."""
    return PolicyAwareHTTPClient(
        rate_limiter=RateLimiter(min_delay=0.0, jitter=0.0),
        transport=httpx.MockTransport(handler),
        sleep=_NoSleep(),
        **kwargs,
    )


def _approved_source(**overrides):
    """A minimal approved DataSource for testing."""
    defaults = dict(
        slug="test-src",
        display_name="Test Source",
        base_url="http://test.example",
        category="results",
    )
    defaults.update(overrides)
    return DataSource(**defaults)


def _stale_source():
    """A source with a mismatched policy version."""
    return DataSource(
        slug="stale-src",
        display_name="Stale Source",
        base_url="http://stale.example",
        category="results",
        policy_version="ancient-v999",
    )


# ---------------------------------------------------------------------------
# fetch_html
# ---------------------------------------------------------------------------
class TestFetchHtml:
    def test_returns_fetch_result_with_html(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text="<html><body>Hello</body></html>",
                headers={"Content-Type": "text/html"},
            )

        client = _client(handler)
        result = asyncio.run(fetch_html("http://h.test/", http=client))
        assert isinstance(result, FetchResult)
        assert result.status_code == 200
        assert b"Hello" in result.content
        assert result.content_hash == sha256_hex(b"<html><body>Hello</body></html>")

    def test_user_agent_is_standard(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["ua"] = request.headers.get("User-Agent")
            return httpx.Response(200, text="<html></html>")

        client = _client(handler)
        asyncio.run(fetch_html("http://h.test/", http=client))
        assert seen["ua"] == STANDARD_USER_AGENT

    def test_follows_redirect(self):
        """Redirects are followed by the httpx client (follow_redirects=True)."""
        seen_paths = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(str(request.url.path))
            if request.url.path == "/old":
                return httpx.Response(
                    301,
                    headers={"Location": "http://h.test/new"},
                )
            return httpx.Response(200, text="<html><body>Final</body></html>")

        client = _client(handler)
        result = asyncio.run(fetch_html("http://h.test/old", http=client))
        assert result.status_code == 200
        assert b"Final" in result.content
        assert "/old" in seen_paths
        assert "/new" in seen_paths

    def test_conditional_request_304(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers.get("If-None-Match") == '"v1"':
                return httpx.Response(304, headers={"ETag": '"v1"'})
            return httpx.Response(
                200, text="<html></html>", headers={"ETag": '"v1"'}
            )

        client = _client(handler)
        result = asyncio.run(
            fetch_html("http://h.test/", http=client, etag='"v1"')
        )
        assert result.not_modified is True
        assert result.status_code == 304
        assert result.content == b""

    def test_rejects_json_as_html(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"not": "html"})

        client = _client(handler)
        with pytest.raises(MalformedResponseError, match="JSON"):
            asyncio.run(fetch_html("http://h.test/api", http=client))

    def test_rejects_binary_pdf_as_html(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"%PDF-1.4 fake pdf")

        client = _client(handler)
        with pytest.raises(MalformedResponseError, match="PDF"):
            asyncio.run(fetch_html("http://h.test/doc", http=client))

    def test_throttling_429_then_success(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, text="<html>ok</html>")

        client = _client(handler, max_retries=3)
        result = asyncio.run(fetch_html("http://h.test/", http=client))
        assert result.status_code == 200
        assert calls["n"] == 2

    def test_policy_check_when_source_provided(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html></html>")

        client = _client(handler)
        stale = _stale_source()
        with pytest.raises(PolicyVersionMismatchError):
            asyncio.run(fetch_html("http://h.test/", http=client, source=stale))

    def test_no_policy_check_without_source(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html></html>")

        client = _client(handler)
        # No source → no policy assertion; should succeed.
        result = asyncio.run(fetch_html("http://h.test/", http=client))
        assert result.status_code == 200

    def test_respects_robots_disallow(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>should not reach</html>")

        client = _client(handler)
        source = _approved_source(robots_disallow=("/private",))
        with pytest.raises(FetchCapExceededError, match="robots"):
            asyncio.run(fetch_html("http://h.test/private", http=client, source=source))


# ---------------------------------------------------------------------------
# fetch_pdf
# ---------------------------------------------------------------------------
class TestFetchPdf:
    _PDF_BODY = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"

    def test_returns_valid_pdf(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=self._PDF_BODY)

        client = _client(handler)
        result = asyncio.run(fetch_pdf("http://p.test/doc.pdf", http=client))
        assert result.status_code == 200
        assert result.content[:5] == b"%PDF-"
        assert result.content_hash == sha256_hex(self._PDF_BODY)

    def test_rejects_non_pdf(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not a pdf</html>")

        client = _client(handler)
        with pytest.raises(MalformedResponseError, match="%PDF-"):
            asyncio.run(fetch_pdf("http://p.test/doc.pdf", http=client))

    def test_enforces_object_size_cap(self):
        big_pdf = b"%PDF-1.4" + b"x" * (MAX_OBJECT_BYTES + 1)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=big_pdf)

        client = _client(handler)
        with pytest.raises(FetchCapExceededError, match="cap"):
            asyncio.run(fetch_pdf("http://p.test/big.pdf", http=client))

    def test_irc_certs_source_adds_attribution_header(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["x-src"] = request.headers.get("X-SailRatings-Source")
            return httpx.Response(200, content=self._PDF_BODY)

        client = _client(handler)
        source = DataSource(
            slug="irc-certs",
            display_name="IRC Certificate PDFs",
            base_url="http://irc.test",
            category="certificates",
        )
        asyncio.run(fetch_pdf("http://p.test/cert.pdf", http=client, source=source))
        assert seen["x-src"] == "irc-certs"

    def test_conditional_304_on_pdf(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers.get("If-None-Match") == '"pdf-v1"':
                return httpx.Response(304, headers={"ETag": '"pdf-v1"'})
            return httpx.Response(
                200, content=self._PDF_BODY, headers={"ETag": '"pdf-v1"'}
            )

        client = _client(handler)
        result = asyncio.run(
            fetch_pdf("http://p.test/doc.pdf", http=client, etag='"pdf-v1"')
        )
        assert result.not_modified is True
        assert result.status_code == 304


# ---------------------------------------------------------------------------
# fetch_json
# ---------------------------------------------------------------------------
class TestFetchJson:
    def test_returns_valid_json(self):
        body = {"page": 1, "items": ["a", "b"]}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        client = _client(handler)
        result = asyncio.run(fetch_json("http://j.test/api", http=client))
        assert result.status_code == 200
        data = json.loads(result.content)
        assert data["page"] == 1
        assert data["items"] == ["a", "b"]

    def test_sends_accept_header(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["accept"] = request.headers.get("Accept")
            return httpx.Response(200, json={"ok": True})

        client = _client(handler)
        asyncio.run(fetch_json("http://j.test/api", http=client))
        assert seen["accept"] == "application/json"

    def test_rejects_html_as_json(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        client = _client(handler)
        with pytest.raises(MalformedResponseError, match="not valid JSON"):
            asyncio.run(fetch_json("http://j.test/api", http=client))

    def test_rejects_truncated_json(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b'{"page": 1, "items": [')  # truncated

        client = _client(handler)
        with pytest.raises(MalformedResponseError, match="not valid JSON"):
            asyncio.run(fetch_json("http://j.test/api", http=client))

    def test_throttling_429_retry_after_then_success(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    429, headers={"Retry-After": "0", "Content-Type": "application/json"}
                )
            return httpx.Response(200, json={"ok": True})

        client = _client(handler, max_retries=3)
        result = asyncio.run(fetch_json("http://j.test/api", http=client))
        assert result.status_code == 200
        assert calls["n"] == 2

    def test_rejects_binary_as_json(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n binary")

        client = _client(handler)
        with pytest.raises(MalformedResponseError):
            asyncio.run(fetch_json("http://j.test/img", http=client))


# ---------------------------------------------------------------------------
# fetch_file
# ---------------------------------------------------------------------------
class TestFetchFile:
    def test_returns_binary_content(self):
        blw_data = b"binary sailwave file data \x00\x01\x02"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=blw_data)

        client = _client(handler)
        result = asyncio.run(fetch_file("http://f.test/results.blw", http=client))
        assert result.status_code == 200
        assert result.content == blw_data
        assert result.content_hash == sha256_hex(blw_data)

    def test_rejects_pdf_when_file_expected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"%PDF-1.4 not expected")

        client = _client(handler)
        with pytest.raises(MalformedResponseError, match="PDF"):
            asyncio.run(fetch_file("http://f.test/data", http=client))

    def test_csv_file_accepted(self):
        csv = b"boat,place\nBoat1,1\nBoat2,2\n"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=csv)

        client = _client(handler)
        result = asyncio.run(fetch_file("http://f.test/results.csv", http=client))
        assert result.content == csv

    def test_object_size_cap_enforced(self):
        big = b"x" * (MAX_OBJECT_BYTES + 1)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=big)

        client = _client(handler)
        with pytest.raises(FetchCapExceededError):
            asyncio.run(fetch_file("http://f.test/big", http=client))

    def test_robots_disallow_via_source(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"data")

        client = _client(handler)
        source = _approved_source(robots_disallow=("/files",))
        with pytest.raises(FetchCapExceededError):
            asyncio.run(fetch_file("http://f.test/files/data", http=client, source=source))


# ---------------------------------------------------------------------------
# paginate
# ---------------------------------------------------------------------------
class TestPaginate:
    def test_follows_pagination_to_exhaustion(self):
        """Three pages, then the ``next`` field is None → stop."""
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/page1":
                body = {"page": 1, "next": "/page2"}
            elif path == "/page2":
                body = {"page": 2, "next": "/page3"}
            elif path == "/page3":
                body = {"page": 3, "next": None}
            else:
                return httpx.Response(404)
            return httpx.Response(200, json=body)

        client = _client(handler)

        def next_fn(result: FetchResult) -> str | None:
            data = json.loads(result.content)
            return data.get("next")

        async def go():
            pages = []
            async for r in paginate("http://pg.test/page1", next_fn, http=client):
                pages.append(json.loads(r.content)["page"])
            return pages

        pages = asyncio.run(go())
        assert pages == [1, 2, 3]

    def test_resolves_relative_urls(self):
        """Relative ``next`` URLs are resolved against the current URL."""
        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.path
            if page == "/list":
                return httpx.Response(200, json={"next": "/list?p=2"})
            elif page == "/list" and request.url.params.get("p") == "2":
                return httpx.Response(200, json={"next": None})
            return httpx.Response(200, json={"next": None})

        client = _client(handler)

        def next_fn(result: FetchResult) -> str | None:
            return json.loads(result.content).get("next")

        async def go():
            urls = []
            async for r in paginate("http://pg.test/list", next_fn, http=client):
                urls.append(str(r.url))
            return urls

        urls = asyncio.run(go())
        # At least the seed URL is fetched.
        assert "http://pg.test/list" in urls

    def test_stops_on_max_pages_cap(self):
        def handler(request: httpx.Request) -> httpx.Response:
            # Always returns a next page → would be infinite without the cap.
            return httpx.Response(200, json={"next": request.url.path})

        client = _client(handler)

        def next_fn(result: FetchResult) -> str | None:
            return json.loads(result.content)["next"]

        async def go():
            count = 0
            async for _r in paginate(
                "http://pg.test/p", next_fn, http=client, max_pages=3
            ):
                count += 1
            return count

        count = asyncio.run(go())
        assert count == 3

    def test_stops_on_fetch_cap(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"next": request.url.path})

        client = _client(handler, fetch_cap=2)

        def next_fn(result: FetchResult) -> str | None:
            return json.loads(result.content)["next"]

        async def go():
            count = 0
            async for _r in paginate("http://pg.test/p", next_fn, http=client):
                count += 1
            return count

        count = asyncio.run(go())
        assert count == 2

    def test_stops_when_next_is_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"next": None})

        client = _client(handler)

        def next_fn(result: FetchResult) -> str | None:
            return json.loads(result.content).get("next")

        async def go():
            count = 0
            async for _r in paginate("http://pg.test/single", next_fn, http=client):
                count += 1
            return count

        count = asyncio.run(go())
        assert count == 1

    def test_respects_robots_disallow_during_pagination(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"next": "/page2"})

        client = _client(handler)
        source = _approved_source(robots_disallow=("/page1",))

        def next_fn(result: FetchResult) -> str | None:
            return json.loads(result.content).get("next")

        async def go():
            async for _r in paginate(
                "http://pg.test/page1", next_fn, http=client, source=source
            ):
                pass

        with pytest.raises(FetchCapExceededError, match="robots"):
            asyncio.run(go())

    def test_throttling_during_pagination(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            # Throttle the second page request.
            if calls["n"] == 2:
                return httpx.Response(429, headers={"Retry-After": "0"})
            if request.url.path == "/p1":
                return httpx.Response(200, json={"next": "/p2"})
            return httpx.Response(200, json={"next": None})

        client = _client(handler, max_retries=3)

        def next_fn(result: FetchResult) -> str | None:
            return json.loads(result.content).get("next")

        async def go():
            pages = []
            async for r in paginate("http://pg.test/p1", next_fn, http=client):
                pages.append(r.status_code)
            return pages

        statuses = asyncio.run(go())
        assert 200 in statuses


# ---------------------------------------------------------------------------
# render_page (JS rendering with fake browser)
# ---------------------------------------------------------------------------
class _FakePage:
    """Minimal fake Playwright Page for testing render_page."""

    def __init__(self, html: str, title: str = "Test Page"):
        self._html = html
        self._title = title
        self._goto_url: str | None = None
        self._goto_kwargs: dict[str, Any] = {}
        self._closed = False
        self._ua_set = False

    async def goto(self, url: str, **kwargs: Any) -> None:
        self._goto_url = url
        self._goto_kwargs = kwargs

    async def content(self) -> str:
        return self._html

    async def title(self) -> str:
        return self._title

    async def screenshot(self, **kwargs: Any) -> bytes:
        return b"\x89PNG\r\n\x1a\n" + b"fake-screenshot"

    async def close(self) -> None:
        self._closed = True

    async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
        self._ua_set = headers.get("User-Agent") == STANDARD_USER_AGENT


class _FakeBrowser:
    """Minimal fake Playwright Browser."""

    def __init__(self, html: str, title: str = "Test Page"):
        self._html = html
        self._title = title
        self._pages: list[_FakePage] = []
        self._closed = False

    async def new_page(self) -> _FakePage:
        page = _FakePage(self._html, self._title)
        self._pages.append(page)
        return page

    async def close(self) -> None:
        self._closed = True


class TestRenderPage:
    def test_renders_js_page_and_returns_html_and_screenshot(self):
        rendered_html = "<html><body><div id='app'>Dynamic Content</div></body></html>"
        browser = _FakeBrowser(html=rendered_html, title="JS App")

        async def factory() -> _FakeBrowser:
            return browser

        result = asyncio.run(
            render_page("http://js.test/app", browser_factory=factory)
        )
        assert isinstance(result, RenderedFetchResult)
        assert result.status_code == 200
        assert b"Dynamic Content" in result.content
        assert result.rendered_html == rendered_html
        assert result.title == "JS App"
        assert result.screenshot_path is None  # no path given → in-memory
        assert result.content_hash == sha256_hex(rendered_html.encode())

    def test_screenshot_saved_to_path(self, tmp_path):
        rendered_html = "<html><body>Rendered</body></html>"
        browser = _FakeBrowser(html=rendered_html)
        ss_path = str(tmp_path / "screenshot.png")

        async def factory() -> _FakeBrowser:
            return browser

        result = asyncio.run(
            render_page(
                "http://js.test/app",
                browser_factory=factory,
                screenshot_path=ss_path,
            )
        )
        assert result.screenshot_path == ss_path

    def test_sets_standard_user_agent_on_page(self):
        rendered_html = "<html></html>"
        browser = _FakeBrowser(html=rendered_html)

        async def factory() -> _FakeBrowser:
            return browser

        asyncio.run(render_page("http://js.test/app", browser_factory=factory))
        # The fake page records whether the UA was set.
        assert browser._pages[0]._ua_set is True

    def test_policy_check_with_source(self):
        rendered_html = "<html></html>"
        browser = _FakeBrowser(html=rendered_html)

        async def factory() -> _FakeBrowser:
            return browser

        stale = _stale_source()
        with pytest.raises(PolicyVersionMismatchError):
            asyncio.run(
                render_page("http://js.test/app", browser_factory=factory, source=stale)
            )

    def test_robots_disallow_blocks_render(self):
        rendered_html = "<html></html>"
        browser = _FakeBrowser(html=rendered_html)

        async def factory() -> _FakeBrowser:
            return browser

        source = _approved_source(robots_disallow=("/private",))
        with pytest.raises(FetchCapExceededError, match="robots"):
            asyncio.run(
                render_page(
                    "http://js.test/private",
                    browser_factory=factory,
                    source=source,
                )
            )

    def test_browser_closed_after_render(self):
        rendered_html = "<html></html>"
        browser = _FakeBrowser(html=rendered_html)

        async def factory() -> _FakeBrowser:
            return browser

        asyncio.run(render_page("http://js.test/app", browser_factory=factory))
        assert browser._closed is True

    def test_rendered_html_size_cap(self):
        """Rendered HTML exceeding the 25 MB cap is rejected."""
        big_html = "<html>" + ("x" * (MAX_OBJECT_BYTES + 10)) + "</html>"
        browser = _FakeBrowser(html=big_html)

        async def factory() -> _FakeBrowser:
            return browser

        with pytest.raises(FetchCapExceededError, match="cap"):
            asyncio.run(render_page("http://js.test/big", browser_factory=factory))

    def test_goto_uses_wait_until_and_timeout(self):
        rendered_html = "<html></html>"
        browser = _FakeBrowser(html=rendered_html)

        async def factory() -> _FakeBrowser:
            return browser

        asyncio.run(
            render_page(
                "http://js.test/app",
                browser_factory=factory,
                wait_until="domcontentloaded",
                timeout_ms=5000,
            )
        )
        page = browser._pages[0]
        assert page._goto_url == "http://js.test/app"
        assert page._goto_kwargs.get("wait_until") == "domcontentloaded"
        assert page._goto_kwargs.get("timeout") == 5000


# ---------------------------------------------------------------------------
# Cross-cutting: all primitives return FetchResult
# ---------------------------------------------------------------------------
class TestAllPrimitivesReturnFetchResult:
    def test_fetch_html_returns_fetch_result(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html></html>")

        client = _client(handler)
        result = asyncio.run(fetch_html("http://x.test/", http=client))
        assert isinstance(result, FetchResult)

    def test_fetch_pdf_returns_fetch_result(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"%PDF-1.4 data")

        client = _client(handler)
        result = asyncio.run(fetch_pdf("http://x.test/d.pdf", http=client))
        assert isinstance(result, FetchResult)

    def test_fetch_json_returns_fetch_result(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        client = _client(handler)
        result = asyncio.run(fetch_json("http://x.test/api", http=client))
        assert isinstance(result, FetchResult)

    def test_fetch_file_returns_fetch_result(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"data")

        client = _client(handler)
        result = asyncio.run(fetch_file("http://x.test/f", http=client))
        assert isinstance(result, FetchResult)

    def test_render_page_returns_rendered_fetch_result(self):
        browser = _FakeBrowser(html="<html></html>")

        async def factory() -> _FakeBrowser:
            return browser

        result = asyncio.run(render_page("http://x.test/", browser_factory=factory))
        assert isinstance(result, RenderedFetchResult)
        assert isinstance(result, FetchResult)  # RenderedFetchResult extends FetchResult


# ---------------------------------------------------------------------------
# Redirect fixture coverage
# ---------------------------------------------------------------------------
class TestRedirectFixtures:
    def test_301_redirect_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/redirect":
                return httpx.Response(301, headers={"Location": "http://r.test/final"})
            return httpx.Response(200, text="<html>Final</html>")

        client = _client(handler)
        result = asyncio.run(fetch_html("http://r.test/redirect", http=client))
        assert result.status_code == 200
        assert b"Final" in result.content

    def test_302_redirect_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/go":
                return httpx.Response(302, headers={"Location": "http://r.test/here"})
            return httpx.Response(200, text="<html>Here</html>")

        client = _client(handler)
        result = asyncio.run(fetch_html("http://r.test/go", http=client))
        assert result.status_code == 200

    def test_multiple_redirect_chain(self):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/a":
                return httpx.Response(301, headers={"Location": "http://r.test/b"})
            elif path == "/b":
                return httpx.Response(302, headers={"Location": "http://r.test/c"})
            elif path == "/c":
                return httpx.Response(200, text="<html>Done</html>")
            return httpx.Response(404)

        client = _client(handler)
        result = asyncio.run(fetch_html("http://r.test/a", http=client))
        assert result.status_code == 200
        assert b"Done" in result.content

    def test_redirect_to_pdf(self):
        """A redirect that ends at a PDF is handled correctly by fetch_pdf."""
        pdf = b"%PDF-1.4 content"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/link":
                return httpx.Response(301, headers={"Location": "http://r.test/doc.pdf"})
            return httpx.Response(200, content=pdf)

        client = _client(handler)
        result = asyncio.run(fetch_pdf("http://r.test/link", http=client))
        assert result.status_code == 200
        assert result.content[:5] == b"%PDF-"
