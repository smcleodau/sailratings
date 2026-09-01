"""Replay, reparse and backfill contracts (DP-02-04 / SPEC-013).

This module defines the **handoff / output contracts** for the replay /
backfill pipeline:

* :class:`ReplayPlanV1` — the *input contract* that describes which
  artifacts to reparse, with which parser version, and into which
  isolated batch.  The ``plan_id`` is the idempotency key: re-running
  a replay with the same ``plan_id`` returns the existing batch
  instead of creating a new one.

* :class:`PublicationReceiptV1` — the *output contract* produced when
  a batch is explicitly promoted to publication.  Promotion is never an
  in-place rewrite: the old published batch is retained and the
  receipt records the link between the promoted batch and the batch it
  superseded.

Both dataclasses support JSON round-trip (``to_dict`` / ``from_dict``
/ ``to_json`` / ``from_json``) so they can be persisted to the
database, passed across Temporal activity boundaries, or written to a
file for offline inspection.

Design principles
-----------------

* **Replay is idempotent.**  Submitting the same ``plan_id`` twice
  yields the same batch — no duplicate parsing.

* **Replay is resumable.**  The batch's ``status`` field tracks
  progress (``pending → running → comparing → awaiting_approval →
  promoted | rejected``).  A crashed workflow resumes from the last
  persisted status.

* **Publication is an explicit promotion.**  Nothing is published
  until :meth:`promote_batch` is called.  The old published batch is
  retained (``retained = True`` in the receipt).

* **Old outputs are retained.**  Every parsed artifact from a
  superseded batch remains in the database, marked as superseded, so
  the full audit history is queryable.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _sha256_hex(data: str) -> str:
    """Return the SHA-256 hex digest of *data* (UTF-8 encoded)."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "v1"


# ---------------------------------------------------------------------------
# Batch status
# ---------------------------------------------------------------------------


