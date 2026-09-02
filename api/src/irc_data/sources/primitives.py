"""Acquisition primitives — bounded HTTP, browser, API and document fetchers.

Implements **DP-01-04** of SPEC-012: a shared library of acquisition
primitives that every source adapter uses instead of bespoke HTTP code.

Every primitive:

* Accepts an optional ``source: DataSource`` arg; calls
  :func:`assert_policy_current` and :func:`assert_source_approved` if
  provided.
* Respects ``robots_disallow`` from the source record (raises
  :class:`SourceNotApprovedError` when the URL path is disallowed).
* Sends the standard policy ``User-Agent`` (enforced by
  :class:`~irc_data.sources.http_client.HttpClient`).
* Enforces rate limits, retries (incl. 429 throttling honouring
  ``Retry-After``), conditional requests and the 25 MB object-size cap
  via the shared :class:`HttpClient`.
* Returns :class:`~irc_data.sources.models.FetchResult` — never raw
  bytes directly.

Primitives::

    fetch_html(url)              — GET with rate-limit, retry, conditional, hash
    fetch_pdf(url)               — Same + 25 MB cap; PDF magic-byte validation
    fetch_json(url)              — Same + JSON parseability validation
    fetch_xml(url)               — Same + XML parseability validation
    fetch_file(url)              — Generic binary; magic-byte rejection
    paginate(seed_url, next_fn)  — Async generator: follows pagination until cap
    render_page(url)             — Playwright headless; HTML + screenshot evidence
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable
from urllib.parse import urljoin
from xml.etree import ElementTree

from irc_data.sources.envelope import FetchResult as _EnvelopeFetchResult
from irc_data.sources.http_client import (
    MAX_OBJECT_SIZE,
    STANDARD_USER_AGENT,
    HttpClient,
    NotModified,
)
from irc_data.sources.models import DataSource, FetchResult
from irc_data.sources.policy import (
    SourceNotApprovedError,
    assert_policy_current,
    assert_source_approved,
    is_within_collection_window,
)

logger = logging.getLogger(__name__)

# Re-exported for backward compatibility with pre-refactor imports.
PolicyAwareHttpClient = HttpClient

__all__ = [
    "PDF_MAGIC",
    "fetch_html",
    "fetch_pdf",
    "fetch_json",
    "fetch_xml",
    "fetch_file",
    "paginate",
    "render_page",
]

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


def _path_disallowed(url: str, robots_disallow: list[str]) -> bool:
    """Return ``True`` if *url*'s path matches any cached disallow rule."""
    from urllib.parse import urlparse

    path = urlparse(url).path or "/"
    for rule in robots_disallow:
        if not rule:
            continue
        if rule == "/":
            return True
        if path.startswith(rule):
            return True
    return False


def _enforce_window(source: DataSource | None) -> None:
    """Enforce the nightly collection window when a source is provided."""
    if source is not None and not is_within_collection_window():
        raise SourceNotApprovedError(
            getattr(source, "slug", "<unknown>"),
            reason="outside nightly collection window (01:00-06:00)",
        )


def _check_robots(url: str, source: Any) -> None:
    """Raise if the URL is disallowed by the source's robots rules.

    Works with both :class:`~irc_data.sources.models.DataSource`
    (``is_disallowed`` method) and :class:`~irc_data.sources.gate.SourceRecord`
    (plain ``robots_disallow`` list).
    """
    if source is None:
        return
    if hasattr(source, "is_disallowed"):
        disallowed = source.is_disallowed(url)
    else:
        disallowed = _path_disallowed(url, getattr(source, "robots_disallow", []) or [])
    if disallowed:
        raise SourceNotApprovedError(
            getattr(source, "slug", "<unknown>"),
            reason=f"URL '{url}' is disallowed by robots.txt",
        )


def _policy_version(source: DataSource | None) -> str:
    return source.policy_version if source else "v1.0"


def _not_modified_result(
    url: str,
    nm: NotModified,
    source: DataSource | None,
) -> FetchResult:
    """Build a 304 ``FetchResult`` from a :class:`NotModified` sentinel."""
    return FetchResult(
        url=url,
        content=b"",
        content_hash="",
        etag=nm.etag,
        last_modified=nm.last_modified,
        fetched_at=_now_iso(),
        policy_version=_policy_version(source),
        status_code=304,
        not_modified=True,
    )


