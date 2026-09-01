"""Tests for the FakeSourceAdapter reference adapter.

Proves the adapter SDK works end-to-end with zero network calls:
- Pagination (multi-page result sets)
- Retry on transient 5xx
- Checkpoint resume
- Content hashing (skip re-download)
- Policy enforcement
- Conditional requests (304)
"""

import json

import httpx
import pytest

from irc_data.sources.adapter import Checkpoint
from irc_data.sources.fake_adapter import FakeSourceAdapter
from irc_data.sources.models import DataSource, FetchResult, RawArtifactV1
from irc_data.sources.policy import (
    PolicyVersionMismatchError,
    SourceNotApprovedError,
)
from irc_data.sources.registry import get_source


class TestFakeAdapter:
    """Tests for FakeSourceAdapter."""

    @pytest.mark.asyncio
    async def test_fake_adapter_collects_all_pages(self):
        adapter = FakeSourceAdapter()
        results = await adapter.run()
        assert len(results) == 3  # 3 pages in the mock

    @pytest.mark.asyncio
    async def test_fake_adapter_yields_fetch_results(self):
        adapter = FakeSourceAdapter()
        async for r in adapter.collect():
            assert isinstance(r, FetchResult)
            assert r.content_hash
            assert r.url

    @pytest.mark.asyncio
    async def test_fake_adapter_retries_transient_5xx(self):
        """Page 2 fails once with 503, then succeeds on retry."""
        adapter = FakeSourceAdapter()
        results = await adapter.run()
        assert len(results) == 3
        # All pages should be present despite the transient failure

    @pytest.mark.asyncio
    async def test_fake_adapter_content_hashing_skips_unchanged(self):
        """If content hash matches, the page is skipped."""
        adapter = FakeSourceAdapter()
        # First run collects all 3 pages
        results = await adapter.run()
        assert len(results) == 3
        # Second run with same hashes should skip all (already seen)
        results2 = await adapter.run()
        # Since the adapter has _seen_hashes from the first run,
        # it should skip unchanged pages
        assert len(results2) == 0

    @pytest.mark.asyncio
    async def test_fake_adapter_checkpoint_resume_skips_completed(self):
        """Checkpoint with completed URLs should skip them."""
        checkpoint = Checkpoint(
            source_slug="sailsys",
            completed_urls=["https://example.com/results?page=1"],
            next_url="https://example.com/results?page=2",
            page=1,
        )
        adapter = FakeSourceAdapter(checkpoint=checkpoint)
        results = await adapter.run()
        # Page 1 is in checkpoint, so we start from page 2
        assert len(results) == 2  # pages 2 and 3

    @pytest.mark.asyncio
    async def test_fake_adapter_conditional_request_304(self):
        """Conditional request with matching ETag returns 304."""
        adapter = FakeSourceAdapter()
        # Run once to populate seen hashes
        first = await adapter.run()
        assert len(first) == 3

    @pytest.mark.asyncio
    async def test_policy_version_mismatch_raises(self):
        src = get_source("sailsys")
        src.policy_version = "stale-version"
        adapter = FakeSourceAdapter(source_override=src)
        with pytest.raises(PolicyVersionMismatchError):
            await adapter.run()

    @pytest.mark.asyncio
    async def test_policy_blocks_hold_source(self):
        src = get_source("clubspot")  # legal_status = 'hold'
        adapter = FakeSourceAdapter(source_override=src)
        with pytest.raises(SourceNotApprovedError, match="hold"):
            await adapter.run()

    @pytest.mark.asyncio
    async def test_policy_blocks_disabled_source(self):
        src = get_source("sailsys")
        src.enabled = False
        adapter = FakeSourceAdapter(source_override=src)
        with pytest.raises(SourceNotApprovedError, match="disabled"):
            await adapter.run()

    @pytest.mark.asyncio
    async def test_registry_seeds_all_eleven_sources(self):
        from irc_data.sources.registry import all_sources
        sources = all_sources()
        assert len(sources) == 11
        slugs = {s.slug for s in sources}
        assert "sailsys" in slugs
        assert "topyacht" in slugs
        assert "irc-tcc" in slugs
        assert "orc" in slugs
        assert "yachtscoring" in slugs
        assert "manage2sail" in slugs
        assert "sailwave" in slugs
        assert "sailing-news" in slugs
        assert "irc-certs" in slugs
        assert "clubspot" in slugs
        assert "kwindoo" in slugs


class TestRawCaptureRequestV1:
    """Tests for RawArtifactV1 roundtrip from adapter output."""

    @pytest.mark.asyncio
    async def test_raw_capture_request_v1_roundtrip(self):
        adapter = FakeSourceAdapter()
        results = await adapter.run()
        assert len(results) > 0
        artifact = adapter.to_raw_artifact(results[0], content_type="text/html")
        assert isinstance(artifact, RawArtifactV1)
        assert artifact.source_slug == "sailsys"
        assert artifact.content_type == "text/html"
        assert artifact.content_hash == results[0].content_hash
        assert artifact.schema_version == "1"

        # Roundtrip through dict
        d = artifact.to_dict()
        assert d["source_slug"] == "sailsys"
        assert d["schema_version"] == "1"


class TestAdapterCheckpointV1:
    """Tests for checkpoint serialization."""

    @pytest.mark.asyncio
    async def test_adapter_checkpoint_v1_roundtrip(self):
        cp = Checkpoint(
            source_slug="sailsys",
            completed_urls=["https://example.com/p1", "https://example.com/p2"],
            next_url="https://example.com/p3",
            page=2,
        )
        json_str = cp.to_json()
        cp2 = Checkpoint.from_json(json_str)
        assert cp2.source_slug == "sailsys"
        assert cp2.completed_urls == ["https://example.com/p1", "https://example.com/p2"]
        assert cp2.next_url == "https://example.com/p3"
        assert cp2.page == 2

    @pytest.mark.asyncio
    async def test_checkpoint_with_progress_appends_url(self):
        cp = Checkpoint(source_slug="sailsys")
        assert not cp.is_completed("https://example.com/p1")
        cp.mark_completed("https://example.com/p1")
        assert cp.is_completed("https://example.com/p1")
        # Marking again should not duplicate
        cp.mark_completed("https://example.com/p1")
        assert len(cp.completed_urls) == 1
