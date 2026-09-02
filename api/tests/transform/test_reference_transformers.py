"""Tests for the DP-03-04 reference transformers.

Covers :class:`RaceResultTransformer`, :class:`CertificateTransformer`
and :class:`TCCListingTransformer` — mapping, coercion, reject
behaviour, registry lookup, and the batch-level entry point.
"""

from __future__ import annotations

import pytest

from irc_data.parsers.extraction_contract import ExtractedRecord
from irc_data.transform import (
    ASSERTION_SCHEMA_VERSION,
    RECORD_TYPE_TRANSFORMERS,
    TRANSFORMER_REGISTRY,
    CertificateTransformer,
    InputSchemaValidationError,
    RaceResultTransformer,
    RecordTransformError,
    RejectStage,
    TCCListingTransformer,
    get_transformer,
    get_transformer_for_record_type,
    transform_batch,
)
from tests.transform.conftest import make_batch, make_record


# ---------------------------------------------------------------------------
# RaceResultTransformer
# ---------------------------------------------------------------------------


class TestRaceResultTransformer:
    def test_maps_valid_records(self, race_result_batch):
        tb = RaceResultTransformer().transform(race_result_batch)
        assert tb.assertion_count() == 2
        assert tb.reject_count() == 0
        for assertion in tb.assertions:
            assert assertion.assertion_type == "race_result"
            assert assertion.transformer_name == "RaceResultTransformer"
            assert assertion.transformer_version == "1.0.0"
            assert assertion.schema_version == "v1"

    def test_data_payload_normalized(self, race_result_batch):
        tb = RaceResultTransformer().transform(race_result_batch)
        first = tb.assertions[0]
        assert first.data["sail_number"] == "GBR1234"
        assert first.data["boat_name"] == "Sunshine"
        assert first.data["place"] == 1
        assert first.data["tcc"] == "1.015"

    def test_blank_sail_number_rejected(self, mixed_race_result_batch):
        tb = RaceResultTransformer().transform(mixed_race_result_batch)
        assert tb.assertion_count() == 1
        assert tb.reject_count() == 1
        reject = tb.rejects[0]
        assert reject.stage == RejectStage.TRANSFORM.value
        assert any("sail_number" in r for r in reject.reject_reasons)
        assert reject.source_record_index == 1
        assert reject.raw_fields["boat_name"] == "Nameless"

    def test_unhandled_record_type_skipped(self, batch_identity):
        record = make_record(
            batch_identity, "certificate", 0, {"cert_number": "GBR-001"}
        )
        batch = make_batch([record])
        tb = RaceResultTransformer().transform(batch)
        assert tb.assertion_count() == 0
        assert tb.reject_count() == 0

    def test_invalid_tcc_rejected_at_output_validation(self, batch_identity):
        """A TCC outside the schema bounds fails output-schema validation."""
        record = make_record(
            batch_identity,
            "race_result",
            0,
            {"sail_number": "GBR1234", "tcc": "9.999"},
        )
        batch = make_batch([record])
        tb = RaceResultTransformer().transform(batch)
        assert tb.assertion_count() == 0
        assert tb.reject_count() == 1
        reject = tb.rejects[0]
        assert reject.stage == RejectStage.OUTPUT_SCHEMA_VALIDATION.value
        assert any("tcc" in r for r in reject.reject_reasons)

    def test_bad_event_date_rejected(self, batch_identity):
        record = make_record(
            batch_identity,
            "race_result",
            0,
            {"sail_number": "GBR1234", "event_date": "32/13/2026"},
        )
        batch = make_batch([record])
        tb = RaceResultTransformer().transform(batch)
        assert tb.assertion_count() == 0
        assert tb.reject_count() == 1
        assert any("event_date" in r for r in tb.rejects[0].reject_reasons)

    def test_event_name_alias_fallback(self, batch_identity):
        record = make_record(
            batch_identity,
            "race_result",
            0,
            {"sail_number": "GBR1234", "event": "Cowes Week"},
        )
        batch = make_batch([record])
        tb = RaceResultTransformer().transform(batch)
        assert tb.assertion_count() == 1
        assert tb.assertions[0].data["event_name"] == "Cowes Week"


# ---------------------------------------------------------------------------
# CertificateTransformer
# ---------------------------------------------------------------------------


