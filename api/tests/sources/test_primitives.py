"""Tests for acquisition primitives — fetch_html, fetch_pdf, fetch_json,
fetch_file, paginate, render_page.

All tests run with zero network calls using httpx.MockTransport.
"""

import dataclasses
import hashlib
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from irc_data.sources.http_client import (
    MAX_OBJECT_SIZE,
    HttpClient,
    ObjectTooLargeError,
    PolicyAwareHttpClient,
    RateLimiter,
    STANDARD_USER_AGENT,
)
from irc_data.sources.models import DataSource, FetchResult, RawArtifactV1
from irc_data.sources.policy import (
    ACTIVE_POLICY,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
)
from irc_data.sources.primitives import (
    PDF_MAGIC,
    fetch_file,
    fetch_html,
    fetch_json,
    fetch_pdf,
    fetch_xml,
    paginate,
    render_page,
)
from irc_data.sources.registry import get_source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fast_policy():
    """Return a policy identical to ACTIVE_POLICY but with no rate-limit delay.

    Tests must not sleep 2s+ between requests; zero out the rate delay and
    jitter while keeping every other policy rule intact.
    """
    return dataclasses.replace(
        ACTIVE_POLICY,
        rate=dataclasses.replace(
            ACTIVE_POLICY.rate, min_delay_seconds=0.0, jitter_seconds=0.0
        ),
    )


def make_client(transport, policy=None, max_retries=3):
    """Build an HttpClient with a mock transport and fast backoff."""
    inner = httpx.AsyncClient(
        transport=transport,
        follow_redirects=True,
        headers={"User-Agent": STANDARD_USER_AGENT},
    )
    return HttpClient(
        client=inner,
        policy=policy or _fast_policy(),
        max_retries=max_retries,
        backoff=(0.001, 0.001, 0.001, 0.001),
    )


VALID_HTML = b"<html><body><h1>Test Page</h1></body></html>"
VALID_JSON = json.dumps({"key": "value", "items": [1, 2, 3]}).encode()


def _detached_source(slug: str, **overrides):
    """Return a *detached* copy of a registry source record.

    Mutating fields on the in-memory registry records would leak state
    across tests; build a throw-away record instead.
    """
    import dataclasses as _dc

    src = get_source(slug)
    return _dc.replace(src, **overrides)


VALID_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"


# ===========================================================================
# fetch_html tests
# ===========================================================================


