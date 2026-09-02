"""IRC TCC Listings → canonical assertions field mapping (DP-06-03).

This module is the **mapping contract** for the DP-06 vertical-slice
source ``irc-tcc`` (IRC TCC Listings, selected by DP-06-01; adapter and
parser certified under DP-06-02).  It converts golden
:class:`~irc_data.parsers.extraction_contract.ExtractionBatchV1` records
for that source into lineage-complete canonical assertions
(:class:`~irc_data.transform.transformation_contract.CanonicalAssertionV1`)
via the DP-03-04 transformation pipeline.

The scope of DP-06-03, and where each piece lives:

* **Field mapping** — :data:`FIELD_MAPPINGS` declares, for every
  canonical ``tcc_listing`` field, the source field(s) it is mapped
  from (with per-column unit semantics), or — on the other side —
  :data:`UNSUPPORTED_SOURCE_FIELDS` records which extracted source
  fields have *no* canonical target, with explicit reasons.  Together
  they make the acceptance criterion checkable: *every* extracted
  source field is either mapped or explicitly unsupported, and *every*
  canonical field has a mapping or an explicit not-provided reason.

* **Units** — :class:`UnitSemantics` declares the unit each numeric
  source column is expressed in.  IRC TCC listing decimals are already
  canonical SI (metres / dimensionless), so no numeric conversion is
  required; the declaration is attached to the emitted assertion
  payload (``units`` block) so consumers never guess.

* **Missing semantics** — :class:`MissingSemantics` / :data:`MISSING_*`
  constants define what absent, blank and sentinel source values mean
  and how they are normalised (always to canonical ``None``).  Missing
  **required** fields divert the record to the reject stream; missing
  optional fields publish as explicit ``None``.

* **Transforms** — :class:`IRCTCCListingTransformer` (a DP-03-04
  ``BaseTransformer``) applies the mapping: secondary-cert detection
  (``" - SEC"`` / ``" (SH)"`` name suffixes and the ``Secondary``
  column), ISO-8601 date normalisation, decimal coercion and sail-number
  normalisation.  It is a pure function of the extraction batch.

* **Rejects** — :class:`RejectReason` codes; a record is rejected when a
  required canonical field cannot be produced or a present value is
  unparseable.  Rejects carry machine-readable reasons and never
  partially publish (enforced by the DP-03-04 base class).

* **Source spans** — preserved end-to-end: the parser attaches one
  ``CSV_ROW`` :class:`~irc_data.parsers.extraction_contract.Locator`
  per extracted field; the transformer copies every contributing field's
  locator onto the assertion lineage (``source_locators``), and
  :func:`irc_data.transform.lineage.trace_to_artifact` resolves an
  ``assertion_id`` all the way back to the raw artifact bytes and the
  exact CSV line a value was read from.

Determinism: mapping, units and versions are module constants — replaying
the same golden artifact always yields the same ``transformation_id``,
``assertion_id`` values and payload content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from irc_data.parsers.extraction_contract import (
    ExtractedRecord,
    ExtractionBatchV1,
)
from irc_data.transform.transformation_contract import (
    ASSERTION_SCHEMA_VERSION,
    BaseTransformer,
    RecordTransformError,
)

# ---------------------------------------------------------------------------
# Source / contract identity
# ---------------------------------------------------------------------------

#: The DP-06-01 selected source this mapping applies to
#: (``data_sources.slug``).
SOURCE_SLUG = "irc-tcc"

#: The canonical assertion type this mapping produces.
ASSERTION_TYPE = "tcc_listing"

#: Version of *this mapping* (bump when mapping/units/missing semantics
#: change).  Also the transformer version — the mapping and the code
#: that applies it are versioned together.
MAPPING_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitSemantics:
    """Unit declaration for a numeric canonical field.

    ``source_unit`` is the unit the source column is expressed in;
    ``canonical_unit`` is the unit the canonical assertion carries.
    ``conversion`` is the transform applied (``"identity"`` when the
    source already publishes canonical units — true for every IRC TCC
    listing column).
    """

    source_unit: str
    canonical_unit: str
    conversion: str = "identity"

    def to_dict(self) -> dict[str, str]:
        return {
            "source_unit": self.source_unit,
            "canonical_unit": self.canonical_unit,
            "conversion": self.conversion,
        }


#: Canonical units for the IRC TCC listing.  IRC publishes SI metric
#: values: hull dimensions in metres, TCCs dimensionless ratios, SSS in
#: points, STIX/AVS in their rule-defined scales.  No unit conversion is
#: required — every numeric column is already canonical.
CANONICAL_UNITS: dict[str, UnitSemantics] = {
    "tcc": UnitSemantics("dimensionless", "dimensionless"),
    "non_spi_tcc": UnitSemantics("dimensionless", "dimensionless"),
    "lh": UnitSemantics("m", "m"),
    "beam": UnitSemantics("m", "m"),
    "draft": UnitSemantics("m", "m"),
    "dlr": UnitSemantics("dimensionless", "dimensionless"),
    "ssb_base_value": UnitSemantics("points", "points"),
    "stix": UnitSemantics("points", "points"),
    "avs": UnitSemantics("degrees", "degrees"),
    "crew": UnitSemantics("count", "count"),
    "headsails": UnitSemantics("count", "count"),
    "flying_headsails": UnitSemantics("count", "count"),
    "spinnakers": UnitSemantics("count", "count"),
    "cert_year": UnitSemantics("year", "year"),
    "series_date": UnitSemantics("year", "year"),
    "age_date": UnitSemantics("year", "year"),
    "racing_area": UnitSemantics("code", "code"),
}


# ---------------------------------------------------------------------------
# Missing-value semantics
# ---------------------------------------------------------------------------


class MissingSemantics:
    """What absent/blank source values mean, and how they normalise.

    IRC TCC listings publish ``""`` for "not measured / not applicable /
    not published for this certificate".  We never invent values:

    * ``NOT_PUBLISHED`` — the CSV cell is empty or the column absent;
      normalised to canonical ``None`` (publishable for optional fields).
    * ``NOT_PARSEABLE`` — the cell has content but it cannot be coerced
      to the canonical type; for a **required** field this rejects the
      record, for an **optional** field it rejects the record too —
      a malformed value must not silently degrade to "not published"
      (that would destroy the distinction between "source says nothing"
      and "source said something we could not read").
    * Required-field absence (``sail_number``, ``tcc``) rejects the
      record: an assertion we cannot tie to a boat and a rating is not
      publishable.
    """

    NOT_PUBLISHED = "not_published"
    NOT_PARSEABLE = "not_parseable"
    REQUIRED_MISSING = "required_missing"


# ---------------------------------------------------------------------------
# Reject reasons (machine-readable codes)
# ---------------------------------------------------------------------------


class RejectReason:
    """Stable machine-readable reject-reason codes for this mapping."""

    MISSING_SAIL_NUMBER = "missing_required_field:sail_number"
    MISSING_TCC = "missing_required_field:tcc"
    UNPARSEABLE_TCC = "not_parseable:tcc"
    UNPARSEABLE_DATE = "not_parseable:date"
    UNPARSEABLE_DECIMAL = "not_parseable:decimal"
    UNPARSEABLE_INT = "not_parseable:int"


# ---------------------------------------------------------------------------
# Field mapping declarations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldMapping:
    """Declaration of how one canonical field is produced.

    canonical_field
        Name of the field in the canonical ``tcc_listing`` assertion.
    source_fields
        Extracted-record field names consulted, in precedence order
        (the IRC parser already collapses 2009/2026 column aliases, so
        there is normally exactly one).
    required
        Whether the canonical schema requires this field.
    transform
        Name of the transform applied (documented, and dispatched, by
        the transformer).
    missing_semantics
        How absent/blank source values are interpreted.
    reason
        Free-text rationale (audit trail for reviewers).
    """

    canonical_field: str
    source_fields: tuple[str, ...]
    required: bool
    transform: str
    missing_semantics: str = MissingSemantics.NOT_PUBLISHED
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_field": self.canonical_field,
            "source_fields": list(self.source_fields),
            "required": self.required,
            "transform": self.transform,
            "missing_semantics": self.missing_semantics,
            "reason": self.reason,
        }


#: The complete field mapping for the ``irc-tcc`` source →
#: ``tcc_listing`` canonical assertion.  This is the authoritative
#: answer to "every required canonical field has mapping or explicit
#: unsupported reason".
FIELD_MAPPINGS: tuple[FieldMapping, ...] = (
    FieldMapping(
        "sail_number", ("sail_number",), True, "normalise_sail_number",
        MissingSemantics.REQUIRED_MISSING,
        "Primary on-water identity; normalised to upper-case, "
        "whitespace-trimmed.  Without it the assertion cannot be tied "
        "to a boat → reject.",
    ),
    FieldMapping(
        "boat_name", ("boat_name",), False, "clean_boat_name",
        MissingSemantics.NOT_PUBLISHED,
        "Display name; secondary-cert suffixes (' - SEC', ' (SH)') are "
        "stripped by the parser and folded into is_secondary.",
    ),
    FieldMapping(
        "cert_number", ("cert_number",), False, "coerce_str",
        MissingSemantics.NOT_PUBLISHED,
        "IRC certificate number when published on the listing row.",
    ),
    FieldMapping(
        "cert_year", ("cert_year",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED,
        "Certificate year (2009 column 'SYSCertYear' aliases here).",
    ),
    FieldMapping(
        "tcc", ("tcc",), True, "coerce_decimal",
        MissingSemantics.REQUIRED_MISSING,
        "The rating itself — the entire point of the source.  Dimensionless.",
    ),
    FieldMapping(
        "non_spi_tcc", ("non_spi_tcc",), False, "coerce_decimal",
        MissingSemantics.NOT_PUBLISHED,
        "Non-spinnaker TCC when published.  Dimensionless.",
    ),
    FieldMapping(
        "endorsed", ("endorsed",), False, "coerce_str",
        MissingSemantics.NOT_PUBLISHED,
        "'Endorsed' flag text (2009 column 'E' aliases here).",
    ),
    FieldMapping(
        "is_secondary", ("is_secondary",), False, "coerce_bool",
        MissingSemantics.NOT_PUBLISHED,
        "Derived by the parser from the ' - SEC'/' (SH)' name suffixes "
        "and the 'Secondary' column; the mapping never invents it.",
    ),
    FieldMapping(
        "issue_date", ("issue_date",), False, "coerce_iso_date",
        MissingSemantics.NOT_PUBLISHED,
        "Certificate issue/valid date normalised to ISO-8601.",
    ),
    FieldMapping(
        "crew", ("crew",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED, "Max crew number.",
    ),
    FieldMapping(
        "dlr", ("dlr",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED,
        "Displacement-length ratio (dimensionless).",
    ),
    FieldMapping(
        "lh", ("lh",), False, "coerce_decimal",
        MissingSemantics.NOT_PUBLISHED,
        "Hull length in metres (2009 column 'LOA' aliases here).",
    ),
    FieldMapping(
        "beam", ("beam",), False, "coerce_decimal",
        MissingSemantics.NOT_PUBLISHED, "Beam in metres.",
    ),
    FieldMapping(
        "draft", ("draft",), False, "coerce_decimal",
        MissingSemantics.NOT_PUBLISHED, "Draft in metres.",
    ),
    FieldMapping(
        "single_furling_headsail", ("single_furling_headsail",), False,
        "coerce_str", MissingSemantics.NOT_PUBLISHED,
        "Single-furling-headsail allowance flag text.",
    ),
    FieldMapping(
        "headsails", ("headsails",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED, "Declared headsail count.",
    ),
    FieldMapping(
        "flying_headsails", ("flying_headsails",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED, "Declared flying-headsail count.",
    ),
    FieldMapping(
        "spinnakers", ("spinnakers",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED, "Declared spinnaker count.",
    ),
    FieldMapping(
        "series_date", ("series_date",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED, "Series date (year).",
    ),
    FieldMapping(
        "age_date", ("age_date",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED, "Age date (year).",
    ),
    FieldMapping(
        "racing_area", ("racing_area",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED, "IRC racing-area code.",
    ),
    FieldMapping(
        "ssb_base_value", ("ssb_base_value",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED, "SSS base value (points).",
    ),
    FieldMapping(
        "stix", ("stix",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED, "STIX stability index (points).",
    ),
    FieldMapping(
        "avs", ("avs",), False, "coerce_int",
        MissingSemantics.NOT_PUBLISHED, "Angle of vanishing stability (deg).",
    ),
    FieldMapping(
        "category", ("category",), False, "coerce_str",
        MissingSemantics.NOT_PUBLISHED, "Certification category text.",
    ),
    FieldMapping(
        "valid_code", ("valid_code",), False, "coerce_str",
        MissingSemantics.NOT_PUBLISHED, "IRC validity code text.",
    ),
)


@dataclass(frozen=True)
class UnsupportedField:
    """An extracted source field with **no** canonical target, and why.

    Listing these explicitly is what makes the mapping *complete*:
    nothing the parser emits is silently dropped — every field is either
    in :data:`FIELD_MAPPINGS` or here with a reason.
    """

    source_field: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"source_field": self.source_field, "reason": self.reason}


#: Extracted fields the parser may emit that this mapping deliberately
#: does not publish into the canonical ``tcc_listing`` assertion.
UNSUPPORTED_SOURCE_FIELDS: tuple[UnsupportedField, ...] = (
    UnsupportedField(
        "secondary",
        "Raw 'Secondary'/'Short Handed' column text.  Superseded at "
        "extraction time by the derived boolean 'is_secondary'; keeping "
        "the raw flag would duplicate the same fact in two shapes.",
    ),
    UnsupportedField(
        "country",
        "Country is *derived* from the sail-number prefix, not asserted "
        "by the source.  Derivation belongs to the identity/enrichment "
        "stage (DP-06-04), not to a source assertion — the listing does "
        "not state a country.",
    ),
    UnsupportedField(
        "design",
        "Design is *heuristically inferred* from hull dimensions, not "
        "published per-row.  Heuristic output is enrichment (DP-06-04), "
        "not a source assertion.",
    ),
)


#: Canonical ``tcc_listing`` schema fields (v1) with no source column,
#: with the explicit reason.  These are part of the canonical contract
#: but are not read from any source field — the acceptance criterion
#: requires the reason to be stated.
CANONICAL_FIELDS_NOT_PROVIDED: dict[str, str] = {
    "units": (
        "Not read from a source column.  Populated by the mapping itself "
        "from CANONICAL_UNITS: the per-field unit declaration for every "
        "numeric value present in the payload, so the canonical record "
        "is self-describing.  IRC TCC listings publish canonical SI "
        "units, so every conversion is 'identity'."
    ),
}


# ---------------------------------------------------------------------------
# Mapping audit helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MappingCoverageReport:
    """Result of auditing extracted fields against the mapping."""

    source_slug: str
    mapping_version: str
    mapped: dict[str, tuple[str, ...]]  # canonical_field -> source_fields
    unsupported: dict[str, str]  # source_field -> reason
    unmapped_source_fields: tuple[str, ...]  # extracted, neither mapped nor declared
    unmapped_canonical_fields: tuple[str, ...]  # canonical schema fields with no mapping

    @property
    def complete(self) -> bool:
        """True when every extracted field is mapped or declared
        unsupported, and every canonical schema field has a mapping or a
        declared not-provided reason."""
        return not self.unmapped_source_fields and not self.unmapped_canonical_fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_slug": self.source_slug,
            "mapping_version": self.mapping_version,
            "mapped": {k: list(v) for k, v in self.mapped.items()},
            "unsupported": dict(self.unsupported),
            "unmapped_source_fields": list(self.unmapped_source_fields),
            "unmapped_canonical_fields": list(self.unmapped_canonical_fields),
            "complete": self.complete,
        }


def audit_mapping_coverage(
    batch: ExtractionBatchV1,
    *,
    assertion_type: str = ASSERTION_TYPE,
    schema_version: str = ASSERTION_SCHEMA_VERSION,
) -> MappingCoverageReport:
    """Audit the mapping against an extraction batch and the schema.

    Checks both directions of the acceptance criterion:

    1. Every field name the batch actually contains is either mapped to
       a canonical field or declared unsupported (with reason).
    2. Every field of the registered canonical schema either has a
       declared mapping or a declared not-provided reason.
    """
    from irc_data.transform.schemas import get_assertion_schema

    mapped_sources: dict[str, tuple[str, ...]] = {
        m.canonical_field: m.source_fields for m in FIELD_MAPPINGS
    }
    unsupported = {u.source_field: u.reason for u in UNSUPPORTED_SOURCE_FIELDS}
    covered_sources = {s for sources in mapped_sources.values() for s in sources}

    batch_fields: set[str] = set()
    for record in batch.records:
        for f in record.fields:
            batch_fields.add(f.name)

    unmapped_source = tuple(
        sorted(
            name
            for name in batch_fields
            if name not in covered_sources and name not in unsupported
        )
    )

    schema = get_assertion_schema(assertion_type, schema_version)
    canonical_fields = set(schema.model_fields.keys())
    mapped_canonical = set(mapped_sources.keys()) | set(
        CANONICAL_FIELDS_NOT_PROVIDED.keys()
    )
    unmapped_canonical = tuple(sorted(canonical_fields - mapped_canonical))

    return MappingCoverageReport(
        source_slug=batch.source_slug,
        mapping_version=MAPPING_VERSION,
        mapped=mapped_sources,
        unsupported=unsupported,
        unmapped_source_fields=unmapped_source,
        unmapped_canonical_fields=unmapped_canonical,
    )


def field_mapping_table() -> list[dict[str, Any]]:
    """The full mapping table (mapped + unsupported + not-provided),
    suitable for docs and audit output."""
    rows: list[dict[str, Any]] = [m.to_dict() for m in FIELD_MAPPINGS]
    rows.extend(u.to_dict() for u in UNSUPPORTED_SOURCE_FIELDS)
    rows.extend(
        {
            "canonical_field": name,
            "source_fields": [],
            "required": False,
            "transform": "none",
            "missing_semantics": MissingSemantics.NOT_PUBLISHED,
            "reason": reason,
        }
        for name, reason in CANONICAL_FIELDS_NOT_PROVIDED.items()
    )
    return rows


# ---------------------------------------------------------------------------
# Transformer — applies the mapping
# ---------------------------------------------------------------------------

from decimal import Decimal, InvalidOperation  # noqa: E402
from datetime import date, datetime  # noqa: E402

_SEC_SUFFIX_RE = re.compile(r"\s*-\s*SEC\s*$", re.IGNORECASE)
_SH_SUFFIX_RE = re.compile(r"\s*\(\s*SH\s*\)\s*$", re.IGNORECASE)
_SAIL_WS_RE = re.compile(r"\s+")


def _normalise_sail_number(value: Any) -> str | None:
    """Normalise a sail number: trim, collapse internal whitespace,
    upper-case.  ``None``/blank → ``None`` (missing)."""
    if value is None:
        return None
    text = _SAIL_WS_RE.sub(" ", str(value).strip())
    return text.upper() if text else None


def _clean_boat_name(value: Any) -> str | None:
    """Clean a boat name: strip secondary-cert suffixes and trim.

    The parser normally does this already; the transformer is defensive
    so a record built by hand (or by a future parser variant) still maps
    to the canonical name.
    """
    if value is None:
        return None
    name = str(value).strip()
    name = _SEC_SUFFIX_RE.sub("", name)
    name = _SH_SUFFIX_RE.sub("", name)
    name = name.strip()
    return name or None


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_decimal(value: Any, label: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}: not a decimal: {value!r}")
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
            raise ValueError(f"{label}: not a decimal: {value!r}") from exc
    raise ValueError(f"{label}: not a decimal: {value!r}")


def _coerce_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}: not an integer: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"{label}: not an integer: {value!r}")
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        raise ValueError(f"{label}: not an integer: {value!r}")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Tolerate float-formatted ints ("196.0") as the 2009 files use them.
        try:
            return int(text)
        except ValueError:
            try:
                f = float(text)
            except ValueError as exc:
                raise ValueError(f"{label}: not an integer: {value!r}") from exc
            if f.is_integer():
                return int(f)
            raise ValueError(f"{label}: not an integer: {value!r}")
    raise ValueError(f"{label}: not an integer: {value!r}")


def _coerce_iso_date(value: Any, label: str) -> str | None:
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
        for candidate in (text, text.replace("/", "-")):
            try:
                return date.fromisoformat(candidate).isoformat()
            except ValueError:
                continue
        raise ValueError(f"{label}: not an ISO-8601 date: {value!r}")
    raise ValueError(f"{label}: not an ISO-8601 date: {value!r}")


def _coerce_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text in ("1", "true", "yes", "y", "sec", "sh")


#: Dispatch table from declared transform name → callable.  Keeping this
#: explicit makes the mapping declaration executable and auditable.
_TRANSFORMS = {
    "normalise_sail_number": _normalise_sail_number,
    "clean_boat_name": _clean_boat_name,
    "coerce_str": _coerce_str,
    "coerce_bool": _coerce_bool,
}


class IRCTCCListingTransformer(BaseTransformer):
    """Map ``irc-tcc`` extracted records to canonical ``tcc_listing``
    assertions (DP-06-03).

    Pure function of the extraction batch.  Applies
    :data:`FIELD_MAPPINGS`: required-field enforcement, per-field
    transforms, missing-value semantics.  Records that cannot produce a
    publishable identity (``sail_number``, ``tcc``) or that contain
    present-but-unparseable values are diverted to the reject stream with
    machine-readable reasons; they never partially publish.
    """

    transformer_name = "IRCTCCListingTransformer"
    transformer_version = MAPPING_VERSION
    schema_version = ASSERTION_SCHEMA_VERSION

    HANDLED_RECORD_TYPES = ("tcc_listing_row", "tcc_listing")

    def map_record(
        self,
        record: ExtractedRecord,
        batch: ExtractionBatchV1,
    ) -> tuple[str, dict[str, Any]] | None:
        if record.record_type not in self.HANDLED_RECORD_TYPES:
            return None  # not handled by this transformer

        reasons: list[str] = []
        data: dict[str, Any] = {}

        for mapping in FIELD_MAPPINGS:
            raw = self._first_present(record, mapping.source_fields)
            value = self._apply(mapping, raw, reasons)
            data[mapping.canonical_field] = value
            if mapping.required and value is None:
                reasons.append(
                    f"missing_required_field:{mapping.canonical_field}"
                )

        if reasons:
            raise RecordTransformError(reasons)

        # Attach the declared units for the numeric payload fields so the
        # canonical record is self-describing (units are part of the
        # mapping contract, and stable because they are module constants).
        data["units"] = {
            name: sem.to_dict()
            for name, sem in CANONICAL_UNITS.items()
            if data.get(name) is not None
        }
        return (ASSERTION_TYPE, data)

    # ------------------------------------------------------------------
    # Mapping application
    # ------------------------------------------------------------------

    @staticmethod
    def _first_present(record: ExtractedRecord, names: tuple[str, ...]) -> Any:
        for name in names:
            value = record.get_value(name)
            if value is not None and value != "":
                return value
        return None

    @staticmethod
    def _apply(mapping: FieldMapping, raw: Any, reasons: list[str]) -> Any:
        """Apply one field mapping; collect reject reasons on failure."""
        if raw is None:
            return None  # missing → canonical None (required check is separate)
        name = mapping.transform
        try:
            if name in _TRANSFORMS:
                return _TRANSFORMS[name](raw)
            if name == "coerce_decimal":
                return _coerce_decimal(raw, mapping.canonical_field)
            if name == "coerce_int":
                return _coerce_int(raw, mapping.canonical_field)
            if name == "coerce_iso_date":
                return _coerce_iso_date(raw, mapping.canonical_field)
            raise ValueError(f"unknown transform {name!r}")
        except ValueError as exc:
            reasons.append(str(exc))
            return None


__all__ = [
    "SOURCE_SLUG",
    "ASSERTION_TYPE",
    "MAPPING_VERSION",
    "UnitSemantics",
    "CANONICAL_UNITS",
    "MissingSemantics",
    "RejectReason",
    "FieldMapping",
    "FIELD_MAPPINGS",
    "UnsupportedField",
    "UNSUPPORTED_SOURCE_FIELDS",
    "CANONICAL_FIELDS_NOT_PROVIDED",
    "MappingCoverageReport",
    "audit_mapping_coverage",
    "field_mapping_table",
    "IRCTCCListingTransformer",
]
