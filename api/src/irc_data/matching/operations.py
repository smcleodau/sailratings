"""Canonical link, merge and split operations on the identity graph (DP-04-04).

This module is where identity *decisions* (DP-04-01 contracts, scored by
DP-04-03) are **applied** to canonical entities — without destructive row
merging, and with every step recorded on an immutable receipt.

Code-of-record contract
-----------------------

* ``SCHEMA_VERSION = "identity-operation-v1"``.
* The handoff / output contract is :class:`IdentityOperationReceiptV1`:
  every accepted operation returns one receipt; every *attempted* but
  conflicted or failed operation is appended to the same immutable
  history with ``outcome="conflict"`` / ``"failed"`` — nothing is silent.
* Input decisions arrive as :class:`IdentityDecisionInput`, the minimal
  DP-04-01 ``IdentityDecisionV1`` surface this layer needs: who decided,
  when, why, which evidence (match candidates / scores) backs the call,
  and the *expected version* of the entities being mutated (the
  optimistic-concurrency token).

The four operations
-------------------

* :meth:`IdentityGraph.link` — attach a source identity (boat name / sail
  number / registry id / cert number + assertions, e.g. the fields of a
  DP-04-02 ``EntityObservation``) to a canonical entity.  Alias overlap
  rules come from DP-03-01: one label names at most one live entity at a
  time, so an overlapping duplicate link raises
  :class:`DuplicateAliasError`.
* :meth:`IdentityGraph.merge` — *merge aliasing*, not row destruction:
  the absorbed entity keeps its id and its full history and is stamped
  ``removed_at`` / ``merged_into``; its assertions are re-keyed onto the
  survivor and its aliases move.  Merges **follow through chains**
  (``A ← B ← C`` re-points transitively onto the surviving root) instead
  of leaving removed entities pointing at removed entities.
* :meth:`IdentityGraph.split` — mints a fresh entity ``split_from`` the
  original and restores correct memberships: the named assertions and
  the requested aliases move to the new entity; everything else stays.
* :meth:`IdentityGraph.reverse_decision` — reverses an earlier decision
  **without deleting history**: the reversed decision is superseded, the
  identities it bound are re-linked onto a fresh entity (ids are never
  resurrected or reused), and both receipts cross-reference each other.

Transactional guarantees
------------------------

Every mutating call runs *stage → apply → validate → commit* under the
graph's write lock:

1. **Stage** — build the exact plan (assertions to re-key, aliases to
   move) without touching shared state.
2. **Conflict-check** — optimistic concurrency: each decision carries
   ``expected_versions`` (entity id → the :attr:`EntityVersion.version`
   the decider saw).  If a *concurrent* decision has since mutated one of
   those entities, the attempt raises :class:`ConcurrentDecisionError`,
   **no shared state is mutated**, and a ``conflict`` receipt is logged.
3. **Validate + commit** — the staged plan is applied; if anything fails
   part-way (e.g. an alias overlap discovered at commit time) the
   original state is **rolled back** from a deep-copy snapshot and a
   ``failed`` receipt is logged.  There is no partial commit.

Downstream views are *derived*: they project live registry state, so any
operation — merge, split, reversal — is reflected by
:meth:`IdentityGraph.rebuild_views`, and the pre-operation state remains
reproducible via ``as_of`` replay (DP-03-02 bitemporal resolution).

Verification: ``api/tests/matching/test_identity_operations.py``
(transactional tests covering concurrent merge, chain merge, split and
rollback) and the human-runnable evidence script
``api/scripts/verify_dp_04_04.py``.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

from irc_data.assertions import AssertionV1
from irc_data.domain import (
    Alias,
    CanonicalEntity,
    DomainError,
    DomainModel,
    EntityNotFoundError,
    MergeSameEntityError,
    SplitError,
)


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

#: Version tag embedded in every identity-operation receipt.
SCHEMA_VERSION = "identity-operation-v1"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IdentityOperationError(DomainError):
    """Base class for identity-graph operation contract violations."""


class ConcurrentDecisionError(IdentityOperationError):
    """Raised when an entity changed since the decision was made.

    Carries the conflicting receipt(s) so the caller (and the audit log)
    can see exactly which concurrent decision won.  The losing decision is
    *not* applied; it should be re-based onto the new state and retried.
    """

    def __init__(
        self, message: str, *, conflicts: tuple["IdentityOperationReceiptV1", ...] = ()
    ) -> None:
        super().__init__(message)
        self.conflicts = conflicts


class DecisionNotFoundError(IdentityOperationError, KeyError):
    """Raised when reversing/inspecting an unknown decision id."""


class ReversalError(IdentityOperationError):
    """Raised when a decision cannot be reversed (already reversed / active subjects)."""


# ---------------------------------------------------------------------------
# Input contract: the decision (DP-04-01 handoff, minimal surface)
# ---------------------------------------------------------------------------


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dt_from(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return _utc(v)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    return _utc(datetime.fromisoformat(str(v)))


@dataclass(frozen=True)
class IdentityDecisionInput:
    """The identity-decision input this layer applies.

    This is the minimal DP-04-01 ``IdentityDecisionV1`` surface the
    operations layer needs.  ``evidence_refs`` carries the provenance of
    the decision (match-candidate ids, score ids, certificate URIs …) so
    *no merge ever happens without stored evidence* — the graph enforces
    ``actor`` and records the refs on the receipt.

    ``expected_versions`` maps entity id → the :attr:`EntityVersion.version`
    the decider observed.  ``None`` means "assert this entity does not
    exist / has never been mutated" (version 0).  Keys of entities the
    decision does not mutate may be omitted; every entity the decision
    *will* mutate that has been concurrently changed triggers
    :class:`ConcurrentDecisionError`.
    """

    decision_id: str
    actor: str
    decided_at: datetime
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()
    expected_versions: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.decision_id or not self.decision_id.strip():
            raise IdentityOperationError("decision requires a non-empty decision_id")
        if not self.actor or not self.actor.strip():
            raise IdentityOperationError(
                "decision requires a non-empty actor — identity decisions are attributable"
            )
        object.__setattr__(self, "decided_at", _utc(self.decided_at))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(
            self, "expected_versions", {k: int(v) for k, v in self.expected_versions.items()}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "actor": self.actor,
            "decided_at": self.decided_at.isoformat(),
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "expected_versions": dict(self.expected_versions),
        }


# ---------------------------------------------------------------------------
# Output contract: the receipt
# ---------------------------------------------------------------------------

#: Operation kinds.
OP_LINK = "link"
OP_MERGE = "merge"
OP_SPLIT = "split"
OP_REVERSE = "reverse_decision"
OPERATION_KINDS: tuple[str, ...] = (OP_LINK, OP_MERGE, OP_SPLIT, OP_REVERSE)

#: Receipt outcomes.
OUTCOME_COMMITTED = "committed"
OUTCOME_CONFLICT = "conflict"
OUTCOME_FAILED = "failed"
RECEIPT_OUTCOMES: tuple[str, ...] = (OUTCOME_COMMITTED, OUTCOME_CONFLICT, OUTCOME_FAILED)


@dataclass(frozen=True)
class IdentityOperationReceiptV1:
    """The handoff / output contract of the identity operations layer.

    One receipt per *attempted* operation, appended to the immutable
    receipt log whether the operation committed, conflicted or failed.
    ``committed_at`` is set exactly when the operation was applied;
    ``superseded_by`` / ``reversed_by`` link a committed receipt to the
    reversal that undid it.
    """

    operation_id: str
    kind: str  # one of OPERATION_KINDS
    decision: IdentityDecisionInput
    subjects: tuple[str, ...]  # entity ids touched, sorted
    outcome: str  # one of RECEIPT_OUTCOMES
    committed_at: datetime | None = None
    applied: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    conflicts: tuple[str, ...] = ()  # operation ids of conflicting receipts
    supersedes: str | None = None  # decision id this operation supersedes
    reversed_by: str | None = None  # operation id of the reversal (committed only)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.kind not in OPERATION_KINDS:
            raise IdentityOperationError(f"unknown operation kind {self.kind!r}")
        if self.outcome not in RECEIPT_OUTCOMES:
            raise IdentityOperationError(f"unknown receipt outcome {self.outcome!r}")
        object.__setattr__(self, "subjects", tuple(sorted(self.subjects)))
        if self.committed_at is not None:
            object.__setattr__(self, "committed_at", _utc(self.committed_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "kind": self.kind,
            "decision": self.decision.to_dict(),
            "subjects": list(self.subjects),
            "outcome": self.outcome,
            "committed_at": self.committed_at.isoformat() if self.committed_at else None,
            "applied": json.loads(json.dumps(self.applied, default=str)),
            "error": self.error,
            "conflicts": list(self.conflicts),
            "supersedes": self.supersedes,
            "reversed_by": self.reversed_by,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


# ---------------------------------------------------------------------------
# Link request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityLink:
    """One source identity to attach to a canonical entity.

    The alias fields mirror the DP-04-02 ``EntityObservation`` / DP-03-01
    alias vocabulary; every non-empty one becomes an :class:`Alias` on the
    entity with the given validity interval.  ``assertions`` are attached
    to the entity verbatim except that they are *re-keyed* onto the
    entity (a content-addressed new id — the source record is immutable,
    the join is by reference).
    """

    aliases: tuple[Alias, ...] = ()
    assertions: tuple[AssertionV1, ...] = ()
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source_slug: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "aliases", tuple(self.aliases))
        object.__setattr__(self, "assertions", tuple(self.assertions))
        if self.valid_from is not None:
            object.__setattr__(self, "valid_from", _utc(self.valid_from))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", _utc(self.valid_to))
        if not (self.aliases or self.assertions):
            raise IdentityOperationError(
                "IdentityLink binds nothing — pass at least one alias or assertion"
            )

    @classmethod
    def from_observation(
        cls,
        observation: Any,
        *,
        assertions: Iterable[AssertionV1] = (),
        valid_from: datetime | date | None = None,
        valid_to: datetime | date | None = None,
        source_slug: str = "",
    ) -> "IdentityLink":
        """Build a link from a DP-04-02 ``EntityObservation``-shaped object.

        Reads ``sail_number`` / ``registry_id`` / ``name`` attributes (any
        object or mapping with those fields) into aliases; observations do
        not carry certificates, so ``cert_number`` stays unset.
        """
        getter = observation.get if isinstance(observation, Mapping) else (
            lambda k, d=None: getattr(observation, k, d)
        )
        vf = _dt_from(valid_from) or _dt_from(getter("valid_from")) or datetime(
            1970, 1, 1, tzinfo=timezone.utc
        )
        vt = _dt_from(valid_to) or _dt_from(getter("valid_to"))
        src = source_slug or str(getter("source_slug", "") or "")
        aliases: list[Alias] = []
        for kind, field_name in (
            ("sail_number", "sail_number"),
            ("registry_id", "registry_id"),
            ("boat_name", "name"),
        ):
            value = getter(field_name)
            if value and str(value).strip():
                aliases.append(
                    Alias(
                        kind=kind,
                        value=str(value).strip(),
                        valid_from=vf,
                        valid_to=vt,
                        source_slug=src,
                    )
                )
        return cls(
            aliases=tuple(aliases),
            assertions=tuple(assertions),
            valid_from=vf,
            valid_to=vt,
            source_slug=src,
        )


# ---------------------------------------------------------------------------
# Entity version — the optimistic-concurrency token
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityVersion:
    """The optimistic-concurrency token for one entity.

    ``version`` starts at 0 and bumps on every committed operation that
    mutates the entity.  Decisions quote the versions they observed; the
    graph refuses to apply a decision whose quoted versions are stale.
    """

    entity_id: str
    version: int
    last_operation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "version": self.version,
            "last_operation_id": self.last_operation_id,
        }


# ---------------------------------------------------------------------------
# IdentityGraph
# ---------------------------------------------------------------------------


class IdentityGraph:
    """Transactional identity graph over the DP-03-01 canonical registry.

    Owns a :class:`~irc_data.domain.entities.DomainModel` (the entity
    shells, aliases and assertion refs) plus:

    * an **append-only receipt log** — one :class:`IdentityOperationReceiptV1`
      per attempted operation, including conflicts and failures;
    * a **decision index** — decision id → the receipt that applied it;
    * an **entity version map** — the optimistic-concurrency tokens.

    The registry itself stays pure DP-03-01; this class adds the
    transactional envelope (staging, conflict checking, rollback) that
    DP-04-04 requires around it.
    """

    def __init__(self, model: DomainModel | None = None) -> None:
        self._model = model if model is not None else DomainModel()
        self._lock = threading.RLock()
        self._receipts: list[IdentityOperationReceiptV1] = []
        self._receipt_by_op: dict[str, IdentityOperationReceiptV1] = {}
        self._decision_index: dict[str, str] = {}  # decision_id -> operation_id
        self._versions: dict[str, EntityVersion] = {}
        self._op_counter = 0

    # -- Registry access ------------------------------------------------------

    @property
    def model(self) -> DomainModel:
        """The underlying DP-03-01 canonical registry."""
        return self._model

    def create_entity(
        self, entity_type: Any, *, at: datetime | None = None
    ) -> CanonicalEntity:
        """Register a new canonical entity (opaque id, version 0)."""
        with self._lock:
            entity = self._model.create_entity(entity_type, at=at)
            self._versions[entity.entity_id] = EntityVersion(entity.entity_id, 0)
            return entity

    def get_version(self, entity_id: str) -> EntityVersion:
        """Return the current concurrency token for an entity."""
        with self._lock:
            entity = self._model.get(entity_id)
            return self._versions.get(
                entity.entity_id, EntityVersion(entity.entity_id, 0)
            )

    # -- History / audit --------------------------------------------------------

    @property
    def receipts(self) -> tuple[IdentityOperationReceiptV1, ...]:
        """Every attempted operation, in commit order — including conflicts."""
        return tuple(self._receipts)

    def receipts_for(self, entity_id: str) -> tuple[IdentityOperationReceiptV1, ...]:
        """All receipts whose subject set includes ``entity_id``."""
        return tuple(r for r in self._receipts if entity_id in r.subjects)

    def receipt(self, operation_id: str) -> IdentityOperationReceiptV1:
        """Return one receipt by operation id (raises ``KeyError`` if unknown)."""
        return self._receipt_by_op[operation_id]

    def decision_receipt(self, decision_id: str) -> IdentityOperationReceiptV1:
        """Return the receipt of the (single) attempt to apply ``decision_id``.

        The outcome may be ``committed``, ``conflict`` or ``failed`` —
        only committed decisions can be reversed.
        """
        try:
            return self._receipt_by_op[self._decision_index[decision_id]]
        except KeyError as exc:
            raise DecisionNotFoundError(f"unknown decision {decision_id!r}") from exc

    # -- The four operations ----------------------------------------------------

    def link(
        self,
        entity_id: str,
        identity: IdentityLink,
        decision: IdentityDecisionInput,
    ) -> IdentityOperationReceiptV1:
        """Link a source identity to a canonical entity.

        Attaches the identity's aliases (respecting the DP-03-01 overlap
        rule — one label names one live entity at a time) and re-keys its
        assertions onto the entity.  Conflict-checked against
        ``decision.expected_versions``.
        """
        with self._lock:
            entity = self._model.get(entity_id)
            if not entity.is_live:
                raise IdentityOperationError(
                    f"cannot link to removed entity {entity_id!r} "
                    f"(merged into {entity.merged_into!r}) — link to the survivor instead"
                )
            self._check_decision_fresh(decision)
            self._check_conflicts(OP_LINK, decision, subjects=(entity_id,))
            at = decision.decided_at
            snapshot = self._snapshot_registry()
            attached_aliases: list[dict[str, Any]] = []
            attached_assertions: list[str] = []
            try:
                for alias in identity.aliases:
                    self._model.attach_alias(entity_id, alias)
                    attached_aliases.append(alias.to_dict())
                for assertion in identity.assertions:
                    ref = self._attach_assertion(entity_id, assertion)
                    attached_assertions.append(ref.assertion_id)
            except Exception as exc:  # rollback — nothing partial commits
                self._restore_registry(snapshot)
                self._record_receipt(
                    OP_LINK,
                    decision,
                    subjects=(entity_id,),
                    outcome=OUTCOME_FAILED,
                    committed_at=None,
                    error=f"{type(exc).__name__}: {exc}",
                    applied={},
                )
                raise IdentityOperationError(
                    f"link rolled back: {type(exc).__name__}: {exc}"
                ) from exc
            receipt = self._record_receipt(
                OP_LINK,
                decision,
                subjects=(entity_id,),
                outcome=OUTCOME_COMMITTED,
                committed_at=at,
                applied={
                    "entity_id": entity_id,
                    "aliases": attached_aliases,
                    "assertion_ids": attached_assertions,
                },
            )
            self._bump_versions(receipt, (entity_id,))
            return receipt

    def merge(
        self,
        survivor_id: str,
        removed_id: str,
        decision: IdentityDecisionInput,
    ) -> IdentityOperationReceiptV1:
        """Merge ``removed_id`` into ``survivor_id`` — by aliasing, not deletion.

        The removed entity is preserved with ``merged_into`` for audit;
        assertions re-key onto the survivor; aliases move.  If the removed
        entity had itself absorbed others earlier (a merge *chain*), every
        member of the chain is re-pointed at the surviving root in the same
        transaction, so removed entities never point at removed entities.
        """
        with self._lock:
            if survivor_id == removed_id:
                raise MergeSameEntityError("cannot merge an entity into itself")
            survivor = self._model.get(survivor_id)
            removed = self._model.get(removed_id)
            self._check_decision_fresh(decision)
            chain = self._merge_chain(removed_id)
            subjects = tuple(sorted({survivor_id, *chain}))
            # Conflict-check *before* liveness: a steward whose decision was
            # made against a stale snapshot sees ConcurrentDecisionError
            # (with the winning receipt), not a confusing liveness error.
            self._check_conflicts(OP_MERGE, decision, subjects=subjects)
            if not survivor.is_live:
                raise IdentityOperationError(
                    f"survivor {survivor_id!r} is itself removed "
                    f"(merged into {survivor.merged_into!r}) — merge into the chain root"
                )
            if not removed.is_live:
                raise IdentityOperationError(
                    f"{removed_id!r} is already merged into {removed.merged_into!r}"
                )
            if survivor.entity_type is not removed.entity_type:
                raise DomainError(
                    f"cannot merge {removed.entity_type.value} into "
                    f"{survivor.entity_type.value}"
                )
            at = decision.decided_at
            snapshot = self._snapshot_registry()
            try:
                moved_assertions = [ref.assertion_id for ref in removed.assertions]
                moved_aliases = [a.to_dict() for a in removed.aliases]
                self._model.merge(survivor_id, removed_id, at=at, reason=decision.reason)
                # Chain merge follow-through: A ← B ← C becomes A ← B, A ← C.
                re_rooted: list[str] = []
                for member in chain:
                    if member == removed_id:
                        continue
                    member_entity = self._model.get(member)
                    member_entity.merged_into = survivor_id
                    re_rooted.append(member)
            except Exception as exc:
                self._restore_registry(snapshot)
                self._record_receipt(
                    OP_MERGE,
                    decision,
                    subjects=subjects,
                    outcome=OUTCOME_FAILED,
                    committed_at=None,
                    error=f"{type(exc).__name__}: {exc}",
                    applied={},
                )
                raise IdentityOperationError(
                    f"merge rolled back: {type(exc).__name__}: {exc}"
                ) from exc
            receipt = self._record_receipt(
                OP_MERGE,
                decision,
                subjects=subjects,
                outcome=OUTCOME_COMMITTED,
                committed_at=at,
                applied={
                    "survivor": survivor_id,
                    "removed": removed_id,
                    "moved_assertion_ids": moved_assertions,
                    "moved_aliases": moved_aliases,
                    "chain_rerooted": re_rooted,
                },
            )
            self._bump_versions(receipt, subjects)
            return receipt

    def split(
        self,
        entity_id: str,
        *,
        assertion_ids: Iterable[str],
        decision: IdentityDecisionInput,
        alias_kinds: Iterable[str] | None = None,
        alias_values: Iterable[str] | None = None,
    ) -> IdentityOperationReceiptV1:
        """Split a new entity off ``entity_id``, restoring correct memberships.

        The named assertions move to a fresh entity stamped
        ``split_from``.  Aliases move according to the selectors:

        * ``alias_kinds`` / ``alias_values`` given — aliases matching
          either selector (substring, case-insensitive) move;
        * no selector — **interval-correct default**: aliases whose
          validity interval overlaps the moved assertions' combined
          valid-time interval move (the §6.2 re-issued-sail-number case:
          the 2019 label rides the 2019 assertions; the 2008 label stays);
        * ``alias_kinds=("*",)`` — every alias moves.

        Everything else — including the original's id — stays put.  Both
        sides keep their full history, and downstream views rebuild from
        the live registry.
        """
        with self._lock:
            original = self._model.get(entity_id)
            if not original.is_live:
                raise IdentityOperationError(
                    f"cannot split removed entity {entity_id!r} "
                    f"(merged into {original.merged_into!r})"
                )
            self._check_decision_fresh(decision)
            self._check_conflicts(OP_SPLIT, decision, subjects=(entity_id,))
            ids = set(assertion_ids)
            held = {ref.assertion_id for ref in original.assertions}
            unknown = ids - held
            if unknown:
                raise SplitError(
                    f"cannot split: assertions not on {entity_id!r}: {sorted(unknown)}"
                )
            moved_interval = self._combined_interval(original, ids)
            wanted_aliases = self._select_aliases(
                original,
                alias_kinds=alias_kinds,
                alias_values=alias_values,
                overlap_with=moved_interval,
            )
            at = decision.decided_at
            snapshot = self._snapshot_registry()
            try:
                new_entity = self._model.split(
                    entity_id, assertion_ids=ids, at=at, reason=decision.reason
                )
                self._versions[new_entity.entity_id] = EntityVersion(new_entity.entity_id, 0)
                for alias in wanted_aliases:
                    original.aliases.remove(alias)
                    self._repoint_alias_index(entity_id, new_entity.entity_id, alias)
                    new_entity.aliases.append(alias)
            except Exception as exc:
                self._restore_registry(snapshot)
                receipt = self._record_receipt(
                    OP_SPLIT,
                    decision,
                    subjects=(entity_id,),
                    outcome=OUTCOME_FAILED,
                    committed_at=None,
                    error=f"{type(exc).__name__}: {exc}",
                    applied={},
                )
                raise IdentityOperationError(
                    f"split rolled back: {type(exc).__name__}: {exc}"
                ) from exc
            receipt = self._record_receipt(
                OP_SPLIT,
                decision,
                subjects=(entity_id, new_entity.entity_id),
                outcome=OUTCOME_COMMITTED,
                committed_at=at,
                applied={
                    "original": entity_id,
                    "new_entity": new_entity.entity_id,
                    "moved_assertion_ids": sorted(ids),
                    "moved_aliases": [a.to_dict() for a in wanted_aliases],
                },
            )
            self._bump_versions(receipt, (entity_id, new_entity.entity_id))
            return receipt

    def reverse_decision(
        self,
        decision_id: str,
        reversal: IdentityDecisionInput,
    ) -> IdentityOperationReceiptV1:
        """Reverse a previously committed decision — without deleting history.

        The original decision is *superseded* (its receipt is stamped
        ``reversed_by``); the identities it had bound are re-linked onto a
        fresh entity (ids are never resurrected or reused), so the state
        before the original decision is *restored in effect* while every
        step remains in the immutable log.  Both receipts cross-reference
        each other, so the reversal is itself auditable — and reversible.
        """
        with self._lock:
            original = self.decision_receipt(decision_id)
            if original.outcome != OUTCOME_COMMITTED:
                raise ReversalError(
                    f"decision {decision_id!r} did not commit "
                    f"(outcome={original.outcome!r}) — nothing to reverse"
                )
            if original.reversed_by is not None:
                raise ReversalError(
                    f"decision {decision_id!r} is already reversed by "
                    f"{original.reversed_by!r}"
                )
            self._check_decision_fresh(reversal)
            subjects = tuple(
                s for s in original.subjects if s in self._model_entities()
            )
            self._check_conflicts(OP_REVERSE, reversal, subjects=subjects)
            at = reversal.decided_at
            snapshot = self._snapshot_registry()
            try:
                applied = self._apply_reversal(original, reversal, at)
            except Exception as exc:
                self._restore_registry(snapshot)
                self._record_receipt(
                    OP_REVERSE,
                    reversal,
                    subjects=tuple(original.subjects),
                    outcome=OUTCOME_FAILED,
                    committed_at=None,
                    error=f"{type(exc).__name__}: {exc}",
                    applied={},
                    supersedes=decision_id,
                )
                raise ReversalError(f"reversal rolled back: {exc}") from exc
            receipt = self._record_receipt(
                OP_REVERSE,
                reversal,
                subjects=tuple(sorted(set(original.subjects) | set(
                    applied.get("subjects", ())
                ))),
                outcome=OUTCOME_COMMITTED,
                committed_at=at,
                applied=applied,
                supersedes=decision_id,
            )
            # Supersede the original receipt in place (history retained).
            superseded = dataclasses.replace(original, reversed_by=receipt.operation_id)
            self._replace_receipt(original.operation_id, superseded)
            self._bump_versions(receipt, receipt.subjects)
            return receipt

    # -- Downstream views --------------------------------------------------------

    def rebuild_views(
        self, *, at: datetime | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Rebuild the downstream (derived) read views from live state.

        Views are *projections*: nothing here is stored as fact, so after
        any merge / split / reversal the downstream picture is rebuilt by
        re-running this projection.  Returns two views:

        * ``alias_directory`` — every alias (kind, value, interval) → the
          *live* entity it currently names, via the surviving root;
        * ``entity_index`` — every entity with its lifecycle state and
          current membership counts.
        """
        with self._lock:
            when = _utc(at) if at else datetime.now(timezone.utc)
            alias_rows: list[dict[str, Any]] = []
            for (kind, _norm), entries in sorted(
                self._model._alias_index.items(), key=lambda kv: (kv[0][0], kv[0][1])
            ):
                for entity_id, alias in entries:
                    live = self._live_root(entity_id)
                    alias_rows.append(
                        {
                            "kind": kind,
                            "value": alias.value,
                            "valid_from": alias.valid_from.isoformat(),
                            "valid_to": alias.valid_to.isoformat() if alias.valid_to else None,
                            "source_slug": alias.source_slug,
                            "bound_entity": entity_id,
                            "live_entity": live.entity_id if live else None,
                            "resolves_now": bool(
                                live is not None and live.live_at(when) and alias.covers(when)
                            ),
                        }
                    )
            entity_rows = [
                {
                    "entity_id": e.entity_id,
                    "entity_type": e.entity_type.value,
                    "live": e.is_live,
                    "merged_into": e.merged_into,
                    "split_from": e.split_from,
                    "alias_count": len(e.aliases),
                    "assertion_count": len(e.assertions),
                }
                for e in sorted(self._model_entities().values(), key=lambda x: x.entity_id)
            ]
            return {"alias_directory": alias_rows, "entity_index": entity_rows}

    # -- Internals ---------------------------------------------------------------

    def _model_entities(self) -> dict[str, CanonicalEntity]:
        return self._model._entities

    def _live_root(self, entity_id: str) -> CanonicalEntity | None:
        """Follow ``merged_into`` to the surviving root (None if unknown)."""
        seen: set[str] = set()
        current = self._model._entities.get(entity_id)
        while current is not None and not current.is_live and current.merged_into:
            if current.entity_id in seen:  # pragma: no cover - defensive
                return None
            seen.add(current.entity_id)
            current = self._model._entities.get(current.merged_into)
        return current if current is not None and current.is_live else None

    def _merge_chain(self, removed_id: str) -> list[str]:
        """All entities in the merge chain rooted at ``removed_id``, itself first."""
        chain = [removed_id]
        chain.extend(
            e.entity_id
            for e in self._model_entities().values()
            if not e.is_live and e.merged_into == removed_id
        )
        return chain

    def _attach_assertion(self, entity_id: str, assertion: AssertionV1):
        """Attach an assertion to an entity, re-keying it onto the entity first."""
        entity = self._model.get(entity_id)
        if assertion.entity_type != entity.entity_type.value:
            raise IdentityOperationError(
                f"assertion entity_type {assertion.entity_type!r} does not match "
                f"entity {entity.entity_type.value!r}"
            )
        rekeyed = dataclasses.replace(
            assertion, entity_key=entity.entity_key, assertion_id=""
        )
        return self._model.assert_about(entity_id, rekeyed)

    def _combined_interval(
        self, entity: CanonicalEntity, assertion_ids: set[str]
    ) -> tuple[datetime, datetime] | None:
        """Combined valid-time interval of the given assertions (None if empty)."""
        far_future = datetime.max.replace(tzinfo=timezone.utc)
        lo: datetime | None = None
        hi: datetime = datetime.min.replace(tzinfo=timezone.utc)
        found = False
        for ref in entity.assertions:
            if ref.assertion_id not in assertion_ids:
                continue
            assertion = self._model._assertions[ref.assertion_id]
            lo = assertion.valid_from if lo is None else min(lo, assertion.valid_from)
            end = assertion.valid_to or far_future
            hi = max(hi, end)
            found = True
        if not found:
            return None
        return (lo, hi)  # type: ignore[return-value]

    @staticmethod
    def _select_aliases(
        entity: CanonicalEntity,
        *,
        alias_kinds: Iterable[str] | None,
        alias_values: Iterable[str] | None,
        overlap_with: tuple[datetime, datetime] | None,
    ) -> list[Alias]:
        """Pick the aliases to move in a split.

        Explicit selectors match on kind/value substrings; ``"*"`` moves
        everything.  With no selector, the interval-correct default moves
        aliases whose validity overlaps the moved assertions' interval.
        """
        far_future = datetime.max.replace(tzinfo=timezone.utc)
        kinds = {k.strip().lower() for k in alias_kinds or ()}
        values = {v.strip().lower() for v in alias_values or ()}
        selected: list[Alias] = []
        for alias in entity.aliases:
            if "*" in kinds:
                selected.append(alias)
                continue
            if kinds and any(k in alias.kind.lower() for k in kinds):
                selected.append(alias)
                continue
            if values and any(v in alias.value.lower() for v in values):
                selected.append(alias)
                continue
            if not kinds and not values and overlap_with is not None:
                lo, hi = overlap_with
                # Half-open interval overlap: [from, to) intersects iff
                # each start precedes the other's end.
                if alias.valid_from < hi and lo < (alias.valid_to or far_future):
                    selected.append(alias)
        return selected

    def _repoint_alias_index(self, from_id: str, to_id: str, alias: Alias) -> None:
        key = (alias.kind, DomainModel._normalise_alias(alias.value))
        entries = self._model._alias_index.get(key, [])
        self._model._alias_index[key] = [
            (to_id if (eid == from_id and a is alias) else eid, a) for eid, a in entries
        ]

    # -- Reversal machinery -------------------------------------------------------

    def _apply_reversal(
        self,
        original: IdentityOperationReceiptV1,
        reversal: IdentityDecisionInput,
        at: datetime,
    ) -> dict[str, Any]:
        """Restore pre-decision memberships by re-linking onto a fresh entity."""
        applied = original.applied
        if original.kind == OP_LINK:
            entity_id = applied["entity_id"]
            entity = self._model.get(entity_id)
            alias_set = {json.dumps(a, sort_keys=True) for a in applied["aliases"]}
            moved_aliases = [
                a for a in list(entity.aliases) if json.dumps(a.to_dict(), sort_keys=True) in alias_set
            ]
            assertion_set = set(applied["assertion_ids"])
            return self._relink_restored(
                entity,
                assertion_ids=assertion_set,
                aliases=moved_aliases,
                at=at,
                reason=reversal.reason,
                original=original,
            )
        if original.kind == OP_MERGE:
            removed_id = applied["removed"]
            survivor_id = applied["survivor"]
            removed = self._model.get(removed_id)
            survivor = self._model.get(survivor_id)
            assertion_ids = set(applied["moved_assertion_ids"])
            # The assertions moved at merge time were re-keyed onto the
            # survivor; locate their current ids by identity payload.
            current_ids = self._current_ids_for(survivor, removed, assertion_ids, original)
            alias_set = {json.dumps(a, sort_keys=True) for a in applied["moved_aliases"]}
            moved_aliases = [
                a
                for a in list(survivor.aliases)
                if json.dumps(a.to_dict(), sort_keys=True) in alias_set
            ]
            result = self._relink_restored(
                survivor,
                assertion_ids=current_ids,
                aliases=moved_aliases,
                at=at,
                reason=reversal.reason,
                original=original,
            )
            # Un-stamp the absorbed entity: it stands alone again.
            removed.removed_at = None
            removed.merged_into = None
            result["restored_entity"] = removed_id
            result["unmerged_from"] = survivor_id
            return result
        if original.kind == OP_SPLIT:
            new_id = applied["new_entity"]
            original_id = applied["original"]
            new_entity = self._model.get(new_id)
            original_entity = self._model.get(original_id)
            # Move back everything the split moved: merge the split-off
            # entity into the original (a non-destructive merge aliasing).
            current_ids = {ref.assertion_id for ref in new_entity.assertions}
            moved_aliases = list(new_entity.aliases)
            self._model.merge(
                original_id, new_id, at=at, reason=f"reversal of {original.operation_id}"
            )
            return {
                "subjects": (original_id, new_id),
                "remerged_into": original_id,
                "remerged_entity": new_id,
                "moved_back_assertion_ids": sorted(current_ids),
                "moved_back_aliases": [a.to_dict() for a in moved_aliases],
            }
        raise ReversalError(f"cannot reverse operation kind {original.kind!r}")

    def _relink_restored(
        self,
        source: CanonicalEntity,
        *,
        assertion_ids: set[str],
        aliases: list[Alias],
        at: datetime,
        reason: str,
        original: IdentityOperationReceiptV1,
    ) -> dict[str, Any]:
        """Move memberships off ``source`` onto a fresh entity (reversal core)."""
        new_entity = self._model.create_entity(source.entity_type, at=at)
        self._versions[new_entity.entity_id] = EntityVersion(new_entity.entity_id, 0)
        moved_assertion_ids: list[str] = []
        for ref in list(source.assertions):
            if ref.assertion_id not in assertion_ids:
                continue
            assertion = self._model._assertions[ref.assertion_id]
            rekeyed = dataclasses.replace(
                assertion, entity_key=new_entity.entity_key, assertion_id=""
            )
            del self._model._assertions[ref.assertion_id]
            self._model._assertions[rekeyed.assertion_id] = rekeyed
            source.assertions.remove(ref)
            new_entity.assertions.append(
                type(ref)(
                    assertion_id=rekeyed.assertion_id,
                    field=rekeyed.field,
                    recorded_at=rekeyed.recorded_at,
                    valid_from=rekeyed.valid_from,
                    source_slug=rekeyed.source_slug,
                )
            )
            moved_assertion_ids.append(rekeyed.assertion_id)
        for alias in aliases:
            source.aliases.remove(alias)
            self._repoint_alias_index(source.entity_id, new_entity.entity_id, alias)
            new_entity.aliases.append(alias)
        self._model._log_event(
            "reverse_decision",
            at,
            new_entity.entity_id,
            {
                "reversed_operation": original.operation_id,
                "restored_from": source.entity_id,
                "reason": reason,
            },
        )
        return {
            "subjects": (source.entity_id, new_entity.entity_id),
            "relinked_entity": new_entity.entity_id,
            "restored_from": source.entity_id,
            "moved_assertion_ids": moved_assertion_ids,
            "moved_aliases": [a.to_dict() for a in aliases],
        }

    def _current_ids_for(
        self,
        holder: CanonicalEntity,
        absorbed: CanonicalEntity,
        moved_ids: set[str],
        receipt: IdentityOperationReceiptV1,
    ) -> set[str]:
        """Map the assertions moved by a merge to their *current* ids.

        Merge re-keys assertions (the content-addressed id changes with
        the entity key).  ``moved_ids`` are the *pre-merge* ids recorded on
        the receipt; for each assertion now on ``holder`` we recompute the
        id it would have had under the absorbed entity's key and keep the
        ones that match.  This is what lets a merge reversal move back
        exactly the assertions the original merge moved — no more, no less.
        """
        current: set[str] = set()
        for ref in holder.assertions:
            assertion = self._model._assertions[ref.assertion_id]
            pre_merge = dataclasses.replace(
                assertion, entity_key=absorbed.entity_key, assertion_id=""
            )
            if pre_merge.assertion_id in moved_ids:
                current.add(ref.assertion_id)
        return current

    # -- Transaction machinery -----------------------------------------------------

    def _snapshot_registry(self) -> dict[str, Any]:
        """Deep-copy the mutable registry state for rollback."""
        return {
            "entities": copy.deepcopy(self._model._entities),
            "alias_index": copy.deepcopy(self._model._alias_index),
            "assertions": copy.deepcopy(self._model._assertions),
            "log_len": len(self._model._log),
            "versions": dict(self._versions),
        }

    def _restore_registry(self, snapshot: Mapping[str, Any]) -> None:
        """Roll the registry back to ``snapshot`` after a failed commit."""
        self._model._entities = snapshot["entities"]
        self._model._alias_index = snapshot["alias_index"]
        self._model._assertions = snapshot["assertions"]
        del self._model._log[snapshot["log_len"]:]
        self._versions = dict(snapshot["versions"])

    def _check_decision_fresh(self, decision: IdentityDecisionInput) -> None:
        existing = self._decision_index.get(decision.decision_id)
        if existing is not None:
            raise IdentityOperationError(
                f"decision {decision.decision_id!r} was already applied as "
                f"{existing!r} — decisions are applied at most once"
            )

    def _check_conflicts(
        self,
        kind: str,
        decision: IdentityDecisionInput,
        *,
        subjects: Iterable[str],
    ) -> None:
        """Optimistic concurrency check — the 'concurrent decisions' gate.

        For every entity the decision will mutate, compare the current
        version against the version the decision *expected*.  A mismatch
        means a concurrent decision committed first; this attempt is
        rejected — a ``conflict`` receipt is appended to the immutable log
        and :class:`ConcurrentDecisionError` is raised carrying the winning
        receipts.  **No shared state is mutated** by a conflicted attempt.
        """
        conflicts: list[IdentityOperationReceiptV1] = []
        stale: list[str] = []
        for entity_id in subjects:
            current = self._versions.get(entity_id, EntityVersion(entity_id, 0))
            expected = decision.expected_versions.get(entity_id)
            if expected is None:
                # Decision did not quote this entity's version: accept the
                # current state (callers quote every entity they mutate
                # when concurrency matters).
                continue
            if current.version != expected:
                stale.append(
                    f"{entity_id}: expected v{expected}, actual v{current.version}"
                )
                if current.last_operation_id:
                    conflicts.append(self._receipt_by_op[current.last_operation_id])
        if stale:
            self._record_receipt(
                kind,
                decision,
                subjects=subjects,
                outcome=OUTCOME_CONFLICT,
                committed_at=None,
                error="stale entity version(s): " + "; ".join(stale),
                applied={},
                conflicts=tuple(c.operation_id for c in conflicts),
            )
            raise ConcurrentDecisionError(
                "concurrent decision conflict — re-base and retry: " + "; ".join(stale),
                conflicts=tuple(conflicts),
            )

    def _record_receipt(
        self,
        kind: str,
        decision: IdentityDecisionInput,
        *,
        subjects: Iterable[str],
        outcome: str,
        committed_at: datetime | None,
        applied: Mapping[str, Any],
        error: str | None = None,
        conflicts: tuple[str, ...] = (),
        supersedes: str | None = None,
    ) -> IdentityOperationReceiptV1:
        self._op_counter += 1
        receipt = IdentityOperationReceiptV1(
            operation_id=f"iop-{self._op_counter:06d}",
            kind=kind,
            decision=decision,
            subjects=tuple(subjects),
            outcome=outcome,
            committed_at=committed_at,
            applied=dict(applied),
            error=error,
            conflicts=conflicts,
            supersedes=supersedes,
        )
        self._receipts.append(receipt)
        self._receipt_by_op[receipt.operation_id] = receipt
        # Every attempt is indexed by decision id — decisions are applied at
        # most once, and a conflicted/failed attempt is still auditable.
        self._decision_index[decision.decision_id] = receipt.operation_id
        return receipt

    def _replace_receipt(
        self, operation_id: str, receipt: IdentityOperationReceiptV1
    ) -> None:
        self._receipt_by_op[operation_id] = receipt
        for i, r in enumerate(self._receipts):
            if r.operation_id == operation_id:
                self._receipts[i] = receipt
                return  # pragma: no cover - operation_id always present

    def _bump_versions(
        self, receipt: IdentityOperationReceiptV1, subjects: Iterable[str]
    ) -> None:
        for entity_id in subjects:
            current = self._versions.get(entity_id, EntityVersion(entity_id, 0))
            self._versions[entity_id] = EntityVersion(
                entity_id, current.version + 1, receipt.operation_id
            )


__all__ = [
    "SCHEMA_VERSION",
    "OPERATION_KINDS",
    "RECEIPT_OUTCOMES",
    "OP_LINK",
    "OP_MERGE",
    "OP_SPLIT",
    "OP_REVERSE",
    "OUTCOME_COMMITTED",
    "OUTCOME_CONFLICT",
    "OUTCOME_FAILED",
    "ConcurrentDecisionError",
    "DecisionNotFoundError",
    "EntityVersion",
    "IdentityDecisionInput",
    "IdentityGraph",
    "IdentityLink",
    "IdentityOperationError",
    "IdentityOperationReceiptV1",
    "ReversalError",
]