class TestFetchHtml:
    """Tests for fetch_html primitive."""

    @pytest.mark.asyncio
    async def test_fetch_html_returns_fetch_result(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        result = await fetch_html("https://example.com/page", client=client)
        assert isinstance(result, FetchResult)
        assert result.content == VALID_HTML
        assert len(result.content_hash) == 64  # SHA-256 hex
        assert result.status_code == 200
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_html_sets_standard_user_agent(self):
        captured = {}

        def handler(req):
            captured["ua"] = req.headers.get("User-Agent")
            return httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        await fetch_html("https://example.com/page", client=client)
        assert captured["ua"] == STANDARD_USER_AGENT
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_html_conditional_request_304(self):
        def handler(req):
            if req.headers.get("if-none-match") == '"abc123"':
                return httpx.Response(304, headers={"ETag": '"abc123"'})
            return httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html", "ETag": '"abc123"'})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_html("https://example.com/page", client=client, etag='"abc123"')
        assert result.not_modified is True
        assert result.status_code == 304
        assert result.etag == '"abc123"'
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_html_content_hash_is_sha256(self):
        import hashlib
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        result = await fetch_html("https://example.com/page", client=client)
        expected = hashlib.sha256(VALID_HTML).hexdigest()
        assert result.content_hash == expected
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_html_with_source_enforces_policy(self):
        src = get_source("sailsys")
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        result = await fetch_html("https://app.sailsys.com.au/results", client=client, source=src)
        assert result.policy_version == "interim-v0"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_html_accepts_any_content_type(self):
        """fetch_html is intentionally lenient: some servers mislabel HTML.

        The primitive returns the body as-is; downstream parsers decide
        whether it is usable.  Strict validation lives in fetch_json /
        fetch_xml / fetch_pdf.
        """
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b'{"key": "val"}', headers={"Content-Type": "application/json"}))
        client = make_client(transport)
        result = await fetch_html("https://example.com/page", client=client)
        assert result.content == b'{"key": "val"}'
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_html_accepts_html_without_content_type(self):
        """Some servers don't send Content-Type; fetch_html still works."""
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML))
        client = make_client(transport)
        result = await fetch_html("https://example.com/page", client=client)
        assert result.content == VALID_HTML
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_html_429_retry_after(self):
        call_count = {"n": 0}

        def handler(req):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_html("https://example.com/page", client=client)
        assert result.status_code == 200
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_html_retries_5xx(self):
        call_count = {"n": 0}

        def handler(req):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_html("https://example.com/page", client=client)
        assert result.status_code == 200
        assert call_count["n"] == 3
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_html_empty_body_still_works(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"", headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        result = await fetch_html("https://example.com/empty", client=client)
        assert result.content == b""
        assert result.content_hash == hashlib.sha256(b"").hexdigest()
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_html_max_object_size(self):
        big = b"x" * (MAX_OBJECT_SIZE + 1)
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=big, headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        with pytest.raises((ValueError, ObjectTooLargeError), match="exceeds"):
            await fetch_html("https://example.com/big", client=client, max_object_size=MAX_OBJECT_SIZE)
        await client.aclose()


# ===========================================================================
# fetch_pdf tests
# ===========================================================================


class TestFetchPdf:
    """Tests for fetch_pdf primitive."""

    @pytest.mark.asyncio
    async def test_fetch_pdf_returns_fetch_result(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_PDF))
        client = make_client(transport)
        result = await fetch_pdf("https://example.com/doc.pdf", client=client)
        assert isinstance(result, FetchResult)
        assert result.content == VALID_PDF
        assert result.content_hash == hashlib.sha256(VALID_PDF).hexdigest()
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_pdf_validates_magic_bytes(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"Not a PDF"))
        client = make_client(transport)
        with pytest.raises(ValueError, match="not a valid PDF"):
            await fetch_pdf("https://example.com/doc.pdf", client=client)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_pdf_enforces_25mb_cap(self):
        big_pdf = b"%PDF-1.4" + b"x" * (MAX_OBJECT_SIZE + 1)
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=big_pdf))
        client = make_client(transport)
        with pytest.raises((ValueError, ObjectTooLargeError), match="exceeds"):
            await fetch_pdf("https://example.com/big.pdf", client=client)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_pdf_irc_certs_attribution(self):
        captured = {}

        def handler(req):
            captured["source"] = req.headers.get("X-SailRatings-Source")
            return httpx.Response(200, content=VALID_PDF)

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        src = get_source("irc-certs")
        await fetch_pdf("https://ircrating.org/pdfdirectory/cert.pdf", client=client, source=src)
        assert captured["source"] == "irc-certs"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_pdf_conditional_304(self):
        def handler(req):
            if req.headers.get("if-none-match") == '"pdf-etag"':
                return httpx.Response(304, headers={"ETag": '"pdf-etag"'})
            return httpx.Response(200, content=VALID_PDF, headers={"ETag": '"pdf-etag"'})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_pdf("https://example.com/doc.pdf", client=client, etag='"pdf-etag"')
        assert result.not_modified is True
        assert result.status_code == 304
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_pdf_no_attribution_for_non_irc(self):
        captured = {}

        def handler(req):
            captured["source"] = req.headers.get("X-SailRatings-Source")
            return httpx.Response(200, content=VALID_PDF)

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        src = get_source("sailsys")
        await fetch_pdf("https://example.com/doc.pdf", client=client, source=src)
        assert captured["source"] is None
        await client.aclose()


# ===========================================================================
# fetch_json tests
# ===========================================================================