def _to_fetch_result(
    env: _EnvelopeFetchResult,
    source: DataSource | None,
    *,
    status_code: int = 200,
    screenshot_path: str | None = None,
) -> FetchResult:
    """Convert an envelope :class:`FetchResult` to the models contract."""
    return FetchResult(
        url=env.url,
        content=env.content,
        content_hash=env.content_hash,
        etag=env.etag,
        last_modified=env.last_modified,
        fetched_at=env.fetched_at,
        policy_version=_policy_version(source) if source else env.policy_version,
        status_code=status_code,
        not_modified=False,
        screenshot_path=screenshot_path,
    )


def _check_size(content: bytes, url: str, max_object_size: int, kind: str) -> None:
    if len(content) > max_object_size:
        raise ValueError(
            f"{kind} at {url} is {len(content)} bytes "
            f"(exceeds {max_object_size} byte cap)"
        )


def _default_client() -> HttpClient:
    """Build a default :class:`HttpClient` bound to the active policy.

    Used when the caller does not supply a shared client.  Rate limiting,
    retry, conditional requests and the object-size cap all come from the
    active policy; the inner ``httpx`` client is created lazily with the
    standard User-Agent.
    """
    return HttpClient()


async def _fetch(
    url: str,
    client: HttpClient,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> _EnvelopeFetchResult | NotModified:
    """Thin wrapper over :meth:`HttpClient.fetch` (keyword style)."""
    return await client.fetch(
        url,
        etag=etag,
        last_modified=last_modified,
        extra_headers=extra_headers,
    )


# ---------------------------------------------------------------------------
# fetch_html
# ---------------------------------------------------------------------------


async def fetch_html(
    url: str,
    client: HttpClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    max_object_size: int = MAX_OBJECT_SIZE,
) -> FetchResult:
    """Fetch an HTML page with rate-limit, retry, conditional, and hash check.

    Returns a :class:`FetchResult` with the page content.  The shared
    :class:`HttpClient` enforces rate limits, 5xx/429 retry, conditional
    requests and the 25 MB object-size cap.
    """
    _check_source(source)
    _check_robots(url, source)

    owns_client = client is None
    if client is None:
        client = _default_client()

    try:
        result = await _fetch(
            url, client, etag=etag, last_modified=last_modified
        )

        if isinstance(result, NotModified):
            return _not_modified_result(url, result, source)

        _check_size(result.content, url, max_object_size, "HTML response")

        return _to_fetch_result(result, source)
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# fetch_pdf
# ---------------------------------------------------------------------------


PDF_MAGIC = b"%PDF"


async def fetch_pdf(
    url: str,
    client: HttpClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    max_object_size: int = MAX_OBJECT_SIZE,
) -> FetchResult:
    """Fetch a PDF with size cap and magic-byte validation.

    Enforces the 25 MB cap and validates that the response starts with
    ``%PDF``.  If the source is ``irc-certs``, sends the attribution
    header required by SPEC-012 §3.5.
    """
    _check_source(source)
    _check_robots(url, source)

    owns_client = client is None
    if client is None:
        client = _default_client()

    try:
        # IRC cert attribution (SPEC-012 §3.5)
        extra_headers: dict[str, str] | None = None
        if source and source.slug == "irc-certs":
            extra_headers = {"X-SailRatings-Source": "irc-certs"}

        result = await _fetch(
            url,
            client,
            etag=etag,
            last_modified=last_modified,
            extra_headers=extra_headers,
        )

        if isinstance(result, NotModified):
            return _not_modified_result(url, result, source)

        content = result.content
        _check_size(content, url, max_object_size, "PDF")

        # Magic byte validation
        if not content.startswith(PDF_MAGIC):
            raise ValueError(
                f"Response from {url} is not a valid PDF "
                f"(missing %PDF magic bytes)"
            )

        return _to_fetch_result(result, source)
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# fetch_json
# ---------------------------------------------------------------------------


async def fetch_json(
    url: str,
    client: HttpClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    max_object_size: int = MAX_OBJECT_SIZE,
) -> FetchResult:
    """Fetch JSON with parseability validation.

    Sends ``Accept: application/json`` and validates that the response
    body parses as JSON.  Malformed JSON raises :class:`ValueError`.
    """
    _check_source(source)
    _check_robots(url, source)

    owns_client = client is None
    if client is None:
        client = _default_client()

    try:
        extra_headers: dict[str, str] = {"Accept": "application/json"}
        if source and source.slug == "irc-certs":
            extra_headers["X-SailRatings-Source"] = "irc-certs"

        result = await _fetch(
            url,
            client,
            etag=etag,
            last_modified=last_modified,
            extra_headers=extra_headers,
        )

        if isinstance(result, NotModified):
            return _not_modified_result(url, result, source)

        content = result.content
        _check_size(content, url, max_object_size, "JSON")

        # Parseability check
        try:
            json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(
                f"Response from {url} is not valid JSON: {exc}"
            ) from exc

        return _to_fetch_result(result, source)
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# fetch_xml
# ---------------------------------------------------------------------------


async def fetch_xml(
    url: str,
    client: HttpClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    max_object_size: int = MAX_OBJECT_SIZE,
) -> FetchResult:
    """Fetch XML with parseability validation.

    Sends ``Accept: application/xml, text/xml`` and validates that the
    response body parses as XML.  Covers XML API endpoints (DP-01-04
    scope: JSON/XML APIs).
    """
    _check_source(source)
    _check_robots(url, source)

    owns_client = client is None
    if client is None:
        client = _default_client()

    try:
        extra_headers: dict[str, str] = {"Accept": "application/xml, text/xml"}
        if source and source.slug == "irc-certs":
            extra_headers["X-SailRatings-Source"] = "irc-certs"

        result = await _fetch(
            url,
            client,
            etag=etag,
            last_modified=last_modified,
            extra_headers=extra_headers,
        )

        if isinstance(result, NotModified):
            return _not_modified_result(url, result, source)

        content = result.content
        _check_size(content, url, max_object_size, "XML")

        # Parseability check
        try:
            ElementTree.fromstring(content.decode("utf-8"))
        except (ElementTree.ParseError, UnicodeDecodeError) as exc:
            raise ValueError(
                f"Response from {url} is not valid XML: {exc}"
            ) from exc

        return _to_fetch_result(result, source)
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# fetch_file
# ---------------------------------------------------------------------------


async def fetch_file(
    url: str,
    client: HttpClient | None = None,
    source: DataSource | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    max_object_size: int = MAX_OBJECT_SIZE,
    reject_magic: list[bytes] | None = None,
) -> FetchResult:
    """Generic binary fetch (Sailwave ``.blw`` files etc.).

    Optionally rejects responses whose leading bytes match any of
    *reject_magic* — useful to detect HTML error pages served with a
    200 status when a binary file was expected.
    """
    _check_source(source)
    _check_robots(url, source)

    owns_client = client is None
    if client is None:
        client = _default_client()

    try:
        result = await _fetch(
            url, client, etag=etag, last_modified=last_modified
        )

        if isinstance(result, NotModified):
            return _not_modified_result(url, result, source)

        content = result.content
        _check_size(content, url, max_object_size, "File")

        # Magic byte rejection
        if reject_magic:
            for magic in reject_magic:
                if content.startswith(magic):
                    raise ValueError(
                        f"File at {url} has rejected magic bytes "
                        f"({magic!r}) — expected a different format"
                    )

        return _to_fetch_result(result, source)
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# paginate
# ---------------------------------------------------------------------------


async def paginate(
    seed_url: str,
    next_fn: Callable[[FetchResult], str | None],
    client: HttpClient | None = None,
    source: DataSource | None = None,
    fetch_fn: Callable | None = None,
    max_pages: int = 100,
    max_bytes: int = 500 * 1024 * 1024,
    max_object_size: int = MAX_OBJECT_SIZE,
) -> AsyncIterator[FetchResult]:
    """Async generator that follows pagination links until exhausted or cap hit.

    Args:
        seed_url: The first URL to fetch.
        next_fn: A callable that takes the previous :class:`FetchResult`
            and returns the next URL (or ``None`` to stop).
        client: Optional shared HTTP client.
        source: Optional :class:`DataSource` for policy enforcement.
        fetch_fn: Optional custom fetch function (defaults to
            :func:`fetch_html`).
        max_pages: Maximum pages to fetch before stopping.
        max_bytes: Maximum total bytes before stopping.
        max_object_size: Maximum size per individual object.

    Yields:
        :class:`FetchResult` for each page fetched.
    """
    _check_source(source)
    _check_robots(seed_url, source)

    owns_client = client is None
    if client is None:
        client = _default_client()

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
                    total_bytes,
                    max_bytes,
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


def _evidence_dir(evidence_dir: str | None) -> str:
    """Resolve the directory used to store rendered-evidence screenshots.

    Order of precedence:
      1. explicit *evidence_dir* argument
      2. ``SAILRATINGS_RENDER_EVIDENCE_DIR`` environment variable
      3. ``data/rendered_evidence`` under the current working directory
      4. the system temp dir (last-resort fallback)

    The chosen directory is created if necessary.  Returns a path that
    is guaranteed writable (or raises).
    """
    import os
    import tempfile

    candidates: list[str] = []
    if evidence_dir:
        candidates.append(evidence_dir)
    env_dir = os.environ.get("SAILRATINGS_RENDER_EVIDENCE_DIR")
    if env_dir:
        candidates.append(env_dir)
    candidates.append(os.path.join(os.getcwd(), "data", "rendered_evidence"))
    candidates.append(tempfile.gettempdir())

    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            continue
        if os.access(path, os.W_OK):
            return path
    # Last resort — temp dir always exists.
    return tempfile.gettempdir()


async def _capture_screenshot(page: Any, evidence_dir: str | None) -> str:
    """Capture a full-page screenshot to the evidence directory."""
    import os
    import uuid

    directory = _evidence_dir(evidence_dir)
    path = os.path.join(directory, f"sailratings_render_{uuid.uuid4().hex}.png")
    await page.screenshot(path=path, full_page=True)
    return path


async def render_page(
    url: str,
    source: DataSource | None = None,
    browser: Any = None,
    wait_for: str | None = None,
    timeout_ms: int = 30000,
    max_object_size: int = MAX_OBJECT_SIZE,
    screenshot: bool = True,
    evidence_dir: str | None = None,
) -> FetchResult:
    """Render a JavaScript-heavy page using Playwright headless.

    Returns a :class:`FetchResult` with the fully rendered HTML and an
    optional screenshot path as rendered evidence (dynamic pages must
    preserve rendered evidence per the DP-01-04 acceptance criteria).

    Args:
        url: The page URL to render.
        source: Optional :class:`DataSource` for policy enforcement.
        browser: An injectable browser object (must have async
            ``new_page()`` method).  If not provided, Playwright is
            launched.
        wait_for: Optional selector to wait for before capturing
            content.
        timeout_ms: Maximum time to wait for page load / selector.
        max_object_size: Maximum size for the rendered HTML.
        screenshot: If ``True`` (default), capture a screenshot as
            evidence.
        evidence_dir: Directory in which to store the screenshot so the
            rendered evidence is preserved beyond the process lifetime.
            When ``None`` (default) the
            :envvar:`SAILRATINGS_RENDER_EVIDENCE_DIR` environment
            variable is honoured, falling back to
            ``data/rendered_evidence`` under the current working
            directory; if that directory cannot be created the system
            temp dir is used as a last resort.

    Returns:
        :class:`FetchResult` with rendered HTML and ``screenshot_path``.
    """
    _check_source(source)
    _check_robots(url, source)

    owns_browser = browser is None
    pw = None

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
        _check_size(content_bytes, url, max_object_size, "Rendered page")

        # Screenshot evidence — stored in a stable, preserved location so
        # that rendered evidence for dynamic pages survives the process.
        screenshot_path: str | None = None
        if screenshot:
            screenshot_path = await _capture_screenshot(page, evidence_dir)

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

        return FetchResult(
            url=final_url,
            content=content_bytes,
            content_hash=_sha256(content_bytes),
            etag=etag,
            last_modified=last_modified,
            fetched_at=_now_iso(),
            policy_version=_policy_version(source),
            status_code=200,
            screenshot_path=screenshot_path,
        )
    finally:
        if owns_browser:
            try:
                await browser.close()
            except Exception:
                pass
            if pw is not None:
                try:
                    await pw.stop()
                except Exception:
                    pass
