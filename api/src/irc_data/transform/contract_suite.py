"""Transformation contract suite — runs sample adapters through the full
extraction → transformation pipeline (DP-03-04 verification).

The contract suite enforces that the pipeline, end to end:

1. Sample adapters yield fetch envelopes that convert to parser inputs.
2. Parsers produce extraction batches whose fields cite their source.
3. Transformers publish canonical assertions that identify their
   transformer and schema version.
4. Every assertion carries complete lineage (artifact → extraction batch
   → source record).
5. Rerunning the same input is deterministic (identical
   ``transformation_hash`` and assertion IDs).
6. Invalid records divert to the reject stream and never partially
   publish.
7. Structurally invalid input batches are rejected wholesale
   (:class:`InputSchemaValidationError`).

Usage::

    from irc_data.sources.fake_adapter import StubSourceAdapter
    from irc_data.transform.contract_suite import TransformationContractSuite

    suite = TransformationContractSuite(
        adapter_factory=lambda: StubSourceAdapter(pages={"/r": RESULTS_HTML}),
    )
    results = suite.run_all()   # or: await suite.run_all_async()
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from irc_data.parsers.extraction_contract import (
    ExtractionBatchV1,
    ParserInputV1,
)
from irc_data.parsers.reference_parsers import parse_artifact
from irc_data.sources.envelope import RawCaptureRequestV1
from irc_data.transform.transformation_contract import (
    InputSchemaValidationError,
    TransformationBatchV1,
)
from irc_data.transform.reference_transformers import (
    BaseTransformer,
    RaceResultTransformer,
    transform_batch,
)


# ---------------------------------------------------------------------------
# Sample payloads used by the suite
# ---------------------------------------------------------------------------

#: A sample results page the suite feeds through the stub adapter.
SAMPLE_RESULTS_HTML = b"""<html><body>
<table>
  <tr><th>Place</th><th>Sail No</th><th>Boat Name</th><th>TCC</th><th>Corrected</th></tr>
  <tr><td>1</td><td>GBR1234</td><td>Sunshine</td><td>1.015</td><td>01:22:10</td></tr>
  <tr><td>2</td><td>AUS5678</td><td>Wild Oats</td><td>0.985</td><td>01:23:15</td></tr>
  <tr><td>3</td><td>IRL9012</td><td>Lightning</td><td>0.972</td><td>01:24:45</td></tr>
</table>
</body></html>"""

#: A sample results page with one valid row and one invalid row (blank
#: sail number) — used to prove rejects never partially publish.
SAMPLE_MIXED_HTML = b"""<html><body>
<table>
  <tr><th>Place</th><th>Sail No</th><th>Boat Name</th><th>TCC</th></tr>
  <tr><td>1</td><td>GBR1234</td><td>Sunshine</td><td>1.015</td></tr>
  <tr><td>2</td><td></td><td>Nameless</td><td>0.985</td></tr>