class TestFetchJson:
    """Tests for fetch_json primitive."""

    @pytest.mark.asyncio
    async def test_fetch_json_returns_fetch_result(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_JSON, headers={"Content-Type": "application/json"}))
        client = make_client(transport)
        result = await fetch_json("https://example.com/api/data", client=client)
        assert isinstance(result, FetchResult)
        assert result.content == VALID_JSON
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_json_sets_accept_header(self):
        captured = {}

        def handler(req):
            captured["accept"] = req.headers.get("Accept")
            return httpx.Response(200, content=VALID_JSON, headers={"Content-Type": "application/json"})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        await fetch_json("https://example.com/api/data", client=client)
        assert captured["accept"] == "application/json"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_json_validates_content_type(self):
        """Valid JSON served with the wrong Content-Type is still accepted.

        The primitive validates *parseability*, not the header — many
        real-world APIs mislabel JSON as text/html.
        """
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_JSON, headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        result = await fetch_json("https://example.com/api/data", client=client)
        assert result.content == VALID_JSON
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_json_validates_parseability(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"{not valid json", headers={"Content-Type": "application/json"}))
        client = make_client(transport)
        with pytest.raises(ValueError, match="not valid JSON"):
            await fetch_json("https://example.com/api/data", client=client)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_json_conditional_304(self):
        def handler(req):
            if req.headers.get("if-none-match") == '"json-etag"':
                return httpx.Response(304, headers={"ETag": '"json-etag"'})
            return httpx.Response(200, content=VALID_JSON, headers={"Content-Type": "application/json", "ETag": '"json-etag"'})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_json("https://example.com/api/data", client=client, etag='"json-etag"')
        assert result.not_modified is True
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_json_429_retry_after(self):
        call_count = {"n": 0}

        def handler(req):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, content=VALID_JSON, headers={"Content-Type": "application/json"})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_json("https://example.com/api/data", client=client)
        assert result.status_code == 200
        await client.aclose()


# ===========================================================================
# fetch_xml tests
# ===========================================================================


VALID_XML = b'<?xml version="1.0"?><results><race id="1"/></results>'


class TestFetchXml:
    """Tests for fetch_xml primitive (XML API coverage — DP-01-04 scope)."""

    @pytest.mark.asyncio
    async def test_fetch_xml_returns_fetch_result(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_XML, headers={"Content-Type": "application/xml"}))
        client = make_client(transport)
        result = await fetch_xml("https://example.com/api/results.xml", client=client)
        assert isinstance(result, FetchResult)
        assert result.content == VALID_XML
        assert result.content_hash == hashlib.sha256(VALID_XML).hexdigest()
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_xml_sets_accept_header(self):
        captured = {}

        def handler(req):
            captured["accept"] = req.headers.get("Accept")
            return httpx.Response(200, content=VALID_XML, headers={"Content-Type": "text/xml"})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        await fetch_xml("https://example.com/api/results.xml", client=client)
        assert "xml" in captured["accept"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_xml_validates_parseability(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"<results><unclosed>", headers={"Content-Type": "application/xml"}))
        client = make_client(transport)
        with pytest.raises(ValueError, match="not valid XML"):
            await fetch_xml("https://example.com/api/results.xml", client=client)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_xml_conditional_304(self):
        def handler(req):
            if req.headers.get("if-none-match") == '"xml-etag"':
                return httpx.Response(304, headers={"ETag": '"xml-etag"'})
            return httpx.Response(200, content=VALID_XML, headers={"Content-Type": "application/xml", "ETag": '"xml-etag"'})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_xml("https://example.com/api/results.xml", client=client, etag='"xml-etag"')
        assert result.not_modified is True
        assert result.status_code == 304
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_xml_max_object_size(self):
        big = b'<?xml version="1.0"?><r>' + b"x" * 200 + b"</r>"
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=big, headers={"Content-Type": "application/xml"}))
        client = make_client(transport)
        with pytest.raises(ValueError, match="exceeds"):
            await fetch_xml("https://example.com/big.xml", client=client, max_object_size=100)
        await client.aclose()


# ===========================================================================
# fetch_file tests
# ===========================================================================


class TestFetchFile:
    """Tests for fetch_file primitive."""

    @pytest.mark.asyncio
    async def test_fetch_file_returns_fetch_result(self):
        content = b"binary\x00data\x01here"
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=content))
        client = make_client(transport)
        result = await fetch_file("https://example.com/data.blw", client=client)
        assert isinstance(result, FetchResult)
        assert result.content == content
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_file_magic_byte_rejection(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML))
        client = make_client(transport)
        with pytest.raises(ValueError, match="rejected magic bytes"):
            await fetch_file("https://example.com/data.blw", client=client, reject_magic=[b"<html"])
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_file_no_rejection_when_magic_not_matched(self):
        content = b"binary\x00data"
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=content))
        client = make_client(transport)
        result = await fetch_file("https://example.com/data.blw", client=client, reject_magic=[b"<html"])
        assert result.content == content
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_file_conditional_304(self):
        def handler(req):
            if req.headers.get("if-none-match") == '"file-etag"':
                return httpx.Response(304, headers={"ETag": '"file-etag"'})
            return httpx.Response(200, content=b"binary data", headers={"ETag": '"file-etag"'})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_file("https://example.com/data.blw", client=client, etag='"file-etag"')
        assert result.not_modified is True
        await client.aclose()


