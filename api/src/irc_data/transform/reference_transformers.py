"""Reference transformer implementations (DP-03-04).

Reference transformers that demonstrate the :class:`Transformer`
execution contract by converting extracted records (DP-02-03
:class:`ExtractionBatchV1`) into canonical assertions.

Each transformer is a **pure function** of the extraction batch:

1. Accepts an :class:`ExtractionBatchV1` (validated on entry).
2. Maps each :class:`ExtractedRecord` to an assertion draft.
3. Output-schema validation happens in the base class — drafts that
   fail become rejects, never partial publications.

These are **reference implementations** — they prove the contract works
end-to-end and serve as templates for production transformers.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from irc_data.parsers.extraction_contract import (
    ExtractedRecord,
    ExtractionBatchV1,
)
from irc_data.transform.transformation_contract import (
    ASSERTION_SCHEMA_VERSION,
    BaseTransformer,
    RecordTransformError,
    TransformationBatchV1,
)


# ---------------------------------------------------------------------------
# Shared coercion helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> int | None:
    """Coerce a value to ``int`` or return ``None`` if absent.

    Raises :class:`ValueError` if the value is present but not coercible.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"not an integer: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"not an integer: {value!r}")
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        raise ValueError(f"not an integer: {value!r}")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return int(text)
    raise ValueError(f"not an integer: {value!r}")


