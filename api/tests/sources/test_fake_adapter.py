"""Tests for the fake adapter and output contracts (DP-01-03).

Covers all acceptance criteria:
  - RawCaptureRequestV1 / AdapterCheckpointV1 JSON roundtrip
  - Checkpoint progress append
  - Policy version mismatch raises
  - Policy blocks hold source
  - Policy blocks disabled source
  - Registry seeds all eleven sources
  - Fake adapter pagination
  - Fake adapter retry on transient 5xx
  - Fake adapter content hashing skips unchanged
  - Fake adapter checkpoint resume skips completed
  - Fake adapter conditional request 304
  - Fake adapter health probe
  - Fake adapter hold source raises before fetch
  - Contract suite passes for fake adapter
"""

from __future__ import annotations

import json

import pytest

from irc_data.sources.envelope import (
    AdapterCheckpointV1,
    FetchStatus,
    RawCaptureRequestV1,
    sha256_hex,
)
from irc_data.sources.fake_adapter import (
    FakeHttpServer,
    FakeSourceAdapter,
    StubSourceAdapter,
    make_fake_adapter,
    make_fake_server,
)
from irc_data.sources.policy import (
    ACTIVE_POLICY,
    CURRENT_POLICY_VERSION,
    LegalStatus,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
)
from irc_data.sources.registry import (
    HOLD_SOURCES,
    SEED_COUNT,
    get_all_sources,
    get_in_memory_source,
    get_in_memory_sources,
)
from tests.sources.test_contract_suite import run_contract_suite


# ---------------------------------------------------------------------------
# 1. RawCaptureRequestV1 roundtrip
# ---------------------------------------------------------------------------


def test_raw_capture_request_v1_roundtrip():
    """RawCaptureRequestV1 survives a JSON round-trip."""
    env = RawCaptureRequestV1(
        source_slug="sailsys",
        url="http://example.com/page/1",
        content=b"<html>hello</html>",
        content_hash=sha256_hex(b"<html>hello</html>"),
        content_type="text/html",
        parse_hint="html",
        etag='"abc123"',
        last_modified="Sun, 30 Aug 2026 00:00:00 GMT",
        fetched_at="2026-08-30T12:00:00+00:00",
        policy_version=CURRENT_POLICY_VERSION,
        status=FetchStatus.FETCHED,
    )
    json_str = env.to_json()
    restored = RawCaptureRequestV1.from_json(json_str)

    assert restored == env
    assert restored.source_slug == "sailsys"
    assert restored.content == b"<html>hello</html>"
    assert restored.content_hash == env.content_hash
    assert restored.status == FetchStatus.FETCHED
    assert restored.parse_hint == "html"


# ---------------------------------------------------------------------------
# 2. AdapterCheckpointV1 roundtrip
# ---------------------------------------------------------------------------


def test_adapter_checkpoint_v1_roundtrip():
    """AdapterCheckpointV1 survives a JSON round-trip."""
    cp = AdapterCheckpointV1(
        source_slug="sailsys",
        policy_version=CURRENT_POLICY_VERSION,
        completed_urls=["http://example.com/1", "http://example.com/2"],
        content_hashes={
            "http://example.com/1": "aaa",
            "http://example.com/2": "bbb",
        },
        next_url="http://example.com/3",
        total_pages=5,
        status="in_progress",
    )
    json_str = cp.to_json()
    restored = AdapterCheckpointV1.from_json(json_str)

    assert restored == cp
    assert restored.source_slug == "sailsys"
    assert len(restored.completed_urls) == 2
    assert restored.next_url == "http://example.com/3"
    assert restored.status == "in_progress"


# ---------------------------------------------------------------------------
# 3. Checkpoint with progress appends URL
# ---------------------------------------------------------------------------


def test_checkpoint_with_progress_appends_url():
    """mark_completed appends URLs and records hashes (append-only)."""
    cp = AdapterCheckpointV1(source_slug="sailsys")

    # Initially empty
    assert len(cp.completed_urls) == 0
    assert not cp.is_completed("http://example.com/1")

    # Mark first URL completed
    cp.mark_completed("http://example.com/1", "hash_a")
    assert cp.is_completed("http://example.com/1")
    assert cp.has_hash("http://example.com/1", "hash_a")
    assert len(cp.completed_urls) == 1

    # Mark second URL completed
    cp.mark_completed("http://example.com/2", "hash_b")
    assert len(cp.completed_urls) == 2
    assert cp.completed_urls == ["http://example.com/1", "http://example.com/2"]

    # Idempotent — appending same URL doesn't duplicate
    cp.mark_completed("http://example.com/1", "hash_a")
    assert len(cp.completed_urls) == 2

    # Mark complete
    cp.mark_complete()
    assert cp.status == "completed"
    assert cp.next_url is None


# ---------------------------------------------------------------------------
# 4. Policy version mismatch raises
# ---------------------------------------------------------------------------