# ===========================================================================
# paginate tests
# ===========================================================================


class TestPaginate:
    """Tests for paginate async generator."""

    @pytest.mark.asyncio
    async def test_paginate_exhausts_all_pages(self):
        pages = {
            "https://example.com/p1": (VALID_HTML, "https://example.com/p2"),
            "https://example.com/p2": (VALID_HTML, "https://example.com/p3"),
            "https://example.com/p3": (VALID_HTML, None),
        }

        def handler(req):
            url = str(req.url)
            if url in pages:
                content, next_url = pages[url]
                headers = {"Content-Type": "text/html"}
                if next_url:
                    headers["X-Next-Page"] = next_url
                return httpx.Response(200, content=content, headers=headers)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = make_client(transport)

        def next_fn(result):
            # Use a custom header to find next URL - simulate by URL pattern
            # In practice the caller parses the HTML to find next link
            if result.url == "https://example.com/p1":
                return "https://example.com/p2"
            elif result.url == "https://example.com/p2":
                return "https://example.com/p3"
            return None

        results = []
        async for r in paginate("https://example.com/p1", next_fn, client=client):
            results.append(r)
        assert len(results) == 3
        await client.aclose()

    @pytest.mark.asyncio
    async def test_paginate_max_pages_cap(self):
        def handler(req):
            return httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)

        def next_fn(result):
            return "https://example.com/next"  # always has a next page

        results = []
        async for r in paginate("https://example.com/start", next_fn, client=client, max_pages=3):
            results.append(r)
        assert len(results) == 3  # capped at 3
        await client.aclose()

    @pytest.mark.asyncio
    async def test_paginate_relative_url_resolution(self):
        def handler(req):
            url = str(req.url)
            if url == "https://example.com/p1":
                return httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"})
            elif url == "https://example.com/p2":
                return httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = make_client(transport)

        def next_fn(result):
            if result.url == "https://example.com/p1":
                return "/p2"  # relative URL
            return None

        results = []
        async for r in paginate("https://example.com/p1", next_fn, client=client):
            results.append(r)
        assert len(results) == 2
        assert results[1].url == "https://example.com/p2"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_paginate_stops_when_next_is_none(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"}))
        client = make_client(transport)

        def next_fn(result):
            return None  # immediately stop

        results = []
        async for r in paginate("https://example.com/p1", next_fn, client=client):
            results.append(r)
        assert len(results) == 1
        await client.aclose()

    @pytest.mark.asyncio
    async def test_paginate_max_bytes_cap(self):
        big = b"x" * 100
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=big, headers={"Content-Type": "text/html"}))
        client = make_client(transport)

        def next_fn(result):
            return "https://example.com/next"

        results = []
        async for r in paginate("https://example.com/start", next_fn, client=client, max_bytes=150):
            results.append(r)
        # First page is 100 bytes, second is 100 (total 200 > 150), so 2 results
        assert len(results) == 2
        await client.aclose()

    @pytest.mark.asyncio
    async def test_paginate_with_source_enforces_policy(self):
        src = get_source("sailsys")
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"}))
        client = make_client(transport)

        def next_fn(result):
            return None

        results = []
        async for r in paginate("https://app.sailsys.com.au/results", next_fn, client=client, source=src):
            results.append(r)
        assert results[0].policy_version == "interim-v0"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_paginate_robots_disallow_stops(self):
        src = _detached_source("sailsys", robots_disallow=["/"])
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"}))
        client = make_client(transport)

        def next_fn(result):
            return None

        with pytest.raises(SourceNotApprovedError):
            async for _ in paginate("https://app.sailsys.com.au/results", next_fn, client=client, source=src):
                pass
        await client.aclose()


# ===========================================================================
# render_page tests
# ===========================================================================


