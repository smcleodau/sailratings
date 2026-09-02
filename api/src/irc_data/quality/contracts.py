"""Validation / quarantine / promotion contracts (DP-05-02).

Handoff / output contracts for the quality gates that stand between the
pipeline stages (extraction → canonical → identity) and the **canonical
consumer views**.

Goal (from the issue)
---------------------
**Prevent bad batches entering canonical views.**

* **Validate** extraction batches (DP-02-03), canonical batches
  (DP-03-04 transformation output) and identity effects (merges /
  splits / new-entity churn).
* **Quarantine** any batch that fails a rule — with samples and the
  rule failures attached — so a reviewer can see exactly what was
  wrong without touching consumers.
* **Require explicit promotion.**  Nothing enters a consumer view
  until a reviewer (or an explicit auto-promote decision) calls
  promote.

Acceptance criteria encoded here
--------------------------------

* **Partial publication cannot occur.**  A batch is promoted
  atomically in a single transaction, and *only* from the
  ``awaiting_promotion`` state.  Rows in a failed/quarantined batch are
  never visible to the consumer view — the view is defined as
  ``promoted batch rows only`` (see :mod:`irc_data.quality.gate_store`).

* **Retry / replay creates a new version.**  ``(pipeline, source_slug,
  version)`` is unique; a retry of content ``C`` after ``C`` was
  quarantined is ingested as ``version = prior + 1``.  Prior versions
  are retained for audit and never reused.

* **Consumers see only promoted versions.**  ``get_consumer_view()``
  filters on batches whose status is ``promoted``.  Quarantined /
  pending / superseded batches are invisible.

Contracts
---------

* :class:`GateFinding` — one rule failure attached to a batch, with a
  ``sample`` of offending records.
* :class:`QuarantineRecordV1` — the **quarantine handoff**: why the
  batch was quarantined, which rule classes fired, and sample rows.
* :class:`GateVerdictV1` — the full validation report for one batch
  (every rule evaluated, pass/fail, counts).
* :class:`PromotionReceiptV1` — the **promotion output contract**:
  proof that a batch version was explicitly promoted and which version
  it superseded.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


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


def _sha256_hex(data: str) -> str:
    """Return the SHA-256 hex digest of *data* (UTF-8 encoded)."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def canonical_json(obj: Any) -> str:
    """Serialize *obj* to a canonical JSON string for hashing."""
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


# ---------------------------------------------------------------------------
# GateKind — which stage a gate guards
# ---------------------------------------------------------------------------


class GateKind(str, enum.Enum):
    """The pipeline stage a gate guards.

    ``EXTRACTION``
        Validates :class:`~irc_data.parsers.extraction_contract.ExtractionBatchV1`
        payloads — provenance, determinism, schema identity, record shape.
    ``CANONICAL``
        Validates :class:`~irc_data.transform.transformation_contract.TransformationBatchV1`
        payloads — reject partitioning, assertion identity / lineage /
        output-schema conformance, determinism.
    ``IDENTITY``
        Validates identity effects (merge / split / new-entity churn)
        before they touch the canonical entity registry or views.
    """

    EXTRACTION = "extraction"
    CANONICAL = "canonical"
    IDENTITY = "identity"


# ---------------------------------------------------------------------------
# RuleClass — the taxonomy of rule failures
# ---------------------------------------------------------------------------


class RuleClass(str, enum.Enum):
    """The class of a gate rule — used to group failures for review.

    ``SCHEMA``
        Contract / envelope identity: versions, required fields, ids and
        hashes present and well-formed.
    ``PROVENANCE``
        Every value cites its source (artifact id + content hash);
        lineage chains are complete.
    ``DETERMINISM``
        The payload's content-derived id/hash matches recomputation —
        replaying the same input must not silently produce different
        output.
    ``COMPLETENESS``
        Expected content is present: non-empty records, known record
        types, every input record accounted for (assertion / reject /
        skip partition).
    ``VALUE_DOMAIN``
        Individual field values are in-domain (confidence in [0, 1],
        TCC-like ratings within plausible bounds, valid timestamps …).
    ``IDENTITY_EFFECT``
        Identity-resolution effects are sane: no self-merges, no
        cross-type merges, churn bounded, supersession pointers
        well-formed.
    """

    SCHEMA = "schema"
    PROVENANCE = "provenance"
    DETERMINISM = "determinism"
    COMPLETENESS = "completeness"
    VALUE_DOMAIN = "value_domain"
    IDENTITY_EFFECT = "identity_effect"


# ---------------------------------------------------------------------------
# QualityBatchStatus — lifecycle of a gated batch
# ---------------------------------------------------------------------------