class TestCertificateTransformer:
    def test_maps_valid_certificate(self, batch_identity):
        record = make_record(
            batch_identity,
            "certificate",
            0,
            {
                "cert_number": "GBR 12345",
                "issue_date": "2026-05-01",
                "lh": "12.34",
                "beam": "4.10",
                "dlr": "42",
            },
        )
        batch = make_batch([record])
        tb = CertificateTransformer().transform(batch)
        assert tb.assertion_count() == 1
        assert tb.reject_count() == 0
        data = tb.assertions[0].data
        assert data["cert_number"] == "GBR 12345"
        assert data["issue_date"] == "2026-05-01"
        assert data["lh"] == "12.34"
        assert data["dlr"] == 42

    def test_missing_cert_number_rejected(self, batch_identity):
        record = make_record(
            batch_identity, "certificate", 0, {"lh": "12.34"}
        )
        batch = make_batch([record])
        tb = CertificateTransformer().transform(batch)
        assert tb.assertion_count() == 0
        assert tb.reject_count() == 1
        assert any("cert_number" in r for r in tb.rejects[0].reject_reasons)

    def test_bad_decimal_rejected(self, batch_identity):
        record = make_record(
            batch_identity,
            "certificate",
            0,
            {"cert_number": "GBR 12345", "lh": "not-a-number"},
        )
        batch = make_batch([record])
        tb = CertificateTransformer().transform(batch)
        assert tb.assertion_count() == 0
        assert tb.reject_count() == 1
        assert any("lh" in r for r in tb.rejects[0].reject_reasons)


# ---------------------------------------------------------------------------
# TCCListingTransformer
# ---------------------------------------------------------------------------


class TestTCCListingTransformer:
    def test_maps_valid_listing_row(self, batch_identity):
        record = make_record(
            batch_identity,
            "tcc_listing_row",
            0,
            {
                "sail_number": "GBR1234",
                "boat_name": "Sunshine",
                "cert_number": "GBR 12345",
                "cert_year": "2026",
                "tcc": "1.015",
                "endorsed": "Endorsed",
            },
        )
        batch = make_batch([record])
        tb = TCCListingTransformer().transform(batch)
        assert tb.assertion_count() == 1
        data = tb.assertions[0].data
        assert data["sail_number"] == "GBR1234"
        assert data["tcc"] == "1.015"
        assert data["cert_year"] == 2026
        assert data["is_secondary"] is False

    def test_missing_tcc_rejected(self, batch_identity):
        record = make_record(
            batch_identity,
            "tcc_listing_row",
            0,
            {"sail_number": "GBR1234"},
        )
        batch = make_batch([record])
        tb = TCCListingTransformer().transform(batch)
        assert tb.assertion_count() == 0
        assert tb.reject_count() == 1
        assert any("tcc" in r for r in tb.rejects[0].reject_reasons)

    def test_secondary_flag_preserved(self, batch_identity):
        record = make_record(
            batch_identity,
            "tcc_listing_row",
            0,
            {"sail_number": "GBR1234", "tcc": "1.015", "is_secondary": True},
        )
        batch = make_batch([record])
        tb = TCCListingTransformer().transform(batch)
        assert tb.assertions[0].data["is_secondary"] is True


# ---------------------------------------------------------------------------
# Input schema validation — fail fast on structurally invalid batches
# ---------------------------------------------------------------------------


class TestInputSchemaValidation:
    def test_wrong_input_type_rejected(self):
        with pytest.raises(InputSchemaValidationError):
            RaceResultTransformer().transform({"not": "a batch"})

    def test_wrong_contract_version_rejected(self, race_result_batch):
        bad = type(race_result_batch).from_dict(race_result_batch.to_dict())
        bad.contract_version = "v999"
        with pytest.raises(InputSchemaValidationError):
            RaceResultTransformer().transform(bad)

    def test_wrong_schema_version_rejected(self, race_result_batch):
        bad = type(race_result_batch).from_dict(race_result_batch.to_dict())
        bad.schema_version = "v999"
        with pytest.raises(InputSchemaValidationError):
            RaceResultTransformer().transform(bad)

    def test_missing_batch_id_rejected(self, race_result_batch):
        bad = type(race_result_batch).from_dict(race_result_batch.to_dict())
        bad.batch_id = ""
        with pytest.raises(InputSchemaValidationError):
            RaceResultTransformer().transform(bad)

    def test_missing_artifact_identity_rejected(self, race_result_batch):
        bad = type(race_result_batch).from_dict(race_result_batch.to_dict())
        bad.artifact_id = ""
        with pytest.raises(InputSchemaValidationError):
            RaceResultTransformer().transform(bad)

    def test_invalid_batch_publishes_nothing(self, race_result_batch):
        """Wholesale rejection: no partial output from an invalid batch."""
        bad = type(race_result_batch).from_dict(race_result_batch.to_dict())
        bad.schema_version = "v999"
        with pytest.raises(InputSchemaValidationError):
            RaceResultTransformer().transform(bad)