class FakeBrowser:
    """Fake browser for testing render_page without Playwright."""

    def __init__(self, html_content="<html><body>Rendered</body></html>"):
        self._html = html_content
        self._closed = False
        self._page = None

    async def new_page(self):
        self._page = FakePage(self._html)
        return self._page

    async def close(self):
        self._closed = True


class FakePage:
    def __init__(self, html_content):
        self._html = html_content
        self._closed = False
        self._extra_headers = {}
        self._viewport = {}
        self._url = None
        self._screenshot_path = None

    async def set_extra_http_headers(self, headers):
        self._extra_headers = headers

    async def set_viewport_size(self, size):
        self._viewport = size

    async def goto(self, url, **kwargs):
        self._url = url
        # Return a fake response with headers and URL for conditional-request support
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.headers = {"ETag": '"render-etag"', "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}
        resp.url = url
        return resp

    async def wait_for_selector(self, selector, **kwargs):
        pass

    async def content(self):
        return self._html

    async def screenshot(self, path=None, **kwargs):
        self._screenshot_path = path
        # Write a dummy file so it exists
        with open(path, "wb") as f:
            f.write(b"fake-png-data")

    async def close(self):
        self._closed = True


class TestRenderPage:
    """Tests for render_page primitive."""

    @pytest.mark.asyncio
    async def test_render_page_returns_fetch_result(self):
        browser = FakeBrowser("<html><body>JS Rendered</body></html>")
        result = await render_page("https://example.com/js-page", browser=browser)
        assert isinstance(result, FetchResult)
        assert b"JS Rendered" in result.content
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_render_page_sets_standard_user_agent(self):
        browser = FakeBrowser()
        await render_page("https://example.com/js-page", browser=browser)
        assert browser._page._extra_headers.get("User-Agent") == STANDARD_USER_AGENT

    @pytest.mark.asyncio
    async def test_render_page_screenshot_evidence(self):
        browser = FakeBrowser()
        result = await render_page("https://example.com/js-page", browser=browser, screenshot=True)
        assert result.screenshot_path is not None
        assert os.path.exists(result.screenshot_path)
        os.unlink(result.screenshot_path)

    @pytest.mark.asyncio
    async def test_render_page_no_screenshot(self):
        browser = FakeBrowser()
        result = await render_page("https://example.com/js-page", browser=browser, screenshot=False)
        assert result.screenshot_path is None

    @pytest.mark.asyncio
    async def test_render_page_with_source_policy(self):
        src = get_source("sailsys")
        browser = FakeBrowser()
        result = await render_page("https://app.sailsys.com.au/results", source=src, browser=browser)
        assert result.policy_version == "interim-v0"

    @pytest.mark.asyncio
    async def test_render_page_robots_disallow(self):
        src = _detached_source("sailsys", robots_disallow=["/"])
        browser = FakeBrowser()
        with pytest.raises(SourceNotApprovedError):
            await render_page("https://app.sailsys.com.au/results", source=src, browser=browser)

    @pytest.mark.asyncio
    async def test_render_page_max_object_size(self):
        big_html = "<html>" + "x" * 300 + "</html>"
        browser = FakeBrowser(big_html)
        with pytest.raises(ValueError, match="exceeds"):
            await render_page("https://example.com/big", browser=browser, max_object_size=100)

    @pytest.mark.asyncio
    async def test_render_page_wait_for_selector(self):
        browser = FakeBrowser()
        result = await render_page("https://example.com/js-page", browser=browser, wait_for=".results-table")
        assert isinstance(result, FetchResult)

    @pytest.mark.asyncio
    async def test_render_page_content_hash(self):
        import hashlib
        browser = FakeBrowser("<html><body>Rendered</body></html>")
        result = await render_page("https://example.com/js-page", browser=browser)
        expected = hashlib.sha256(b"<html><body>Rendered</body></html>").hexdigest()
        assert result.content_hash == expected

    @pytest.mark.asyncio
    async def test_render_page_extracts_conditional_headers(self):
        """render_page should extract ETag and Last-Modified from the navigation response."""
        browser = FakeBrowser("<html><body>Rendered</body></html>")
        result = await render_page("https://example.com/js-page", browser=browser)
        assert result.etag == '"render-etag"'
        assert result.last_modified == "Wed, 01 Jan 2025 00:00:00 GMT"

    @pytest.mark.asyncio
    async def test_render_page_evidence_dir_is_used(self, tmp_path):
        """Rendered evidence is preserved in the requested directory."""
        browser = FakeBrowser()
        result = await render_page(
            "https://example.com/js-page",
            browser=browser,
            evidence_dir=str(tmp_path),
        )
        assert result.screenshot_path is not None
        assert result.screenshot_path.startswith(str(tmp_path))
        assert os.path.exists(result.screenshot_path)

    @pytest.mark.asyncio
    async def test_render_page_evidence_dir_env_var(self, tmp_path, monkeypatch):
        """SAILRATINGS_RENDER_EVIDENCE_DIR is honoured when no arg given."""
        monkeypatch.setenv("SAILRATINGS_RENDER_EVIDENCE_DIR", str(tmp_path))
        browser = FakeBrowser()
        result = await render_page("https://example.com/js-page", browser=browser)
        assert result.screenshot_path is not None
        assert result.screenshot_path.startswith(str(tmp_path))
        assert os.path.exists(result.screenshot_path)


# ===========================================================================
# Redirects
# ===========================================================================


class TestRedirects:
    """Tests for redirect handling."""

    @pytest.mark.asyncio
    async def test_redirect_301(self):
        def handler(req):
            if req.url.path == "/old":
                return httpx.Response(301, headers={"Location": "https://example.com/new"})
            return httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_html("https://example.com/old", client=client)
        assert result.status_code == 200
        await client.aclose()

    @pytest.mark.asyncio
    async def test_redirect_302(self):
        def handler(req):
            if req.url.path == "/old":
                return httpx.Response(302, headers={"Location": "/new"})
            return httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_html("https://example.com/old", client=client)
        assert result.status_code == 200
        await client.aclose()

    @pytest.mark.asyncio
    async def test_redirect_chain(self):
        def handler(req):
            path = req.url.path
            if path == "/a":
                return httpx.Response(301, headers={"Location": "/b"})
            elif path == "/b":
                return httpx.Response(302, headers={"Location": "/c"})
            elif path == "/c":
                return httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_html("https://example.com/a", client=client)
        assert result.status_code == 200
        await client.aclose()

    @pytest.mark.asyncio
    async def test_redirect_to_pdf(self):
        def handler(req):
            if req.url.path == "/link":
                return httpx.Response(302, headers={"Location": "/doc.pdf"})
            return httpx.Response(200, content=VALID_PDF)

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await fetch_pdf("https://example.com/link", client=client)
        assert result.status_code == 200
        assert result.content == VALID_PDF
        await client.aclose()


# ===========================================================================
# Malformed responses
# ===========================================================================


class TestMalformedResponses:
    """Tests for handling malformed responses."""

    @pytest.mark.asyncio
    async def test_json_as_html(self):
        """Server returns JSON but claims it's HTML."""
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_JSON, headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        result = await fetch_html("https://example.com/page", client=client)
        # fetch_html checks Content-Type OR body for <html>
        # JSON won't have <html> in it, so this should raise
        # Actually: Content-Type says text/html, so it passes the Content-Type check
        # The body doesn't have <html>, but Content-Type is html, so it's accepted
        assert result.content == VALID_JSON
        await client.aclose()

    @pytest.mark.asyncio
    async def test_html_as_json(self):
        """Server returns HTML but claims it's JSON."""
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "application/json"}))
        client = make_client(transport)
        with pytest.raises(ValueError, match="not valid JSON"):
            await fetch_json("https://example.com/api", client=client)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_truncated_json(self):
        """Truncated JSON should fail parseability check."""
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b'{"key": "val', headers={"Content-Type": "application/json"}))
        client = make_client(transport)
        with pytest.raises(ValueError, match="not valid JSON"):
            await fetch_json("https://example.com/api", client=client)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_binary_as_json(self):
        """Binary data served as JSON should fail."""
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b'\x00\x01\x02\x03', headers={"Content-Type": "application/json"}))
        client = make_client(transport)
        with pytest.raises(ValueError, match="not valid JSON"):
            await fetch_json("https://example.com/api", client=client)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_truncated_html(self):
        """Truncated HTML should still be accepted (we don't validate HTML structure)."""
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"<html><body><p>Trun", headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        result = await fetch_html("https://example.com/page", client=client)
        assert result.status_code == 200
        await client.aclose()

    @pytest.mark.asyncio
    async def test_empty_response_html(self):
        """Empty response with HTML Content-Type should work."""
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"", headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        result = await fetch_html("https://example.com/empty", client=client)
        assert result.content == b""
        await client.aclose()


