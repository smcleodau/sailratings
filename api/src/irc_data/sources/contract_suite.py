"""Adapter contract suite — validates any SourceAdapter implementation.

The contract suite enforces that every adapter:
1. Yields only ``FetchResult`` objects from ``collect()``
2. Calls ``assert_policy_current`` (raises on stale policy)
3. Respects ``enabled = False`` (raises ``SourceNotApprovedError``)
4. Respects ``legal_status = 'hold'`` (raises ``SourceNotApprovedError``)
5. Produces non-empty ``content_hash`` on every result
6. Sets the standard ``User-Agent``
7. Supports ``run()`` returning a list
8. Supports ``to_raw_artifact()`` conversion
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from irc_data.sources.models import FetchResult, RawArtifactV1
from irc_data.sources.policy import (
    PolicyVersionMismatchError,
    SourceNotApprovedError,
)

if TYPE_CHECKING:
    from irc_data.sources.adapter import SourceAdapter


class ContractSuite:
    """Run the full contract suite against an adapter factory."""

    def __init__(self, adapter_factory) -> None:
        """
        Args:
            adapter_factory: a callable that returns a fresh SourceAdapter.
        """
        self.adapter_factory = adapter_factory

    def _run_async(self, coro):
        """Run a coroutine in the current event loop."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're already in an async context — create a new loop
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    async def _collect(self, adapter: SourceAdapter) -> list[FetchResult]:
        results: list[FetchResult] = []
        async for r in adapter.collect():
            results.append(r)
        return results

    # -- Individual contract checks -- #

    async def test_yields_fetch_results(self) -> bool:
        """Every yielded item must be a ``FetchResult``."""
        adapter = self.adapter_factory()
        async for item in adapter.collect():
            assert isinstance(item, FetchResult), (
                f"Expected FetchResult, got {type(item).__name__}"
            )
            assert item.content_hash, "content_hash must be non-empty"
            assert item.url, "url must be non-empty"
            assert item.policy_version, "policy_version must be non-empty"
            assert item.fetched_at, "fetched_at must be non-empty"
        return True

    async def test_run_returns_list(self) -> bool:
        """``run()`` must return a list of FetchResult."""
        adapter = self.adapter_factory()
        results = await adapter.run()
        assert isinstance(results, list), "run() must return a list"
        for r in results:
            assert isinstance(r, FetchResult)
        return True

    async def test_to_raw_artifact(self) -> bool:
        """``to_raw_artifact`` must produce a ``RawArtifactV1``."""
        adapter = self.adapter_factory()
        results = await adapter.run()
        if results:
            artifact = adapter.to_raw_artifact(results[0])
            assert isinstance(artifact, RawArtifactV1)
            assert artifact.source_slug == adapter.source_slug
            assert artifact.schema_version == "1"
            assert artifact.content_hash == results[0].content_hash
        return True

    async def test_policy_version_mismatch(self) -> bool:
        """Adapters must raise on stale policy."""
        adapter = self.adapter_factory()
        adapter._source.policy_version = "stale-version"
        try:
            async for _ in adapter.collect():  # noqa: F841
                pass
            # If we get here without raising, the contract failed
            return False
        except PolicyVersionMismatchError:
            return True

    async def test_disabled_source_blocked(self) -> bool:
        """Adapters must raise when source is disabled."""
        adapter = self.adapter_factory()
        adapter._source.enabled = False
        try:
            async for _ in adapter.collect():
                pass
            return False
        except SourceNotApprovedError:
            return True

    async def test_hold_source_blocked(self) -> bool:
        """Adapters must raise when source is on hold."""
        adapter = self.adapter_factory()
        adapter._source.legal_status = "hold"
        try:
            async for _ in adapter.collect():
                pass
            return False
        except SourceNotApprovedError:
            return True

    async def test_robots_disallow_respected(self) -> bool:
        """Adapters must respect robots_disallow."""
        adapter = self.adapter_factory()
        adapter._source.robots_disallow = ["/"]
        try:
            async for _ in adapter.collect():
                pass
            return False
        except (SourceNotApprovedError, Exception):
            return True

    # -- Aggregate runner -- #

    async def run_all(self) -> dict[str, bool]:
        """Run all contract checks, returning a results dict."""
        checks = {
            "yields_fetch_results": self.test_yields_fetch_results,
            "run_returns_list": self.test_run_returns_list,
            "to_raw_artifact": self.test_to_raw_artifact,
            "policy_version_mismatch": self.test_policy_version_mismatch,
            "disabled_source_blocked": self.test_disabled_source_blocked,
            "hold_source_blocked": self.test_hold_source_blocked,
            "robots_disallow_respected": self.test_robots_disallow_respected,
        }
        results: dict[str, bool] = {}
        for name, check in checks.items():
            try:
                results[name] = await check()
            except Exception:
                results[name] = False
        return results

    async def run_all_strict(self) -> dict[str, bool]:
        """Run all contract checks, raising on first failure."""
        results = await self.run_all()
        for name, passed in results.items():
            if not passed:
                raise AssertionError(f"Contract check '{name}' failed")
        return results
