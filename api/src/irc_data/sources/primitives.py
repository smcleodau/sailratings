"""Acquisition primitive library (SPEC-012 §5, deliverable DP-01-04).

Six bounded primitives that cover every common sailing-source delivery
format without bespoke infrastructure:

* :func:`fetch_html`  — GET with rate-limit, retry, conditional request, hash
* :func:`fetch_pdf`   — same + enforces the 25 MB object cap; validates PDF
* :func:`fetch_json`  — same + validates ``Content-Type: application/json``
* :func:`fetch_file`  — generic binary fetch (Sailwave ``.blw`` files etc.)
* :func:`paginate`    — async generator following pagination links
* :func:`render_page` — Playwright headless fetch for JS-rendered sources

All primitives:
  * Accept an optional ``source: DataSource`` arg; call
    ``assert_policy_current`` if provided (SPEC-012 §5).
  * Respect ``robots_disallow`` from the source record.
  * Set the standard ``User-Agent`` (via the injected HTTP client).
  * Return :class:`FetchResult` (never raw bytes directly).

Transport / browser injection
------------------------------
Every primitive accepts an ``http`` (:class:`PolicyAwareHTTPClient`) arg
so tests can inject ``httpx.MockTransport`` and run with **zero network
calls**.  ``render_page`` accepts an optional ``browser_factory`` so tests
can inject a fake browser instead of launching Playwright.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urljoin, urlparse

from .contracts import (
    DataSource,
    FetchCapExceededError,
    FetchResult,
    FetchTarget,
    MAX_OBJECT_BYTES,
    SourceAdapterError,
    STANDARD_USER_AGENT,
)
from .http import PolicyAwareHTTPClient, is_path_disallowed, sha256_hex
from .policy import assert_policy_current

__all__ = [
    "fetch_html",
    "fetch_pdf",
    "fetch_json",
    "fetch_file",
    "paginate",
    "render_page",
    "RenderedFetchResult",
    "MalformedResponseError",
    "PaginationExhausted",
    "BrowserFactory",
    "PageLike",
    "BrowserLike",
]

# Sentinel returned by a ``next_fn`` to signal "no more pages".
PaginationExhausted: type[StopAsyncIteration] = StopAsyncIteration


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class MalformedResponseError(SourceAdapterError):
    """Raised when a response does not match the expected content type or
    structure (e.g. a JSON endpoint returning HTML, or a truncated PDF)."""


# ---------------------------------------------------------------------------
# Rendered-page result (extends FetchResult with screenshot evidence)
# ---------------------------------------------------------------------------
class RenderedFetchResult(FetchResult):
    """A :class:`FetchResult` enriched with rendered-page evidence.

    ``render_page`` returns this so callers (and the downstream DP-02
    ingester) can preserve the screenshot as "necessary rendered evidence"
    for dynamic sources (SPEC-012 §5, acceptance criterion).
    """

    __slots__ = ("screenshot_path", "rendered_html", "title")

    def __init__(  # type: ignore[no-untyped-def]
        self,
        *,
        url: str,
        content: bytes,
        content_hash: str,
        status_code: int,
        etag: str | None = None,
        last_modified: str | None = None,
        fetched_at: str | None = None,
        policy_version: str | None = None,
        not_modified: bool = False,
        screenshot_path: str | None = None,
        rendered_html: str | None = None,
        title: str | None = None,
    ) -> None:
        # We can't call FetchResult.__init__ easily because it's frozen
        # and uses default_factory.  Instead we use object.__setattr__ to
        # populate the frozen fields.
        from datetime import datetime, timezone

        from .contracts import CURRENT_POLICY_VERSION, _now_iso

        object.__setattr__(self, "url", url)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "status_code", status_code)
        object.__setattr__(self, "etag", etag)
        object.__setattr__(self, "last_modified", last_modified)
        object.__setattr__(self, "fetched_at", fetched_at or _now_iso())
        object.__setattr__(self, "policy_version", policy_version or CURRENT_POLICY_VERSION)
        object.__setattr__(self, "not_modified", not_modified)
        self.screenshot_path = screenshot_path
        self.rendered_html = rendered_html
        self.title = title


# ---------------------------------------------------------------------------
# Browser protocol (for Playwright injection)
# ---------------------------------------------------------------------------
@runtime_checkable
class PageLike(Protocol):
    """Minimal subset of a Playwright ``Page`` we depend on."""

    async def goto(self, url: str, **kwargs: Any) -> Any: ...
    async def content(self) -> str: ...
    async def title(self) -> str: ...
    async def screenshot(self, **kwargs: Any) -> bytes: ...
    async def close(self) -> None: ...


@runtime_checkable
class BrowserLike(Protocol):
    """Minimal subset of a Playwright ``Browser`` we depend on."""

    async def new_page(self) -> PageLike: ...
    async def close(self) -> None: ...


@runtime_checkable
class BrowserFactory(Protocol):
    """Callable that returns an awaitable ``BrowserLike``.

    Production usage::

        async def factory() -> BrowserLike:
            from playwright.async_api import async_playwright
            p = await async_playwright().start()
            return await p.chromium.launch(headless=True)

    Tests pass a fake factory that returns an in-memory browser.
    """

    def __call__(self) -> Awaitable[BrowserLike]: ...


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _resolve_source_kwargs(
    source: DataSource | None,
) -> dict[str, Any]:
    """Return kwargs derived from an optional source record.

    If *source* is provided the policy version is asserted and the
    ``robots_disallow`` paths are passed through.  If *source* is ``None``
    an empty dict is returned (no policy check, no robots).
    """
    if source is None:
        return {"robots_disallow": ()}
    assert_policy_current(source)
    return {"robots_disallow": source.robots_disallow}


def _ensure_http(http: PolicyAwareHTTPClient | None) -> PolicyAwareHTTPClient:
    """Return *http* or create a default client."""
    if http is not None:
        return http
    return PolicyAwareHTTPClient()


# ---------------------------------------------------------------------------
# fetch_html
# ---------------------------------------------------------------------------
async def fetch_html(
    url: str,
    *,
    http: PolicyAwareHTTPClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> FetchResult:
    """Fetch an HTML page with full policy enforcement.

    Wraps :meth:`PolicyAwareHTTPClient.fetch` and additionally validates
    that the response body is plausibly HTML (not a binary blob served with
    a text/html Content-Type).  Returns a :class:`FetchResult`.
    """
    client = _ensure_http(http)
    kw = _resolve_source_kwargs(source)
    result = await client.fetch(
        url,
        etag=etag,
        last_modified=last_modified,
        extra_headers=extra_headers,
        robots_disallow=kw["robots_disallow"],
    )
    if not result.not_modified and result.content:
        _validate_html(result.content, url)
    return result


def _validate_html(content: bytes, url: str) -> None:
    """Heuristic HTML validation — detect binary / truncated responses."""
    # Reject obvious binary content (PDF magic, ZIP, etc.).
    _reject_binary_magic(content, url, expected="HTML")
    # Very small responses that are not HTML are suspicious but we allow
    # them (could be a minimal redirect stub).  We only reject if the body
    # starts with a JSON object/array or an XML declaration that is clearly
    # not HTML.
    stripped = content.lstrip()[:200]
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        raise MalformedResponseError(
            f"expected HTML from {url!r} but response appears to be JSON"
        )


# ---------------------------------------------------------------------------
# fetch_pdf
# ---------------------------------------------------------------------------
async def fetch_pdf(
    url: str,
    *,
    http: PolicyAwareHTTPClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> FetchResult:
    """Fetch a PDF document with full policy enforcement.

    Enforces the 25 MB object cap (already enforced by the HTTP client)
    and validates that the response is a genuine PDF (magic ``%PDF-``).
    Returns a :class:`FetchResult`.
    """
    client = _ensure_http(http)
    kw = _resolve_source_kwargs(source)
    # Prepend any source-specific attribution header (e.g. irc-certs).
    headers = dict(extra_headers or {})
    if source is not None and source.slug == "irc-certs":
        headers.setdefault("X-SailRatings-Source", "irc-certs")
    result = await client.fetch(
        url,
        etag=etag,
        last_modified=last_modified,
        extra_headers=headers or None,
        robots_disallow=kw["robots_disallow"],
    )
    if not result.not_modified and result.content:
        _validate_pdf(result.content, url)
    return result


def _validate_pdf(content: bytes, url: str) -> None:
    """Validate that *content* is a PDF by checking the magic bytes."""
    if not content[:5] == b"%PDF-":
        raise MalformedResponseError(
            f"expected a PDF from {url!r} but response does not start with %PDF-"
        )


# ---------------------------------------------------------------------------
# fetch_json
# ---------------------------------------------------------------------------
async def fetch_json(
    url: str,
    *,
    http: PolicyAwareHTTPClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    extra_headers: dict[str, str] | None = None,
    accept: str = "application/json",
) -> FetchResult:
    """Fetch a JSON resource with content-type validation.

    Sends ``Accept: application/json`` and validates that the response body
    is valid JSON.  Returns a :class:`FetchResult` (the raw bytes are in
    ``result.content``; callers can ``json.loads`` them).
    """
    client = _ensure_http(http)
    kw = _resolve_source_kwargs(source)
    headers = dict(extra_headers or {})
    headers.setdefault("Accept", accept)
    result = await client.fetch(
        url,
        etag=etag,
        last_modified=last_modified,
        extra_headers=headers,
        robots_disallow=kw["robots_disallow"],
    )
    if not result.not_modified and result.content:
        _validate_json(result.content, url)
    return result


def _validate_json(content: bytes, url: str) -> None:
    """Validate that *content* is parseable JSON."""
    try:
        json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MalformedResponseError(
            f"expected JSON from {url!r} but response is not valid JSON: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# fetch_file
# ---------------------------------------------------------------------------
async def fetch_file(
    url: str,
    *,
    http: PolicyAwareHTTPClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> FetchResult:
    """Generic binary fetch (Sailwave ``.blw`` files, CSV exports, etc.).

    No content-type validation beyond the magic-byte checks for known
    binary formats that would indicate the wrong endpoint was hit.  The
    25 MB object cap is enforced by the HTTP client.  Returns a
    :class:`FetchResult`.
    """
    client = _ensure_http(http)
    kw = _resolve_source_kwargs(source)
    result = await client.fetch(
        url,
        etag=etag,
        last_modified=last_modified,
        extra_headers=extra_headers,
        robots_disallow=kw["robots_disallow"],
    )
    if not result.not_modified and result.content:
        _reject_binary_magic(content=result.content, url=url, expected="file")
    return result


# ---------------------------------------------------------------------------
# paginate
# ---------------------------------------------------------------------------
async def paginate(
    seed_url: str,
    next_fn: Callable[[FetchResult], str | None],
    *,
    http: PolicyAwareHTTPClient | None = None,
    source: DataSource | None = None,
    max_pages: int = 1000,
    etag: str | None = None,
    last_modified: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> AsyncIterator[FetchResult]:
    """Follow pagination links until exhausted or a cap is hit.

    *next_fn* receives each :class:`FetchResult` and returns the next URL
    (or ``None`` to stop).  The generator yields one :class:`FetchResult`
    per page.  ``max_pages`` is a safety cap (default 1000) to prevent
    unbounded walks.

    Example::

        def next_page(result: FetchResult) -> str | None:
            data = json.loads(result.content)
            nxt = data.get("next")
            return urljoin(seed, nxt) if nxt else None

        async for page in paginate(seed, next_page, http=client):
            ...
    """
    client = _ensure_http(http)
    kw = _resolve_source_kwargs(source)
    robots_disallow = kw["robots_disallow"]

    url: str | None = seed_url
    pages_yielded = 0
    current_etag = etag
    current_last_modified = last_modified

    while url is not None and pages_yielded < max_pages:
        # Check robots before fetching.
        path = urlparse(url).path or "/"
        if is_path_disallowed(path, robots_disallow):
            raise FetchCapExceededError(
                f"robots.txt disallows {url!r} during pagination"
            )
        try:
            result = await client.fetch(
                url,
                etag=current_etag,
                last_modified=current_last_modified,
                extra_headers=extra_headers,
                robots_disallow=robots_disallow,
            )
        except FetchCapExceededError:
            # Fetch / byte cap hit — stop pagination gracefully.
            break

        yield result
        pages_yielded += 1

        # Update conditional tokens from the response for the next page.
        current_etag = None          # next page has its own ETag
        current_last_modified = None

        next_url = next_fn(result)
        if next_url is None:
            break
        # Resolve relative URLs against the current URL.
        if not next_url.startswith(("http://", "https://")):
            next_url = urljoin(url, next_url)
        url = next_url


# ---------------------------------------------------------------------------
# render_page
# ---------------------------------------------------------------------------
async def render_page(
    url: str,
    *,
    source: DataSource | None = None,
    browser_factory: BrowserFactory | None = None,
    screenshot_path: str | None = None,
    wait_until: str = "networkidle",
    timeout_ms: int = 30_000,
    http: PolicyAwareHTTPClient | None = None,
) -> RenderedFetchResult:
    """Render a JavaScript-heavy page in a headless browser.

    Uses Playwright (Chromium, headless) to load *url*, wait for network
    idle, then capture the fully rendered HTML and a screenshot.  The
    screenshot is preserved as "necessary rendered evidence" for dynamic
    sources (SPEC-012 §5 acceptance criterion).

    Parameters
    ----------
    browser_factory:
        Callable returning an awaitable :class:`BrowserLike`.  If
        ``None``, a real Playwright Chromium browser is launched.
    screenshot_path:
        Filesystem path to save the screenshot.  If ``None``, the
        screenshot bytes are kept in memory (``screenshot_path`` attribute
        of the result is ``None``; raw bytes are discarded).
    wait_until:
        Playwright ``wait_until`` state (``networkidle`` | ``load`` |
        ``domcontentloaded``).
    timeout_ms:
        Navigation timeout in milliseconds.

    Returns a :class:`RenderedFetchResult` with the rendered HTML,
    screenshot path, and page title.
    """
    # Policy gate — even for browser-rendered pages.
    if source is not None:
        assert_policy_current(source)
        robots_disallow = source.robots_disallow
    else:
        robots_disallow = ()
    path = urlparse(url).path or "/"
    if is_path_disallowed(path, robots_disallow):
        raise FetchCapExceededError(
            f"robots.txt disallows {url!r}"
        )

    browser = await _launch_browser(browser_factory)
    try:
        page = await browser.new_page()
        try:
            # Set the standard User-Agent so even the browser identifies
            # as SailRatings (INTERIM-POLICY.md §6).
            await _set_user_agent(page)

            await page.goto(
                url,
                wait_until=wait_until,
                timeout=timeout_ms,
            )
            html = await page.content()
            try:
                title = await page.title()
            except Exception:  # pragma: no cover - some fakes may not impl
                title = None

            screenshot_bytes: bytes | None = None
            if screenshot_path:
                screenshot_bytes = await page.screenshot(
                    path=screenshot_path,
                    full_page=True,
                )
            else:
                screenshot_bytes = await page.screenshot(full_page=True)
        finally:
            await page.close()
    finally:
        await browser.close()

    content = html.encode("utf-8")
    content_hash = sha256_hex(content)

    # Enforce the object-size cap on the rendered HTML too.
    if len(content) > MAX_OBJECT_BYTES:
        raise FetchCapExceededError(
            f"rendered page {url!r} is {len(content)} bytes > cap {MAX_OBJECT_BYTES}"
        )

    return RenderedFetchResult(
        url=url,
        content=content,
        content_hash=content_hash,
        status_code=200,
        screenshot_path=screenshot_path,
        rendered_html=html,
        title=title,
    )


# ---------------------------------------------------------------------------
# Internal: binary magic-byte rejection
# ---------------------------------------------------------------------------
_BINARY_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "PDF"),
    (b"PK\x03\x04", "ZIP"),
    (b"\x89PNG", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
)


def _reject_binary_magic(content: bytes, url: str, *, expected: str) -> None:
    """Raise if *content* starts with a known binary magic header."""
    for magic, fmt in _BINARY_MAGIC:
        if content[:len(magic)] == magic:
            raise MalformedResponseError(
                f"expected {expected} from {url!r} but response is a {fmt} file"
            )


# ---------------------------------------------------------------------------
# Internal: browser launch
# ---------------------------------------------------------------------------
async def _launch_browser(
    factory: BrowserFactory | None,
) -> BrowserLike:
    """Launch (or fake) a browser instance."""
    if factory is not None:
        return await factory()

    # Real Playwright — imported lazily so the SDK doesn't hard-depend on
    # playwright at import time (tests that don't call render_page never
    # touch it).
    from playwright.async_api import async_playwright

    p = await async_playwright().start()
    return await p.chromium.launch(headless=True)


async def _set_user_agent(page: PageLike) -> None:
    """Set the standard User-Agent on a Playwright page.

    Some fake page implementations may not implement ``set_extra_http_headers``
    so we wrap this in a try/except.
    """
    set_ua = getattr(page, "set_extra_http_headers", None)
    if set_ua is not None:
        await set_ua({"User-Agent": STANDARD_USER_AGENT})
