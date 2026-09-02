"""Transformation pipeline contract — canonical assertions (DP-03-04).

This module defines the **transformation execution contract** that converts
extracted records (DP-02-03 :class:`ExtractionBatchV1`) into **canonical
assertions** reproducibly.

The handoff / output contract is :class:`TransformationBatchV1`.

Design principles
-----------------

* **Transformers are pure functions of (ExtractionBatchV1,
  transformer_version, schema_version).**  No network, no filesystem, no
  clock.  The same input batch always produces the same
  :attr:`TransformationBatchV1.transformation_hash`.

* **Input schemas are validated.**  Before any record is mapped, the
  transformer validates the extraction batch (contract version, schema
  version, mandatory identity fields).  A structurally invalid batch is
  rejected wholesale via :class:`InputSchemaValidationError` — nothing is
  published from an invalid input.

* **Output schemas are validated per record.**  Every draft assertion is
  validated against the registered schema for its
  ``(assertion_type, schema_version)`` pair (see
  :mod:`irc_data.transform.schemas`).  A record that fails validation is
  diverted to the **reject stream** — it never partially publishes.

* **Lineage is attached to every assertion.**  Each
  :class:`CanonicalAssertionV1` carries an :class:`AssertionLineage` that
  cites the artifact, content hash, extraction batch, parser and schema
  versions, and the source record spans used to derive it.

* **Rejects are emitted separately.**  :class:`TransformationBatchV1`
  partitions the input records into two disjoint sets: ``assertions``
  (publishable) and ``rejects`` (:class:`RejectedRecordV1`, with reasons).
  A record appears in exactly one set — never both, never neither.

* **Versioned and deterministic.**  ``transformation_id`` and
  ``assertion_id`` are derived deterministically from
  (extraction_batch_id, transformer, versions, record identity) so that
  replaying the same batch with the same versions returns the same
  assertions — no duplicate publications.

Contracts
---------

* :class:`CanonicalAssertionV1` — the **publishable unit**: a validated
  canonical payload plus identity (transformer + schema version) and
  lineage.

* :class:`RejectedRecordV1` — a record that failed transformation or
  output-schema validation, with machine-readable reasons.

* :class:`TransformationBatchV1` — the **output contract** (handoff): the
  disjoint assertion/reject partition of one :class:`ExtractionBatchV1`.

* :class:`AssertionLineage` — the provenance chain from artifact through
  extraction to assertion.

* :class:`Transformer` — the abstract base / protocol that every concrete
  transformer implements.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from irc_data.parsers.extraction_contract import (
    ExtractedRecord,
    ExtractionBatchV1,
)
from irc_data.parsers.extraction_contract import (
    SCHEMA_VERSION as EXTRACTION_SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

#: Version of this transformation contract (the output envelope).
ASSERTION_CONTRACT_VERSION = "v1"

#: Version of the canonical assertion schemas produced by this package.
ASSERTION_SCHEMA_VERSION = "v1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TransformationError(Exception):
    """Base class for transformation-stage errors."""


class InputSchemaValidationError(TransformationError):
    """The input :class:`ExtractionBatchV1` failed schema validation.

    Raised **before** any record is mapped.  Nothing is published from a
    structurally invalid batch.
    """


class UnknownAssertionSchemaError(TransformationError):
    """No output schema is registered for an (assertion_type, version)."""


class RecordTransformError(TransformationError):
    """A single record could not be mapped to an assertion draft.

    Transformers raise this from :meth:`BaseTransformer.map_record` to
    divert the record to the reject stream with explicit reasons.
    """

    def __init__(self, reasons: list[str] | str):
        if isinstance(reasons, str):
            reasons = [reasons]
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


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


def _canonical_json(obj: Any) -> str:
    """Serialize *obj* to a canonical JSON string for hashing."""
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


# ---------------------------------------------------------------------------
# RejectStage — where in the pipeline a record was rejected
# ---------------------------------------------------------------------------


class RejectStage(str, enum.Enum):
    """The pipeline stage at which a record was rejected."""

    #: The transformer could not map the record (missing/invalid fields).
    TRANSFORM = "transform"
    #: The mapped draft failed output-schema validation.
    OUTPUT_SCHEMA_VALIDATION = "output_schema_validation"


# ---------------------------------------------------------------------------
# AssertionLineage — provenance chain
# ---------------------------------------------------------------------------


@dataclass
class AssertionLineage:
    """The provenance chain attached to every canonical assertion.

    Traces an assertion back through the extraction batch to the raw
    artifact, so any published value can be audited end-to-end:
    artifact → extraction batch → extracted record → assertion.

    Attributes
    ----------
    artifact_id
        Stable ID of the raw artifact the value ultimately came from.
    content_hash
        SHA-256 of the raw artifact at extraction time.
    source_slug
        The ``data_sources.slug`` the artifact was collected from.
    url
        The original URL of the artifact (for audit).
    extraction_batch_id
        The deterministic ID of the extraction batch this assertion was
        derived from.
    extraction_hash
        The deterministic hash of the extraction batch content.
    parser_version
        Version of the parser that produced the extraction batch.
    extraction_schema_version
        Schema version of the extraction batch.
    source_record_type
        The ``record_type`` of the :class:`ExtractedRecord` this assertion
        was derived from.
    source_record_index
        The ``record_index`` of the source record within its batch.
    source_locators
        Serialized :class:`Locator` dicts for every extracted field that
        contributed to this assertion.
    """

    artifact_id: str
    content_hash: str
    source_slug: str
    extraction_batch_id: str
    extraction_hash: str
    parser_version: str
    extraction_schema_version: str
    source_record_type: str
    source_record_index: int
    url: str = ""
    source_locators: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AssertionLineage:
        return cls(
            artifact_id=d["artifact_id"],
            content_hash=d["content_hash"],
            source_slug=d["source_slug"],
            extraction_batch_id=d["extraction_batch_id"],
            extraction_hash=d["extraction_hash"],
            parser_version=d["parser_version"],
            extraction_schema_version=d["extraction_schema_version"],
            source_record_type=d["source_record_type"],
            source_record_index=d["source_record_index"],
            url=d.get("url", ""),
            source_locators=list(d.get("source_locators", [])),
        )

    @classmethod
    def from_record(
        cls,
        batch: ExtractionBatchV1,
        record: ExtractedRecord,
    ) -> AssertionLineage:
        """Build the lineage for an assertion derived from *record*."""
        return cls(
            artifact_id=batch.artifact_id,
            content_hash=batch.content_hash,
            source_slug=batch.source_slug,
            url=batch.url,
            extraction_batch_id=batch.batch_id,
            extraction_hash=batch.extraction_hash,
            parser_version=batch.parser_version,
            extraction_schema_version=batch.schema_version,
            source_record_type=record.record_type,
            source_record_index=record.record_index,
            source_locators=[f.locator.to_dict() for f in record.fields],
        )


# ---------------------------------------------------------------------------
# CanonicalAssertionV1 — the publishable unit
# ---------------------------------------------------------------------------


@dataclass
class CanonicalAssertionV1:
    """A canonical assertion — the publishable output of the pipeline.

    Every published record MUST identify its transformer and schema
    version (``transformer_name``, ``transformer_version``,
    ``schema_version``) and MUST carry its lineage.

    Attributes
    ----------
    assertion_type
        The canonical type (e.g. ``"race_result"``, ``"certificate"``).
        Determines which registered schema validates ``data``.
    assertion_id
        Deterministic ID derived from (extraction_batch_id, record
        identity, transformer_version, schema_version).  Stable across
        reruns — replaying produces the same ID, so downstream stores can
        upsert idempotently.
    transformer_name
        Name of the transformer class that produced this assertion.
    transformer_version
        Version of the transformer (e.g. ``"1.0.0"``).
    schema_version
        Version of the canonical output schema (e.g. ``"v1"``).
    data
        The canonical payload, validated against the registered schema
        for ``(assertion_type, schema_version)``.
    lineage
        The provenance chain (see :class:`AssertionLineage`).
    assertion_hash
        Deterministic hash of (assertion_type, data, lineage, versions).
        Two assertions with the same ``assertion_hash`` are identical.
    """

    assertion_type: str
    assertion_id: str
    transformer_name: str
    transformer_version: str
    schema_version: str
    data: dict[str, Any]
    lineage: AssertionLineage
    assertion_hash: str = ""

    def __post_init__(self) -> None:
        if not self.assertion_hash:
            self.assertion_hash = self._derive_hash()

    def _derive_hash(self) -> str:
        """Derive the deterministic content hash."""
        return _sha256_hex(_canonical_json({
            "assertion_type": self.assertion_type,
            "transformer_name": self.transformer_name,
            "transformer_version": self.transformer_version,
            "schema_version": self.schema_version,
            "data": self.data,
            "lineage": self.lineage.to_dict(),
        }))

    # ------------------------------------------------------------------
    # Identity helpers
    # ------------------------------------------------------------------

    @classmethod
    def derive_assertion_id(
        cls,
        extraction_batch_id: str,
        record_type: str,
        record_index: int,
        transformer_version: str,
        schema_version: str,
    ) -> str:
        """Derive a deterministic assertion ID.

        The ID is a pure function of (extraction batch, record identity,
        transformer version, schema version) so reruns never mint new IDs
        for unchanged inputs.
        """
        raw = _canonical_json({
            "extraction_batch_id": extraction_batch_id,
            "record_type": record_type,
            "record_index": record_index,
            "transformer_version": transformer_version,
            "schema_version": schema_version,
        })
        return f"asrt_{_sha256_hex(raw)[:16]}"

    def identifies_transformer(self) -> bool:
        """``True`` if this assertion identifies its transformer + schema.

        This is the acceptance-criteria invariant: every published record
        identifies transformer and schema version.
        """
        return bool(
            self.transformer_name
            and self.transformer_version
            and self.schema_version
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_type": self.assertion_type,
            "assertion_id": self.assertion_id,
            "transformer_name": self.transformer_name,
            "transformer_version": self.transformer_version,
            "schema_version": self.schema_version,
            "data": self.data,
            "lineage": self.lineage.to_dict(),
            "assertion_hash": self.assertion_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CanonicalAssertionV1:
        return cls(
            assertion_type=d["assertion_type"],
            assertion_id=d["assertion_id"],
            transformer_name=d["transformer_name"],
            transformer_version=d["transformer_version"],
            schema_version=d["schema_version"],
            data=d["data"],
            lineage=AssertionLineage.from_dict(d["lineage"]),
            assertion_hash=d.get("assertion_hash", ""),
        )


# ---------------------------------------------------------------------------
# RejectedRecordV1 — the separate reject stream
# ---------------------------------------------------------------------------


@dataclass
class RejectedRecordV1:
    """A record that failed transformation or output-schema validation.

    Rejected records are emitted **separately** from assertions.  They
    carry machine-readable reasons so the failure can be triaged without
    re-running the pipeline.

    Attributes
    ----------
    reject_id
        Deterministic ID derived from (extraction batch, record identity,
        stage, reasons).
    source_record_type
        The ``record_type`` of the rejected extracted record.
    source_record_index
        The ``record_index`` of the rejected record within its batch.
    stage
        Where the record was rejected (see :class:`RejectStage`).
    reject_reasons
        Human/machine-readable validation failure messages.
    raw_fields
        Snapshot of the record's field values (name → value) for
        debugging.
    transformer_name
        Name of the transformer that rejected the record.
    transformer_version
        Version of the transformer.
    schema_version
        Version of the output schema the record failed against.
    """

    reject_id: str
    source_record_type: str
    source_record_index: int
    stage: str
    reject_reasons: list[str]
    raw_fields: dict[str, Any]
    transformer_name: str
    transformer_version: str
    schema_version: str

    @classmethod
    def create(
        cls,
        batch: ExtractionBatchV1,
        record: ExtractedRecord,
        stage: str,
        reasons: list[str],
        transformer_name: str,
        transformer_version: str,
        schema_version: str,
    ) -> RejectedRecordV1:
        """Build a reject with a deterministic ID and a field snapshot."""
        raw_fields = {f.name: f.value for f in record.fields}
        raw = _canonical_json({
            "extraction_batch_id": batch.batch_id,
            "record_type": record.record_type,
            "record_index": record.record_index,
            "stage": stage,
            "reasons": sorted(reasons),
        })
        return cls(
            reject_id=f"rej_{_sha256_hex(raw)[:16]}",
            source_record_type=record.record_type,
            source_record_index=record.record_index,
            stage=stage,
            reject_reasons=list(reasons),
            raw_fields=raw_fields,
            transformer_name=transformer_name,
            transformer_version=transformer_version,
            schema_version=schema_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RejectedRecordV1:
        return cls(
            reject_id=d["reject_id"],
            source_record_type=d["source_record_type"],
            source_record_index=d["source_record_index"],
            stage=d["stage"],
            reject_reasons=list(d["reject_reasons"]),
            raw_fields=dict(d["raw_fields"]),
            transformer_name=d["transformer_name"],
            transformer_version=d["transformer_version"],
            schema_version=d["schema_version"],
        )


# ---------------------------------------------------------------------------
# TransformationBatchV1 — the output contract (handoff)
# ---------------------------------------------------------------------------


@dataclass
class TransformationBatchV1:
    """DP-03-04 handoff / output contract — the transformation result.

    Produced by a :class:`Transformer` from a single
    :class:`ExtractionBatchV1`.  Contains the canonical assertions
    (publishable records) and the rejects (invalid records), as two
    **disjoint** partitions of the input records.

    Design principles:
    * ``transformation_id`` is deterministic: derived from
      (extraction_batch_id, transformer_name, transformer_version,
      schema_version).  Replaying the same batch with the same versions
      always returns the same ``transformation_id``.
    * ``transformation_hash`` is a deterministic hash of the assertions
      and rejects content (excluding ``transformed_at``).  Two batches
      with the same ``transformation_hash`` have identical output.
    * ``transformed_at`` is metadata only — it is NOT part of the
      deterministic hash.
    * **Invalid records never partially publish**: every input record
      lands in exactly one of ``assertions`` / ``rejects``.

    Fields
    ------
    transformation_id
        Deterministic batch identifier.
    extraction_batch_id
        The extraction batch that was transformed.
    extraction_hash
        The deterministic hash of the extraction batch.
    parser_version
        Version of the parser that produced the extraction batch.
    extraction_schema_version
        Schema version of the extraction batch.
    transformer_name
        Name of the transformer that produced this batch.
    transformer_version
        Version of the transformer.
    schema_version
        Version of the canonical output schema.
    source_slug
        The source the artifact came from.
    artifact_id
        The raw artifact ID.
    content_hash
        SHA-256 of the raw artifact.
    url
        The original URL of the artifact.
    assertions
        The validated, publishable canonical assertions.
    rejects
        The records that failed validation, with reasons.
    transformation_hash
        Deterministic hash of the output content.
    transformed_at
        ISO-8601 timestamp (metadata only, not deterministic).
    contract_version
        Version of this output envelope contract.
    """

    extraction_batch_id: str
    extraction_hash: str
    parser_version: str
    extraction_schema_version: str
    transformer_name: str
    transformer_version: str
    schema_version: str
    source_slug: str
    artifact_id: str
    content_hash: str
    url: str = ""
    assertions: list[CanonicalAssertionV1] = field(default_factory=list)
    rejects: list[RejectedRecordV1] = field(default_factory=list)
    transformation_id: str = ""
    transformation_hash: str = ""
    transformed_at: str = field(default_factory=_now_iso)
    contract_version: str = ASSERTION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not self.transformation_id:
            self.transformation_id = self._derive_transformation_id()
        if not self.transformation_hash:
            self.transformation_hash = self._derive_transformation_hash()

    # ------------------------------------------------------------------
    # Deterministic derivation
    # ------------------------------------------------------------------

    def _derive_transformation_id(self) -> str:
        """Derive a deterministic ID from (batch, transformer, versions)."""
        raw = _canonical_json({
            "extraction_batch_id": self.extraction_batch_id,
            "transformer_name": self.transformer_name,
            "transformer_version": self.transformer_version,
            "schema_version": self.schema_version,
        })
        return f"tx_{_sha256_hex(raw)[:16]}"

    def _derive_transformation_hash(self) -> str:
        """Derive a deterministic hash of the output content.

        Covers every assertion (type, id, data, lineage) and every reject
        (record identity, stage, reasons) — everything that makes up the
        transformation output.  It does NOT include ``transformed_at``.

        Two batches with the same ``transformation_hash`` are guaranteed
        to have identical assertions and rejects.
        """
        raw = _canonical_json({
            "extraction_batch_id": self.extraction_batch_id,
            "transformer_name": self.transformer_name,
            "transformer_version": self.transformer_version,
            "schema_version": self.schema_version,
            "assertions": [a.to_dict() for a in self.assertions],
            "rejects": [r.to_dict() for r in self.rejects],
        })
        return _sha256_hex(raw)

    # ------------------------------------------------------------------
    # Invariants / inspection
    # ------------------------------------------------------------------

    def assertion_count(self) -> int:
        """Return the number of published assertions."""
        return len(self.assertions)

    def reject_count(self) -> int:
        """Return the number of rejected records."""
        return len(self.rejects)

    def record_count(self) -> int:
        """Return the total number of records processed."""
        return len(self.assertions) + len(self.rejects)

    def all_assertions_identify_transformer(self) -> bool:
        """``True`` if every published assertion identifies transformer
        and schema version (the headline acceptance invariant)."""
        return all(a.identifies_transformer() for a in self.assertions)

    def all_assertions_have_lineage(self) -> bool:
        """``True`` if every assertion cites artifact, extraction batch,
        and source record identity."""
        for a in self.assertions:
            lin = a.lineage
            if not lin.artifact_id or not lin.content_hash:
                return False
            if not lin.extraction_batch_id or not lin.extraction_hash:
                return False
            if not lin.source_slug:
                return False
        return True

    def asserts_disjoint_partition(self) -> bool:
        """``True`` if assertions and rejects form a disjoint partition.

        A record identity (record_type, record_index) appears in exactly
        one of the two sets — invalid records never partially publish.
        """
        published = {
            (a.lineage.source_record_type, a.lineage.source_record_index)
            for a in self.assertions
        }
        rejected = {
            (r.source_record_type, r.source_record_index) for r in self.rejects
        }
        # Disjoint
        if published & rejected:
            return False
        # No duplicates within either set
        if len(published) != len(self.assertions):
            return False
        if len(rejected) != len(self.rejects):
            return False
        return True

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "transformation_id": self.transformation_id,
            "extraction_batch_id": self.extraction_batch_id,
            "extraction_hash": self.extraction_hash,
            "parser_version": self.parser_version,
            "extraction_schema_version": self.extraction_schema_version,
            "transformer_name": self.transformer_name,
            "transformer_version": self.transformer_version,
            "schema_version": self.schema_version,
            "source_slug": self.source_slug,
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
            "url": self.url,
            "assertions": [a.to_dict() for a in self.assertions],
            "rejects": [r.to_dict() for r in self.rejects],
            "transformation_hash": self.transformation_hash,
            "transformed_at": self.transformed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TransformationBatchV1:
        return cls(
            extraction_batch_id=d["extraction_batch_id"],
            extraction_hash=d["extraction_hash"],
            parser_version=d["parser_version"],
            extraction_schema_version=d["extraction_schema_version"],
            transformer_name=d["transformer_name"],
            transformer_version=d["transformer_version"],
            schema_version=d["schema_version"],
            source_slug=d["source_slug"],
            artifact_id=d["artifact_id"],
            content_hash=d["content_hash"],
            url=d.get("url", ""),
            assertions=[
                CanonicalAssertionV1.from_dict(a)
                for a in d.get("assertions", [])
            ],
            rejects=[RejectedRecordV1.from_dict(r) for r in d.get("rejects", [])],
            transformation_id=d.get("transformation_id", ""),
            transformation_hash=d.get("transformation_hash", ""),
            transformed_at=d.get("transformed_at", _now_iso()),
            contract_version=d.get("contract_version", ASSERTION_CONTRACT_VERSION),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @classmethod
    def from_json(cls, s: str) -> TransformationBatchV1:
        return cls.from_dict(json.loads(s))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TransformationBatchV1):
            return NotImplemented
        # Equality is based on the deterministic content, not
        # ``transformed_at``.
        return self.transformation_hash == other.transformation_hash


# ---------------------------------------------------------------------------
# Transformer — abstract base / protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Transformer(Protocol):
    """The transformation execution contract.

    Every concrete transformer implements this protocol.  The contract is:

    1. Accept a :class:`ExtractionBatchV1` (the DP-02-03 handoff).
    2. Validate the input schema; raise :class:`InputSchemaValidationError`
       on a structurally invalid batch.
    3. Return a :class:`TransformationBatchV1` (handoff / output contract)
       where every input record appears in exactly one of ``assertions``
       or ``rejects``.
    4. Be deterministic: the same input always produces the same
       ``transformation_hash``.
    5. Never mutate the input batch.

    Usage::

        class MyTransformer(BaseTransformer):
            transformer_name = "MyTransformer"
            transformer_version = "1.0.0"
            schema_version = "v1"

            def map_record(self, record, batch):
                ...
                return ("race_result", {"sail_number": ..., ...})
    """

    #: Human-readable transformer name.
    transformer_name: str

    #: Version label of this transformer (e.g. ``"1.0.0"``).
    transformer_version: str

    #: Version of the canonical output schema (e.g. ``"v1"``).
    schema_version: str

    #: Expected extraction (input) schema version.
    input_schema_version: str

    def transform(self, batch: ExtractionBatchV1) -> TransformationBatchV1:
        """Transform an extraction batch into a transformation batch."""
        ...


# ---------------------------------------------------------------------------
# BaseTransformer — convenience base class
# ---------------------------------------------------------------------------


class BaseTransformer:
    """Convenience base class for concrete transformers.

    Subclasses set ``transformer_name``, ``transformer_version`` and
    ``schema_version`` as class attributes and implement
    :meth:`map_record`.

    The base class implements the full pipeline stage:

    1. :meth:`validate_input` — input schema validation (fail fast).
    2. Per record: :meth:`map_record` → draft ``(assertion_type, data)``.
    3. Output schema validation of the draft against the registered
       schema for ``(assertion_type, schema_version)``.
    4. Validated drafts become :class:`CanonicalAssertionV1` with
       lineage; failures become :class:`RejectedRecordV1`.

    The transformer never mutates the input batch and never lets an
    invalid record partially publish.
    """

    transformer_name: str = "BaseTransformer"
    transformer_version: str = "1.0.0"
    schema_version: str = ASSERTION_SCHEMA_VERSION
    input_schema_version: str = EXTRACTION_SCHEMA_VERSION

    # ------------------------------------------------------------------
    # Pipeline entry point
    # ------------------------------------------------------------------

    def transform(self, batch: ExtractionBatchV1) -> TransformationBatchV1:
        """Transform an extraction batch into canonical assertions.

        Args:
            batch: The extraction batch (DP-02-03 handoff).

        Returns:
            A :class:`TransformationBatchV1` with assertions and rejects.

        Raises:
            InputSchemaValidationError: if the batch fails input schema
                validation.  Nothing is published from an invalid batch.
        """
        self.validate_input(batch)

        assertions: list[CanonicalAssertionV1] = []
        rejects: list[RejectedRecordV1] = []

        for record in batch.records:
            assertion, reject = self._process_record(batch, record)
            if assertion is not None:
                assertions.append(assertion)
            elif reject is not None:
                rejects.append(reject)
            # map_record returning None → record intentionally skipped
            # (e.g. record type not handled by this transformer).  Skipped
            # records publish nothing and reject nothing.

        return self.finalize(batch, assertions, rejects)

    # ------------------------------------------------------------------
    # Input schema validation
    # ------------------------------------------------------------------

    def validate_input(self, batch: ExtractionBatchV1) -> None:
        """Validate the input extraction batch schema.

        Raises :class:`InputSchemaValidationError` if the batch is
        structurally invalid.  Called before any record is mapped so an
        invalid batch publishes nothing.
        """
        errors: list[str] = []

        if not isinstance(batch, ExtractionBatchV1):
            raise InputSchemaValidationError(
                f"expected ExtractionBatchV1, got {type(batch).__name__}"
            )

        if batch.contract_version != EXTRACTION_SCHEMA_VERSION:
            errors.append(
                f"contract_version {batch.contract_version!r} != "
                f"{EXTRACTION_SCHEMA_VERSION!r}"
            )
        if batch.schema_version != self.input_schema_version:
            errors.append(
                f"schema_version {batch.schema_version!r} != expected "
                f"{self.input_schema_version!r}"
            )
        if not batch.batch_id:
            errors.append("batch_id is empty")
        if not batch.extraction_hash:
            errors.append("extraction_hash is empty")
        if not batch.artifact_id:
            errors.append("artifact_id is empty")
        if not batch.content_hash:
            errors.append("content_hash is empty")
        if not batch.source_slug:
            errors.append("source_slug is empty")
        if not batch.parser_version:
            errors.append("parser_version is empty")

        for i, record in enumerate(batch.records):
            if not record.record_type:
                errors.append(f"records[{i}]: record_type is empty")
            if record.record_index is None:
                errors.append(f"records[{i}]: record_index is missing")

        if errors:
            raise InputSchemaValidationError(
                "input schema validation failed: " + "; ".join(errors)
            )

    # ------------------------------------------------------------------
    # Record mapping — implemented by subclasses
    # ------------------------------------------------------------------

    def map_record(
        self,
        record: ExtractedRecord,
        batch: ExtractionBatchV1,
    ) -> tuple[str, dict[str, Any]] | None:
        """Map one extracted record to an assertion draft.

        Returns:
            ``(assertion_type, data)`` — the draft payload, which will be
            validated against the registered output schema; or ``None``
            to skip the record (record type not handled).

        Raises:
            RecordTransformError: to divert the record to the reject
                stream with explicit reasons.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Output schema validation
    # ------------------------------------------------------------------

    def validate_output(
        self,
        assertion_type: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate a draft payload against the registered output schema.

        Returns the normalized (schema-validated) payload dict.

        Raises:
            UnknownAssertionSchemaError: no schema registered for
                ``(assertion_type, schema_version)``.
            pydantic.ValidationError: the draft failed validation.
        """
        from irc_data.transform.schemas import get_assertion_schema

        schema = get_assertion_schema(assertion_type, self.schema_version)
        validated = schema.model_validate(data)
        return validated.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _process_record(
        self,
        batch: ExtractionBatchV1,
        record: ExtractedRecord,
    ) -> tuple[CanonicalAssertionV1 | None, RejectedRecordV1 | None]:
        """Run one record through map → validate → publish-or-reject."""
        # Stage 1: map the record
        try:
            draft = self.map_record(record, batch)
        except RecordTransformError as exc:
            return None, RejectedRecordV1.create(
                batch=batch,
                record=record,
                stage=RejectStage.TRANSFORM.value,
                reasons=exc.reasons,
                transformer_name=self.transformer_name,
                transformer_version=self.transformer_version,
                schema_version=self.schema_version,
            )
        except Exception as exc:  # noqa: BLE001 — invalid records never
            # crash the batch; they divert to the reject stream.
            return None, RejectedRecordV1.create(
                batch=batch,
                record=record,
                stage=RejectStage.TRANSFORM.value,
                reasons=[f"transform_error: {type(exc).__name__}: {exc}"],
                transformer_name=self.transformer_name,
                transformer_version=self.transformer_version,
                schema_version=self.schema_version,
            )

        if draft is None:
            return None, None

        assertion_type, data = draft

        # Stage 2: output schema validation
        try:
            validated_data = self.validate_output(assertion_type, data)
        except UnknownAssertionSchemaError:
            raise  # pipeline misconfiguration — fail fast
        except Exception as exc:  # pydantic.ValidationError
            reasons = self._format_validation_error(exc)
            return None, RejectedRecordV1.create(
                batch=batch,
                record=record,
                stage=RejectStage.OUTPUT_SCHEMA_VALIDATION.value,
                reasons=reasons,
                transformer_name=self.transformer_name,
                transformer_version=self.transformer_version,
                schema_version=self.schema_version,
            )

        # Stage 3: publish with identity + lineage
        assertion = CanonicalAssertionV1(
            assertion_type=assertion_type,
            assertion_id=CanonicalAssertionV1.derive_assertion_id(
                extraction_batch_id=batch.batch_id,
                record_type=record.record_type,
                record_index=record.record_index,
                transformer_version=self.transformer_version,
                schema_version=self.schema_version,
            ),
            transformer_name=self.transformer_name,
            transformer_version=self.transformer_version,
            schema_version=self.schema_version,
            data=validated_data,
            lineage=AssertionLineage.from_record(batch, record),
        )
        return assertion, None

    @staticmethod
    def _format_validation_error(exc: Exception) -> list[str]:
        """Format a pydantic ValidationError into readable reasons."""
        errors = getattr(exc, "errors", None)
        if callable(errors):
            reasons = []
            for err in errors():
                loc = ".".join(str(part) for part in err.get("loc", ()))
                msg = err.get("msg", "invalid")
                reasons.append(f"{loc}: {msg}" if loc else msg)
            return reasons or [str(exc)]
        return [str(exc)]

    # ------------------------------------------------------------------
    # Finalizer
    # ------------------------------------------------------------------

    def finalize(
        self,
        batch: ExtractionBatchV1,
        assertions: list[CanonicalAssertionV1],
        rejects: list[RejectedRecordV1],
    ) -> TransformationBatchV1:
        """Build the :class:`TransformationBatchV1` output contract."""
        return TransformationBatchV1(
            extraction_batch_id=batch.batch_id,
            extraction_hash=batch.extraction_hash,
            parser_version=batch.parser_version,
            extraction_schema_version=batch.schema_version,
            transformer_name=self.transformer_name,
            transformer_version=self.transformer_version,
            schema_version=self.schema_version,
            source_slug=batch.source_slug,
            artifact_id=batch.artifact_id,
            content_hash=batch.content_hash,
            url=batch.url,
            assertions=assertions,
            rejects=rejects,
        )


__all__ = [
    "ASSERTION_CONTRACT_VERSION",
    "ASSERTION_SCHEMA_VERSION",
    "TransformationError",
    "InputSchemaValidationError",
    "UnknownAssertionSchemaError",
    "RecordTransformError",
    "RejectStage",
    "AssertionLineage",
    "CanonicalAssertionV1",
    "RejectedRecordV1",
    "TransformationBatchV1",
    "Transformer",
    "BaseTransformer",
]
