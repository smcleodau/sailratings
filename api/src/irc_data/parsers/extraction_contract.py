"""Parser execution contract — immutable raw references (DP-02-03 / SPEC-013).

This module defines the **parser execution contract** that stops parsers
from depending on live web state.  Parsers consume **artifact IDs plus
parser/schema versions** and emit **extracted records with source spans**.

The handoff / output contract is :class:`ExtractionBatchV1`.

Design principles
-----------------

* **Parsers are pure functions of (artifact, parser_version,
  schema_version).**  No network, no filesystem, no clock.  The same
  inputs always produce the same :attr:`ExtractionBatchV1.extraction_hash`.

* **Every field cites its source.**  Each :class:`ExtractedField`
  carries a :class:`Locator` that identifies the artifact and the
  position within it where the value was found.

* **Artifacts are immutable references.**  :class:`ParserInputV1`
  wraps raw bytes with a content hash and stable artifact ID.  The
  parser never mutates the input.

* **Versioned and deterministic.**  The ``batch_id`` is derived
  deterministically from (artifact_id, parser_version, schema_version)
  so that replaying the same artifact with the same versions returns
  the same batch — no duplicate work.

Contracts
---------

* :class:`ParserInputV1` — the **input contract**: an immutable raw
  artifact reference plus parser/schema versions.

* :class:`ExtractionBatchV1` — the **output contract** (handoff): a
  batch of extracted records with source spans, produced by a parser
  from a single :class:`ParserInputV1`.

* :class:`Locator` — a source span that cites where in the artifact a
  field value came from.

* :class:`ExtractedField` — a single extracted value with its locator.

* :class:`ExtractedRecord` — a record of extracted fields.

* :class:`Parser` — the abstract base / protocol that every concrete
  parser inherits from.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _sha256_hex(data: bytes | str) -> str:
    """Return the SHA-256 hex digest of *data*."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# LocatorType — how a value is located within an artifact
# ---------------------------------------------------------------------------


class LocatorType(str, enum.Enum):
    """How a value is located within the source artifact.

    ``BYTE_OFFSET``
        A byte range within the artifact content (``start``..``end``).
    ``LINE_COL``
        A line and column range (``start`` = line, ``end`` = col, or
        encoded as ``start = line * 10000 + col``).
    ``JSON_PATH``
        A JSON Pointer / dot-path (``path`` field, e.g. ``"results[0].place"``).
    ``CSS_SELECTOR``
        A CSS selector path (``path`` field, e.g. ``"table tr:nth-child(2) td:nth-child(1)"``).
    ``XPATH``
        An XPath expression (``path`` field).
    ``PDF_PAGE``
        A page number and optionally a word/line offset (``page``,
        ``start`` = word index on that page).
    ``CSV_ROW``
        A CSV row and column (``row`` = 0-based row index, ``start``
        = column index).
    ``TABLE_CELL``
        A table cell reference (``row``, ``start`` = column).
    ``WHOLE_ARTIFACT``
        The value represents the entire artifact (no specific span).
    """

    BYTE_OFFSET = "byte_offset"
    LINE_COL = "line_col"
    JSON_PATH = "json_path"
    CSS_SELECTOR = "css_selector"
    XPATH = "xpath"
    PDF_PAGE = "pdf_page"
    CSV_ROW = "csv_row"
    TABLE_CELL = "table_cell"
    WHOLE_ARTIFACT = "whole_artifact"


# ---------------------------------------------------------------------------
# Locator — source span (where a value came from)
# ---------------------------------------------------------------------------


