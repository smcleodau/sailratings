"""DP-03-04 verification: contract suite runs sample adapters through
extraction and transformation.

These tests are the acceptance evidence for the issue:

* **Every published record identifies transformer and schema version.**
* **Rerun is deterministic** — identical ``transformation_hash`` and
  assertion IDs across runs.
* **Invalid records never partially publish** — rejects are emitted in
  a separate stream that is disjoint from the assertions.

The suite runs end-to-end: sample adapter → fetch envelopes → parser
(DP-02-03) → extraction batches → transformer → canonical assertions.
"""

from __future__ import annotations

import pytest

from irc_data.transform import (
    RaceResultTransformer,
    TransformationBatchV1,
    TransformationContractSuite,
    run_sample_pipeline_contract,
)
from irc_data.transform.contract_suite import (
    SAMPLE_MIXED_HTML,
    SAMPLE_RESULTS_HTML,
    run_pipeline,
)
from irc_data.transform.transformation_contract import InputSchemaValidationError


# ---------------------------------------------------------------------------
# Module-scope: run the shipped suite once against the default sample adapter
# ---------------------------------------------------------------------------


def test_shipped_sample_pipeline_contract_passes():
    """``run_sample_pipeline_contract`` — the verification entry point —
    passes against the default stub adapter and reference transformer."""
    results = run_sample_pipeline_contract()
    assert results, "suite returned no results"
    assert all(results.values()), (
        f"contract checks failed: "
        f"{[k for k, v in results.items() if not v]}"
    )


# ---------------------------------------------------------------------------
# Contract suite over the HTML sample adapter
# ---------------------------------------------------------------------------


class TestTransformationContractSuiteHTML:
    @pytest.fixture
    def suite(self, stub_adapter_factory) -> TransformationContractSuite:
        return TransformationContractSuite(
            adapter_factory=stub_adapter_factory,
            transformer_factory=RaceResultTransformer,
            mixed_pages={"/results/1": SAMPLE_MIXED_HTML},
        )

    def test_all_checks_pass(self, suite):
        results = suite.run_all()
        assert all(results.values()), (
            f"failed checks: {[k for k, v in results.items() if not v]}"
        )

    def test_adapter_yields_extractable_envelopes(self, suite):
        assert suite.test_adapter_yields_extractable_envelopes()

    def test_every_assertion_identifies_transformer(self, suite):
        """AC: every published record identifies transformer + schema."""
        assert suite.test_every_assertion_identifies_transformer()

    def test_every_assertion_has_lineage(self, suite):
        assert suite.test_every_assertion_has_lineage()

    def test_rerun_is_deterministic(self, suite):
        """AC: rerun is deterministic."""
        assert suite.test_rerun_is_deterministic()

    def test_invalid_records_never_partially_publish(self, suite):
        """AC: invalid records never partially publish."""
        assert suite.test_invalid_records_never_partially_publish()

    def test_invalid_input_batch_rejected_wholesale(self, suite):
        assert suite.test_invalid_input_batch_rejected_wholesale()

    def test_input_batch_not_mutated(self, suite):
        assert suite.test_input_batch_not_mutated()

    def test_rejects_carry_reasons_and_identity(self, suite):
        assert suite.test_rejects_carry_reasons_and_identity()

    def test_run_all_strict_raises_on_failure(
        self, stub_adapter_factory, monkeypatch
    ):
        """The suite reports failures rather than silently passing."""
        suite = TransformationContractSuite(
            adapter_factory=stub_adapter_factory,
            transformer_factory=RaceResultTransformer,
        )
        monkeypatch.setattr(
            suite,
            "test_rerun_is_deterministic",
            lambda: False,
        )
        with pytest.raises(AssertionError, match="rerun_is_deterministic"):
            suite.run_all_strict()


# ---------------------------------------------------------------------------
# Contract suite over a JSON sample adapter (different parser path)
# ---------------------------------------------------------------------------


class TestTransformationContractSuiteJSON:
    def test_all_checks_pass_for_json_adapter(self, json_stub_adapter_factory):
        suite = TransformationContractSuite(
            adapter_factory=json_stub_adapter_factory,
            transformer_factory=RaceResultTransformer,
            mixed_pages={
                "/results/1": (
                    b'{"results": ['
                    b'{"place": 1, "sail_number": "GBR1234", "tcc": "1.015"},'
                    b'{"place": 2, "sail_number": "", "tcc": "0.985"}'
                    b"]}"
                )
            },
        )
        results = suite.run_all()
        assert all(results.values()), (
            f"failed checks: {[k for k, v in results.items() if not v]}"
        )


# ---------------------------------------------------------------------------
# Direct pipeline helpers — acceptance criteria asserted at the batch level
# ---------------------------------------------------------------------------


class TestPipelineAcceptanceCriteria:
    def test_every_published_record_identifies_transformer(
        self, stub_adapter_factory
    ):
        envelopes = _collect(stub_adapter_factory)
        batches = run_pipeline(envelopes, transformer=RaceResultTransformer())
        assert batches, "pipeline produced no batches"
        total = 0
        for tb in batches:
            assert tb.all_assertions_identify_transformer()
            for a in tb.assertions:
                assert a.transformer_name
                assert a.transformer_version
                assert a.schema_version
                total += 1
        assert total > 0, "no assertions published from sample data"

    def test_rerun_deterministic_across_full_pipeline(
        self, stub_adapter_factory
    ):
        first = run_pipeline(
            _collect(stub_adapter_factory), transformer=RaceResultTransformer()
        )
        second = run_pipeline(
            _collect(stub_adapter_factory), transformer=RaceResultTransformer()
        )
        assert [tb.transformation_hash for tb in first] == [
            tb.transformation_hash for tb in second
        ]
        for tb1, tb2 in zip(first, second):
            assert tb1 == tb2
            assert [a.assertion_id for a in tb1.assertions] == [
                a.assertion_id for a in tb2.assertions
            ]

    def test_reject_stream_is_separate_and_disjoint(
        self, stub_adapter_factory
    ):
        adapter = stub_adapter_factory()
        adapter._pages = {"/results/1": SAMPLE_MIXED_HTML}
        envelopes = _collect(lambda: adapter)
        batches = run_pipeline(envelopes, transformer=RaceResultTransformer())
        saw_assertion = saw_reject = False
        for tb in batches:
            assert tb.asserts_disjoint_partition()
            saw_assertion |= tb.assertion_count() > 0
            saw_reject |= tb.reject_count() > 0
        assert saw_assertion and saw_reject

    def test_output_contract_round_trips(self, stub_adapter_factory):
        """TransformationBatchV1 (the handoff contract) serializes cleanly."""
        batches = run_pipeline(
            _collect(stub_adapter_factory), transformer=RaceResultTransformer()
        )
        for tb in batches:
            clone = TransformationBatchV1.from_json(tb.to_json())
            assert clone == tb
            assert clone.transformation_id == tb.transformation_id

    def test_structurally_invalid_batch_publishes_nothing(
        self, stub_adapter_factory
    ):
        from irc_data.parsers.extraction_contract import ExtractionBatchV1
        from irc_data.transform.contract_suite import run_extraction

        batch = run_extraction(_collect(stub_adapter_factory))[0]
        bad = ExtractionBatchV1.from_dict(batch.to_dict())
        bad.schema_version = "v999"
        with pytest.raises(InputSchemaValidationError):
            RaceResultTransformer().transform(bad)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect(factory):
    """Synchronously collect envelopes from a fresh adapter instance."""
    import asyncio

    async def go():
        return [e async for e in factory().collect()]

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(go())