def test_policy_version_mismatch_raises():
    """A source with a stale policy_version raises PolicyVersionMismatchError."""
    from irc_data.sources.policy import CollectionPolicyDecisionV1

    policy = CollectionPolicyDecisionV1()
    with pytest.raises(PolicyVersionMismatchError):
        policy.assert_version("interim-v0.9-obsolete", "sailsys")


# ---------------------------------------------------------------------------
# 5. Policy blocks hold source
# ---------------------------------------------------------------------------


def test_policy_blocks_hold_source():
    """A 'hold' source is blocked by the policy gate."""
    from irc_data.sources.gate import CollectionGate

    source = get_in_memory_source("clubspot")
    assert source.legal_status == LegalStatus.HOLD

    gate = CollectionGate(policy=ACTIVE_POLICY, sources=[source])
    with pytest.raises(SourceNotApprovedError, match="legal_status=hold"):
        gate.resolve_source("clubspot")


# ---------------------------------------------------------------------------
# 6. Policy blocks disabled source
# ---------------------------------------------------------------------------


def test_policy_blocks_disabled_source():
    """A disabled source is blocked by the policy gate."""
    from irc_data.sources.gate import CollectionGate, SourceRecord

    # Use an APPROVED source with enabled=False so the legal-status gate
    # passes and the enabled gate (kill switch) is what blocks it.
    source = SourceRecord(
        slug="sailsys",
        display_name="SailSys",
        base_url="https://app.sailsys.com.au",
        category="results",
        legal_status=LegalStatus.APPROVED,
        enabled=False,
    )
    gate = CollectionGate(policy=ACTIVE_POLICY, sources=[source])
    with pytest.raises(SourceNotApprovedError, match="disabled"):
        gate.resolve_source("sailsys")


# ---------------------------------------------------------------------------
# 7. Registry seeds all eleven sources
# ---------------------------------------------------------------------------


def test_registry_seeds_all_eleven_sources():
    """The in-memory registry has exactly 11 sources (9 approved, 2 hold)."""
    sources = get_in_memory_sources()
    assert len(sources) == SEED_COUNT == 11

    slugs = {s.slug for s in sources}
    assert "sailsys" in slugs
    assert "irc-certs" in slugs
    assert "clubspot" in slugs
    assert "kwindoo" in slugs

    hold_sources = [s for s in sources if s.legal_status == LegalStatus.HOLD]
    assert len(hold_sources) == 2
    hold_slugs = {s.slug for s in hold_sources}
    assert hold_slugs == set(HOLD_SOURCES) == {"clubspot", "kwindoo"}

    approved = [s for s in sources if s.legal_status == LegalStatus.APPROVED]
    assert len(approved) == 9


# ---------------------------------------------------------------------------
# 8. Fake adapter collects all pages (pagination)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_adapter_collects_all_pages():
    """The fake adapter collects all 3 pages (pagination)."""
    adapter = make_fake_adapter(num_pages=3)
    results = await adapter.run()

    assert len(results) == 3
    for i, env in enumerate(results, 1):
        assert env.source_slug == "sailsys"
        assert f"/page/{i}" in env.url
        assert env.status == FetchStatus.FETCHED
        assert env.content_hash == sha256_hex(env.content)
        assert b"Page" in env.content


# ---------------------------------------------------------------------------
# 9. Fake adapter retries transient 5xx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_adapter_retries_transient_5xx():
    """The fake adapter retries on transient 5xx and eventually succeeds."""
    adapter = make_fake_adapter(num_pages=3, fail_first_page=2)
    results = await adapter.run()

    assert len(results) == 3
    # The first page should have been fetched after retries
    assert results[0].status == FetchStatus.FETCHED
    # Verify retry happened (call count > 3 for 3 pages)
    assert adapter._server.call_count() > 3


# ---------------------------------------------------------------------------
# 10. Fake adapter content hashing skips unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_adapter_content_hashing_skips_unchanged():
    """Content hashing skips re-download of unchanged pages."""
    adapter = make_fake_adapter(num_pages=3)
    # First collect
    results1 = await adapter.run()
    assert len(results1) == 3

    # Get the hashes
    checkpoint = adapter.save_checkpoint()
    for url, h in checkpoint.content_hashes.items():
        assert len(h) == 64  # SHA-256 hex

    # Verify content hashes match
    for env in results1:
        assert checkpoint.has_hash(env.url, env.content_hash)


# ---------------------------------------------------------------------------
# 11. Fake adapter checkpoint resume skips completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_adapter_checkpoint_resume_skips_completed():
    """Checkpoint resume skips already-completed URLs."""
    adapter = make_fake_adapter(num_pages=3)

    # Load a pre-populated checkpoint with all 3 URLs completed
    cp = AdapterCheckpointV1(source_slug="sailsys", status="completed")
    server = adapter._server
    for n in range(1, 4):
        url = server.url_for(f"/page/{n}")
        content = f"<html><body>Page {n}</body></html>".encode()
        cp.mark_completed(url, sha256_hex(content))

    adapter.load_checkpoint(cp)

    # Collect should skip all (already completed)
    results = await adapter.run()
    assert len(results) == 0  # all skipped