@dataclass
class Locator:
    """A source span that cites where in an artifact a value was found.

    Every :class:`ExtractedField` carries a ``Locator`` so that any
    extracted value can be traced back to its source artifact and the
    precise position within it.

    Attributes
    ----------
    artifact_id
        The stable ID of the source artifact (from
        :attr:`ParserInputV1.artifact_id`).
    content_hash
        The SHA-256 hash of the source artifact at the time of
        extraction.  This lets a consumer verify that the artifact
        has not changed since extraction.
    locator_type
        How the value is located (see :class:`LocatorType`).
    start
        Start position (byte offset, line number, row index, or word
        index, depending on ``locator_type``).
    end
        End position (byte offset, column, or end index).
    path
        Structured path for JSON / CSS / XPath locators.
    page
        Page number for PDF artifacts (1-based).
    row
        Row index for CSV / tabular data (0-based).
    snippet
        A short text snippet of the source at this location, for human
        review and debugging.
    """

    artifact_id: str
    content_hash: str
    locator_type: str = LocatorType.WHOLE_ARTIFACT.value
    start: int | None = None
    end: int | None = None
    path: str | None = None
    page: int | None = None
    row: int | None = None
    snippet: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Locator:
        return cls(
            artifact_id=d["artifact_id"],
            content_hash=d["content_hash"],
            locator_type=d.get("locator_type", LocatorType.WHOLE_ARTIFACT.value),
            start=d.get("start"),
            end=d.get("end"),
            path=d.get("path"),
            page=d.get("page"),
            row=d.get("row"),
            snippet=d.get("snippet"),
        )

    @classmethod
    def whole_artifact(
        cls, artifact_id: str, content_hash: str
    ) -> Locator:
        """Create a locator that references the entire artifact."""
        return cls(
            artifact_id=artifact_id,
            content_hash=content_hash,
            locator_type=LocatorType.WHOLE_ARTIFACT.value,
        )

    @classmethod
    def byte_range(
        cls,
        artifact_id: str,
        content_hash: str,
        start: int,
        end: int | None = None,
        snippet: str | None = None,
    ) -> Locator:
        """Create a byte-offset locator."""
        return cls(
            artifact_id=artifact_id,
            content_hash=content_hash,
            locator_type=LocatorType.BYTE_OFFSET.value,
            start=start,
            end=end,
            snippet=snippet,
        )

    @classmethod
    def json_path(
        cls,
        artifact_id: str,
        content_hash: str,
        path: str,
        snippet: str | None = None,
    ) -> Locator:
        """Create a JSON-path locator."""
        return cls(
            artifact_id=artifact_id,
            content_hash=content_hash,
            locator_type=LocatorType.JSON_PATH.value,
            path=path,
            snippet=snippet,
        )

    @classmethod
    def css_selector(
        cls,
        artifact_id: str,
        content_hash: str,
        path: str,
        snippet: str | None = None,
    ) -> Locator:
        """Create a CSS-selector locator."""
        return cls(
            artifact_id=artifact_id,
            content_hash=content_hash,
            locator_type=LocatorType.CSS_SELECTOR.value,
            path=path,
            snippet=snippet,
        )

    @classmethod
    def pdf_page(
        cls,
        artifact_id: str,
        content_hash: str,
        page: int,
        start: int | None = None,
        snippet: str | None = None,
    ) -> Locator:
        """Create a PDF-page locator."""
        return cls(
            artifact_id=artifact_id,
            content_hash=content_hash,
            locator_type=LocatorType.PDF_PAGE.value,
            page=page,
            start=start,
            snippet=snippet,
        )

    @classmethod
    def csv_row(
        cls,
        artifact_id: str,
        content_hash: str,
        row: int,
        start: int | None = None,
        snippet: str | None = None,
    ) -> Locator:
        """Create a CSV-row locator."""
        return cls(
            artifact_id=artifact_id,
            content_hash=content_hash,
            locator_type=LocatorType.CSV_ROW.value,
            row=row,
            start=start,
            snippet=snippet,
        )

    @classmethod
    def table_cell(
        cls,
        artifact_id: str,
        content_hash: str,
        row: int,
        start: int,
        snippet: str | None = None,
    ) -> Locator:
        """Create a table-cell locator (row, column)."""
        return cls(
            artifact_id=artifact_id,
            content_hash=content_hash,
            locator_type=LocatorType.TABLE_CELL.value,
            row=row,
            start=start,
            snippet=snippet,
        )


# ---------------------------------------------------------------------------
# ExtractedField — a single value with provenance
# ---------------------------------------------------------------------------


