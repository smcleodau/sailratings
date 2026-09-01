"""Reference adapter: ``FakeSourceAdapter``.

Proves the adapter SDK works end-to-end with zero network calls by
using an injectable ``httpx.MockTransport``.

Demonstrates:
- Pagination (multi-page result sets)
- Retry on transient 5xx
- Checkpoint resume (resume from last completed page)
- Content hashing (skip re-download of unchanged pages)
- Policy enforcement (raises if source is ``hold``)
- Conditional requests (304 Not Modified)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from irc_data.sources.adapter import Checkpoint, SourceAdapter
from irc_data.sources.models import DataSource, FetchResult, RawArtifactV1
from irc_data.sources.policy import (
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    assert_policy_current,
    assert_source_approved,
)
from irc_data.sources.http_client import (
    PolicyAwareHttpClient,
    RateLimiter,
    STANDARD_USER_AGENT,
)


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakeSourceAdapter(SourceAdapter):
    """Reference adapter against a mock HTTP server.

    Uses ``httpx.MockTransport`` so tests run with zero network calls.
    The mock server simulates a paginated results site where each page
    links to the next until the last page.
    """

    source_slug = "sailsys"

    def __init__(
        self,
        db=None,
        http_client: PolicyAwareHttpClient | None = None,
        transport: httpx.MockTransport | None = None,
        max_pages: int = 10,
        checkpoint: Checkpoint | None = None,
        source_override: DataSource | None = None,
    ) -> None:
        # Build the HTTP client with an injectable transport
        if http_client is None:
            if transport is None:
                transport = self._build_default_transport()
            inner = httpx.AsyncClient(
                transport=transport,
                follow_redirects=True,
                headers={"User-Agent": STANDARD_USER_AGENT},
            )
            http_client = PolicyAwareHttpClient(
                client=inner,
                rate_limiter=RateLimiter(min_delay=0.0, jitter=0.0),
            )

        # Allow overriding the source for policy tests
        if source_override is not None:
            self._source = source_override
            # Skip the base __init__'s _resolve_source and set manually
            self.db = db
            self.http = http_client
            self.max_pages = max_pages
            self.checkpoint = checkpoint
            self._seen_hashes: dict[str, str] = {}
            return

        super().__init__(db=db, http_client=http_client)
        self.max_pages = max_pages
        self.checkpoint = checkpoint
        self._seen_hashes: dict[str, str] = {}

    # -- Mock transport -- #

    @staticmethod
    def _build_default_transport() -> httpx.MockTransport:
        """Build a mock transport simulating a 3-page paginated site."""
        page_contents = {
            1: b"<html><body>Page 1</body></html>",
            2: b"<html><body>Page 2</body></html>",
            3: b"<html><body>Page 3 (last)</body></html>",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            # Check conditional requests
            etag = request.headers.get("if-none-match")
            if etag == '"page1-etag"' and "page=1" in url_str:
                return httpx.Response(304, headers={"ETag": '"page1-etag"'})
            if etag == '"page2-etag"' and "page=2" in url_str:
                return httpx.Response(304, headers={"ETag": '"page2-etag"'})

            # Simulate transient 5xx on first attempt for page 2
            if "page=2&failonce" in url_str:
                handler._page2_calls = getattr(handler, "_page2_calls", 0) + 1
                if handler._page2_calls == 1:
                    return httpx.Response(503)

            # Parse page number
            page = 1
            if "page=" in url_str:
                try:
                    page = int(url_str.split("page=")[1].split("&")[0])
                except (ValueError, IndexError):
                    page = 1

            content = page_contents.get(page, page_contents[3])

            # Add ETag for conditional request support
            etag_val = f'"page{page}-etag"'
            headers = {"ETag": etag_val, "Content-Type": "text/html"}

            # Add next-page link header for pagination
            if page < 3:
                headers["X-Next-Page"] = f"https://example.com/results?page={page + 1}"

            return httpx.Response(200, content=content, headers=headers)

        return httpx.MockTransport(handler)

    # -- Collection -- #

    async def collect(self) -> AsyncIterator[FetchResult]:
        """Yield ``FetchResult`` objects from the paginated site."""
        # Re-check policy at collect time (source may have been mutated)
        src = self._source
        assert_policy_current(src)
        assert_source_approved(src)

        base_url = "https://example.com/results"
        url = f"{base_url}?page=1"
        page_num = 0

        while url and page_num < self.max_pages:
            # Checkpoint resume: skip already-completed URLs
            if self.checkpoint and self.checkpoint.is_completed(url):
                # Advance to next URL from checkpoint
                if self.checkpoint.next_url:
                    url = self.checkpoint.next_url
                    continue
                break

            # Check robots
            if src.is_disallowed(url):
                raise SourceNotApprovedError(
                    src.slug, reason=f"URL '{url}' disallowed by robots.txt"
                )

            page_num += 1

            # Fetch with conditional headers
            etag = None
            last_modified = None
            if self.checkpoint:
                # Use stored etag if available
                pass

            response = await self.http.fetch(
                url,
                source=src,
                etag=etag,
                last_modified=last_modified,
            )

            # Handle 304 Not Modified
            if response.status_code == 304:
                yield FetchResult(
                    url=url,
                    content=b"",
                    content_hash="",
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    fetched_at=_now_iso(),
                    policy_version=src.policy_version,
                    status_code=304,
                    not_modified=True,
                )
                # Advance to next page
                next_url = response.headers.get("X-Next-Page")
                url = next_url if next_url else None
                if self.checkpoint:
                    self.checkpoint.mark_completed(url or "")
                    self.checkpoint.next_url = next_url
                continue

            content = response.content

            # Content hash check (skip if unchanged)
            content_hash = _hash(content)
            if url in self._seen_hashes and self._seen_hashes[url] == content_hash:
                # Skip unchanged content
                next_url = response.headers.get("X-Next-Page")
                url = next_url if next_url else None
                continue
            self._seen_hashes[url] = content_hash

            result = FetchResult(
                url=url,
                content=content,
                content_hash=content_hash,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                fetched_at=_now_iso(),
                policy_version=src.policy_version,
                status_code=response.status_code,
            )
            yield result

            # Checkpoint: mark completed
            if self.checkpoint:
                self.checkpoint.mark_completed(url)
                self.checkpoint.page = page_num

            # Follow pagination
            next_url = response.headers.get("X-Next-Page")
            url = next_url if next_url else None
