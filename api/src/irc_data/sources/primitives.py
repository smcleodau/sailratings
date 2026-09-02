"""Acquisition primitives — bounded HTTP, browser, API and document fetchers.

Every primitive:
- Accepts an optional ``source: DataSource`` arg; calls policy checks if provided
- Respects ``robots_disallow`` from the source record
- Sets the standard ``User-Agent``
- Returns ``FetchResult`` (never raw bytes directly)

Primitives::

    fetch_html(url)          — GET with rate-limit, retry, conditional, hash
    fetch_pdf(url)           — Same + 25 MB cap; PDF magic-byte validation
    fetch_json(url)          — Same + Content-Type validation, JSON parseability
    fetch_file(url)          — Generic binary; magic-byte rejection for wrong formats
    paginate(seed_url, next_fn) — Async generator: follows pagination until cap
    render_page(url)         — Playwright headless; HTML + screenshot evidence
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable
from urllib.parse import urljoin, urlparse

import httpx

from irc_data.sources.http_client import (
    MAX_OBJECT_SIZE,
    PolicyAwareHttpClient,
    RateLimiter,
    STANDARD_USER_AGENT,
)
from irc_data.sources.models import DataSource, FetchResult, RawArtifactV1
from irc_data.sources.policy import (
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    assert_policy_current,
    assert_source_approved,
    is_within_collection_window,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_source(source: DataSource | None) -> None:
    """Run policy + approval checks if a source is provided.

    Enforcement order matches the acceptance criteria: version first, then
    the kill switch (enabled), then robots/legal status.
    """
    if source is None:
        return
    # 1. Policy version gate (raises PolicyVersionMismatchError if stale)
    assert_policy_current(source)
    # 2. Kill switch — disabled sources produce zero fetch attempts.
    if not getattr(source, "enabled", True):
        raise SourceNotApprovedError(
            getattr(source, "slug", "<unknown>"),
            reason="source is disabled (kill switch)",
        )
    # 3. Legal status / robots approval gate.
    assert_source_approved(source)


def _enforce_window(source: DataSource | None) -> None:
    """Enforce the nightly collection window when a source is provided.

    Health probes bypass this via the primitives' ``skip_window`` path;
    ordinary collection must run inside the 01:00–06:00 window.
    """
    if source is not None and not is_within_collection_window():
        raise SourceNotApprovedError(
            getattr(source, "slug", "<unknown>"),
            reason="outside nightly collection window (01:00-06:00)",
        )


def _check_robots(url: str, source: DataSource | None) -> None:
    """Raise if the URL is disallowed by robots."""
    if source and source.is_disallowed(url):
        raise SourceNotApprovedError(
            source.slug,
            reason=f"URL '{url}' is disallowed by robots.txt",
        )


def _build_fetch_result(
    url: str,
    response: httpx.Response,
    source: DataSource | None = None,
    screenshot_path: str | None = None,
) -> FetchResult:
    """Build a ``FetchResult`` from an ``httpx.Response``."""
    content = response.content
    policy_version = source.policy_version if source else "interim-v0"
    not_modified = response.status_code == 304

    return FetchResult(
        url=str(response.url) if response.url else url,
        content=content if not not_modified else b"",
        content_hash=_sha256(content) if content else "",
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
        fetched_at=_now_iso(),
        policy_version=policy_version,
        status_code=response.status_code,
        not_modified=not_modified,
        screenshot_path=screenshot_path,
    )


# ---------------------------------------------------------------------------
# fetch_html
# ---------------------------------------------------------------------------


async def fetch_html(
    url: str,
    client: PolicyAwareHttpClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    max_object_size: int = MAX_OBJECT_SIZE,
) -> FetchResult:
    """Fetch an HTML page with rate-limit, retry, conditional, and hash check.

    Returns a ``FetchResult`` with the page content.
    Validates that the response looks like HTML.
    """
    _check_source(source)
    _check_robots(url, source)

    owns_client = client is None
    if client is None:
        client = PolicyAwareHttpClient(rate_limiter=RateLimiter(min_delay=0.0, jitter=0.0))

    try:
        response = await client.raw(
            url,
            etag=etag,
            last_modified=last_modified,
        )

        # 304 Not Modified
        if response.status_code == 304:
            return FetchResult(
                url=url,
                content=b"",
                content_hash="",
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                fetched_at=_now_iso(),
                policy_version=source.policy_version if source else "interim-v0",
                status_code=304,
                not_modified=True,
            )

        # Size check
        content_length = len(response.content)
        if content_length > max_object_size:
            raise ValueError(
                f"HTML response from {url} is {content_length} bytes "
                f"(exceeds {max_object_size} byte cap)"
            )

        # Validate it looks like HTML
        content_type = response.headers.get("Content-Type", "").lower()
        body_preview = response.content[:200].lower()
        if content_type and "text/html" not in content_type and "application/xhtml" not in content_type:
            # Some servers don't set Content-Type properly, check body
            if b"<html" not in body_preview and b"<!doctype html" not in body_preview:
                raise ValueError(
                    f"Expected HTML from {url}, got Content-Type: {content_type}"
                )

        return _build_fetch_result(url, response, source)
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# fetch_pdf
# ---------------------------------------------------------------------------


PDF_MAGIC = b"%PDF"


async def fetch_pdf(
    url: str,
    client: PolicyAwareHttpClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    max_object_size: int = MAX_OBJECT_SIZE,
) -> FetchResult:
    """Fetch a PDF with size cap and magic-byte validation.

    Enforces the 25 MB cap and validates that the response starts with
    ``%PDF``.  If the source is ``irc-certs``, sends the attribution header.
    """
    _check_source(source)
    _check_robots(url, source)

    owns_client = client is None
    if client is None:
        client = PolicyAwareHttpClient(rate_limiter=RateLimiter(min_delay=0.0, jitter=0.0))

    try:
        # IRC cert attribution
        extra_headers: dict[str, str] | None = None
        if source and source.slug == "irc-certs":
            extra_headers = {"X-SailRatings-Source": "irc-certs"}

        response = await client.raw(
            url,
            etag=etag,
            last_modified=last_modified,
            extra_headers=extra_headers,
        )

        # 304 Not Modified
        if response.status_code == 304:
            return FetchResult(
                url=url,
                content=b"",
                content_hash="",
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                fetched_at=_now_iso(),
                policy_version=source.policy_version if source else "interim-v0",
                status_code=304,
                not_modified=True,
            )

        content = response.content

        # Size cap
        if len(content) > max_object_size:
            raise ValueError(
                f"PDF at {url} is {len(content)} bytes "
                f"(exceeds {max_object_size} byte cap)"
            )

        # Magic byte validation
        if not content.startswith(PDF_MAGIC):
            raise ValueError(
                f"Response from {url} is not a valid PDF "
                f"(missing %PDF magic bytes)"
            )

        return _build_fetch_result(url, response, source)
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# fetch_json
# ---------------------------------------------------------------------------


async def fetch_json(
    url: str,
    client: PolicyAwareHttpClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    max_object_size: int = MAX_OBJECT_SIZE,
) -> FetchResult:
    """Fetch JSON with Content-Type validation and parseability check.

    Sends ``Accept: application/json`` header and validates that the
    response can be parsed as JSON.
    """
    _check_source(source)
    _check_robots(url, source)

    owns_client = client is None
    if client is None:
        client = PolicyAwareHttpClient(rate_limiter=RateLimiter(min_delay=0.0, jitter=0.0))

    try:
        extra_headers = {"Accept": "application/json"}
        # IRC cert attribution
        if source and source.slug == "irc-certs":
            extra_headers["X-SailRatings-Source"] = "irc-certs"

        response = await client.raw(
            url,
            etag=etag,
            last_modified=last_modified,
            extra_headers=extra_headers,
        )

        # 304 Not Modified
        if response.status_code == 304:
            return FetchResult(
                url=url,
                content=b"",
                content_hash="",
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                fetched_at=_now_iso(),
                policy_version=source.policy_version if source else "interim-v0",
                status_code=304,
                not_modified=True,
            )

        content = response.content

        # Size check
        if len(content) > max_object_size:
            raise ValueError(
                f"JSON response from {url} is {len(content)} bytes "
                f"(exceeds {max_object_size} byte cap)"
            )

        # Content-Type validation
        content_type = response.headers.get("Content-Type", "").lower()
        if content_type and "json" not in content_type:
            raise ValueError(
                f"Expected JSON from {url}, got Content-Type: {content_type}"
            )

        # Parseability check
        try:
            json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(
                f"Response from {url} is not valid JSON: {e}"
            ) from e

        return _build_fetch_result(url, response, source)
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# fetch_file
# ---------------------------------------------------------------------------


async def fetch_file(
    url: str,
    client: PolicyAwareHttpClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    max_object_size: int = MAX_OBJECT_SIZE,
    reject_magic: list[bytes] | None = None,
) -> FetchResult:
    """Fetch a generic binary file (Sailwave .blw files, CSVs, etc.).

    Optionally rejects responses whose magic bytes match *reject_magic*
    (e.g. to prevent storing HTML when a binary was expected).
    """
    _check_source(source)
    _check_robots(url, source)

    owns_client = client is None
    if client is None:
        client = PolicyAwareHttpClient(rate_limiter=RateLimiter(min_delay=0.0, jitter=0.0))

    try:
        response = await client.raw(
            url,
            etag=etag,
            last_modified=last_modified,
        )

        # 304 Not Modified
        if response.status_code == 304:
            return FetchResult(
                url=url,
                content=b"",
                content_hash="",
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                fetched_at=_now_iso(),
                policy_version=source.policy_version if source else "interim-v0",
                status_code=304,
                not_modified=True,
            )

        content = response.content

        # Size check
        if len(content) > max_object_size:
            raise ValueError(
                f"File at {url} is {len(content)} bytes "
                f"(exceeds {max_object_size} byte cap)"
            )

        # Magic byte rejection
        if reject_magic:
            for magic in reject_magic:
                if content.startswith(magic):
                    raise ValueError(
                        f"File at {url} has rejected magic bytes "
                        f"({magic!r}) — expected a different format"
                    )

        return _build_fetch_result(url, response, source)
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# paginate
# ---------------------------------------------------------------------------


async def paginate(
    seed_url: str,
    next_fn: Callable[[FetchResult], str | None],
    client: PolicyAwareHttpClient | None = None,
    source: DataSource | None = None,
    fetch_fn: Callable | None = None,
    max_pages: int = 100,
    max_bytes: int = 500 * 1024 * 1024,
    max_object_size: int = MAX_OBJECT_SIZE,
) -> AsyncIterator[FetchResult]:
    """Async generator that follows pagination links until exhausted or cap hit.

    Args:
        seed_url: The first URL to fetch.
        next_fn: A callable that takes the previous ``FetchResult`` and
            returns the next URL (or ``None`` to stop).
        client: Optional shared HTTP client.
        source: Optional ``DataSource`` for policy enforcement.
        fetch_fn: Optional custom fetch function (defaults to ``fetch_html``).
        max_pages: Maximum pages to fetch before stopping.
        max_bytes: Maximum total bytes before stopping.
        max_object_size: Maximum size per individual object.

    Yields:
        ``FetchResult`` for each page fetched.
    """
    _check_source(source)
    _check_robots(seed_url, source)

    owns_client = client is None
    if client is None:
        client = PolicyAwareHttpClient(rate_limiter=RateLimiter(min_delay=0.0, jitter=0.0))

    if fetch_fn is None:
        fetch_fn = fetch_html

    try:
        url: str | None = seed_url
        page = 0
        total_bytes = 0

        while url and page < max_pages:
            _check_robots(url, source)
            page += 1

            result = await fetch_fn(
                url,
                client=client,
                source=source,
                max_object_size=max_object_size,
            )

            total_bytes += len(result.content)
            if total_bytes > max_bytes:
                logger.warning(
                    "Pagination stopped: total bytes %d exceeds %d",
                    total_bytes, max_bytes,
                )
                yield result
                break

            yield result

            # Get next URL
            next_url = next_fn(result)
            if next_url:
                # Resolve relative URLs
                if not next_url.startswith("http"):
                    next_url = urljoin(url, next_url)
            url = next_url
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# render_page
# ---------------------------------------------------------------------------


async def render_page(
    url: str,
    source: DataSource | None = None,
    browser=None,
    wait_for: str | None = None,
    timeout_ms: int = 30000,
    max_object_size: int = MAX_OBJECT_SIZE,
    screenshot: bool = True,
) -> FetchResult:
    """Render a JavaScript-heavy page using Playwright headless.

    Returns a ``FetchResult`` with the fully rendered HTML and an
    optional screenshot path as evidence.

    Args:
        url: The page URL to render.
        source: Optional ``DataSource`` for policy enforcement.
        browser: An injectable browser object (must have async ``new_page()``
            method).  If not provided, Playwright is launched.
        wait_for: Optional selector to wait for before capturing content.
        timeout_ms: Maximum time to wait for page load / selector.
        max_object_size: Maximum size for the rendered HTML.
        screenshot: If ``True`` (default), capture a screenshot as evidence.

    Returns:
        ``FetchResult`` with rendered HTML and ``screenshot_path``.
    """
    _check_source(source)
    _check_robots(url, source)

    owns_browser = browser is None

    if owns_browser:
        try:
            from playwright.async_api import async_playwright

            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
        except ImportError:
            raise RuntimeError(
                "Playwright is required for render_page() but is not installed"
            )

    try:
        page = await browser.new_page()

        # Set standard User-Agent
        await page.set_extra_http_headers({"User-Agent": STANDARD_USER_AGENT})

        # Set viewport
        await page.set_viewport_size({"width": 1280, "height": 720})

        # Navigate — capture the response to extract conditional headers
        nav_response = await page.goto(
            url, wait_until="networkidle", timeout=timeout_ms
        )

        # Wait for selector if specified
        if wait_for:
            await page.wait_for_selector(wait_for, timeout=timeout_ms)

        # Get rendered HTML
        content = await page.content()
        content_bytes = content.encode("utf-8")

        # Size check
        if len(content_bytes) > max_object_size:
            raise ValueError(
                f"Rendered page from {url} is {len(content_bytes)} bytes "
                f"(exceeds {max_object_size} byte cap)"
            )

        # Screenshot
        screenshot_path: str | None = None
        if screenshot:
            import tempfile

            tmp = tempfile.NamedTemporaryFile(
                suffix=".png", delete=False, prefix="sailratings_render_"
            )
            tmp.close()
            await page.screenshot(path=tmp.name, full_page=True)
            screenshot_path = tmp.name

        # Extract conditional headers from the main navigation response
        etag = None
        last_modified = None
        final_url = url
        if nav_response is not None:
            try:
                etag = nav_response.headers.get("ETag")
                last_modified = nav_response.headers.get("Last-Modified")
                final_url = str(nav_response.url) or url
            except Exception:
                pass

        await page.close()

        policy_version = source.policy_version if source else "interim-v0"

        return FetchResult(
            url=final_url,
            content=content_bytes,
            content_hash=_sha256(content_bytes),
            etag=etag,
            last_modified=last_modified,
            fetched_at=_now_iso(),
            policy_version=policy_version,
            status_code=200,
            screenshot_path=screenshot_path,
        )
    finally:
        if owns_browser:
            try:
                await browser.close()
            except Exception:
                pass
            try:
                await pw.stop()
            except Exception:
                pass