@dataclass
class ExtractedField:
    """A single extracted field value with its source locator.

    Attributes
    ----------
    name
        The field name (e.g. ``"sail_number"``, ``"tcc"``, ``"place"``).
    value
        The extracted value.  May be a string, number, boolean, ``None``
        (field absent), or a nested dict/list.
    locator
        Where in the source artifact this value was found.
    """

    name: str
    value: Any
    locator: Locator

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "locator": self.locator.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExtractedField:
        return cls(
            name=d["name"],
            value=d["value"],
            locator=Locator.from_dict(d["locator"]),
        )


# ---------------------------------------------------------------------------
# ExtractedRecord — a record of fields
# ---------------------------------------------------------------------------


@dataclass
class ExtractedRecord:
    """A record composed of one or more extracted fields.

    Attributes
    ----------
    record_type
        The semantic type of this record (e.g. ``"race_result"``,
        ``"certificate"``, ``"tcc_listing_row"``).
    record_index
        Position of this record within the batch (0-based).
    fields
        The extracted fields that make up this record.
    """

    record_type: str
    record_index: int = 0
    fields: list[ExtractedField] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type,
            "record_index": self.record_index,
            "fields": [f.to_dict() for f in self.fields],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExtractedRecord:
        return cls(
            record_type=d["record_type"],
            record_index=d.get("record_index", 0),
            fields=[ExtractedField.from_dict(f) for f in d.get("fields", [])],
        )

    def get_field(self, name: str) -> ExtractedField | None:
        """Return the first field with the given name, or ``None``."""
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def get_value(self, name: str, default: Any = None) -> Any:
        """Return the value of the first field with the given name."""
        f = self.get_field(name)
        return f.value if f else default

    def field_names(self) -> list[str]:
        """Return the names of all fields in this record."""
        return [f.name for f in self.fields]


# ---------------------------------------------------------------------------
# ParserInputV1 — the input contract (immutable raw reference)
# ---------------------------------------------------------------------------


