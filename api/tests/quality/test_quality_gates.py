"""Verification tests for the DP-05-02 quality gates.

Verification criterion from the issue:

    "Fault fixtures trigger each rule class and verify isolation."

Acceptance criteria under test:

* **Partial publication cannot occur** — promotion is only possible
  from ``awaiting_promotion``; quarantined / pending batches raise
  ``PromotionError``; the promote+supersede transition is atomic.
* **Retry/replay creates a new version** — re-ingesting content for a
  ``(pipeline, source_slug)`` pair always bumps ``version``.
* **Consumers see only promoted versions** — the consumer view returns
  rows from the promoted batch only; quarantined / pending /
  awaiting-promotion / superseded versions are invisible.

Runs against in-memory SQLite (no Postgres/Alembic dependency); the
store layer uses portable SQL so behaviour is identical on Postgres in
production.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from irc_data.quality import gate_store, gates
from irc_data.quality.contracts import (
    GateKind,
    QualityBatchStatus,
    RuleClass,
)
from irc_data.quality.gate_store import PromotionError

from . import fixtures as fx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    """Fresh in-memory SQLite engine with the quality-gate tables."""
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    gate_store.init_quality_tables(eng)
    return eng


def _rule_classes(verdict) -> set[str]:
    """Extract the failed rule classes from a verdict (dict or object)."""
    failures = (
        verdict.get("failures", []) if isinstance(verdict, dict)
        else verdict.failures
    )
    out: set[str] = set()
    for f in failures:
        out.add(f["rule_class"] if isinstance(f, dict) else f.rule_class)
    return out


# ---------------------------------------------------------------------------
# 1. Every rule class fires on its fault fixture, and the batch is isolated
# ---------------------------------------------------------------------------


class TestExtractionGate:
    GATE = GateKind.EXTRACTION.value
    PIPELINE = "extraction"

    def test_clean_passes_and_awaits_promotion(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.clean_extraction_batch(),
        )
        assert result["outcome"] == "awaiting_promotion"
        assert result["verdict"]["rules_evaluated"] == 5
        assert result["verdict"]["failures"] == []
        batch = gate_store.get_batch(engine, result["batch"]["batch_key"])
        assert batch["status"] == QualityBatchStatus.AWAITING_PROMOTION.value

    def test_schema_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.extraction_bad_schema_envelope(),
        )
        assert result["outcome"] == "quarantined"
        assert _rule_classes(result["verdict"]) == {RuleClass.SCHEMA.value}

    def test_provenance_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.extraction_broken_provenance(),
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.PROVENANCE.value in _rule_classes(result["verdict"])

    def test_determinism_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.extraction_broken_determinism(),
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.DETERMINISM.value in _rule_classes(result["verdict"])

    def test_completeness_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.extraction_empty_records(),
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.COMPLETENESS.value in _rule_classes(result["verdict"])

    def test_value_domain_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.extraction_bad_indices(),
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.VALUE_DOMAIN.value in _rule_classes(result["verdict"])


class TestCanonicalGate:
    GATE = GateKind.CANONICAL.value
    PIPELINE = "canonical"

    def test_clean_passes_and_awaits_promotion(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.clean_canonical_batch(),
        )
        assert result["outcome"] == "awaiting_promotion"
        assert result["verdict"]["rules_evaluated"] == 5
        assert result["verdict"]["failures"] == []

    def test_schema_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.canonical_bad_envelope(),
        )
        assert result["outcome"] == "quarantined"
        assert _rule_classes(result["verdict"]) == {RuleClass.SCHEMA.value}

    def test_determinism_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.canonical_broken_determinism(),
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.DETERMINISM.value in _rule_classes(result["verdict"])

    def test_partition_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.canonical_partition_overlap(),
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.COMPLETENESS.value in _rule_classes(result["verdict"])

    def test_lineage_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.canonical_broken_lineage(),
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.PROVENANCE.value in _rule_classes(result["verdict"])

    def test_output_schema_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.canonical_output_schema_violation(),
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.VALUE_DOMAIN.value in _rule_classes(result["verdict"])


class TestIdentityGate:
    GATE = GateKind.IDENTITY.value
    PIPELINE = "identity"

    def test_clean_passes_and_awaits_promotion(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.clean_identity_batch(),
        )
        assert result["outcome"] == "awaiting_promotion"
        assert result["verdict"]["failures"] == []

    def test_shape_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.identity_malformed(),
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.IDENTITY_EFFECT.value in _rule_classes(result["verdict"])

    def test_self_merge_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.identity_self_merge(),
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.IDENTITY_EFFECT.value in _rule_classes(result["verdict"])

    def test_duplicate_effects_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.identity_duplicates(),
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.IDENTITY_EFFECT.value in _rule_classes(result["verdict"])

    def test_churn_rule(self, engine):
        result = gates.ingest_validate_and_optionally_promote(
            engine, pipeline=self.PIPELINE, source_slug=fx.SOURCE_SLUG,
            gate=self.GATE, payload=fx.identity_churn(threshold=3),
            context={"max_effects": 3},
        )
        assert result["outcome"] == "quarantined"
        assert RuleClass.VALUE_DOMAIN.value in _rule_classes(result["verdict"])


# ---------------------------------------------------------------------------
# 2. Quarantine carries samples + rule failures
# ---------------------------------------------------------------------------


def test_quarantine_record_has_failures_and_samples(engine):
    result = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value,
        payload=fx.extraction_broken_provenance(),
    )
    q = result["quarantine"]
    assert q is not None
    # Rule failures attached with samples.
    assert q["failures"], "quarantine must carry rule failures"
    failure = q["failures"][0]
    assert failure["rule_class"] == RuleClass.PROVENANCE.value
    assert failure["sample"], "finding must carry a sample of offenders"
    # The sample names the offending field (record_type[index].name).
    assert "race_result[0].place" in failure["sample"][0]
    # Sample rows attached.
    assert q["sample_rows"], "quarantine must carry staged sample rows"
    # Deterministic quarantine id.
    assert q["quarantine_id"]
    assert q["status"] == "open"


def test_quarantine_queue_lists_open_records(engine):
    gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value,
        payload=fx.extraction_broken_provenance(),
    )
    queue = gate_store.list_quarantine(engine, status="open")
    assert len(queue) == 1
    assert queue[0]["pipeline"] == "extraction"
    assert "provenance" in queue[0]["rule_classes"]


# ---------------------------------------------------------------------------
# 3. Partial publication cannot occur
# ---------------------------------------------------------------------------


def test_quarantined_batch_cannot_be_promoted(engine):
    result = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value,
        payload=fx.extraction_broken_provenance(),
    )
    with pytest.raises(PromotionError):
        gates.promote_batch(engine, result["batch"]["batch_key"],
                            promoted_by="tester")
    # Status is still quarantined.
    batch = gate_store.get_batch(engine, result["batch"]["batch_key"])
    assert batch["status"] == QualityBatchStatus.QUARANTINED.value
    # And nothing is visible to consumers.
    assert gates.get_consumer_view(engine, "extraction", fx.SOURCE_SLUG) == []


def test_pending_batch_cannot_be_promoted(engine):
    batch = gates.ingest_batch_version(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value, payload=fx.clean_extraction_batch(),
    )
    # Not yet validated → pending.  Promotion must fail.
    with pytest.raises(PromotionError):
        gates.promote_batch(engine, batch["batch_key"], promoted_by="tester")


def test_promotion_is_atomic_supersede(engine):
    """v1 promoted; v2 promoted → v1 superseded in the same transaction,
    only v2 visible.  No state where both (or neither) are visible."""
    r1 = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="canonical", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.CANONICAL.value, payload=fx.clean_canonical_batch(),
    )
    gates.promote_batch(engine, r1["batch"]["batch_key"], promoted_by="alice")

    # A second, equally-valid version.
    r2 = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="canonical", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.CANONICAL.value, payload=fx.clean_canonical_batch(),
    )
    assert r2["batch"]["version"] == 2
    receipt = gates.promote_batch(
        engine, r2["batch"]["batch_key"], promoted_by="bob"
    )

    assert receipt.superseded_batch_key == r1["batch"]["batch_key"]
    assert receipt.superseded_version == 1

    b1 = gate_store.get_batch(engine, r1["batch"]["batch_key"])
    b2 = gate_store.get_batch(engine, r2["batch"]["batch_key"])
    assert b1["status"] == QualityBatchStatus.SUPERSEDED.value
    assert b2["status"] == QualityBatchStatus.PROMOTED.value


def test_promotion_is_idempotent(engine):
    r = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value, payload=fx.clean_extraction_batch(),
    )
    receipt1 = gates.promote_batch(
        engine, r["batch"]["batch_key"], promoted_by="alice"
    )
    receipt2 = gates.promote_batch(
        engine, r["batch"]["batch_key"], promoted_by="alice"
    )
    assert receipt1.receipt_id == receipt2.receipt_id


# ---------------------------------------------------------------------------
# 4. Retry/replay creates a new version
# ---------------------------------------------------------------------------


def test_retry_creates_new_version(engine):
    """A retry of quarantined content lands in version N+1; the
    quarantined version is retained and stays quarantined."""
    r1 = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value,
        payload=fx.extraction_broken_provenance(),
    )
    assert r1["batch"]["version"] == 1
    assert r1["outcome"] == "quarantined"

    # Retry the SAME faulty content → new version, also quarantined.
    r2 = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value,
        payload=fx.extraction_broken_provenance(),
    )
    assert r2["batch"]["version"] == 2
    assert r2["batch"]["batch_key"] != r1["batch"]["batch_key"]
    assert r2["outcome"] == "quarantined"

    # The v1 quarantine record is untouched.
    b1 = gate_store.get_batch(engine, r1["batch"]["batch_key"])
    assert b1["status"] == QualityBatchStatus.QUARANTINED.value

    # Retry with the FIXED content → version 3, passes, promotable.
    r3 = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value, payload=fx.clean_extraction_batch(),
    )
    assert r3["batch"]["version"] == 3
    assert r3["outcome"] == "awaiting_promotion"


# ---------------------------------------------------------------------------
# 5. Consumers see only promoted versions
# ---------------------------------------------------------------------------


def test_consumer_view_empty_until_promoted(engine):
    """Ingest + validate a clean batch but DON'T promote — consumers
    must see nothing."""
    gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value, payload=fx.clean_extraction_batch(),
    )
    view = gates.get_consumer_view(engine, "extraction", fx.SOURCE_SLUG)
    assert view == [], "awaiting_promotion rows must not leak to consumers"


def test_consumer_view_shows_promoted_rows_only(engine):
    """After promotion, consumers see the promoted batch's rows — and
    nothing from the quarantined retry."""
    # v1: quarantined fault.
    gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value,
        payload=fx.extraction_broken_provenance(),
    )
    # v2: clean, promoted.
    r2 = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value, payload=fx.clean_extraction_batch(),
    )
    gates.promote_batch(engine, r2["batch"]["batch_key"], promoted_by="alice")

    view = gates.get_consumer_view(engine, "extraction", fx.SOURCE_SLUG)
    assert len(view) == 3  # the 3 clean records
    kinds = {r["row_kind"] for r in view}
    assert kinds == {"record"}
    # None of the rows carry the faulty locator's artifact id.
    for row in view:
        for field in row["row_json"]["fields"]:
            assert field["locator"]["artifact_id"] == fx.ARTIFACT_ID


def test_consumer_view_tracks_latest_promotion(engine):
    """After a superseding promotion, consumers see only the newest
    promoted version's rows."""
    r1 = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="canonical", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.CANONICAL.value, payload=fx.clean_canonical_batch(),
    )
    gates.promote_batch(engine, r1["batch"]["batch_key"], promoted_by="alice")
    view_v1 = gates.get_consumer_view(engine, "canonical", fx.SOURCE_SLUG)
    assert len(view_v1) == 3  # 2 assertions + 1 reject

    r2 = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="canonical", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.CANONICAL.value, payload=fx.clean_canonical_batch(),
    )
    gates.promote_batch(engine, r2["batch"]["batch_key"], promoted_by="bob")
    view_v2 = gates.get_consumer_view(engine, "canonical", fx.SOURCE_SLUG)
    assert len(view_v2) == 3
    # Same content here, but the batch_key proves we're reading v2.
    assert gate_store.get_promoted_batch(
        engine, "canonical", fx.SOURCE_SLUG
    )["batch_key"] == r2["batch"]["batch_key"]


