"""Tests for the DP-03-04 canonical assertion schemas and registry."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from irc_data.transform import (
    ASSERTION_SCHEMA_VERSION,
    ASSERTION_SCHEMAS,
    CertificateAssertionV1,
    RaceResultAssertionV1,
    TCCListingAssertionV1,
    UnknownAssertionSchemaError,
    get_assertion_schema,
    has_assertion_schema,
    register_assertion_schema,
    validate_assertion,
)


class TestRaceResultAssertionV1:
    def test_minimal_valid_payload(self):
        model = RaceResultAssertionV1(sail_number="GBR1234")
        assert model.sail_number == "GBR1234"

    def test_blank_sail_number_rejected(self):
        with pytest.raises(ValidationError):
            RaceResultAssertionV1(sail_number="   ")

    def test_tcc_bounds_enforced(self):
        with pytest.raises(ValidationError):
            RaceResultAssertionV1(sail_number="GBR1234", tcc="0")
        with pytest.raises(ValidationError):
            RaceResultAssertionV1(sail_number="GBR1234", tcc="3.5")
        model = RaceResultAssertionV1(sail_number="GBR1234", tcc="1.015")
        assert str(model.tcc) == "1.015"

    def test_place_must_be_positive(self):
        with pytest.raises(ValidationError):
            RaceResultAssertionV1(sail_number="GBR1234", place=0)

    def test_event_date_must_be_iso(self):
        with pytest.raises(ValidationError):
            RaceResultAssertionV1(
                sail_number="GBR1234", event_date="32/13/2026"
            )
        model = RaceResultAssertionV1(
            sail_number="GBR1234", event_date="2026-05-01"
        )
        assert model.event_date == "2026-05-01"

    def test_json_dump_serializes_decimal_as_string(self):
        model = RaceResultAssertionV1(sail_number="GBR1234", tcc="1.015")
        dumped = model.model_dump(mode="json")
        assert dumped["tcc"] == "1.015"


class TestCertificateAssertionV1:
    def test_minimal_valid_payload(self):
        model = CertificateAssertionV1(cert_number="GBR 12345")
        assert model.cert_number == "GBR 12345"

    def test_blank_cert_number_rejected(self):
        with pytest.raises(ValidationError):
            CertificateAssertionV1(cert_number=" ")

    def test_issue_date_must_be_iso(self):
        with pytest.raises(ValidationError):
            CertificateAssertionV1(
                cert_number="GBR 12345", issue_date="May 1 2026"
            )

    def test_hull_measurements_positive(self):
        with pytest.raises(ValidationError):
            CertificateAssertionV1(cert_number="GBR 12345", lh="-1")

    def test_dlr_non_negative(self):
        with pytest.raises(ValidationError):
            CertificateAssertionV1(cert_number="GBR 12345", dlr=-1)

    def test_json_dump_serializes_decimals_as_strings(self):
        model = CertificateAssertionV1(
            cert_number="GBR 12345", lh="12.34", beam="4.10"
        )
        dumped = model.model_dump(mode="json")
        assert dumped["lh"] == "12.34"
        assert dumped["beam"] == "4.10"


class TestTCCListingAssertionV1:
    def test_valid_listing(self):
        model = TCCListingAssertionV1(sail_number="GBR1234", tcc="1.015")
        assert model.is_secondary is False

    def test_tcc_required(self):
        with pytest.raises(ValidationError):
            TCCListingAssertionV1(sail_number="GBR1234")

    def test_cert_year_bounds(self):
        with pytest.raises(ValidationError):
            TCCListingAssertionV1(
                sail_number="GBR1234", tcc="1.0", cert_year=1980
            )
        with pytest.raises(ValidationError):
            TCCListingAssertionV1(
                sail_number="GBR1234", tcc="1.0", cert_year=2200
            )


class TestSchemaRegistry:
    def test_reference_schemas_registered(self):
        assert ASSERTION_SCHEMAS[("race_result", "v1")] is RaceResultAssertionV1
        assert ASSERTION_SCHEMAS[("certificate", "v1")] is CertificateAssertionV1
        assert ASSERTION_SCHEMAS[("tcc_listing", "v1")] is TCCListingAssertionV1

    def test_has_assertion_schema(self):
        assert has_assertion_schema("race_result", "v1")
        assert not has_assertion_schema("race_result", "v2")
        assert not has_assertion_schema("unknown", "v1")

    def test_get_unknown_schema_raises(self):
        with pytest.raises(UnknownAssertionSchemaError):
            get_assertion_schema("unknown", "v1")

    def test_validate_assertion_returns_normalized_payload(self):
        payload = validate_assertion(
            "race_result", {"sail_number": "  GBR1234 ", "tcc": "1.015"}
        )
        assert payload["sail_number"] == "GBR1234"
        assert payload["tcc"] == "1.015"

    def test_validate_assertion_uses_default_version(self):
        assert ASSERTION_SCHEMA_VERSION == "v1"
        payload = validate_assertion(
            "race_result", {"sail_number": "GBR1234"}
        )
        assert payload["sail_number"] == "GBR1234"

    def test_register_custom_schema(self):
        class DummyAssertionV99(BaseModel):
            widget: str

        register_assertion_schema("dummy", "v99", DummyAssertionV99)
        try:
            assert has_assertion_schema("dummy", "v99")
            payload = validate_assertion(
                "dummy", {"widget": "x"}, schema_version="v99"
            )
            assert payload["widget"] == "x"
        finally:
            del ASSERTION_SCHEMAS[("dummy", "v99")]
        assert not has_assertion_schema("dummy", "v99")

    def test_versioned_schemas_coexist(self):
        """Schema versions are addressable independently."""
        from pydantic import Field

        class RaceResultAssertionV2(BaseModel):
            sail_number: str = Field(min_length=1)
            rating: float | None = None

        register_assertion_schema("race_result", "v2", RaceResultAssertionV2)
        try:
            v1 = validate_assertion(
                "race_result", {"sail_number": "GBR1234"}, "v1"
            )
            v2 = validate_assertion(
                "race_result", {"sail_number": "GBR1234"}, "v2"
            )
            assert "tcc" in v1 and "rating" not in v1
            assert "rating" in v2 and "tcc" not in v2
        finally:
            del ASSERTION_SCHEMAS[("race_result", "v2")]
