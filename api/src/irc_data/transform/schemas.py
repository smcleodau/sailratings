"""Canonical assertion schemas — versioned output contracts (DP-03-04).

Each assertion type has a **versioned** pydantic schema.  Transformers
validate every draft assertion against the schema registered for its
``(assertion_type, schema_version)`` pair before publishing.  Records
that fail validation are diverted to the reject stream — they never
partially publish.

Schemas are deliberately strict about required identity fields
(``sail_number`` / ``cert_number``) and tolerant about optional measured
values, matching the reality of source data quality.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_serializer, field_validator

from irc_data.transform.transformation_contract import (
    ASSERTION_SCHEMA_VERSION,
    UnknownAssertionSchemaError,
)


# ---------------------------------------------------------------------------
# RaceResultAssertionV1 — canonical race result
# ---------------------------------------------------------------------------


class RaceResultAssertionV1(BaseModel):
    """Canonical race-result assertion (schema v1).

    Derived from a ``race_result`` extracted record.  The minimal
    publishable identity is a non-empty ``sail_number``; a boat we cannot
    identify on the water is not a publishable result.
    """

    sail_number: str = Field(min_length=1)
    boat_name: str | None = None
    event_name: str | None = None
    event_date: str | None = None  # ISO-8601 date, validated below
    place: int | None = Field(default=None, ge=1)
    tcc: Decimal | None = Field(default=None, gt=0, le=Decimal("3.0"))
    elapsed_time: str | None = None
    corrected_time: str | None = None
    division: str | None = None

    @field_validator("sail_number")
    @classmethod
    def _sail_number_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("sail_number must be non-empty")
        return v

    @field_validator("event_date")
    @classmethod
    def _event_date_iso(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from datetime import date

        try:
            date.fromisoformat(v)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"event_date must be ISO-8601 (YYYY-MM-DD), got {v!r}"
            ) from exc
        return v

    @field_serializer("tcc")
    def _serialize_tcc(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None


# ---------------------------------------------------------------------------
# CertificateAssertionV1 — canonical IRC certificate
# ---------------------------------------------------------------------------


class CertificateAssertionV1(BaseModel):
    """Canonical IRC-certificate assertion (schema v1).

    Derived from a ``certificate`` extracted record.  The minimal
    publishable identity is a non-empty ``cert_number``.
    """

    cert_number: str = Field(min_length=1)
    issue_date: str | None = None
    source: str | None = None

    # Hull
    lh: Decimal | None = Field(default=None, gt=0)
    lwp: Decimal | None = Field(default=None, gt=0)
    beam: Decimal | None = Field(default=None, gt=0)
    draft: Decimal | None = Field(default=None, gt=0)
    displacement: Decimal | None = Field(default=None, gt=0)

    # Rig
    p: Decimal | None = Field(default=None, gt=0)
    e: Decimal | None = Field(default=None, gt=0)
    j: Decimal | None = Field(default=None, gt=0)
    stl: Decimal | None = Field(default=None, gt=0)
    spl: Decimal | None = Field(default=None, gt=0)
    fl: Decimal | None = Field(default=None, gt=0)

    # Sails / other
    hlp: Decimal | None = Field(default=None, gt=0)
    hsa: Decimal | None = Field(default=None, gt=0)
    spa: Decimal | None = Field(default=None, gt=0)
    stix: Decimal | None = None
    avs: Decimal | None = None
    dlr: int | None = Field(default=None, ge=0)

    @field_validator("cert_number")
    @classmethod
    def _cert_number_not_blank(cls, v: str) -> str:
        v = str(v).strip()
        if not v:
            raise ValueError("cert_number must be non-empty")
        return v

    @field_validator("issue_date")
    @classmethod
    def _issue_date_iso(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from datetime import date

        text = str(v)
        try:
            date.fromisoformat(text)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"issue_date must be ISO-8601 (YYYY-MM-DD), got {v!r}"
            ) from exc
        return text

    @field_serializer(
        "lh", "lwp", "beam", "draft", "displacement",
        "p", "e", "j", "stl", "spl", "fl",
        "hlp", "hsa", "spa", "stix", "avs",
    )
    def _serialize_decimal(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None


# ---------------------------------------------------------------------------
# TCCListingAssertionV1 — canonical TCC listing row
# ---------------------------------------------------------------------------


class TCCListingAssertionV1(BaseModel):
    """Canonical TCC-listing assertion (schema v1).

    Derived from a ``tcc_listing_row`` extracted record (the TCC CSV
    parser).  Minimal publishable identity is a non-empty
    ``sail_number`` plus a plausible ``tcc``.
    """

    sail_number: str = Field(min_length=1)
    boat_name: str | None = None
    cert_number: str | None = None
    cert_year: int | None = Field(default=None, ge=1990, le=2100)
    tcc: Decimal = Field(gt=0, le=Decimal("3.0"))
    endorsed: str | None = None
    is_secondary: bool = False

    @field_validator("sail_number")
    @classmethod
    def _sail_number_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("sail_number must be non-empty")
        return v

    @field_serializer("tcc")
    def _serialize_tcc(self, v: Decimal) -> str:
        return str(v)


# ---------------------------------------------------------------------------
# Schema registry — (assertion_type, schema_version) → model
# ---------------------------------------------------------------------------

#: Registry mapping (assertion_type, schema_version) → pydantic model.
ASSERTION_SCHEMAS: dict[tuple[str, str], type[BaseModel]] = {
    ("race_result", "v1"): RaceResultAssertionV1,
    ("certificate", "v1"): CertificateAssertionV1,
    ("tcc_listing", "v1"): TCCListingAssertionV1,
}


def register_assertion_schema(
    assertion_type: str,
    schema_version: str,
    model: type[BaseModel],
) -> None:
    """Register (or replace) the output schema for an assertion type."""
    ASSERTION_SCHEMAS[(assertion_type, schema_version)] = model


def get_assertion_schema(
    assertion_type: str,
    schema_version: str = ASSERTION_SCHEMA_VERSION,
) -> type[BaseModel]:
    """Return the registered schema, or raise
    :class:`UnknownAssertionSchemaError`."""
    model = ASSERTION_SCHEMAS.get((assertion_type, schema_version))
    if model is None:
        known = sorted(f"{t}@{v}" for t, v in ASSERTION_SCHEMAS)
        raise UnknownAssertionSchemaError(
            f"no assertion schema registered for "
            f"({assertion_type!r}, {schema_version!r}); known: {known}"
        )
    return model


def has_assertion_schema(
    assertion_type: str,
    schema_version: str = ASSERTION_SCHEMA_VERSION,
) -> bool:
    """``True`` if a schema is registered for the pair."""
    return (assertion_type, schema_version) in ASSERTION_SCHEMAS


def validate_assertion(
    assertion_type: str,
    data: dict[str, Any],
    schema_version: str = ASSERTION_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Validate *data* against the registered schema and return the
    normalized payload dict."""
    schema = get_assertion_schema(assertion_type, schema_version)
    return schema.model_validate(data).model_dump(mode="json")


__all__ = [
    "RaceResultAssertionV1",
    "CertificateAssertionV1",
    "TCCListingAssertionV1",
    "ASSERTION_SCHEMAS",
    "register_assertion_schema",
    "get_assertion_schema",
    "has_assertion_schema",
    "validate_assertion",
]
