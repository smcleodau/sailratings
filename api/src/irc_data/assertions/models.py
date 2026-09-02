"""Source assertion contract and bitemporal resolution rules (DP-03-02).

This module defines the **handoff / output contract** for the assertion
layer: the model that preserves *who said what, and when the truth
changed*.

Core idea
---------

Every fact the platform believes comes from a **source assertion** — an
immutable, append-only record capturing:

  - **assertion_id**   — unique id (content-derived by default, so the
                         same assertion re-submitted is idempotent).
  - **entity_type**    — what kind of thing the fact is about
                         (``boat`` | ``certificate`` | ``race_result`` | …).
  - **entity_key**     — stable key of the entity within its type
                         (e.g. sail number ``"GBR 8310"``).
  - **field**          — the fact's attribute (``"tcc"``, ``"rating"`` …).
  - **value** / **unit** — the asserted measurement and its unit.
  - **valid_from / valid_to** — *source-valid time*: when the source
                         claims the value was/is true in the real world.
                         ``valid_to = None`` means "still valid".
  - **recorded_at**    — *system-observed time*: when the platform
                         learned of this assertion.  Never changes.
  - **source_slug**    — provenance: the governed source that said it.
  - **provenance_uri** — provenance: the raw artifact hash or URL this
                         assertion was parsed from.
  - **confidence**     — 0.0–1.0 trust weight for conflict resolution.
  - **superseded_by**  — supersession pointer: the id of the assertion
                         that replaces this one.  History is **never
                         overwritten** — a correction is a new assertion
                         and a supersession link, not an update of the
                         value.
  - **status**         — ``active`` | ``retracted`` (a retraction is a
                         *deletion* expressed without erasing history).

Bitemporal guarantees
---------------------

* **Conflicting assertions coexist.**  Two sources (or the same source
  at two times) may assert different values for the same
  ``(entity_type, entity_key, field)``.  Nothing is overwritten; both
  rows live side by side.
* **The current resolved view is reproducible for any prior system
  time.**  :func:`resolve` filters to assertions with
  ``recorded_at <= as_of``, then applies the deterministic resolution
  rules.  Re-running with the same ``as_of`` always yields the same
  winner, because ``recorded_at`` and the resolution inputs are
  immutable.

Resolution rules (deterministic)
--------------------------------

For a set of assertions about one ``(entity_type, entity_key, field)``
as of system time ``as_of``:

1. Keep assertions with ``recorded_at <= as_of`` (bitemporal filter).
2. Keep assertions whose valid-time interval covers ``valid_as_of``
   (defaults to ``as_of``) — i.e. ``valid_from <= valid_as_of`` and
   (``valid_to is None`` or ``valid_as_of < valid_to``).
3. Drop assertions **retracted** at or before ``as_of``
   (``retracted_at <= as_of``) and assertions **superseded** at or
   before ``as_of`` (``superseded_at <= as_of``).  Both are system-time
   facts, so the filter is itself bitemporal: a retraction/correction
   that happened *after* ``as_of`` does not change the view *as of*
   ``as_of``, and retracting a successor never "resurrects" a
   superseded row.
4. Among survivors pick a winner by descending:
   ``confidence`` → ``recorded_at`` → ``valid_from`` → ``assertion_id``
   (lexicographic tiebreak so the result is fully deterministic).
5. Report every non-winner as a *conflict* so the coexistence is
   observable, not silent.

JSON round-trip (``to_dict`` / ``from_dict``) is provided so assertions
can cross Temporal activity boundaries or be dumped to fixtures.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

#: Version tag embedded in every serialised assertion.
SCHEMA_VERSION = "assertion-v1"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class AssertionStatus(str, enum.Enum):
    """Lifecycle status of an assertion.

    ``ACTIVE``
        The assertion stands.  It participates in resolution.
    ``RETRACTED``
        The source (or an operator) withdrew the assertion — a deletion.
        The row is retained for history but never wins resolution.
    """

    ACTIVE = "active"
    RETRACTED = "retracted"


#: Allowed status strings (mirrors the DB check constraint).
STATUSES: tuple[str, ...] = tuple(s.value for s in AssertionStatus)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AssertionValidationError(ValueError):
    """Raised when an assertion fails contract validation."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(dt: datetime) -> datetime:
    """Normalise a datetime to aware-UTC.

    Naive datetimes are assumed to be UTC.  This keeps fixture code
    terse while guaranteeing comparisons never mix naive/aware.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dt_to_iso(dt: datetime | None) -> str | None:
    """Serialise an optional datetime to ISO-8601 (UTC)."""
    if dt is None:
        return None
    return _utc(dt).isoformat()


def _dt_from_iso(s: str | datetime | None) -> datetime | None:
    """Parse an ISO-8601 string (or pass through a datetime) as aware-UTC."""
    if s is None:
        return None
    if isinstance(s, datetime):
        return _utc(s)
    return _utc(datetime.fromisoformat(s))


# ---------------------------------------------------------------------------
# AssertionV1 — the handoff / output contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssertionV1:
    """One immutable source assertion about a single field of an entity.

    Instances are frozen: an assertion, once created, never changes.
    Corrections are expressed as *new* assertions plus a supersession
    pointer on the old row (see :func:`supersede`).
    """

    # -- Identity of the fact ------------------------------------------------
    entity_type: str
    entity_key: str
    field: str

    # -- The asserted value --------------------------------------------------
    value: Any
    unit: str | None = None

    # -- Bitemporal timestamps ------------------------------------------------
    valid_from: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    valid_to: datetime | None = None
    recorded_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # -- Provenance ------------------------------------------------------------
    source_slug: str = ""
    provenance_uri: str | None = None

    # -- Trust -----------------------------------------------------------------
    confidence: float = 1.0

    # -- Supersession / status ---------------------------------------------------
    assertion_id: str = ""
    supersedes: str | None = None
    superseded_by: str | None = None
    #: System time at which this assertion was superseded (the successor's
    #: ``recorded_at``).  Bitemporal: the supersession only affects views
    #: as of times >= ``superseded_at``.
    superseded_at: datetime | None = None
    status: str = AssertionStatus.ACTIVE.value
    #: System time at which this assertion was retracted (a deletion).
    #: ``None`` while the assertion stands.  Bitemporal, like
    #: ``superseded_at`` — the view as of a time before the retraction
    #: still shows the assertion.
    retracted_at: datetime | None = None

    # -- Free-form extras --------------------------------------------------------
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Normalise timestamps to aware-UTC so ordering is total.
        object.__setattr__(self, "valid_from", _utc(self.valid_from))
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", _utc(self.valid_to))
        if self.superseded_at is not None:
            object.__setattr__(self, "superseded_at", _utc(self.superseded_at))
        if self.retracted_at is not None:
            object.__setattr__(self, "retracted_at", _utc(self.retracted_at))
        self.validate()
        if not self.assertion_id:
            object.__setattr__(self, "assertion_id", self.compute_id())

    # -- Validation -----------------------------------------------------------

    def validate(self) -> None:
        """Enforce contract invariants.  Raises AssertionValidationError."""
        if not self.entity_type:
            raise AssertionValidationError("entity_type is required")
        if not self.entity_key:
            raise AssertionValidationError("entity_key is required")
        if not self.field:
            raise AssertionValidationError("field is required")
        if not self.source_slug:
            raise AssertionValidationError("source_slug is required")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise AssertionValidationError(
                f"confidence must be in [0, 1], got {self.confidence!r}"
            )
        if self.status not in STATUSES:
            raise AssertionValidationError(
                f"status must be one of {STATUSES}, got {self.status!r}"
            )
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise AssertionValidationError(
                f"valid_to ({self.valid_to.isoformat()}) precedes "
                f"valid_from ({self.valid_from.isoformat()})"
            )

    # -- Identity ---------------------------------------------------------------

    def _identity_payload(self) -> dict[str, Any]:
        """Canonical payload used to derive the content-addressed id.

        Includes everything that makes the assertion distinct — so a
        *changed* value, time, or provenance yields a *new* id (a new
        assertion), while re-submitting the identical assertion is
        idempotent (same id, no duplicate row).
        """
        return {
            "entity_type": self.entity_type,
            "entity_key": self.entity_key,
            "field": self.field,
            "value": self.value,
            "unit": self.unit,
            "valid_from": _dt_to_iso(self.valid_from),
            "valid_to": _dt_to_iso(self.valid_to),
            "recorded_at": _dt_to_iso(self.recorded_at),
            "source_slug": self.source_slug,
            "provenance_uri": self.provenance_uri,
            "supersedes": self.supersedes,
            "status": self.status,
        }

    def compute_id(self) -> str:
        """Deterministic content-addressed assertion id (SHA-256)."""
        blob = json.dumps(
            self._identity_payload(), sort_keys=True, default=str
        ).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    @property
    def fact_key(self) -> tuple[str, str, str]:
        """The fact this assertion is about: (entity_type, entity_key, field)."""
        return (self.entity_type, self.entity_key, self.field)

    # -- Supersession / retraction helpers -------------------------------------

    def supersede(self, successor: "AssertionV1") -> "AssertionV1":
        """Return a copy of this assertion marked superseded by *successor*.

        The successor must point back via ``supersedes``.  This is the
        correction path: the old row is kept (history) and linked to the
        new one.  ``superseded_at`` is the successor's ``recorded_at`` so
        the supersession is itself bitemporal — views as of times before
        the successor was recorded still show this assertion.
        """
        if successor.assertion_id == self.assertion_id:
            raise AssertionValidationError("an assertion cannot supersede itself")
        return self._replace(
            superseded_by=successor.assertion_id,
            superseded_at=successor.recorded_at,
        )

    def retract(self, at: datetime | None = None) -> "AssertionV1":
        """Return a copy of this assertion marked retracted (a deletion).

        The value is retained for history but the assertion no longer
        participates in resolution as of times >= *at* (defaults to now).
        The retraction is bitemporal: the view as of a time before *at*
        still shows the assertion as active.
        """
        return self._replace(
            status=AssertionStatus.RETRACTED.value,
            retracted_at=_utc(at) if at is not None else datetime.now(timezone.utc),
        )

    def _replace(self, **changes: Any) -> "AssertionV1":
        """frozen-dataclass replace that skips id recomputation."""
        payload = self.to_dict()
        payload.update(changes)
        return AssertionV1.from_dict(payload)

    # -- Bitemporal predicates --------------------------------------------------

    def known_at(self, as_of: datetime) -> bool:
        """True if the system knew of this assertion at system time *as_of*."""
        return self.recorded_at <= _utc(as_of)

    def valid_at(self, valid_as_of: datetime) -> bool:
        """True if the assertion's valid-time interval covers *valid_as_of*."""
        t = _utc(valid_as_of)
        if self.valid_from > t:
            return False
        if self.valid_to is not None and t >= self.valid_to:
            return False
        return True

    # -- Serialisation -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict; timestamps become ISO-8601 strings."""
        return {
            "schema_version": self.schema_version,
            "assertion_id": self.assertion_id,
            "entity_type": self.entity_type,
            "entity_key": self.entity_key,
            "field": self.field,
            "value": self.value,
            "unit": self.unit,
            "valid_from": _dt_to_iso(self.valid_from),
            "valid_to": _dt_to_iso(self.valid_to),
            "recorded_at": _dt_to_iso(self.recorded_at),
            "source_slug": self.source_slug,
            "provenance_uri": self.provenance_uri,
            "confidence": float(self.confidence),
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "superseded_at": _dt_to_iso(self.superseded_at),
            "status": self.status,
            "retracted_at": _dt_to_iso(self.retracted_at),
            "metadata": self.metadata or {},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AssertionV1":
        return cls(
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            assertion_id=d.get("assertion_id", ""),
            entity_type=d["entity_type"],
            entity_key=d["entity_key"],
            field=d["field"],
            value=d["value"],
            unit=d.get("unit"),
            valid_from=_dt_from_iso(d["valid_from"]),
            valid_to=_dt_from_iso(d.get("valid_to")),
            recorded_at=_dt_from_iso(d["recorded_at"]),
            source_slug=d.get("source_slug", ""),
            provenance_uri=d.get("provenance_uri"),
            confidence=float(d.get("confidence", 1.0)),
            supersedes=d.get("supersedes"),
            superseded_by=d.get("superseded_by"),
            superseded_at=_dt_from_iso(d.get("superseded_at")),
            status=d.get("status", AssertionStatus.ACTIVE.value),
            retracted_at=_dt_from_iso(d.get("retracted_at")),
            metadata=d.get("metadata") or {},
        )

    @classmethod
    def from_json(cls, s: str) -> "AssertionV1":
        return cls.from_dict(json.loads(s))


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolutionV1:
    """The resolved view of one fact as of a given system time.

    ``winner`` is the assertion the platform treats as current truth (or
    ``None`` if no assertion survives the filters).  ``conflicts`` lists
    the losing assertions that coexisted — making disagreement explicit
    rather than silently discarding it.
    """

    entity_type: str
    entity_key: str
    field: str
    as_of: datetime
    valid_as_of: datetime
    winner: AssertionV1 | None
    conflicts: tuple[AssertionV1, ...] = ()
    considered: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_key": self.entity_key,
            "field": self.field,
            "as_of": _dt_to_iso(self.as_of),
            "valid_as_of": _dt_to_iso(self.valid_as_of),
            "winner": self.winner.to_dict() if self.winner else None,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "considered": self.considered,
        }