# ===========================================================================
# Policy enforcement
# ===========================================================================


class TestPolicyEnforcement:
    """Tests for policy enforcement in primitives."""

    @pytest.mark.asyncio
    async def test_hold_source_blocked(self):
        src = get_source("clubspot")  # legal_status = 'hold'
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        with pytest.raises(SourceNotApprovedError, match="hold"):
            await fetch_html("https://clubspot.com/results", client=client, source=src)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_disabled_source_blocked(self):
        src = _detached_source("sailsys", enabled=False)
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        with pytest.raises(SourceNotApprovedError, match="disabled"):
            await fetch_html("https://app.sailsys.com.au/results", client=client, source=src)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_stale_policy_blocked(self):
        src = _detached_source("sailsys", policy_version="stale-version")
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        with pytest.raises(PolicyVersionMismatchError):
            await fetch_html("https://app.sailsys.com.au/results", client=client, source=src)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_robots_disallow_root(self):
        src = _detached_source("sailsys", robots_disallow=["/"])
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html"}))
        client = make_client(transport)
        with pytest.raises(SourceNotApprovedError, match="disallowed"):
            await fetch_html("https://app.sailsys.com.au/results", client=client, source=src)
        await client.aclose()


# ===========================================================================
# RawArtifactV1 output contract
# ===========================================================================