def test_consumer_view_row_kind_filter(engine):
    gates.ingest_validate_and_optionally_promote(
        engine, pipeline="canonical", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.CANONICAL.value, payload=fx.clean_canonical_batch(),
    )
    batch = gate_store.list_batches(engine, pipeline="canonical")[0]
    gates.promote_batch(engine, batch["batch_key"], promoted_by="alice")

    assertions = gates.get_consumer_view(
        engine, "canonical", fx.SOURCE_SLUG, row_kind="assertion"
    )
    rejects = gates.get_consumer_view(
        engine, "canonical", fx.SOURCE_SLUG, row_kind="reject"
    )
    assert len(assertions) == 2
    assert len(rejects) == 1
    assert rejects[0]["row_json"]["reject_reasons"]


# ---------------------------------------------------------------------------
# 6. Full pipeline: fault → quarantine → fix → new version → promote
# ---------------------------------------------------------------------------


def test_fault_fix_promote_end_to_end(engine):
    """The acceptance-criteria story, end to end.

    1. A bad canonical batch is ingested → quarantined with failures.
    2. Consumers see nothing.
    3. The content is fixed and re-ingested → new version, passes.
    4. Explicit promotion makes exactly the fixed rows visible.
    """
    # 1. Faulty content → quarantine.
    bad = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="canonical", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.CANONICAL.value,
        payload=fx.canonical_output_schema_violation(),
    )
    assert bad["outcome"] == "quarantined"
    assert bad["quarantine"]["failures"]

    # 2. Nothing visible.
    assert gates.get_consumer_view(engine, "canonical", fx.SOURCE_SLUG) == []
    with pytest.raises(PromotionError):
        gates.promote_batch(engine, bad["batch"]["batch_key"])

    # 3. Fixed content → new version, passes validation.
    good = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="canonical", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.CANONICAL.value, payload=fx.clean_canonical_batch(),
    )
    assert good["batch"]["version"] == 2
    assert good["outcome"] == "awaiting_promotion"

    # Still nothing visible (awaiting explicit promotion).
    assert gates.get_consumer_view(engine, "canonical", fx.SOURCE_SLUG) == []

    # 4. Explicit promotion.
    receipt = gates.promote_batch(
        engine, good["batch"]["batch_key"], promoted_by="reviewer@example"
    )
    assert receipt.version == 2
    view = gates.get_consumer_view(engine, "canonical", fx.SOURCE_SLUG)
    assert len(view) == 3
    # The quarantined version stays quarantined and invisible.
    b1 = gate_store.get_batch(engine, bad["batch"]["batch_key"])
    assert b1["status"] == QualityBatchStatus.QUARANTINED.value


# ---------------------------------------------------------------------------
# 7. Contract round-trips
# ---------------------------------------------------------------------------


def test_quarantine_record_json_round_trip(engine):
    result = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="identity", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.IDENTITY.value, payload=fx.identity_self_merge(),
    )
    from irc_data.quality.contracts import QuarantineRecordV1

    q = gate_store.get_quarantine(engine, result["batch"]["batch_key"])
    assert q is not None
    rt = QuarantineRecordV1.from_json(q.to_json())
    assert rt.quarantine_id == q.quarantine_id
    assert rt.rule_classes() == q.rule_classes()
    assert rt.failures[0].rule_class == RuleClass.IDENTITY_EFFECT.value


def test_promotion_receipt_json_round_trip(engine):
    from irc_data.quality.contracts import PromotionReceiptV1

    r = gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value, payload=fx.clean_extraction_batch(),
    )
    receipt = gates.promote_batch(
        engine, r["batch"]["batch_key"], promoted_by="alice"
    )
    rt = PromotionReceiptV1.from_json(receipt.to_json())
    assert rt.receipt_id == receipt.receipt_id
    assert rt.batch_key == receipt.batch_key
    assert rt.version == 1