def _resolution_rank(a: AssertionV1) -> tuple:
    """Total ordering key for picking a winner among live assertions.

    Higher is better.  The final element (negated assertion_id) makes the
    order total and deterministic so the resolved view is reproducible.
    """
    # Reverse the id so that "smaller id wins" under a max() — pick a
    # stable tiebreak by using the id itself and reversing the comparison
    # at the call site; simpler: use the id and take min on ties via the
    # negative-lexicographic trick below.
    return (
        float(a.confidence),
        a.recorded_at,
        a.valid_from,
        # Deterministic final tiebreak: smaller assertion_id wins.  We
        # invert by mapping to a sortable negative string.
        tuple(-ord(c) for c in a.assertion_id),
    )


def resolve(
    assertions: Iterable[AssertionV1],
    entity_type: str,
    entity_key: str,
    field: str,
    as_of: datetime,
    valid_as_of: datetime | None = None,
) -> ResolutionV1:
    """Resolve the current truth of one fact as of system time *as_of*.

    This is the pure, in-memory implementation of the resolution rules
    described in the module docstring.  It is deterministic: the same
    inputs always produce the same winner.

    ``valid_as_of`` defaults to ``as_of`` (i.e. "what is true *now*
    according to what we knew *then*").
    """
    as_of = _utc(as_of)
    valid_as_of = _utc(valid_as_of) if valid_as_of is not None else as_of

    pool = [
        a
        for a in assertions
        if a.entity_type == entity_type
        and a.entity_key == entity_key
        and a.field == field
    ]
    considered = len(pool)

    # 1. Bitemporal filter: what the system knew at as_of.
    known = [a for a in pool if a.known_at(as_of)]
    known_ids = {a.assertion_id for a in known}

    def superseded_by_as_of(a: AssertionV1) -> bool:
        """True if *a* was already superseded at system time *as_of*."""
        if a.superseded_at is not None:
            return a.superseded_at <= as_of
        # No explicit timestamp: fall back to the superseding assertion's
        # presence in the known set.
        return bool(a.superseded_by) and a.superseded_by in known_ids

    # 2. Valid-time filter: assertions true at valid_as_of.
    live = [a for a in known if a.valid_at(valid_as_of)]
    # 3a. Drop retractions known at as_of (deletions).  ``retracted_at`` is
    # the bitemporal marker; ``status`` alone is only a current-state hint,
    # so a row with status=retracted but no timestamp is treated as
    # retracted from the beginning of system time.
    live = [
        a
        for a in live
        if not (
            a.retracted_at is not None
            and a.retracted_at <= as_of
        )
        and not (
            a.retracted_at is None
            and a.status == AssertionStatus.RETRACTED.value
        )
    ]
    # 3b. Drop assertions superseded at or before as_of.  Retracting the
    # successor never resurrects a superseded row.
    live = [a for a in live if not superseded_by_as_of(a)]

    if not live:
        return ResolutionV1(
            entity_type=entity_type,
            entity_key=entity_key,
            field=field,
            as_of=as_of,
            valid_as_of=valid_as_of,
            winner=None,
            conflicts=(),
            considered=considered,
        )

    winner = max(live, key=_resolution_rank)
    conflicts = tuple(a for a in live if a.assertion_id != winner.assertion_id)
    return ResolutionV1(
        entity_type=entity_type,
        entity_key=entity_key,
        field=field,
        as_of=as_of,
        valid_as_of=valid_as_of,
        winner=winner,
        conflicts=conflicts,
        considered=considered,
    )