def _coerce_decimal(value: Any) -> Decimal | None:
    """Coerce a value to ``Decimal`` or return ``None`` if absent.

    Raises :class:`ValueError` if the value is present but not coercible.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"not a decimal: {value!r}")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal: {value!r}") from exc
    raise ValueError(f"not a decimal: {value!r}")


def _coerce_str(value: Any) -> str | None:
    """Coerce a value to ``str`` or return ``None`` if absent/blank."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _coerce_iso_date(value: Any) -> str | None:
    """Coerce a value to an ISO-8601 date string or ``None`` if absent.

    Accepts ``date``/``datetime`` objects and ISO-8601 strings.
    Raises :class:`ValueError` for present-but-invalid values.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Validate strictly via fromisoformat (covers YYYY-MM-DD).
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise ValueError(f"not an ISO-8601 date: {value!r}") from exc
    raise ValueError(f"not an ISO-8601 date: {value!r}")


def _first_present(
    record: ExtractedRecord, *names: str
) -> Any:
    """Return the first non-``None`` value among *names* in *record*."""
    for name in names:
        value = record.get_value(name)
        if value is not None and value != "":
            return value
    return None


# ===========================================================================
# RaceResultTransformer — race_result records → race_result assertions
# ===========================================================================


class RaceResultTransformer(BaseTransformer):
    """Transform ``race_result`` extracted records into canonical
    race-result assertions.

    Handles records produced by both the HTML and JSON reference parsers.
    The minimal publishable identity is a non-empty ``sail_number`` —
    a result we cannot tie to a boat on the water is rejected, not
    published.
    """

    transformer_name = "RaceResultTransformer"
    transformer_version = "1.0.0"
    schema_version = ASSERTION_SCHEMA_VERSION

    HANDLED_RECORD_TYPES = ("race_result",)

    def map_record(
        self,
        record: ExtractedRecord,
        batch: ExtractionBatchV1,
    ) -> tuple[str, dict[str, Any]] | None:
        if record.record_type not in self.HANDLED_RECORD_TYPES:
            return None  # not handled by this transformer

        reasons: list[str] = []

        sail_number = _coerce_str(record.get_value("sail_number"))
        if not sail_number:
            reasons.append("sail_number is required and must be non-empty")

        def _try(fn, value, label):
            try:
                return fn(value)
            except ValueError as exc:
                reasons.append(f"{label}: {exc}")
                return None

        data: dict[str, Any] = {
            "sail_number": sail_number,
            "boat_name": _coerce_str(record.get_value("boat_name")),
            "event_name": _coerce_str(
                _first_present(record, "event_name", "event")
            ),
            "event_date": _try(
                _coerce_iso_date, record.get_value("event_date"), "event_date"
            ),
            "place": _try(_coerce_int, record.get_value("place"), "place"),
            "tcc": _try(_coerce_decimal, record.get_value("tcc"), "tcc"),
            "elapsed_time": _coerce_str(record.get_value("elapsed_time")),
            "corrected_time": _coerce_str(record.get_value("corrected_time")),
            "division": _coerce_str(record.get_value("division")),
        }

        if reasons:
            raise RecordTransformError(reasons)

        return ("race_result", data)


# ===========================================================================
# CertificateTransformer — certificate records → certificate assertions
# ===========================================================================


class CertificateTransformer(BaseTransformer):
    """Transform ``certificate`` extracted records into canonical
    IRC-certificate assertions.

    The minimal publishable identity is a non-empty ``cert_number``.
    """

    transformer_name = "CertificateTransformer"
    transformer_version = "1.0.0"
    schema_version = ASSERTION_SCHEMA_VERSION

    HANDLED_RECORD_TYPES = ("certificate",)

    #: Extracted field names mapped straight into the assertion payload
    #: (after decimal coercion).
    DECIMAL_FIELDS = (
        "lh", "lwp", "beam", "draft", "displacement",
        "p", "e", "j", "stl", "spl", "fl",
        "hlp", "hsa", "spa", "stix", "avs",
    )

    def map_record(
        self,
        record: ExtractedRecord,
        batch: ExtractionBatchV1,
    ) -> tuple[str, dict[str, Any]] | None:
        if record.record_type not in self.HANDLED_RECORD_TYPES:
            return None

        reasons: list[str] = []

        cert_number = _coerce_str(record.get_value("cert_number"))
        if not cert_number:
            reasons.append("cert_number is required and must be non-empty")

        def _try(fn, value, label):
            try:
                return fn(value)
            except ValueError as exc:
                reasons.append(f"{label}: {exc}")
                return None

        data: dict[str, Any] = {
            "cert_number": cert_number,
            "issue_date": _try(
                _coerce_iso_date, record.get_value("issue_date"), "issue_date"
            ),
            "source": _coerce_str(record.get_value("source")),
            "dlr": _try(_coerce_int, record.get_value("dlr"), "dlr"),
        }
        for name in self.DECIMAL_FIELDS:
            data[name] = _try(_coerce_decimal, record.get_value(name), name)

        if reasons:
            raise RecordTransformError(reasons)

        return ("certificate", data)


# ===========================================================================
# TCCListingTransformer — tcc_listing_row records → tcc_listing assertions
# ===========================================================================


class TCCListingTransformer(BaseTransformer):
    """Transform ``tcc_listing_row`` extracted records into canonical
    TCC-listing assertions."""

    transformer_name = "TCCListingTransformer"
    transformer_version = "1.0.0"
    schema_version = ASSERTION_SCHEMA_VERSION

    HANDLED_RECORD_TYPES = ("tcc_listing_row", "tcc_listing")

    def map_record(
        self,
        record: ExtractedRecord,
        batch: ExtractionBatchV1,
    ) -> tuple[str, dict[str, Any]] | None:
        if record.record_type not in self.HANDLED_RECORD_TYPES:
            return None

        reasons: list[str] = []

        sail_number = _coerce_str(record.get_value("sail_number"))
        if not sail_number:
            reasons.append("sail_number is required and must be non-empty")

        def _try(fn, value, label):
            try:
                return fn(value)
            except ValueError as exc:
                reasons.append(f"{label}: {exc}")
                return None

        tcc = _try(_coerce_decimal, record.get_value("tcc"), "tcc")
        if tcc is None and record.get_value("tcc") is None:
            reasons.append("tcc is required")

        is_secondary_raw = record.get_value("is_secondary")
        is_secondary = bool(is_secondary_raw) if is_secondary_raw is not None else False

        data: dict[str, Any] = {
            "sail_number": sail_number,
            "boat_name": _coerce_str(record.get_value("boat_name")),
            "cert_number": _coerce_str(record.get_value("cert_number")),
            "cert_year": _try(
                _coerce_int, record.get_value("cert_year"), "cert_year"
            ),
            "tcc": tcc,
            "endorsed": _coerce_str(record.get_value("endorsed")),
            "is_secondary": is_secondary,
        }

        if reasons:
            raise RecordTransformError(reasons)

        return ("tcc_listing", data)


# ===========================================================================
# Transformer registry
# ===========================================================================

#: Registry mapping transformer name → transformer class.
TRANSFORMER_REGISTRY: dict[str, type[BaseTransformer]] = {
    RaceResultTransformer.transformer_name: RaceResultTransformer,
    CertificateTransformer.transformer_name: CertificateTransformer,
    TCCListingTransformer.transformer_name: TCCListingTransformer,
}

#: Map extraction record_type → transformer class.  Lets the pipeline
#: pick the right transformer for an extraction batch automatically.
RECORD_TYPE_TRANSFORMERS: dict[str, type[BaseTransformer]] = {
    "race_result": RaceResultTransformer,
    "certificate": CertificateTransformer,
    "tcc_listing_row": TCCListingTransformer,
    "tcc_listing": TCCListingTransformer,
}


def get_transformer(name: str) -> BaseTransformer | None:
    """Return a transformer instance by name, or ``None``."""
    cls = TRANSFORMER_REGISTRY.get(name)
    return cls() if cls else None


def get_transformer_for_record_type(record_type: str) -> BaseTransformer | None:
    """Return a transformer instance for an extraction record type."""
    cls = RECORD_TYPE_TRANSFORMERS.get(record_type)
    return cls() if cls else None


def transform_batch(
    batch: ExtractionBatchV1,
    transformer: BaseTransformer | None = None,
) -> TransformationBatchV1:
    """Transform an extraction batch into canonical assertions.

    This is the main entry point for the transformation stage.  If no
    *transformer* is given, one is selected from the batch's record
    types.  If no transformer is registered for the batch's record
    types, returns an empty :class:`TransformationBatchV1` built with a
    generic identity — nothing is published for unknown record types.
    """
    if transformer is None:
        for record_type in batch.record_types():
            transformer = get_transformer_for_record_type(record_type)
            if transformer is not None:
                break

    if transformer is None:
        # No transformer for these record types — publish nothing.
        from irc_data.transform.transformation_contract import (
            TransformationBatchV1 as _TB,
        )

        return _TB(
            extraction_batch_id=batch.batch_id,
            extraction_hash=batch.extraction_hash,
            parser_version=batch.parser_version,
            extraction_schema_version=batch.schema_version,
            transformer_name="NoOpTransformer",
            transformer_version="0.0.0",
            schema_version=ASSERTION_SCHEMA_VERSION,
            source_slug=batch.source_slug,
            artifact_id=batch.artifact_id,
            content_hash=batch.content_hash,
            url=batch.url,
            assertions=[],
            rejects=[],
        )

    return transformer.transform(batch)


__all__ = [
    "RaceResultTransformer",
    "CertificateTransformer",
    "TCCListingTransformer",
    "TRANSFORMER_REGISTRY",
    "RECORD_TYPE_TRANSFORMERS",
    "get_transformer",
    "get_transformer_for_record_type",
    "transform_batch",
]