@dataclass
class ParserInputV1:
    """DP-02-03 input contract — an immutable raw artifact reference.

    This is the **only** input a parser accepts.  It wraps raw artifact
    bytes with a content hash, stable artifact ID, and the parser /
    schema versions to use.  The parser does not fetch from the web,
    read from the filesystem, or depend on any live state.

    Design principles:
    * The ``artifact_id`` is stable and derived from the content hash
      so that replaying the same content always yields the same ID.
    * The ``content`` is the raw bytes — the parser owns its own
      decoding (utf-8, JSON parse, pdfplumber, etc.).
    * ``parser_version`` and ``schema_version`` are explicit inputs
      so that the same artifact parsed with different versions can
      produce different (versioned) outputs.

    Fields
    ------
    artifact_id
        Stable identifier for this artifact.  If not provided, it is
        derived from ``content_hash``.
    content
        The raw artifact bytes (immutable).
    content_hash
        SHA-256 hex digest of ``content``.
    content_type
        MIME type of the artifact (e.g. ``"text/html"``,
        ``"application/json"``, ``"application/pdf"``).
    parse_hint
        Hint for which parser to use (``"html"``, ``"json"``, ``"pdf"``,
        ``"csv"``).
    source_slug
        The ``data_sources.slug`` the artifact was collected from.
    url
        The original URL the artifact was fetched from (for audit).
    parser_version
        The version label of the parser to use (e.g. ``"1.0.0"``).
    schema_version
        The version of the output schema (e.g. ``"v1"``).
    fetched_at
        ISO-8601 timestamp of when the artifact was originally fetched
        (metadata only; not part of determinism).
    """

    content: bytes
    content_hash: str
    source_slug: str
    url: str = ""
    artifact_id: str = ""
    content_type: str = ""
    parse_hint: str = ""
    parser_version: str = "1.0.0"
    schema_version: str = SCHEMA_VERSION
    fetched_at: str = ""
    contract_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.artifact_id:
            self.artifact_id = f"art_{self.content_hash[:16]}"
        if not self.fetched_at:
            self.fetched_at = _now_iso()

    # ------------------------------------------------------------------
    # Deterministic identity
    # ------------------------------------------------------------------

    def deterministic_key(self) -> str:
        """Return a deterministic key for this input.

        The key is derived from (artifact_id, parser_version,
        schema_version) — the three values that must produce
        deterministic output.  Used by :class:`ExtractionBatchV1`
        to derive its ``batch_id``.
        """
        raw = json.dumps(
            {
                "artifact_id": self.artifact_id,
                "parser_version": self.parser_version,
                "schema_version": self.schema_version,
            },
            sort_keys=True,
        )
        return _sha256_hex(raw)[:16]

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "artifact_id": self.artifact_id,
            "content": self.content.hex(),
            "content_hash": self.content_hash,
            "content_type": self.content_type,
            "parse_hint": self.parse_hint,
            "source_slug": self.source_slug,
            "url": self.url,
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ParserInputV1:
        return cls(
            content=bytes.fromhex(d["content"]),
            content_hash=d["content_hash"],
            source_slug=d["source_slug"],
            url=d.get("url", ""),
            artifact_id=d.get("artifact_id", ""),
            content_type=d.get("content_type", ""),
            parse_hint=d.get("parse_hint", ""),
            parser_version=d.get("parser_version", "1.0.0"),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            fetched_at=d.get("fetched_at", ""),
            contract_version=d.get("contract_version", SCHEMA_VERSION),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> ParserInputV1:
        return cls.from_dict(json.loads(s))

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_bytes(
        cls,
        content: bytes,
        source_slug: str,
        url: str = "",
        content_type: str = "",
        parse_hint: str = "",
        parser_version: str = "1.0.0",
        schema_version: str = SCHEMA_VERSION,
    ) -> ParserInputV1:
        """Build a :class:`ParserInputV1` from raw bytes.

        The ``content_hash`` and ``artifact_id`` are computed
        automatically.
        """
        content_hash = _sha256_hex(content)
        return cls(
            content=content,
            content_hash=content_hash,
            source_slug=source_slug,
            url=url,
            content_type=content_type,
            parse_hint=parse_hint,
            parser_version=parser_version,
            schema_version=schema_version,
        )

    @classmethod
    def from_string(
        cls,
        text: str,
        source_slug: str,
        url: str = "",
        content_type: str = "",
        parse_hint: str = "",
        parser_version: str = "1.0.0",
        schema_version: str = SCHEMA_VERSION,
    ) -> ParserInputV1:
        """Build a :class:`ParserInputV1` from a string (UTF-8 encoded)."""
        content = text.encode("utf-8")
        return cls.from_bytes(
            content=content,
            source_slug=source_slug,
            url=url,
            content_type=content_type,
            parse_hint=parse_hint,
            parser_version=parser_version,
            schema_version=schema_version,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ParserInputV1):
            return NotImplemented
        return self.to_dict() == other.to_dict()


# ---------------------------------------------------------------------------
# ExtractionBatchV1 — the output contract (handoff)
# ---------------------------------------------------------------------------


@dataclass
class ExtractionBatchV1:
    """DP-02-03 handoff / output contract — the parser execution result.

    Produced by a :class:`Parser` from a single :class:`ParserInputV1`.
    Contains the extracted records with source spans, plus metadata
    for determinism and auditability.

    Design principles:
    * The ``batch_id`` is deterministic: derived from
      (artifact_id, parser_version, schema_version).  Replaying the
      same artifact with the same versions always returns the same
      ``batch_id``.
    * The ``extraction_hash`` is a deterministic hash of the records'
      content (excluding ``extracted_at``).  Two batches with the same
      ``extraction_hash`` are guaranteed to have identical records.
    * ``extracted_at`` is metadata only — it is NOT part of the
      deterministic hash and will differ between runs.  Comparisons
      should use ``extraction_hash``, not ``extracted_at``.

    Fields
    ------
    batch_id
        Deterministic batch identifier derived from
        (artifact_id, parser_version, schema_version).
    artifact_id
        The artifact that was parsed (from
        :attr:`ParserInputV1.artifact_id`).
    content_hash
        SHA-256 of the artifact content.
    parser_version
        Version of the parser that produced this batch.
    schema_version
        Version of the output schema.
    source_slug
        The source the artifact came from.
    url
        The original URL of the artifact.
    records
        The extracted records with source spans.
    extraction_hash
        Deterministic hash of the records content (for comparison).
    extracted_at
        ISO-8601 timestamp of extraction (metadata, not deterministic).
    """

    artifact_id: str
    content_hash: str
    parser_version: str
    schema_version: str
    source_slug: str
    url: str = ""
    records: list[ExtractedRecord] = field(default_factory=list)
    batch_id: str = ""
    extraction_hash: str = ""
    extracted_at: str = field(default_factory=_now_iso)
    contract_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.batch_id:
            self.batch_id = self._derive_batch_id()
        if not self.extraction_hash:
            self.extraction_hash = self._derive_extraction_hash()

    # ------------------------------------------------------------------
    # Deterministic derivation
    # ------------------------------------------------------------------

    def _derive_batch_id(self) -> str:
        """Derive a deterministic batch_id from (artifact, versions)."""
        raw = json.dumps(
            {
                "artifact_id": self.artifact_id,
                "parser_version": self.parser_version,
                "schema_version": self.schema_version,
            },
            sort_keys=True,
        )
        return f"batch_{_sha256_hex(raw)[:16]}"

    def _derive_extraction_hash(self) -> str:
        """Derive a deterministic hash of the records content.

        The hash covers the record types, indices, field names, and
        values — everything that makes up the extraction output.  It
        does NOT include ``extracted_at`` or ``batch_id`` (which is
        already deterministic but included for traceability).

        Two batches with the same ``extraction_hash`` are guaranteed
        to have identical records.
        """
        records_data = [r.to_dict() for r in self.records]
        raw = json.dumps(records_data, sort_keys=True, default=str)
        return _sha256_hex(raw)

    # ------------------------------------------------------------------
    # Verification helpers
    # ------------------------------------------------------------------

    def all_fields_cite_source(self) -> bool:
        """Return ``True`` if every field in every record has a locator
        that cites the artifact_id and content_hash.
        """
        for record in self.records:
            for field in record.fields:
                loc = field.locator
                if not loc.artifact_id:
                    return False
                if not loc.content_hash:
                    return False
        return True

    def record_count(self) -> int:
        """Return the number of extracted records."""
        return len(self.records)

    def field_count(self) -> int:
        """Return the total number of extracted fields across all records."""
        return sum(len(r.fields) for r in self.records)

    def record_types(self) -> list[str]:
        """Return the distinct record types in this batch."""
        seen: list[str] = []
        for r in self.records:
            if r.record_type not in seen:
                seen.append(r.record_type)
        return seen

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "batch_id": self.batch_id,
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "source_slug": self.source_slug,
            "url": self.url,
            "records": [r.to_dict() for r in self.records],
            "extraction_hash": self.extraction_hash,
            "extracted_at": self.extracted_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExtractionBatchV1:
        return cls(
            artifact_id=d["artifact_id"],
            content_hash=d["content_hash"],
            parser_version=d["parser_version"],
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            source_slug=d["source_slug"],
            url=d.get("url", ""),
            records=[ExtractedRecord.from_dict(r) for r in d.get("records", [])],
            batch_id=d.get("batch_id", ""),
            extraction_hash=d.get("extraction_hash", ""),
            extracted_at=d.get("extracted_at", _now_iso()),
            contract_version=d.get("contract_version", SCHEMA_VERSION),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @classmethod
    def from_json(cls, s: str) -> ExtractionBatchV1:
        return cls.from_dict(json.loads(s))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExtractionBatchV1):
            return NotImplemented
        # Equality is based on the deterministic content, not
        # ``extracted_at``.
        return self.extraction_hash == other.extraction_hash

    # ------------------------------------------------------------------
    # Builder
    # ------------------------------------------------------------------

    @classmethod
    def from_parser_input(
        cls,
        parser_input: ParserInputV1,
        records: list[ExtractedRecord],
    ) -> ExtractionBatchV1:
        """Build an :class:`ExtractionBatchV1` from a parser input
        and the extracted records.
        """
        return cls(
            artifact_id=parser_input.artifact_id,
            content_hash=parser_input.content_hash,
            parser_version=parser_input.parser_version,
            schema_version=parser_input.schema_version,
            source_slug=parser_input.source_slug,
            url=parser_input.url,
            records=records,
        )


# ---------------------------------------------------------------------------
# Parser — abstract base / protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Parser(Protocol):
    """The parser execution contract.

    Every concrete parser implements this protocol.  The contract is:

    1. Accept a :class:`ParserInputV1` (immutable raw reference).
    2. Return an :class:`ExtractionBatchV1` (handoff / output contract).
    3. Be deterministic: the same input always produces the same
       ``extraction_hash``.
    4. Every field in every record must cite its source artifact and
       locator (see :meth:`ExtractionBatchV1.all_fields_cite_source`).

    Usage::

        class MyParser:
            parser_version = "1.0.0"
            schema_version = "v1"

            def parse(self, input: ParserInputV1) -> ExtractionBatchV1:
                ...
                return ExtractionBatchV1.from_parser_input(input, records)
    """

    #: Version label of this parser (e.g. ``"1.0.0"``).
    parser_version: str

    #: Version of the output schema (e.g. ``"v1"``).
    schema_version: str

    def parse(self, input: ParserInputV1) -> ExtractionBatchV1:
        """Parse an artifact into an :class:`ExtractionBatchV1`.

        Args:
            input: The immutable raw artifact reference.

        Returns:
            An :class:`ExtractionBatchV1` with extracted records and
            source spans.
        """
        ...


# ---------------------------------------------------------------------------
# BaseParser — convenience base class
# ---------------------------------------------------------------------------


class BaseParser:
    """Convenience base class for concrete parsers.

    Subclasses set ``parser_version`` and ``schema_version`` as class
    attributes and implement :meth:`parse`.

    The base provides :meth:`make_locator` helper to create locators
    that reference the correct artifact_id and content_hash.
    """

    parser_version: str = "1.0.0"
    schema_version: str = SCHEMA_VERSION

    def parse(self, input: ParserInputV1) -> ExtractionBatchV1:
        """Parse an artifact.  Override in subclasses."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Locator helpers
    # ------------------------------------------------------------------

    def make_locator(
        self,
        input: ParserInputV1,
        locator_type: str = LocatorType.WHOLE_ARTIFACT.value,
        start: int | None = None,
        end: int | None = None,
        path: str | None = None,
        page: int | None = None,
        row: int | None = None,
        snippet: str | None = None,
    ) -> Locator:
        """Create a :class:`Locator` that cites the input artifact."""
        return Locator(
            artifact_id=input.artifact_id,
            content_hash=input.content_hash,
            locator_type=locator_type,
            start=start,
            end=end,
            path=path,
            page=page,
            row=row,
            snippet=snippet,
        )

    def make_field(
        self,
        input: ParserInputV1,
        name: str,
        value: Any,
        locator_type: str = LocatorType.WHOLE_ARTIFACT.value,
        **locator_kwargs: Any,
    ) -> ExtractedField:
        """Create an :class:`ExtractedField` with a locator."""
        locator = self.make_locator(
            input, locator_type=locator_type, **locator_kwargs
        )
        return ExtractedField(name=name, value=value, locator=locator)

    def finalize(
        self,
        input: ParserInputV1,
        records: list[ExtractedRecord],
    ) -> ExtractionBatchV1:
        """Build the :class:`ExtractionBatchV1` from the input and records."""
        return ExtractionBatchV1.from_parser_input(input, records)


__all__ = [
    "SCHEMA_VERSION",
    "LocatorType",
    "Locator",
    "ExtractedField",
    "ExtractedRecord",
    "ParserInputV1",
    "ExtractionBatchV1",
    "Parser",
    "BaseParser",
]
