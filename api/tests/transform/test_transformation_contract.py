"""Unit tests for the DP-03-04 transformation contract primitives.

Covers :class:`CanonicalAssertionV1`, :class:`RejectedRecordV1`,
:class:`TransformationBatchV1` and :class:`AssertionLineage` — the
handoff / output contract of the transformation stage.
"""

from __future__ import annotations

from irc_data.transform import (
    ASSERTION_CONTRACT_VERSION,
    ASSERTION_SCHEMA_VERSION,
    AssertionLineage,
    CanonicalAssertionV1,
    RaceResultTransformer,
    RejectedRecordV1,
    RejectStage,
    TransformationBatchV1,
)
from tests.transform.conftest import make_batch, make_record


# ---------------------------------------------------------------------------
# CanonicalAssertionV1
# ---------------------------------------------------------------------------


class TestCanonicalAssertionV1:
    def test_derive_assertion_id_is_deterministic(self):
        id1 = CanonicalAssertionV1.derive_assertion_id(
            extraction_batch_id="batch_abc",
            record_type="race_result",
            record_index=0,
            transformer_version="1.0.0",
            schema_version="v1",
        )
        id2 = CanonicalAssertionV1.derive_assertion_id(
            extraction_batch_id="batch_abc",
            record_type="race_result",
            record_index=0,
            transformer_version="1.0.0",
            schema_version="v1",
        )
        assert id1 == id2
        assert id1.startswith("asrt_")

    def test_assertion_id_changes_with_versions(self):
        base = CanonicalAssertionV1.derive_assertion_id(
            extraction_batch_id="batch_abc",
            record_type="race_result",
            record_index=0,
            transformer_version="1.0.0",
            schema_version="v1",
        )
        new_transformer = CanonicalAssertionV1.derive_assertion_id(
            extraction_batch_id="batch_abc",
            record_type="race_result",
            record_index=0,
            transformer_version="2.0.0",
            schema_version="v1",
        )
        new_schema = CanonicalAssertionV1.derive_assertion_id(
            extraction_batch_id="batch_abc",
            record_type="race_result",
            record_index=0,
            transformer_version="1.0.0",
            schema_version="v2",
        )
        assert base != new_transformer
        assert base != new_schema

    def test_identifies_transformer(self, batch_identity):
        lineage = AssertionLineage(
            artifact_id=batch_identity[0],
            content_hash=batch_identity[1],
            source_slug="sailsys",
            extraction_batch_id="batch_abc",
            extraction_hash="0" * 64,
            parser_version="1.0.0",
            extraction_schema_version="v1",
            source_record_type="race_result",
            source_record_index=0,
        )
        assertion = CanonicalAssertionV1(
            assertion_type="race_result",
            assertion_id="asrt_x",
            transformer_name="RaceResultTransformer",
            transformer_version="1.0.0",
            schema_version="v1",
            data={"sail_number": "GBR1234"},
            lineage=lineage,
        )
        assert assertion.identifies_transformer()

        assertion.transformer_version = ""
        assert not assertion.identifies_transformer()

    def test_round_trip(self, batch_identity):
        lineage = AssertionLineage(
            artifact_id=batch_identity[0],
            content_hash=batch_identity[1],
            source_slug="sailsys",
            extraction_batch_id="batch_abc",
            extraction_hash="0" * 64,
            parser_version="1.0.0",
            extraction_schema_version="v1",
            source_record_type="race_result",
            source_record_index=0,
            url="http://stub.test/results/1",
            source_locators=[{"path": "results[0].sail_number"}],
        )
        assertion = CanonicalAssertionV1(
            assertion_type="race_result",
            assertion_id="asrt_x",
            transformer_name="RaceResultTransformer",
            transformer_version="1.0.0",
            schema_version="v1",
            data={"sail_number": "GBR1234"},
            lineage=lineage,
        )
        clone = CanonicalAssertionV1.from_dict(assertion.to_dict())
        assert clone == assertion or clone.to_dict() == assertion.to_dict()


# ---------------------------------------------------------------------------
# TransformationBatchV1 — identity, hash, disjoint partition
# ---------------------------------------------------------------------------


class TestTransformationBatchV1:
    def test_transformation_id_deterministic(self, race_result_batch):
        transformer = RaceResultTransformer()
        tb1 = transformer.transform(race_result_batch)
        tb2 = transformer.transform(race_result_batch)
        assert tb1.transformation_id == tb2.transformation_id
        assert tb1.transformation_id.startswith("tx_")

    def test_transformation_hash_excludes_timestamp(self, race_result_batch):
        transformer = RaceResultTransformer()
        tb1 = transformer.transform(race_result_batch)
        tb2 = transformer.transform(race_result_batch)
        # Timestamps differ (metadata only) but hashes must match.
        assert tb1.transformation_hash == tb2.transformation_hash
        assert tb1 == tb2  # equality is content-based, not timestamp

    def test_disjoint_partition_helper(self, mixed_race_result_batch):
        tb = RaceResultTransformer().transform(mixed_race_result_batch)
        assert tb.asserts_disjoint_partition()
        assert tb.assertion_count() == 1
        assert tb.reject_count() == 1
        assert tb.record_count() == 2

    def test_disjoint_partition_detects_overlap(self, mixed_race_result_batch):
        tb = RaceResultTransformer().transform(mixed_race_result_batch)
        # Forge an overlapping reject that cites a published record.
        published = tb.assertions[0]
        forged = RejectedRecordV1.create(
            batch=mixed_race_result_batch,
            record=make_record(
                batch_identity=(published.lineage.artifact_id,
                                published.lineage.content_hash),
                record_type=published.lineage.source_record_type,
                record_index=published.lineage.source_record_index,
                fields={"sail_number": "GBR1234"},
            ),
            stage=RejectStage.TRANSFORM.value,
            reasons=["forged"],
            transformer_name=tb.transformer_name,
            transformer_version=tb.transformer_version,
            schema_version=tb.schema_version,
        )
        tb.rejects.append(forged)
        assert not tb.asserts_disjoint_partition()

    def test_round_trip_preserves_content(self, mixed_race_result_batch):
        tb = RaceResultTransformer().transform(mixed_race_result_batch)
        clone = TransformationBatchV1.from_dict(tb.to_dict())
        assert clone.transformation_id == tb.transformation_id
        assert clone.transformation_hash == tb.transformation_hash
        assert clone == tb
        assert len(clone.assertions) == len(tb.assertions)
        assert len(clone.rejects) == len(tb.rejects)

    def test_json_round_trip(self, race_result_batch):
        tb = RaceResultTransformer().transform(race_result_batch)
        clone = TransformationBatchV1.from_json(tb.to_json())
        assert clone.transformation_hash == tb.transformation_hash

    def test_contract_version_tagged(self, race_result_batch):
        tb = RaceResultTransformer().transform(race_result_batch)
        assert tb.contract_version == ASSERTION_CONTRACT_VERSION
        assert tb.schema_version == ASSERTION_SCHEMA_VERSION

    def test_empty_batch_produces_valid_empty_output(self, batch_identity):
        batch = make_batch([], )
        tb = RaceResultTransformer().transform(batch)
        assert tb.assertion_count() == 0
        assert tb.reject_count() == 0
        assert tb.asserts_disjoint_partition()
        assert tb.all_assertions_identify_transformer()
