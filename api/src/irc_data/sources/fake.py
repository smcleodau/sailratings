"""Reference adapter + fake source (SPEC-012 §4.2).

``FakeSourceAdapter`` is the canonical example of the SDK.  It is
shipped alongside the SDK and its tests run with **zero network calls**
— the HTTP transport is a :class:`FakeSourceServer`, an
``httpx.MockTransport`` handler that serves a small paginated result
set and simulates the behaviours every adapter must handle:

* **pagination** — 3 pages of results linked by ``cursor``
* **retry on transient 5xx** — page 2 returns 503 on the first attempt
* **checkpoint resume** — an interrupted run resumes from the last page
* **content hashing** — unchanged pages are skipped on re-fetch
* **conditional requests** — ``If-None-Match`` → 304
* **policy enforcement** — a ``hold`` source raises before any fetch
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .adapter import SourceAdapter
from .contracts import (
    AdapterCheckpointV1,
    FetchResult,
    FetchTarget,
    RawCaptureRequestV1,
)
from .http import PolicyAwareHTTPClient, sha256_hex
from .rate_limit import RateLimiter

__all__ = ["FakeSourceAdapter", "FakeSourceServer"]


# ---------------------------------------------------------------------------
# Fake source server (httpx.MockTransport handler)
# ---------------------------------------------------------------------------
class FakeSourceServer:
    """In-process HTTP server backed by ``httpx.MockTransport``.

    Serves ``/results?page=<n>`` with a ``cursor`` query param that
    paginates through ``PAGE_COUNT`` pages.  The response body is JSON
    of the form::

        {"page": 1, "items": [...], "next": "/results?page=2"}

    Behaviour knobs (constructor args):

    * ``flaky_pages`` — page numbers that return 503 on the first hit
      (proves retry logic).
    * ``etag_for`` — optional mapping of URL → ETag; if the request
      sends ``If-None-Match`` matching, return 304.
    * ``page_count`` — number of pages (default 3).
    """

    def __init__(
        self,
        *,
        page_count: int = 3,
        flaky_pages: tuple[int, ...] = (2,),
        etags: dict[str, str] | None = None,
    ) -> None:
        self.page_count = page_count
        self.flaky_pages = set(flaky_pages)
        self.etags = etags or {}
        self._flaky_hits: set[int] = set()
        self.request_log: list[httpx.Request] = []
        self.hit_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # httpx.MockTransport handler
    # ------------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.request_log.append(request)
        url = str(request.url)
        self.hit_counts[url] = self.hit_counts.get(url, 0) + 1

        parsed = urlparse(url)
        path = parsed.path
        qs = parse_qs(parsed.query)
        page = int(qs.get("page", ["1"])[0])

        # Flaky page: first hit 503, second hit 200 (proves retry).
        if page in self.flaky_pages and page not in self._flaky_hits:
            self._flaky_hits.add(page)
            return httpx.Response(503, text="transient error", headers={})

        if path != "/results":
            return httpx.Response(404, text="not found")

        # Conditional request → 304.
        etag = self.etags.get(url)
        if etag and request.headers.get("If-None-Match") == etag:
            return httpx.Response(304, headers={"ETag": etag})

        body, body_etag = self._page_body(page)
        headers = {"ETag": body_etag, "Content-Type": "application/json"}
        if page < self.page_count:
            headers["X-Next-Page"] = str(page + 1)
        return httpx.Response(200, json=body, headers=headers)

    def _page_body(self, page: int) -> tuple[dict[str, Any], str]:
        items = [{"boat": f"Boat-{page}-{i}", "place": i} for i in range(1, 4)]
        next_url = f"/results?page={page + 1}" if page < self.page_count else None
        body = {"page": page, "items": items, "next": next_url}
        import json

        body_bytes = json.dumps(body, sort_keys=True).encode()
        return body, sha256_hex(body_bytes)

    # ------------------------------------------------------------------
    # Transport accessor
    # ------------------------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def client(self, **kwargs: Any) -> PolicyAwareHTTPClient:
        """Build a :class:`PolicyAwareHTTPClient` wired to this server."""
        return PolicyAwareHTTPClient(
            rate_limiter=RateLimiter(min_delay=0.0, jitter=0.0),
            transport=self.transport(),
            sleep=_NoSleep(),
            **kwargs,
        )

    @property
    def all_page_urls(self) -> list[str]:
        return [f"http://fake.test/results?page={p}" for p in range(1, self.page_count + 1)]


class _NoSleep:
    """Awaitable that returns immediately — used in tests to skip real sleeps."""

    async def __call__(self, _seconds: float) -> None:
        return None


# ---------------------------------------------------------------------------
# Reference adapter
# ---------------------------------------------------------------------------
class FakeSourceAdapter(SourceAdapter):
    """Reference adapter against :class:`FakeSourceServer`.

    Demonstrates pagination, retry, checkpoint resume, content hashing
    and policy enforcement.  The ``base_url`` of the source record must
    point at ``http://fake.test``; the actual transport is injected via
    the ``http`` client so no real network is used.
    """

    source_slug = "fake"

    BASE_URL = "http://fake.test"

    def __init__(
        self,
        *,
        server: FakeSourceServer | None = None,
        registry=None,
        http: PolicyAwareHTTPClient | None = None,
        checkpoint_store=None,
        correlation_id: str | None = None,
    ) -> None:
        self.server = server or FakeSourceServer()
        # Register the `fake` source in an in-memory registry if the
        # caller did not supply one.
        from .contracts import CURRENT_POLICY_VERSION, DataSource
        from .registry import InMemorySourceRegistry

        if registry is None:
            from .registry import seed_registry

            registry = seed_registry()
            if "fake" not in registry:
                registry.upsert(
                    DataSource(
                        slug="fake",
                        display_name="Fake Source (reference adapter)",
                        base_url=self.BASE_URL,
                        category="results",
                        policy_version=CURRENT_POLICY_VERSION,
                        legal_status="approved",
                    )
                )
        super().__init__(
            registry=registry,
            http=http or self.server.client(),
            checkpoint_store=checkpoint_store,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------
    # discover — paginated walk
    # ------------------------------------------------------------------
    async def discover(self) -> AsyncIterator[FetchTarget]:
        """Walk pages until ``next`` is absent or the fetch cap is hit."""
        # Resume from checkpoint if present.
        cp = self.load_checkpoint()
        start_page = self._cursor_page(cp.cursor)

        page = start_page
        while page <= self.server.page_count:
            url = f"{self.BASE_URL}/results?page={page}"
            etag = self.server.etags.get(url)
            yield FetchTarget(
                url=url,
                kind="json",
                etag=etag,
                meta={"page": str(page)},
            )
            page += 1

    @staticmethod
    def _cursor_page(cursor: str | None) -> int:
        if not cursor:
            return 1
        try:
            return int(cursor)
        except (TypeError, ValueError):
            return 1

    # ------------------------------------------------------------------
    # fetch — wraps the SDK fetch to capture the ``next`` cursor
    # ------------------------------------------------------------------
    async def fetch(self, target: FetchTarget) -> FetchResult:
        result = await super().fetch(target)
        # Persist a cursor so an interrupted run resumes on the next page.
        cp = self.load_checkpoint()
        page = int(target.meta.get("page", "1"))
        new_cp = AdapterCheckpointV1(
            source_slug=self.source_slug,
            cursor=str(page + 1),
            completed_urls=cp.completed_urls,
            fetched_count=cp.fetched_count,
            bytes_fetched=cp.bytes_fetched,
            schema_version=cp.schema_version,
            policy_version=cp.policy_version,
            meta=dict(cp.meta),
        )
        self.save_checkpoint(new_cp)
        return result

    # ------------------------------------------------------------------
    # parse_hint
    # ------------------------------------------------------------------
    def parse_hint(self, target: FetchTarget, result: FetchResult) -> str | None:
        if target.kind == "json":
            return "json.results-page"
        return None

    # ------------------------------------------------------------------
    # collect — wraps FetchResult in a RawCaptureRequestV1
    # ------------------------------------------------------------------
    async def collect(self) -> AsyncIterator[RawCaptureRequestV1]:
        async for envelope in super().collect():
            yield envelope
