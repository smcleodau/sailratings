"""Reference fake adapter and in-process HTTP server (DP-01-03).

The :class:`FakeSourceAdapter` is the reference implementation that
proves the SDK's acquisition mechanics work end-to-end:

* **Pagination** — multi-page result sets with a ``next`` link.
* **Retry** — transient 5xx responses are retried with backoff.
* **Checkpoints** — interrupted collection resumes from the last page.
* **Content hashing** — unchanged pages are skipped (hash dedup).
* **Policy enforcement** — ``hold`` / disabled sources raise before
  any fetch is attempted.

The :class:`FakeHttpServer` is a lightweight in-process HTTP server
built on :mod:`httpx`'s :class:`~httpx.MockTransport`.  It makes **zero
real network calls** — all requests are intercepted and served from
configurable in-memory routes.  This keeps the test suite fast and
hermetic.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from irc_data.sources.adapter import (
    DiscoveredItem,
    HealthProbeResult,
    ParseHint,
    SourceAdapter,
)
from irc_data.sources.envelope import (
    AdapterCheckpointV1,
    FetchStatus,
    RawCaptureRequestV1,
    sha256_hex,
)
from irc_data.sources.gate import CollectionGate, SourceRecord
from irc_data.sources.http_client import HttpClient
from irc_data.sources.policy import (
    ACTIVE_POLICY,
    CollectionPolicyDecisionV1,
    CURRENT_POLICY_VERSION,
    LegalStatus,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
)


# ---------------------------------------------------------------------------
# FakeHttpServer — in-process mock HTTP server (zero network calls)
# ---------------------------------------------------------------------------


@dataclass
class FakeRoute:
    """A single route served by :class:`FakeHttpServer`."""

    path: str
    body: bytes
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)

    # If True, the first N requests to this path return status_code,
    # then subsequent requests return 200 (for retry testing).
    fail_first: int = 0
    _hit_count: int = field(default=0, repr=False)


class FakeHttpServer:
    """In-process HTTP server using ``httpx.MockTransport``.

    No real network calls are made — all requests are intercepted by a
    mock transport handler.  Routes are matched by path prefix.

    Usage::

        server = FakeHttpServer(base_url="http://fake.test")
        server.add_route("/page/1", b"<html>page 1</html>")
        server.add_route("/page/2", b"<html>page 2</html>")
        client = server.make_client()
        # client.get("http://fake.test/page/1") → 200, b"<html>page 1</html>"
    """

    def __init__(self, base_url: str = "http://fake.test"):
        self.base_url = base_url.rstrip("/")
        self.routes: dict[str, FakeRoute] = {}
        self.request_log: list[dict[str, Any]] = []
        self._call_counts: dict[str, int] = {}

    def add_route(
        self,
        path: str,
        body: bytes | str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        fail_first: int = 0,
    ) -> FakeRoute:
        """Add a route.  ``body`` may be ``bytes`` or ``str``."""
        if isinstance(body, str):
            body = body.encode("utf-8")
        route = FakeRoute(
            path=path,
            body=body,
            status_code=status_code,
            headers=headers or {},
            fail_first=fail_first,
        )
        self.routes[path] = route
        return route

    def add_paginated(
        self,
        path_prefix: str = "/page/",
        num_pages: int = 3,
        body_template: str = "<html>page {n}</html>",
    ) -> list[FakeRoute]:
        """Add a set of paginated routes with ``next`` links.

        Creates ``num_pages`` routes at ``/page/1``, ``/page/2``, …
        The last page has no ``next`` link.
        """
        routes = []
        for n in range(1, num_pages + 1):
            path = f"{path_prefix}{n}"
            body = body_template.format(n=n).encode("utf-8")
            route = self.add_route(path, body)
            routes.append(route)
        return routes

    @property
    def url(self) -> str:
        return self.base_url

    def url_for(self, path: str) -> str:
        """Build a full URL for *path*."""
        if path.startswith("http"):
            return path
        return f"{self.base_url}{path}"

    # ------------------------------------------------------------------
    # Mock transport handler
    # ------------------------------------------------------------------

    def _handler(self, request: httpx.Request) -> httpx.Response:
        """Mock transport handler — intercepts all requests."""
        parsed = urlparse(str(request.url))
        path = parsed.path or "/"  # treat empty path as root

        # Log the request
        self.request_log.append({
            "method": request.method,
            "url": str(request.url),
            "path": path,
            "headers": dict(request.headers),
        })

        # Track call count per path
        self._call_counts[path] = self._call_counts.get(path, 0) + 1

        # Find matching route (exact match first, then prefix)
        route = self.routes.get(path)
        if route is None:
            # Try prefix match
            for rp, r in self.routes.items():
                if path.startswith(rp):
                    route = r
                    break

        if route is None:
            return httpx.Response(404, text=f"Not Found: {path}")

        # Handle fail_first (for retry testing)
        if route.fail_first > 0 and route._hit_count < route.fail_first:
            route._hit_count += 1
            return httpx.Response(
                route.status_code if route.status_code >= 500 else 503,
                text="Transient error",
            )
        route._hit_count += 1

        # Build response headers
        resp_headers = dict(route.headers)

        # Handle conditional requests
        if_none_match = request.headers.get("if-none-match")
        if_modified_since = request.headers.get("if-modified-since")
        etag = route.headers.get("ETag")
        last_modified = route.headers.get("Last-Modified")

        if if_none_match and etag and if_none_match == etag:
            return httpx.Response(304, headers={"ETag": etag})
        if if_modified_since and last_modified and if_modified_since == last_modified:
            return httpx.Response(304, headers={"Last-Modified": last_modified})

        return httpx.Response(
            route.status_code,
            content=route.body,
            headers=resp_headers,
        )

    def call_count(self, path: str | None = None) -> int:
        """Return the total number of calls, or calls to a specific *path*."""
        if path:
            return self._call_counts.get(path, 0)
        return sum(self._call_counts.values())

    def reset_call_counts(self) -> None:
        """Reset all call counters and the request log."""
        self._call_counts.clear()
        self.request_log.clear()
        for route in self.routes.values():
            route._hit_count = 0

    # ------------------------------------------------------------------
    # Client factory
    # ------------------------------------------------------------------

    def make_client(self, **kwargs: Any) -> httpx.AsyncClient:
        """Create an ``httpx.AsyncClient`` backed by the mock transport."""
        transport = httpx.MockTransport(self._handler)
        defaults = {
            "transport": transport,
            "timeout": httpx.Timeout(10.0),
            "follow_redirects": True,
            "headers": {
                "User-Agent": ACTIVE_POLICY.attribution.user_agent,
            },
        }
        defaults.update(kwargs)
        return httpx.AsyncClient(**defaults)

    def make_http_client(
        self,
        policy: CollectionPolicyDecisionV1 | None = None,
        **kwargs: Any,
    ) -> HttpClient:
        """Create a :class:`HttpClient` backed by the mock transport."""
        client = self.make_client(**kwargs)
        return HttpClient(
            client=client,
            policy=policy or ACTIVE_POLICY,
            # Disable rate limiting delays in tests for speed
            max_retries=3,
            backoff=(0.01, 0.01, 0.01, 0.01),
        )


# ---------------------------------------------------------------------------
# StubSourceAdapter — minimal adapter for contract-suite testing
# ---------------------------------------------------------------------------


class StubSourceAdapter(SourceAdapter):
    """Minimal stub adapter for contract-suite verification.

    Yields a fixed set of pages from in-memory data (no HTTP at all).
    Used by the contract test suite to verify the SDK interface works.
    """

    source_slug = "sailsys"

    def __init__(
        self,
        pages: dict[str, bytes] | None = None,
        db: Any = None,
        http_client: HttpClient | None = None,
        gate: CollectionGate | None = None,
        policy: CollectionPolicyDecisionV1 | None = None,
    ):
        self._pages = pages or {
            "/results/1": b"<html>page 1</html>",
            "/results/2": b"<html>page 2</html>",
            "/results/3": b"<html>page 3</html>",
        }
        self._base_url = "http://stub.test"
        super().__init__(db=db, http_client=http_client, gate=gate, policy=policy)

    async def discover(self) -> list[DiscoveredItem]:
        return [
            DiscoveredItem(
                url=f"{self._base_url}{path}",
                parse_hint=ParseHint.HTML,
                metadata={"page": i + 1},
            )
            for i, path in enumerate(self._pages)
        ]

    def parse_hint_for(self, url: str) -> ParseHint:
        return ParseHint.HTML

    async def fetch(self, url: str) -> RawCaptureRequestV1 | None:
        """Override fetch to serve from in-memory pages (no HTTP)."""
        # Resolve the path
        parsed = urlparse(url)
        path = parsed.path

        content = self._pages.get(path)
        if content is None:
            # Skip if already completed
            if self.checkpoint.is_completed(url):
                return None
            return None

        # Check if already completed (checkpoint resume)
        if self.checkpoint.is_completed(url):
            return None

        content_hash = sha256_hex(content)
        envelope = RawCaptureRequestV1(
            source_slug=self.source_slug,
            url=url,
            content=content,
            content_hash=content_hash,
            parse_hint=ParseHint.HTML.value,
            policy_version=self.policy.version,
            status=FetchStatus.FETCHED,
        )
        self._checkpoint_mark_completed(url, content_hash)
        return envelope


# ---------------------------------------------------------------------------
# FakeSourceAdapter — reference adapter against FakeHttpServer
# ---------------------------------------------------------------------------


class FakeSourceAdapter(SourceAdapter):
    """Reference adapter that fetches from a :class:`FakeHttpServer`.

    Proves pagination, retry, checkpoints, content hashing, and policy
    enforcement — all with zero real network calls.

    The adapter discovers pages by reading a ``/index`` JSON endpoint
    that returns a list of page URLs.  Each page is then fetched and
    emitted as a :class:`RawCaptureRequestV1`.
    """

    source_slug = "sailsys"

    def __init__(
        self,
        server: FakeHttpServer,
        db: Any = None,
        http_client: HttpClient | None = None,
        gate: CollectionGate | None = None,
        policy: CollectionPolicyDecisionV1 | None = None,
    ):
        self._server = server
        super().__init__(db=db, http_client=http_client, gate=gate, policy=policy)

    async def discover(self) -> list[DiscoveredItem]:
        """Discover pages by fetching the ``/index`` JSON endpoint."""
        index_url = self._server.url_for("/index")
        result = await self.http.fetch(index_url, skip_rate_limit=True)

        if isinstance(result, type(None)):  # should not happen
            return []

        # The index returns JSON: {"pages": ["/page/1", "/page/2", ...]}
        try:
            data = json.loads(result.content)
        except (json.JSONDecodeError, TypeError):
            data = {"pages": []}

        pages = data.get("pages", [])
        return [
            DiscoveredItem(
                url=self._server.url_for(p) if not p.startswith("http") else p,
                parse_hint=ParseHint.HTML,
                metadata={"page": i + 1},
            )
            for i, p in enumerate(pages)
        ]

    def parse_hint_for(self, url: str) -> ParseHint:
        return ParseHint.HTML


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_fake_server(
    num_pages: int = 3,
    fail_first_page: int = 0,
) -> FakeHttpServer:
    """Create a :class:`FakeHttpServer` with paginated content.

    Parameters
    ----------
    num_pages
        Number of content pages to serve.
    fail_first_page
        If > 0, the first page returns 503 for this many requests
        (to test retry logic).
    """
    server = FakeHttpServer(base_url="http://fake.test")

    # Build the index
    page_paths = [f"/page/{n}" for n in range(1, num_pages + 1)]
    index_body = json.dumps({"pages": page_paths}).encode("utf-8")
    server.add_route("/index", index_body, headers={"Content-Type": "application/json"})

    # Add each page with an ETag for conditional requests
    for n in range(1, num_pages + 1):
        body = f"<html><body>Page {n}</body></html>".encode("utf-8")
        etag = f'"page-{n}-v1"'
        server.add_route(
            f"/page/{n}",
            body,
            headers={
                "Content-Type": "text/html",
                "ETag": etag,
                "Last-Modified": "Sun, 30 Aug 2026 00:00:00 GMT",
            },
            fail_first=fail_first_page if n == 1 else 0,
        )

    # Add a health endpoint
    server.add_route("/", b"OK", headers={"Content-Type": "text/plain"})

    return server


def make_fake_source_record(
    slug: str = "sailsys",
    legal_status: LegalStatus = LegalStatus.APPROVED,
    enabled: bool = True,
    policy_version: str = CURRENT_POLICY_VERSION,
) -> SourceRecord:
    """Create a :class:`SourceRecord` for the fake adapter."""
    return SourceRecord(
        slug=slug,
        display_name=slug.title(),
        base_url="http://fake.test",
        category="results",
        policy_version=policy_version,
        legal_status=legal_status,
        enabled=enabled,
        robots_disallow=[],
    )


def make_fake_adapter(
    server: FakeHttpServer | None = None,
    num_pages: int = 3,
    fail_first_page: int = 0,
    source_slug: str = "sailsys",
    legal_status: LegalStatus = LegalStatus.APPROVED,
    enabled: bool = True,
    policy_version: str = CURRENT_POLICY_VERSION,
    policy: CollectionPolicyDecisionV1 | None = None,
) -> FakeSourceAdapter:
    """Create a fully-wired :class:`FakeSourceAdapter` for testing.

    This sets up the server, gate, and policy so the adapter is ready
    to collect.  Uses zero real network calls.
    """
    server = server or make_fake_server(num_pages=num_pages, fail_first_page=fail_first_page)
    pol = policy or ACTIVE_POLICY

    # Create source record
    source = make_fake_source_record(
        slug=source_slug,
        legal_status=legal_status,
        enabled=enabled,
        policy_version=policy_version,
    )

    # Create gate with the source registered
    gate = CollectionGate(policy=pol, sources=[source])

    # Create HTTP client backed by the fake server
    http_client = server.make_http_client(policy=pol)

    # Patch the source_slug on the adapter class
    FakeSourceAdapter.source_slug = source_slug

    adapter = FakeSourceAdapter(
        server=server,
        db=None,
        http_client=http_client,
        gate=gate,
        policy=pol,
    )

    return adapter