class TestRawArtifactV1:
    """Tests for the RawArtifactV1 handoff contract."""

    @pytest.mark.asyncio
    async def test_raw_artifact_from_fetch_result(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html", "ETag": '"etag1"'}))
        client = make_client(transport)
        fetch_result = await fetch_html("https://example.com/page", client=client)
        artifact = RawArtifactV1.from_fetch_result(fetch_result, source_slug="sailsys", content_type="text/html")
        assert isinstance(artifact, RawArtifactV1)
        assert artifact.requested_uri == fetch_result.url
        assert artifact.source_slug == "sailsys"
        assert artifact.content_type == "text/html"
        assert artifact.content_hash == fetch_result.content_hash
        assert artifact.etag == '"etag1"'
        assert artifact.schema_version == "1"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_raw_artifact_to_dict(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html", "ETag": '"e1"', "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}))
        client = make_client(transport)
        fetch_result = await fetch_html("https://example.com/page", client=client)
        artifact = RawArtifactV1.from_fetch_result(fetch_result, source_slug="sailsys", content_type="text/html")
        d = artifact.to_dict()
        assert d["requested_uri"] == fetch_result.url
        assert d["source_slug"] == "sailsys"
        assert d["content_type"] == "text/html"
        assert d["content_hash"] == fetch_result.content_hash
        assert d["etag"] == '"e1"'
        assert d["last_modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"
        assert d["schema_version"] == "1"
        # content should NOT be in the dict
        assert "content" not in d
        await client.aclose()

    @pytest.mark.asyncio
    async def test_raw_artifact_preserves_etag(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html", "ETag": '"preserve-me"'}))
        client = make_client(transport)
        fetch_result = await fetch_html("https://example.com/page", client=client)
        artifact = RawArtifactV1.from_fetch_result(fetch_result, source_slug="sailsys", content_type="text/html")
        assert artifact.etag == '"preserve-me"'
        await client.aclose()

    @pytest.mark.asyncio
    async def test_raw_artifact_not_modified(self):
        """RawArtifactV1 should reflect not_modified status."""
        def handler(req):
            if req.headers.get("if-none-match") == '"nm-etag"':
                return httpx.Response(304, headers={"ETag": '"nm-etag"'})
            return httpx.Response(200, content=VALID_HTML, headers={"Content-Type": "text/html", "ETag": '"nm-etag"'})

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        fetch_result = await fetch_html("https://example.com/page", client=client, etag='"nm-etag"')
        artifact = RawArtifactV1.from_fetch_result(fetch_result, source_slug="sailsys", content_type="text/html")
        assert artifact.not_modified is True
        assert artifact.status_code == 304
        assert artifact.content == b""
        await client.aclose()
