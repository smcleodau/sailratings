"""Tests for the adapter contract suite.

Validates that any SourceAdapter implementation passes the contract:
1. Yields FetchResult objects
2. Calls assert_policy_current
3. Respects enabled=False
4. Respects legal_status='hold'
5. Produces content_hash
6. Supports run() and to_raw_artifact()
7. Respects robots_disallow
"""

import pytest

from irc_data.sources.adapter import Checkpoint, SourceAdapter
from irc_data.sources.contract_suite import ContractSuite
from irc_data.sources.fake_adapter import FakeSourceAdapter
from irc_data.sources.models import DataSource, FetchResult, RawArtifactV1
from irc_data.sources.policy import (
    PolicyVersionMismatchError,
    SourceNotApprovedError,
)
from irc_data.sources.registry import get_source


# ---------------------------------------------------------------------------
# Stub adapter for testing the contract suite itself
# ---------------------------------------------------------------------------


class StubAdapter(SourceAdapter):
    """Minimal adapter that yields one page."""

    source_slug = "sailsys"

    async def collect(self):
        src = self._source
        from irc_data.sources.policy import assert_policy_current, assert_source_approved
        assert_policy_current(src)
        assert_source_approved(src)

        if src.is_disallowed("https://example.com/stub"):
            raise SourceNotApprovedError(src.slug, "robots disallowed")

        yield FetchResult(
            url="https://example.com/stub",
            content=b"<html><body>Stub</body></html>",
            content_hash="a" * 64,
            policy_version=src.policy_version,
        )


class FailingAdapter(SourceAdapter):
    """Adapter that always fails the contract (yields non-FetchResult)."""

    source_slug = "sailsys"

    async def collect(self):
        yield "not a FetchResult"  # type: ignore


def make_stub_adapter():
    return StubAdapter()


def make_failing_adapter():
    return FailingAdapter()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContractSuite:
    """Tests for the contract suite itself."""

    @pytest.mark.asyncio
    async def test_stub_adapter_collects_all_pages(self):
        adapter = make_stub_adapter()
        results = []
        async for r in adapter.collect():
            results.append(r)
        assert len(results) == 1
        assert isinstance(results[0], FetchResult)

    @pytest.mark.asyncio
    async def test_contract_suite_passes_for_stub_adapter(self):
        suite = ContractSuite(make_stub_adapter)
        results = await suite.run_all()
        for name, passed in results.items():
            assert passed, f"Contract check '{name}' failed"

    @pytest.mark.asyncio
    async def test_contract_suite_failure_is_reported(self):
        suite = ContractSuite(make_failing_adapter)
        results = await suite.run_all()
        # The failing adapter should fail at least the yields_fetch_results check
        assert results["yields_fetch_results"] is False

    @pytest.mark.asyncio
    async def test_contract_yields_fetch_results(self):
        suite = ContractSuite(make_stub_adapter)
        assert await suite.test_yields_fetch_results()

    @pytest.mark.asyncio
    async def test_contract_run_returns_list(self):
        suite = ContractSuite(make_stub_adapter)
        assert await suite.test_run_returns_list()

    @pytest.mark.asyncio
    async def test_contract_to_raw_artifact(self):
        suite = ContractSuite(make_stub_adapter)
        assert await suite.test_to_raw_artifact()

    @pytest.mark.asyncio
    async def test_contract_policy_version_mismatch(self):
        suite = ContractSuite(make_stub_adapter)
        assert await suite.test_policy_version_mismatch()

    @pytest.mark.asyncio
    async def test_contract_disabled_source_blocked(self):
        suite = ContractSuite(make_stub_adapter)
        assert await suite.test_disabled_source_blocked()

    @pytest.mark.asyncio
    async def test_contract_hold_source_blocked(self):
        suite = ContractSuite(make_stub_adapter)
        assert await suite.test_hold_source_blocked()

    @pytest.mark.asyncio
    async def test_contract_robots_disallow_respected(self):
        suite = ContractSuite(make_stub_adapter)
        assert await suite.test_robots_disallow_respected()
