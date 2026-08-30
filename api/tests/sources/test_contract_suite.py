"""Prove the contract suite is generic: run it against a *second* adapter.

The reference :class:`FakeSourceAdapter` is the obvious target, but the
SPEC-012 §4.2 verification criterion is that the suite "runs identically
against every adapter".  This file defines a minimal ``StubAdapter``
that re-uses the SDK building blocks but answers a different URL shape
(``/feed?page=N`` instead of ``/results?page=N``) and a different
parse-hint.  If the suite passes for both, it is genuinely adapter-
agnostic.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from irc_data.sources import (
    CURRENT_POLICY_VERSION,
    DataSource,
    FetchResult,
    FetchTarget,
    FakeSourceServer,  # re-use the in-process server
    PolicyVersionMismatchError,
    RawCaptureRequestV1,
    SourceNotApprovedError,
    run_adapter_contract,
)
from irc_data.sources.adapter import SourceAdapter
from irc_data.sources.contracts import STANDARD_USER_AGENT
from irc_data.sources.http import PolicyAwareHTTPClient, sha256_hex
from irc_data.sources.rate_limit import RateLimiter


# ---------------------------------------------------------------------------
# Stub adapter — different URL shape, same SDK contract
# ---------------------------------------------------------------------------
class _StubServer(FakeSourceServer):
    """A fake server that serves ``/feed?page=N`` instead of ``/results``."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.request_log.append(request)
        url = str(request.url)
        self.hit_counts[url] = self.hit_counts.get(url, 0) + 1

        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        page = int(qs.get("page", ["1"])[0])

        if page in self.flaky_pages and page not in self._flaky_hits:
            self._flaky_hits.add(page)
            return httpx.Response(503, text="transient error")

        if parsed.path != "/feed":
            return httpx.Response(404, text="not found")

        etag = self.etags.get(url)
        if etag and request.headers.get("If-None-Match") == etag:
            return httpx.Response(304, headers={"ETag": etag})

        body = {"page": page, "entries": [f"entry-{page}-{i}" for i in range(2)],
                "next": f"/feed?page={page + 1}" if page < self.page_count else None}
        body_bytes = json.dumps(body, sort_keys=True).encode()
        headers = {
            "ETag": sha256_hex(body_bytes),
            "Content-Type": "application/rss+xml",
        }
        return httpx.Response(200, json=body, headers=headers)

    @property
    def all_page_urls(self) -> list[str]:
        return [f"http://stub.test/feed?page={p}" for p in range(1, self.page_count + 1)]


class StubAdapter(SourceAdapter):
    """A second adapter proving the contract suite is generic."""

    source_slug = "stub"
    BASE_URL = "http://stub.test"

    def __init__(self, *, server=None, registry=None, checkpoint_store=None,
                 correlation_id=None):
        self.server = server or _StubServer()
        if registry is None:
            from irc_data.sources.registry import seed_registry
            registry = seed_registry()
            if "stub" not in registry:
                registry.upsert(DataSource(
                    slug="stub",
                    display_name="Stub (contract-suite proof)",
                    base_url=self.BASE_URL,
                    category="news",
                    policy_version=CURRENT_POLICY_VERSION,
                    legal_status="approved",
                ))
        super().__init__(
            registry=registry,
            http=self.server.client(),
            checkpoint_store=checkpoint_store,
            correlation_id=correlation_id,
        )

    async def discover(self) -> AsyncIterator[FetchTarget]:
        cp = self.load_checkpoint()
        start = int(cp.cursor) if cp.cursor else 1
        for page in range(start, self.server.page_count + 1):
            url = f"{self.BASE_URL}/feed?page={page}"
            yield FetchTarget(url=url, kind="feed", etag=self.server.etags.get(url),
                              meta={"page": str(page)})

    def parse_hint(self, target: FetchTarget, result: FetchResult) -> str | None:
        return "rss.entries" if target.kind == "feed" else None


# ---------------------------------------------------------------------------
# Factory + expected URLs
# ---------------------------------------------------------------------------
def make_stub_adapter(**kwargs):
    return StubAdapter(server=_StubServer(), **kwargs)


STUB_EXPECTED_URLS = [
    "http://stub.test/feed?page=1",
    "http://stub.test/feed?page=2",
    "http://stub.test/feed?page=3",
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_stub_adapter_collects_all_pages():
    adapter = make_stub_adapter()
    envelopes = asyncio.run(adapter.run())
    assert len(envelopes) == 3
    assert [e.url for e in envelopes] == STUB_EXPECTED_URLS
    for env in envelopes:
        assert env.parse_hint == "rss.entries"
        assert env.content_type == "application/rss+xml"


def test_contract_suite_passes_for_stub_adapter():
    """The same suite passes against a different adapter → generic."""
    report = run_adapter_contract(
        adapter_factory=make_stub_adapter,
        expected_urls=STUB_EXPECTED_URLS,
        expected_envelope_count=3,
    )
    assert report["ok"] is True, report
    assert report["failed"] == 0, report


def test_contract_suite_failure_is_reported():
    """If an adapter violates the contract, the suite raises ContractFailure."""
    from irc_data.sources.contract_suite import ContractFailure

    # An adapter whose server has zero pages yields 0 envelopes → envelope_count check fails.
    def make_empty_adapter(**kwargs):
        server = _StubServer(page_count=0)
        return StubAdapter(server=server, **kwargs)

    with pytest.raises(ContractFailure):
        run_adapter_contract(
            adapter_factory=make_empty_adapter,
            expected_urls=[],
            expected_envelope_count=3,  # intentionally wrong
        )