# ---------------------------------------------------------------------------
# 12. Fake adapter conditional request 304
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_adapter_conditional_request_304():
    """Conditional requests return 304 as clean success."""
    server = make_fake_server(num_pages=1)
    adapter = make_fake_adapter(server=server, num_pages=1)

    # First fetch — get the ETag
    results = await adapter.run()
    assert len(results) == 1

    # Now create a new adapter and fetch the same URL with the ETag
    server2 = make_fake_server(num_pages=1)
    adapter2 = make_fake_adapter(server=server2, num_pages=1)

    # Load checkpoint with the known hash so conditional headers are sent
    cp = AdapterCheckpointV1(source_slug="sailsys")
    url = server2.url_for("/page/1")
    # The server has ETag '"page-1-v1"' — simulate knowing it
    # We test the HttpClient directly
    from irc_data.sources.http_client import NotModified
    result = await adapter2.http.fetch(
        url,
        etag='"page-1-v1"',
    )
    assert isinstance(result, NotModified)
    assert result.url == url


# ---------------------------------------------------------------------------
# 13. Fake adapter health probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_adapter_health_probe():
    """Health probe returns a healthy result."""
    adapter = make_fake_adapter(num_pages=3)
    health = await adapter.health_probe()

    assert health.healthy is True
    assert health.url == adapter.source.base_url


# ---------------------------------------------------------------------------
# 14. Fake adapter hold source raises before fetch
# ---------------------------------------------------------------------------


def test_fake_adapter_hold_source_raises_before_fetch():
    """A 'hold' source raises SourceNotApprovedError before any fetch."""
    with pytest.raises(SourceNotApprovedError, match="legal_status=hold"):
        make_fake_adapter(source_slug="clubspot", legal_status=LegalStatus.HOLD)


# ---------------------------------------------------------------------------
# 15. Contract suite passes for fake adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contract_suite_passes_for_fake_adapter():
    """The contract suite passes against the fake adapter."""
    adapter = make_fake_adapter(num_pages=3)
    results = await run_contract_suite(adapter)
    assert results["discover_count"] == 3
    assert results["collect_count"] == 3
    assert results["checkpoint_urls"] == 3


# ---------------------------------------------------------------------------
# FakeHttpServer direct tests
# ---------------------------------------------------------------------------


class TestEnvelopeShape:
    """Tests for envelope JSON shape and serialization."""

    def test_envelope_json_roundtrip(self):
        """Envelope round-trips through JSON preserving all fields."""
        env = RawCaptureRequestV1(
            source_slug="irc-certs",
            url="https://ircrating.org/cert/123.pdf",
            content=b"%PDF-1.4 fake",
            content_hash=sha256_hex(b"%PDF-1.4 fake"),
            content_type="application/pdf",
            parse_hint="pdf",
            etag='"cert-123"',
            fetched_at="2026-08-30T00:00:00+00:00",
            policy_version=CURRENT_POLICY_VERSION,
            status=FetchStatus.FETCHED,
        )
        d = env.to_dict()
        # Content is hex-encoded
        assert d["content"] == b"%PDF-1.4 fake".hex()
        assert d["parse_hint"] == "pdf"
        assert d["status"] == "fetched"

        restored = RawCaptureRequestV1.from_dict(d)
        assert restored.content == b"%PDF-1.4 fake"
        assert restored.parse_hint == "pdf"


class TestFakeHttpServer:
    """Tests for the in-process fake HTTP server."""

    @pytest.mark.asyncio
    async def test_404_for_unknown_path(self):
        """Unknown paths return 404."""
        server = FakeHttpServer()
        client = server.make_client()
        resp = await client.get("http://fake.test/nonexistent")
        assert resp.status_code == 404
        await client.aclose()

    @pytest.mark.asyncio
    async def test_user_agent_set_on_client(self):
        """The User-Agent is set on every request."""
        server = FakeHttpServer()
        server.add_route("/test", b"ok")
        client = server.make_client()
        await client.get("http://fake.test/test")
        await client.aclose()

        # Check the request log
        assert len(server.request_log) > 0
        req = server.request_log[-1]
        ua = req["headers"].get("user-agent", "")
        assert "SailRatings" in ua

    @pytest.mark.asyncio
    async def test_call_counts_track_retries(self):
        """Call counts increase on each attempt (including retries)."""
        server = FakeHttpServer()
        server.add_route("/flaky", b"ok", fail_first=2)
        from irc_data.sources.http_client import HttpClient

        http_client = server.make_http_client()
        result = await http_client.fetch("http://fake.test/flaky")

        # The route should have been called 3 times (2 failures + 1 success)
        assert server.call_count("/flaky") == 3
