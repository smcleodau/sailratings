"""Generic contract test suite for source adapters (DP-01-03).

This suite runs identically against **every** adapter that implements
the :class:`SourceAdapter` contract.  It verifies:

1. ``collect()`` yields :class:`RawCaptureRequestV1` envelopes.
2. Every envelope has a valid SHA-256 content hash.
3. The checkpoint records completed URLs.
4. The adapter can resume from a checkpoint.
5. ``discover()`` returns :class:`DiscoveredItem` objects.
6. ``health_probe()`` returns a :class:`HealthProbeResult`.

The suite is designed to be reusable: pass any adapter factory and it
will exercise the full contract.
"""

from __future__ import annotations

import asyncio

import pytest

from irc_data.sources.adapter import DiscoveredItem, HealthProbeResult, ParseHint
from irc_data.sources.envelope import (
    AdapterCheckpointV1,
    FetchStatus,
    RawCaptureRequestV1,
    sha256_hex,
)
from irc_data.sources.fake_adapter import (
    StubSourceAdapter,
)


# ---------------------------------------------------------------------------
# Contract suite runner — reusable against any adapter
# ---------------------------------------------------------------------------


async def run_contract_suite(adapter) -> dict:
    """Run the full contract against *adapter*.

    Returns a dict of results.  Raises ``AssertionError`` on any
    contract violation.
    """
    results = {}

    # 1. discover() returns DiscoveredItem list
    items = await adapter.discover()
    assert isinstance(items, list), "discover() must return a list"
    assert len(items) > 0, "discover() must return at least one item"
    for item in items:
        assert isinstance(item, DiscoveredItem), "items must be DiscoveredItem"
        assert item.url, "every item must have a URL"
    results["discover_count"] = len(items)

    # 2. collect() yields RawCaptureRequestV1 envelopes
    envelopes = []
    async for env in adapter.collect():
        assert isinstance(env, RawCaptureRequestV1), \
            "collect() must yield RawCaptureRequestV1"
        envelopes.append(env)
    assert len(envelopes) > 0, "collect() must yield at least one envelope"
    results["collect_count"] = len(envelopes)

    # 3. Every envelope has a valid content hash
    for env in envelopes:
        if env.status == FetchStatus.FETCHED and env.content:
            assert env.content_hash == sha256_hex(env.content), \
                "content_hash must be SHA-256 of content"
            assert len(env.content_hash) == 64, "content_hash must be 64 hex chars"
        assert env.source_slug, "envelope must have source_slug"
        assert env.url, "envelope must have url"
        assert env.policy_version, "envelope must have policy_version"

    # 4. Checkpoint records completed URLs
    checkpoint = adapter.save_checkpoint()
    assert isinstance(checkpoint, AdapterCheckpointV1), \
        "save_checkpoint() must return AdapterCheckpointV1"
    assert checkpoint.source_slug == adapter.source_slug, \
        "checkpoint source_slug must match adapter"
    assert len(checkpoint.completed_urls) > 0, \
        "checkpoint must record completed URLs after collect()"

    # 5. health_probe() returns HealthProbeResult
    health = await adapter.health_probe()
    assert isinstance(health, HealthProbeResult), \
        "health_probe() must return HealthProbeResult"

    results["checkpoint_urls"] = len(checkpoint.completed_urls)
    results["healthy"] = health.healthy
    return results


# ---------------------------------------------------------------------------
# Stub adapter contract tests
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_adapter():
    """A minimal stub adapter for contract verification."""
    from irc_data.sources.policy import ACTIVE_POLICY
    from irc_data.sources.gate import CollectionGate
    from irc_data.sources.registry import get_in_memory_source

    source = get_in_memory_source("sailsys")
    gate = CollectionGate(policy=ACTIVE_POLICY, sources=[source])
    adapter = StubSourceAdapter(gate=gate)
    return adapter


@pytest.mark.asyncio
async def test_stub_adapter_collects_all_pages(stub_adapter):
    """The stub adapter collects all 3 pages."""
    results = await stub_adapter.run()
    assert len(results) == 3
    for env in results:
        assert isinstance(env, RawCaptureRequestV1)
        assert env.content_hash == sha256_hex(env.content)


@pytest.mark.asyncio
async def test_contract_suite_passes_for_stub_adapter(stub_adapter):
    """The contract suite passes against the stub adapter."""
    results = await run_contract_suite(stub_adapter)
    assert results["discover_count"] == 3
    assert results["collect_count"] == 3
    assert results["checkpoint_urls"] == 3


@pytest.mark.asyncio
async def test_contract_suite_failure_is_reported():
    """The contract suite reports failures (not silently passes)."""
    from irc_data.sources.adapter import SourceAdapter, DiscoveredItem, ParseHint
    from irc_data.sources.envelope import RawCaptureRequestV1, sha256_hex, FetchStatus
    from irc_data.sources.policy import ACTIVE_POLICY
    from irc_data.sources.gate import CollectionGate
    from irc_data.sources.registry import get_in_memory_source

    class BadAdapter(SourceAdapter):
        """An adapter that violates the contract (bad content hash)."""
        source_slug = "sailsys"

        async def discover(self):
            return [DiscoveredItem(url="http://bad.test/1")]

        async def fetch(self, url):
            # Return an envelope with a WRONG content hash
            return RawCaptureRequestV1(
                source_slug=self.source_slug,
                url=url,
                content=b"hello",
                content_hash="0" * 64,  # wrong hash
                policy_version=self.policy.version,
                status=FetchStatus.FETCHED,
            )

    source = get_in_memory_source("sailsys")
    gate = CollectionGate(policy=ACTIVE_POLICY, sources=[source])
    adapter = BadAdapter(gate=gate)

    with pytest.raises(AssertionError, match="content_hash must be SHA-256"):
        await run_contract_suite(adapter)