</table>
</body></html>"""


# ---------------------------------------------------------------------------
# Fetch envelope → parser input bridge
# ---------------------------------------------------------------------------


def envelope_to_parser_input(envelope: RawCaptureRequestV1) -> ParserInputV1:
    """Convert a DP-01 fetch envelope into a DP-02 parser input."""
    return ParserInputV1.from_bytes(
        content=envelope.content,
        source_slug=envelope.source_slug,
        url=envelope.url,
        content_type=envelope.content_type or "",
        parse_hint=envelope.parse_hint or "",
    )


def run_extraction(
    envelopes: list[RawCaptureRequestV1],
) -> list[ExtractionBatchV1]:
    """Run the extraction stage over fetch envelopes."""
    batches: list[ExtractionBatchV1] = []
    for envelope in envelopes:
        parser_input = envelope_to_parser_input(envelope)
        batches.append(parse_artifact(parser_input))
    return batches


def run_transformation(
    batches: list[ExtractionBatchV1],
    transformer: BaseTransformer | None = None,
) -> list[TransformationBatchV1]:
    """Run the transformation stage over extraction batches."""
    return [transform_batch(b, transformer=transformer) for b in batches]


def run_pipeline(
    envelopes: list[RawCaptureRequestV1],
    transformer: BaseTransformer | None = None,
) -> list[TransformationBatchV1]:
    """Run extraction + transformation over fetch envelopes."""
    return run_transformation(run_extraction(envelopes), transformer)


# ---------------------------------------------------------------------------
# TransformationContractSuite
# ---------------------------------------------------------------------------


class TransformationContractSuite:
    """Run sample adapters through extraction and transformation.

    Args:
        adapter_factory: a callable returning a fresh adapter instance
            (e.g. ``lambda: StubSourceAdapter(pages=...)``).  The adapter
            must be re-instantiable so determinism checks get a clean
            checkpoint.
        transformer_factory: optional callable returning the transformer
            under test.  Defaults to :class:`RaceResultTransformer`.
        mixed_pages: pages for the invalid-record checks (must include at
            least one invalid row).  Defaults to ``SAMPLE_MIXED_HTML``.
    """

    def __init__(
        self,
        adapter_factory: Callable[[], Any],
        transformer_factory: Callable[[], BaseTransformer] | None = None,
        mixed_pages: dict[str, bytes] | None = None,
    ) -> None:
        self.adapter_factory = adapter_factory
        self.transformer_factory = transformer_factory or RaceResultTransformer
        self.mixed_pages = mixed_pages

    # ------------------------------------------------------------------
    # Async plumbing
    # ------------------------------------------------------------------

    def _run_async(self, coro):
        """Run a coroutine from sync code."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    async def _collect(self, adapter) -> list[RawCaptureRequestV1]:
        results: list[RawCaptureRequestV1] = []
        async for r in adapter.collect():
            results.append(r)
        return results

    def _collect_envelopes(self) -> list[RawCaptureRequestV1]:
        return self._run_async(self._collect(self.adapter_factory()))

    # ------------------------------------------------------------------
    # Contract checks (sync wrappers around the async collection)
    # ------------------------------------------------------------------

    def test_adapter_yields_extractable_envelopes(self) -> bool:
        """Sample adapter envelopes convert to parser inputs and parse."""
        envelopes = self._collect_envelopes()
        assert envelopes, "adapter yielded no envelopes"
        batches = run_extraction(envelopes)
        assert len(batches) == len(envelopes)
        for batch in batches:
            assert batch.batch_id, "extraction batch_id must be set"
            assert batch.extraction_hash, "extraction_hash must be set"
        return True

    def test_every_assertion_identifies_transformer(self) -> bool:
        """Every published record identifies transformer + schema version."""
        batches = run_pipeline(self._collect_envelopes())
        total = 0
        for tb in batches:
            assert tb.all_assertions_identify_transformer()
            for a in tb.assertions:
                assert a.transformer_name, "transformer_name must be non-empty"
                assert a.transformer_version, "transformer_version required"
                assert a.schema_version, "schema_version must be non-empty"
                total += 1
        assert total > 0, "pipeline published no assertions from sample data"
        return True

    def test_every_assertion_has_lineage(self) -> bool:
        """Every assertion cites artifact, extraction batch, and source."""
        batches = run_pipeline(self._collect_envelopes())
        for tb in batches:
            assert tb.all_assertions_have_lineage()
            for a in tb.assertions:
                assert a.lineage.source_locators, (
                    "lineage must carry the source record locators"
                )
                assert a.lineage.extraction_batch_id == tb.extraction_batch_id
        return True

    def test_rerun_is_deterministic(self) -> bool:
        """Re-running the pipeline produces identical deterministic output."""
        first = run_pipeline(self._collect_envelopes())
        second = run_pipeline(self._collect_envelopes())
        assert len(first) == len(second)
        for tb1, tb2 in zip(first, second):
            assert tb1.transformation_id == tb2.transformation_id
            assert tb1.transformation_hash == tb2.transformation_hash
            assert tb1 == tb2
            ids1 = [a.assertion_id for a in tb1.assertions]
            ids2 = [a.assertion_id for a in tb2.assertions]
            assert ids1 == ids2, "assertion IDs must be stable across reruns"
            hashes1 = [a.assertion_hash for a in tb1.assertions]
            hashes2 = [a.assertion_hash for a in tb2.assertions]
            assert hashes1 == hashes2
        return True

    def test_invalid_records_never_partially_publish(self) -> bool:
        """Records failing validation land in rejects — never assertions."""
        adapter = self.adapter_factory()
        if self.mixed_pages is not None:
            adapter._pages = dict(self.mixed_pages)
        envelopes = self._run_async(self._collect(adapter))
        assert envelopes, "adapter yielded no envelopes for mixed pages"

        batches = run_pipeline(envelopes, transformer=self.transformer_factory())
        saw_reject = False
        saw_assertion = False
        for tb in batches:
            saw_reject = saw_reject or tb.reject_count() > 0
            saw_assertion = saw_assertion or tb.assertion_count() > 0
            # Disjoint partition — no record in both streams.
            assert tb.asserts_disjoint_partition()
            # Every assertion in the batch is individually valid:
            # identified, lineage attached, and NOT present in rejects.
            reject_ids = {
                (r.source_record_type, r.source_record_index)
                for r in tb.rejects
            }
            for a in tb.assertions:
                assert a.identifies_transformer()
                key = (
                    a.lineage.source_record_type,
                    a.lineage.source_record_index,
                )
                assert key not in reject_ids
        assert saw_reject, "expected at least one reject from mixed sample"
        assert saw_assertion, "expected valid rows to still publish"
        return True

    def test_invalid_input_batch_rejected_wholesale(self) -> bool:
        """A structurally invalid extraction batch publishes nothing."""
        envelopes = self._collect_envelopes()
        batch = run_extraction(envelopes)[0]

        # Corrupt the schema version — the transformer must refuse the
        # whole batch before mapping any record.
        bad_batch = ExtractionBatchV1.from_dict(batch.to_dict())
        bad_batch.schema_version = "v999"

        transformer = self.transformer_factory()
        try:
            transformer.transform(bad_batch)
        except InputSchemaValidationError:
            return True
        return False

    def test_input_batch_not_mutated(self) -> bool:
        """The transformer is a pure function — input batch unchanged."""
        envelopes = self._collect_envelopes()
        batch = run_extraction(envelopes)[0]
        snapshot = batch.to_json()

        transformer = self.transformer_factory()
        transformer.transform(batch)

        assert batch.to_json() == snapshot, "transformer mutated its input"
        return True

    def test_rejects_carry_reasons_and_identity(self) -> bool:
        """Rejects identify the transformer, the stage, and the reasons."""
        adapter = self.adapter_factory()
        if self.mixed_pages is not None:
            adapter._pages = dict(self.mixed_pages)
        envelopes = self._run_async(self._collect(adapter))
        batches = run_pipeline(envelopes, transformer=self.transformer_factory())

        saw_reject = False
        for tb in batches:
            for r in tb.rejects:
                saw_reject = True
                assert r.reject_id, "reject_id must be set"
                assert r.reject_reasons, "reject must carry reasons"
                assert r.stage in (
                    "transform",
                    "output_schema_validation",
                )
                assert r.transformer_name == tb.transformer_name
                assert r.transformer_version == tb.transformer_version
                assert r.schema_version == tb.schema_version
        return saw_reject

    # ------------------------------------------------------------------
    # Aggregate runners
    # ------------------------------------------------------------------

    def checks(self) -> dict[str, Callable[[], bool]]:
        """Return the named contract checks."""
        return {
            "adapter_yields_extractable_envelopes": (
                self.test_adapter_yields_extractable_envelopes
            ),
            "every_assertion_identifies_transformer": (
                self.test_every_assertion_identifies_transformer
            ),
            "every_assertion_has_lineage": (
                self.test_every_assertion_has_lineage
            ),
            "rerun_is_deterministic": self.test_rerun_is_deterministic,
            "invalid_records_never_partially_publish": (
                self.test_invalid_records_never_partially_publish
            ),
            "invalid_input_batch_rejected_wholesale": (
                self.test_invalid_input_batch_rejected_wholesale
            ),
            "input_batch_not_mutated": self.test_input_batch_not_mutated,
            "rejects_carry_reasons_and_identity": (
                self.test_rejects_carry_reasons_and_identity
            ),
        }

    def run_all(self) -> dict[str, bool]:
        """Run all contract checks, returning a results dict."""
        results: dict[str, bool] = {}
        for name, check in self.checks().items():
            try:
                results[name] = bool(check())
            except Exception:
                results[name] = False
        return results

    def run_all_strict(self) -> dict[str, bool]:
        """Run all contract checks, raising on first failure."""
        results = self.run_all()
        for name, passed in results.items():
            if not passed:
                raise AssertionError(f"Contract check '{name}' failed")
        return results


def run_sample_pipeline_contract(
    adapter_factory: Callable[[], Any] | None = None,
    transformer_factory: Callable[[], BaseTransformer] | None = None,
) -> dict[str, bool]:
    """Convenience entry point: run the contract suite over the default
    sample adapter (stub serving :data:`SAMPLE_RESULTS_HTML`)."""
    if adapter_factory is None:
        from irc_data.sources.fake_adapter import StubSourceAdapter

        def adapter_factory() -> Any:  # type: ignore[no-redef]
            return StubSourceAdapter(
                pages={"/results/1": SAMPLE_RESULTS_HTML},
            )

    suite = TransformationContractSuite(
        adapter_factory=adapter_factory,
        transformer_factory=transformer_factory,
        mixed_pages={"/results/1": SAMPLE_MIXED_HTML},
    )
    return suite.run_all_strict()


__all__ = [
    "SAMPLE_RESULTS_HTML",
    "SAMPLE_MIXED_HTML",
    "envelope_to_parser_input",
    "run_extraction",
    "run_transformation",
    "run_pipeline",
    "TransformationContractSuite",
    "run_sample_pipeline_contract",
]