# ---------------------------------------------------------------------------
# Registry + batch entry point
# ---------------------------------------------------------------------------


class TestRegistries:
    def test_registry_contains_reference_transformers(self):
        assert TRANSFORMER_REGISTRY["RaceResultTransformer"] is RaceResultTransformer
        assert TRANSFORMER_REGISTRY["CertificateTransformer"] is CertificateTransformer
        assert TRANSFORMER_REGISTRY["TCCListingTransformer"] is TCCListingTransformer

    def test_get_transformer_by_name(self):
        t = get_transformer("RaceResultTransformer")
        assert isinstance(t, RaceResultTransformer)
        assert get_transformer("DoesNotExist") is None

    def test_get_transformer_for_record_type(self):
        assert isinstance(
            get_transformer_for_record_type("race_result"), RaceResultTransformer
        )
        assert isinstance(
            get_transformer_for_record_type("certificate"), CertificateTransformer
        )
        assert isinstance(
            get_transformer_for_record_type("tcc_listing_row"),
            TCCListingTransformer,
        )
        assert get_transformer_for_record_type("mystery") is None

    def test_record_type_transformers_cover_reference_set(self):
        for record_type in ("race_result", "certificate", "tcc_listing_row"):
            assert record_type in RECORD_TYPE_TRANSFORMERS

    def test_transform_batch_auto_selects(self, race_result_batch):
        tb = transform_batch(race_result_batch)
        assert tb.transformer_name == "RaceResultTransformer"
        assert tb.assertion_count() == 2

    def test_transform_batch_unknown_record_type_publishes_nothing(
        self, batch_identity
    ):
        record = make_record(
            batch_identity, "mystery_type", 0, {"foo": "bar"}
        )
        batch = make_batch([record])
        tb = transform_batch(batch)
        assert tb.assertion_count() == 0
        assert tb.reject_count() == 0
        assert tb.transformer_name == "NoOpTransformer"

    def test_reference_transformers_share_schema_version(self):
        for cls in TRANSFORMER_REGISTRY.values():
            assert cls.schema_version == ASSERTION_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# RecordTransformError
# ---------------------------------------------------------------------------


class TestRecordTransformError:
    def test_string_reason_normalized_to_list(self):
        err = RecordTransformError("bad record")
        assert err.reasons == ["bad record"]
        assert "bad record" in str(err)

    def test_list_reasons(self):
        err = RecordTransformError(["one", "two"])
        assert err.reasons == ["one", "two"]
        assert "one" in str(err) and "two" in str(err)


# ---------------------------------------------------------------------------
# Lineage attachment
# ---------------------------------------------------------------------------


class TestLineageAttachment:
    def test_lineage_traces_to_artifact(self, race_result_batch):
        tb = RaceResultTransformer().transform(race_result_batch)
        for assertion in tb.assertions:
            lin = assertion.lineage
            assert lin.artifact_id == race_result_batch.artifact_id
            assert lin.content_hash == race_result_batch.content_hash
            assert lin.source_slug == race_result_batch.source_slug
            assert lin.extraction_batch_id == race_result_batch.batch_id
            assert lin.extraction_hash == race_result_batch.extraction_hash
            assert lin.parser_version == race_result_batch.parser_version
            assert lin.extraction_schema_version == (
                race_result_batch.schema_version
            )
            assert lin.source_record_type == "race_result"
            assert lin.source_locators, "must cite source record spans"

    def test_lineage_record_index_matches_source(self, race_result_batch):
        tb = RaceResultTransformer().transform(race_result_batch)
        indices = sorted(a.lineage.source_record_index for a in tb.assertions)
        assert indices == [0, 1]


# ---------------------------------------------------------------------------
# Determinism at the transformer level
# ---------------------------------------------------------------------------


class TestTransformerDeterminism:
    def test_repeated_runs_identical(self, mixed_race_result_batch):
        transformer = RaceResultTransformer()
        tb1 = transformer.transform(mixed_race_result_batch)
        tb2 = transformer.transform(mixed_race_result_batch)
        assert tb1 == tb2
        assert tb1.transformation_hash == tb2.transformation_hash
        assert [a.assertion_id for a in tb1.assertions] == [
            a.assertion_id for a in tb2.assertions
        ]
        assert [r.reject_id for r in tb1.rejects] == [
            r.reject_id for r in tb2.rejects
        ]

    def test_input_batch_not_mutated(self, mixed_race_result_batch):
        snapshot = mixed_race_result_batch.to_json()
        RaceResultTransformer().transform(mixed_race_result_batch)
        assert mixed_race_result_batch.to_json() == snapshot