class BatchStatus(str, enum.Enum):
    """Lifecycle status of a replay batch.

    ``PENDING``
        The batch has been created but parsing has not started.
    ``RUNNING``
        The new parser is running artifacts into the isolated batch.
    ``COMPARING``
        Parsing is complete; old and new outputs are being compared.
    ``AWAITING_APPROVAL``
        Comparison is done; the batch is waiting for explicit promotion
        approval.
    ``PROMOTED``
        The batch has been explicitly promoted to publication.  The old
        published batch is retained.
    ``REJECTED``
        The batch was reviewed and rejected.  Old outputs are untouched.
    ``SUPERSEDED``
        The batch was previously promoted but has since been superseded
        by a newer promoted batch.  Its outputs are retained for audit.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPARING = "comparing"
    AWAITING_APPROVAL = "awaiting_approval"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


# ---------------------------------------------------------------------------
# ArtifactFilter — selection criteria
# ---------------------------------------------------------------------------


@dataclass
class ArtifactFilter:
    """Selection criteria for artifacts to replay.

    All fields are optional — a ``None`` field means "no filter on this
    dimension".  At least one field SHOULD be set to avoid selecting the
    entire corpus.

    Attributes
    ----------
    source_slug
        Only artifacts from this source (e.g. ``"sailsys"``).
    fetched_after
        ISO-8601 timestamp; only artifacts fetched after this time.
    fetched_before
        ISO-8601 timestamp; only artifacts fetched before this time.
    parser_version
        Only artifacts originally parsed with this parser version.
    content_hash
        Only artifacts with this exact content hash.
    limit
        Maximum number of artifacts to select.
    """

    source_slug: str | None = None
    fetched_after: str | None = None
    fetched_before: str | None = None
    parser_version: str | None = None
    content_hash: str | None = None
    limit: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArtifactFilter:
        return cls(
            source_slug=d.get("source_slug"),
            fetched_after=d.get("fetched_after"),
            fetched_before=d.get("fetched_before"),
            parser_version=d.get("parser_version"),
            content_hash=d.get("content_hash"),
            limit=d.get("limit"),
        )


# ---------------------------------------------------------------------------
# ReplayPlanV1 — the input contract (handoff / output contract)
# ---------------------------------------------------------------------------


@dataclass
class ReplayPlanV1:
    """DP-02-04 handoff contract — the replay / backfill plan.

    This is the input to :class:`ReplayWorkflow`.  It describes **which**
    artifacts to reparse, **with which** parser version, and **where** to
    store the results (an isolated batch keyed by ``plan_id``).

    The ``plan_id`` is the idempotency key.  If a batch with this
    ``plan_id`` already exists, the workflow resumes it rather than
    creating a new one.

    Fields
    ------
    plan_id
        Unique identifier for this replay plan.  Must be stable across
        retries so the workflow is idempotent.  If not provided, a
        deterministic ID is derived from the filter + parser version.
    source_slug
        The source being replayed (e.g. ``"sailsys"``).
    new_parser_version
        The version label of the new parser to run (e.g. ``"2.1.0"``).
    artifact_filter
        Selection criteria for which artifacts to replay.
    created_at
        ISO-8601 timestamp of plan creation.
    created_by
        Optional identity of the operator who created the plan.
    notes
        Free-form notes for auditability.
    """

    source_slug: str
    new_parser_version: str
    artifact_filter: ArtifactFilter = field(default_factory=ArtifactFilter)
    plan_id: str = ""
    created_at: str = field(default_factory=_now_iso)
    created_by: str = ""
    notes: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.plan_id:
            self.plan_id = self._derive_plan_id()

    def _derive_plan_id(self) -> str:
        """Derive a deterministic plan_id from the filter + parser version."""
        raw = json.dumps(
            {
                "source_slug": self.source_slug,
                "new_parser_version": self.new_parser_version,
                "filter": self.artifact_filter.to_dict(),
            },
            sort_keys=True,
        )
        return _sha256_hex(raw)[:16]

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "source_slug": self.source_slug,
            "new_parser_version": self.new_parser_version,
            "artifact_filter": self.artifact_filter.to_dict(),
            "created_at": self.created_at,
            "created_by": self.created_by,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReplayPlanV1:
        flt = d.get("artifact_filter") or {}
        if isinstance(flt, ArtifactFilter):
            artifact_filter = flt
        else:
            artifact_filter = ArtifactFilter.from_dict(flt)
        return cls(
            source_slug=d["source_slug"],
            new_parser_version=d["new_parser_version"],
            artifact_filter=artifact_filter,
            plan_id=d.get("plan_id", ""),
            created_at=d.get("created_at", _now_iso()),
            created_by=d.get("created_by", ""),
            notes=d.get("notes", ""),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> ReplayPlanV1:
        return cls.from_dict(json.loads(s))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReplayPlanV1):
            return NotImplemented
        return self.to_dict() == other.to_dict()


# ---------------------------------------------------------------------------
# ComparisonResult — diff between old and new parsed outputs
# ---------------------------------------------------------------------------


@dataclass
class ComparisonResult:
    """Result of comparing old and new parsed outputs for one batch.

    Attributes
    ----------
    batch_id
        The batch being compared.
    total_artifacts
        Total number of artifacts compared.
    identical
        Number of artifacts whose parsed output is identical.
    changed
        Number of artifacts whose parsed output differs.
    added
        Number of artifacts in the new batch that have no old counterpart.
    removed
        Number of artifacts in the old batch that have no new counterpart.
    diff_summary
        Free-form summary string for human review.
    """

    batch_id: int
    total_artifacts: int = 0
    identical: int = 0
    changed: int = 0
    added: int = 0
    removed: int = 0
    diff_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ComparisonResult:
        return cls(
            batch_id=d["batch_id"],
            total_artifacts=d.get("total_artifacts", 0),
            identical=d.get("identical", 0),
            changed=d.get("changed", 0),
            added=d.get("added", 0),
            removed=d.get("removed", 0),
            diff_summary=d.get("diff_summary", ""),
        )

    def has_changes(self) -> bool:
        """True if any artifacts differ between old and new outputs."""
        return self.changed > 0 or self.added > 0 or self.removed > 0


# ---------------------------------------------------------------------------
# PublicationReceiptV1 — the output contract (handoff / output contract)
# ---------------------------------------------------------------------------


@dataclass
class PublicationReceiptV1:
    """DP-02-04 handoff contract — receipt for an explicit promotion.

    Produced when a replay batch is **promoted** to publication.  This
    is never an in-place rewrite: the old published batch is retained
    and its ID is recorded here so the audit trail is complete.

    Fields
    ------
    receipt_id
        Unique identifier for this promotion receipt.
    batch_id
        The batch that was promoted.
    plan_id
        The replay plan that produced the batch.
    source_slug
        The source the batch belongs to.
    promoted_at
        ISO-8601 timestamp of the promotion.
    old_batch_id
        The previously-promoted batch (retained, not deleted).
        ``None`` if this is the first promotion for the source.
    old_retained
        Always ``True`` — old outputs are never deleted.  Exists as an
        explicit boolean for auditors and automated checks.
    artifact_count
        Number of artifacts in the promoted batch.
    promoted_by
        Optional identity of the operator who approved the promotion.
    """

    receipt_id: str
    batch_id: int
    plan_id: str
    source_slug: str
    promoted_at: str = field(default_factory=_now_iso)
    old_batch_id: int | None = None
    old_retained: bool = True
    artifact_count: int = 0
    promoted_by: str = ""
    schema_version: str = SCHEMA_VERSION

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "batch_id": self.batch_id,
            "plan_id": self.plan_id,
            "source_slug": self.source_slug,
            "promoted_at": self.promoted_at,
            "old_batch_id": self.old_batch_id,
            "old_retained": self.old_retained,
            "artifact_count": self.artifact_count,
            "promoted_by": self.promoted_by,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PublicationReceiptV1:
        return cls(
            receipt_id=d["receipt_id"],
            batch_id=d["batch_id"],
            plan_id=d["plan_id"],
            source_slug=d["source_slug"],
            promoted_at=d.get("promoted_at", _now_iso()),
            old_batch_id=d.get("old_batch_id"),
            old_retained=d.get("old_retained", True),
            artifact_count=d.get("artifact_count", 0),
            promoted_by=d.get("promoted_by", ""),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> PublicationReceiptV1:
        return cls.from_dict(json.loads(s))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PublicationReceiptV1):
            return NotImplemented
        return self.to_dict() == other.to_dict()