class QualityBatchStatus(str, enum.Enum):
    """Lifecycle status of a gated batch.

    ``PENDING``
        Ingested, validation not yet run.
    ``VALIDATING``
        Gate rules are being evaluated.
    ``QUARANTINED``
        One or more rules failed.  The batch is isolated with samples
        and rule failures attached.  It can never be promoted; a retry
        must be ingested as a **new version**.
    ``AWAITING_PROMOTION``
        All rules passed.  The batch waits for explicit promotion.
    ``PROMOTED``
        Explicitly promoted — this version is the one consumers see.
    ``SUPERSEDED``
        Was promoted, then a newer version was promoted.  Retained for
        audit, invisible to consumers.
    """

    PENDING = "pending"
    VALIDATING = "validating"
    QUARANTINED = "quarantined"
    AWAITING_PROMOTION = "awaiting_promotion"
    PROMOTED = "promoted"
    SUPERSEDED = "superseded"


# ---------------------------------------------------------------------------
# GateFinding — one rule failure with samples
# ---------------------------------------------------------------------------


@dataclass
class GateFinding:
    """One rule failure attached to a batch.

    Attributes
    ----------
    rule_id
        Stable identifier of the rule, e.g. ``"extraction.provenance.locators"``.
    rule_class
        The :class:`RuleClass` the rule belongs to.
    gate
        The :class:`GateKind` the rule guards.
    severity
        ``"error"`` failures quarantine the batch.  ``"warning"``
        findings are recorded but do not block promotion.
    message
        Human-readable description of what failed.
    sample
        A bounded sample of offending records / field references (at
        most ``MAX_SAMPLE`` entries) so a reviewer can see the problem
        without paging the whole batch.
    failure_count
        Total number of occurrences (the sample is a prefix of these).
    """

    rule_id: str
    rule_class: str
    gate: str
    message: str
    severity: str = "error"
    sample: list[Any] = field(default_factory=list)
    failure_count: int = 0

    #: Maximum number of sample entries kept per finding.
    MAX_SAMPLE: int = field(default=25, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.failure_count:
            self.failure_count = len(self.sample)
        if len(self.sample) > self.MAX_SAMPLE:
            self.sample = self.sample[: self.MAX_SAMPLE]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_class": self.rule_class,
            "gate": self.gate,
            "severity": self.severity,
            "message": self.message,
            "failure_count": self.failure_count,
            "sample": self.sample,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GateFinding:
        return cls(
            rule_id=d["rule_id"],
            rule_class=d["rule_class"],
            gate=d["gate"],
            message=d["message"],
            severity=d.get("severity", "error"),
            sample=list(d.get("sample") or []),
            failure_count=d.get("failure_count", 0),
        )


# ---------------------------------------------------------------------------
# QuarantineRecordV1 — the quarantine handoff contract
# ---------------------------------------------------------------------------


@dataclass
class QuarantineRecordV1:
    """The handoff contract produced when a batch is quarantined.

    Carries everything a reviewer needs to diagnose the failure without
    touching consumers: the rule failures (with samples), a bounded set
    of raw sample rows from the batch payload, and the ids needed to
    find the batch.

    Fields
    ------
    quarantine_id
        Deterministic id: ``sha256(pipeline|source_slug|version)[:16]``
        — the same (pipeline, source, version) always quarantines to the
        same record, so re-validating is idempotent.
    batch_key
        The batch's storage key (``batch_key`` in the gate store).
    pipeline / source_slug / version
        Identity of the batch version that was quarantined.
    gate
        The gate kind whose rules fired.
    failures
        Every :class:`GateFinding` that quarantined the batch.
    sample_rows
        A bounded sample of the offending payload rows.
    quarantined_at
        ISO-8601 timestamp.
    status
        ``open`` (awaiting review) | ``released`` (a newer version was
        promoted; this one is closed) | ``overridden`` (a reviewer
        explicitly waived the failures — recorded, never silent).
    """

    batch_key: str
    pipeline: str
    source_slug: str
    version: int
    gate: str
    failures: list[GateFinding] = field(default_factory=list)
    sample_rows: list[Any] = field(default_factory=list)
    quarantined_at: str = field(default_factory=_now_iso)
    status: str = "open"
    quarantine_id: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.quarantine_id:
            self.quarantine_id = self._derive_id()

    def _derive_id(self) -> str:
        raw = f"{self.pipeline}|{self.source_slug}|{self.version}|{self.gate}"
        return _sha256_hex(raw)[:16]

    def rule_classes(self) -> list[str]:
        """Distinct rule classes that fired, in first-seen order."""
        seen: list[str] = []
        for f in self.failures:
            if f.rule_class not in seen:
                seen.append(f.rule_class)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "quarantine_id": self.quarantine_id,
            "batch_key": self.batch_key,
            "pipeline": self.pipeline,
            "source_slug": self.source_slug,
            "version": self.version,
            "gate": self.gate,
            "failures": [f.to_dict() for f in self.failures],
            "sample_rows": self.sample_rows,
            "quarantined_at": self.quarantined_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QuarantineRecordV1:
        return cls(
            batch_key=d["batch_key"],
            pipeline=d["pipeline"],
            source_slug=d["source_slug"],
            version=int(d["version"]),
            gate=d["gate"],
            failures=[GateFinding.from_dict(f) for f in d.get("failures", [])],
            sample_rows=list(d.get("sample_rows") or []),
            quarantined_at=d.get("quarantined_at", _now_iso()),
            status=d.get("status", "open"),
            quarantine_id=d.get("quarantine_id", ""),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @classmethod
    def from_json(cls, s: str) -> QuarantineRecordV1:
        return cls.from_dict(json.loads(s))


# ---------------------------------------------------------------------------
# GateVerdictV1 — full validation report for one batch
# ---------------------------------------------------------------------------


@dataclass
class GateVerdictV1:
    """The full validation report for one batch version.

    Records every rule that was evaluated (pass or fail) so the review
    UI can show coverage, plus the failures that drove the outcome.

    ``outcome`` is one of ``"passed"`` | ``"quarantined"``.
    """

    batch_key: str
    pipeline: str
    source_slug: str
    version: int
    gate: str
    outcome: str
    rules_evaluated: int = 0
    rules_failed: int = 0
    failures: list[GateFinding] = field(default_factory=list)
    record_count: int = 0
    evaluated_at: str = field(default_factory=_now_iso)
    verdict_id: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.verdict_id:
            raw = (
                f"{self.pipeline}|{self.source_slug}|{self.version}|"
                f"{self.gate}|{self.evaluated_at}"
            )
            self.verdict_id = _sha256_hex(raw)[:16]

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verdict_id": self.verdict_id,
            "batch_key": self.batch_key,
            "pipeline": self.pipeline,
            "source_slug": self.source_slug,
            "version": self.version,
            "gate": self.gate,
            "outcome": self.outcome,
            "rules_evaluated": self.rules_evaluated,
            "rules_failed": self.rules_failed,
            "failures": [f.to_dict() for f in self.failures],
            "record_count": self.record_count,
            "evaluated_at": self.evaluated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GateVerdictV1:
        return cls(
            batch_key=d["batch_key"],
            pipeline=d["pipeline"],
            source_slug=d["source_slug"],
            version=int(d["version"]),
            gate=d["gate"],
            outcome=d["outcome"],
            rules_evaluated=d.get("rules_evaluated", 0),
            rules_failed=d.get("rules_failed", 0),
            failures=[GateFinding.from_dict(f) for f in d.get("failures", [])],
            record_count=d.get("record_count", 0),
            evaluated_at=d.get("evaluated_at", _now_iso()),
            verdict_id=d.get("verdict_id", ""),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )


# ---------------------------------------------------------------------------
# PromotionReceiptV1 — the promotion output contract
# ---------------------------------------------------------------------------


@dataclass
class PromotionReceiptV1:
    """Output contract for an explicit promotion (DP-05-02).

    Promotion is atomic and explicit: the batch moves
    ``awaiting_promotion → promoted`` and any previously-promoted
    version of the same ``(pipeline, source_slug)`` moves to
    ``superseded`` — in the same transaction, so **partial publication
    cannot occur**.

    Fields
    ------
    receipt_id
        Unique id of this promotion.
    batch_key / pipeline / source_slug / version
        Identity of the promoted batch version.
    record_count
        Number of batch rows made visible to consumers.
    superseded_batch_key / superseded_version
        The previously-promoted version (retained, now invisible to
        consumers), or ``None`` for the first promotion.
    promoted_by
        Identity of the reviewer / process that approved promotion.
        Empty string is only valid when ``auto`` is True.
    auto
        Whether this was an explicit auto-promotion decision (the gate
        config opted in) rather than a human click.
    promoted_at
        ISO-8601 timestamp.
    """

    receipt_id: str
    batch_key: str
    pipeline: str
    source_slug: str
    version: int
    record_count: int = 0
    superseded_batch_key: str | None = None
    superseded_version: int | None = None
    promoted_by: str = ""
    auto: bool = False
    promoted_at: str = field(default_factory=_now_iso)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "batch_key": self.batch_key,
            "pipeline": self.pipeline,
            "source_slug": self.source_slug,
            "version": self.version,
            "record_count": self.record_count,
            "superseded_batch_key": self.superseded_batch_key,
            "superseded_version": self.superseded_version,
            "promoted_by": self.promoted_by,
            "auto": self.auto,
            "promoted_at": self.promoted_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PromotionReceiptV1:
        return cls(
            receipt_id=d["receipt_id"],
            batch_key=d["batch_key"],
            pipeline=d["pipeline"],
            source_slug=d["source_slug"],
            version=int(d["version"]),
            record_count=d.get("record_count", 0),
            superseded_batch_key=d.get("superseded_batch_key"),
            superseded_version=d.get("superseded_version"),
            promoted_by=d.get("promoted_by", ""),
            auto=bool(d.get("auto", False)),
            promoted_at=d.get("promoted_at", _now_iso()),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @classmethod
    def from_json(cls, s: str) -> PromotionReceiptV1:
        return cls.from_dict(json.loads(s))


__all__ = [
    "SCHEMA_VERSION",
    "GateKind",
    "RuleClass",
    "QualityBatchStatus",
    "GateFinding",
    "QuarantineRecordV1",
    "GateVerdictV1",
    "PromotionReceiptV1",
    "canonical_json",
]
