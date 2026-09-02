"""Human adjudication queue and evidence view (DP-04-05).

Goal
----

Use humans only where **uncertainty** or **cost** warrants it.  The
automatic resolution pipeline (DP-04-03 pairwise scoring, DP-04-04
auto-merge/auto-reject policy) handles the confident bulk; this module
owns everything that is left over: a *prioritised* review queue of
uncertain and/or high-impact candidate pairs, an *evidence view*
(side-by-side source evidence, score explanation, downstream impact,
reversible actions), and the **decision write-path** that adjudication
feeds.

Design invariants (acceptance criteria)
---------------------------------------

1. **Decision writes through the same contract as automatic
   resolution.**  :class:`DecisionRequestV1` is the *single* write
   contract.  The automatic resolver calls :func:`adjudicate` with
   ``decided_by="system:resolver"``; a human clicking "Merge" or
   "Keep separate" in the MatchCard UI calls it with
   ``decided_by="human:<id>"``.  Both produce the identical
   :class:`ResolutionRecordV1` output contract, share the same status
   machine and the same reversibility guarantees.
2. **Double review is required for high-impact merges.**  A
   ``merge`` decision against a candidate whose impact tier is ``high``
   can never be applied by one human: the first decision is recorded as
   a *vote* and the case stays ``awaiting_second_review``; a *second,
   distinct* reviewer applying the same decision applies it.  A
   conflicting second decision escalates the case instead of silently
   resolving it.  The same-reviewers guard is enforced on the contract,
   not by convention.

Humans are only ever shown candidates where uncertainty or cost
warrants it — candidates the auto-policy would resolve (confident
match / confident non-match, low impact) are never queued
(:func:`AdjudicationQueue.enqueue` returns ``None`` for them).

The module is persistence-agnostic: :class:`AdjudicationStore`
serialises to/from plain dicts so tests, the verification harness and
the API can use an in-memory instance today and a SQL-backed one later
without touching the queue or decision logic.

Builds on: DP-04-02 (``CandidatePair`` — every queued case is explained
by ≥1 blocking rule), DP-04-03 (score explanation contract),
DP-04-04 (auto-resolution policy and the resolution write contract this
module shares).

**Code of record:** ``api/src/irc_data/matching/adjudication.py``
(``SCHEMA_VERSION = "adjudication-v1"``, impact model
``impact-model-v1``).
**Verification:** ``api/tests/matching/test_adjudication.py`` and the
human-runnable usability harness ``api/scripts/verify_dp_04_05.py``.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from irc_data.matching.blocking import BlockingError, CandidatePair

# ---------------------------------------------------------------------------
# Schema / policy versioning
# ---------------------------------------------------------------------------

#: Version tag for the adjudication contract family (queue item, decision
#: request, resolution record).  Serialised on every contract so stored
#: records remain interpretable as the schema evolves.
SCHEMA_VERSION = "adjudication-v1"

#: Identifier of the shipped impact model.  Versioned like the blocking
#: ruleset: changing the impact computation ships ``impact-model-v2``
#: alongside v1 so prior queue orderings remain reproducible.
IMPACT_MODEL_V1_ID = "impact-model-v1"

# ---------------------------------------------------------------------------
# Tunable policy defaults (what "uncertainty or cost warrants" means)
# ---------------------------------------------------------------------------

#: Scores below this are auto-rejectable by DP-04-04 (confident non-match).
AUTO_REJECT_BELOW = 0.20
#: Scores at or above this are auto-mergeable by DP-04-04 (confident match).
AUTO_MERGE_AT_OR_ABOVE = 0.90
#: Impact flags at or above this tier make a candidate *high-cost* — it is
#: queued for a human regardless of score, and merges need double review.
HIGH_IMPACT_FLAGS: frozenset[str] = frozenset({"rated", "has_results", "has_certificate"})

#: Sort weights: priority = impact_weight + uncertainty_weight × uncertainty.
#: Cost (impact) is the primary axis; uncertainty breaks ties.
DEFAULT_IMPACT_WEIGHT = 2.0
DEFAULT_UNCERTAINTY_WEIGHT = 1.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AdjudicationError(ValueError):
    """Base class for adjudication contract violations."""


class CaseNotFoundError(AdjudicationError):
    """Raised when a queue item / case id is unknown."""


class InvalidTransitionError(AdjudicationError):
    """Raised when a decision targets a case in a non-decidable status."""


class DoubleReviewError(AdjudicationError):
    """Raised when double-review rules are violated (e.g. same reviewer twice)."""


# ---------------------------------------------------------------------------
# Input contract: a scored candidate (handoff from DP-04-03 / DP-04-04)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoredCandidateV1:
    """One scored candidate pair handed to adjudication.

    This is the *input* contract for the queue.  ``pair`` is the DP-04-02
    :class:`CandidatePair` (so every case is explained by ≥1 blocking
    rule); ``score`` and ``score_explanation`` come from the DP-04-03
    pairwise scorer; ``impact`` and ``impact_flags`` describe the
    downstream cost of a wrong merge (DP-04-04's impact assessment);
    ``left_evidence`` / ``right_evidence`` are the side-by-side source
    records the MatchCard renders.
    """

    pair: CandidatePair
    score: float
    score_explanation: tuple[str, ...]
    impact: str = "low"
    impact_flags: tuple[str, ...] = ()
    left_evidence: Mapping[str, Any] = field(default_factory=dict)
    right_evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 <= float(self.score) <= 1.0):
            raise AdjudicationError(f"score must be in [0, 1], got {self.score!r}")
        if self.impact not in ("low", "medium", "high"):
            raise AdjudicationError(
                f"impact must be one of low/medium/high, got {self.impact!r}"
            )
        if not self.score_explanation:
            raise AdjudicationError(
                "a scored candidate must carry a non-empty score explanation"
            )

    @property
    def uncertainty(self) -> float:
        """Distance from the nearest confident endpoint: 1.0 at score 0.5."""
        return 1.0 - abs(2.0 * float(self.score) - 1.0)


# ---------------------------------------------------------------------------
# Impact assessment (impact-model-v1)
# ---------------------------------------------------------------------------


def impact_tier(flags: Iterable[str]) -> str:
    """Map impact flags to a tier — ``impact-model-v1``.

    * ``high``   — any flag in :data:`HIGH_IMPACT_FLAGS` (the boat is
                   rated, has race results, or holds certificates: a wrong
                   merge corrupts downstream ratings/history).
    * ``medium`` — some flags, but none high-cost.
    * ``low``    — no flags (a wrong merge is cheap to reverse).
    """
    flag_set = set(flags)
    if flag_set & HIGH_IMPACT_FLAGS:
        return "high"
    if flag_set:
        return "medium"
    return "low"


def _impact_weight(tier: str) -> float:
    return {"low": 0.0, "medium": 1.0, "high": DEFAULT_IMPACT_WEIGHT}[tier]


# ---------------------------------------------------------------------------
# The queue item / evidence view contract
# ---------------------------------------------------------------------------

#: Statuses in which a case may still receive decisions.
DECIDABLE_STATUSES: frozenset[str] = frozenset({"pending", "awaiting_second_review"})

#: Actions the MatchCard offers.  Every action is *reversible*: each one
#: produces a ``ResolutionRecordV1`` that :func:`reverse_resolution` can
#: undo, and ``escalate``/``defer`` never mutate identity at all.
QUEUE_ACTIONS: tuple[str, ...] = ("merge", "separate", "escalate", "defer")


@dataclass(frozen=True)
class QueueItemV1:
    """One adjudication case — the evidence view the MatchCard renders.

    Carries everything the UI needs on one contract: side-by-side source
    evidence, the score explanation, the downstream impact, the available
    reversible actions, and the double-review audit trail.
    """

    case_id: str
    status: str  # pending | awaiting_second_review | applied | escalated | deferred
    queue_reason: str  # uncertain | high_impact | uncertain_high_impact
    priority: float
    pair: CandidatePair
    score: float
    score_explanation: tuple[str, ...]
    impact: str
    impact_flags: tuple[str, ...]
    left_evidence: Mapping[str, Any] = field(default_factory=dict)
    right_evidence: Mapping[str, Any] = field(default_factory=dict)
    actions: tuple[str, ...] = QUEUE_ACTIONS
    requires_second_review: bool = False
    votes: tuple[dict[str, Any], ...] = ()
    enqueued_at: str = ""
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "status": self.status,
            "queue_reason": self.queue_reason,
            "priority": round(self.priority, 6),
            "pair": self.pair.to_dict(),
            "score": self.score,
            "score_explanation": list(self.score_explanation),
            "impact": self.impact,
            "impact_flags": list(self.impact_flags),
            "left_evidence": dict(self.left_evidence),
            "right_evidence": dict(self.right_evidence),
            "actions": list(self.actions),
            "requires_second_review": self.requires_second_review,
            "votes": [dict(v) for v in self.votes],
            "enqueued_at": self.enqueued_at,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "QueueItemV1":
        pair_d = d["pair"]
        pair = CandidatePair(
            left_id=pair_d["left_id"],
            right_id=pair_d["right_id"],
            rules_fired=tuple(pair_d["rules_fired"]),
            matching_keys=tuple(pair_d["matching_keys"]),
            ruleset_id=pair_d.get("ruleset_id", "blocking-rules-v1"),
        )
        return cls(
            case_id=d["case_id"],
            status=d["status"],
            queue_reason=d["queue_reason"],
            priority=float(d["priority"]),
            pair=pair,
            score=float(d["score"]),
            score_explanation=tuple(d.get("score_explanation", ())),
            impact=d["impact"],
            impact_flags=tuple(d.get("impact_flags", ())),
            left_evidence=dict(d.get("left_evidence", {})),
            right_evidence=dict(d.get("right_evidence", {})),
            actions=tuple(d.get("actions", QUEUE_ACTIONS)),
            requires_second_review=bool(d.get("requires_second_review", False)),
            votes=tuple(dict(v) for v in d.get("votes", ())),
            enqueued_at=d.get("enqueued_at", ""),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )


# ---------------------------------------------------------------------------
# The decision write contract — shared with automatic resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionRequestV1:
    """The single write contract for *any* resolution decision.

    The automatic resolver (DP-04-04) and the human MatchCard UI both
    write through this contract.  ``decided_by`` distinguishes the actor
    (``"system:resolver"`` vs ``"human:<id>"``); everything else —
    validation, status machine, double-review guard, produced
    :class:`ResolutionRecordV1` — is identical.
    """

    case_id: str
    decision: str  # merge | separate | escalate | defer
    decided_by: str
    rationale: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.decision not in QUEUE_ACTIONS:
            raise AdjudicationError(
                f"decision must be one of {QUEUE_ACTIONS}, got {self.decision!r}"
            )
        if not self.case_id or not self.case_id.strip():
            raise AdjudicationError("DecisionRequestV1 requires a case_id")
        if not self.decided_by or not self.decided_by.strip():
            raise AdjudicationError("DecisionRequestV1 requires decided_by")


@dataclass(frozen=True)
class ResolutionRecordV1:
    """The output contract of a decision — the durable audit record.

    ``reversible`` plus ``undo_of`` implement the *reversible actions*
    requirement: every applied record can be undone by writing a second
    record whose ``undo_of`` points back at it.
    """

    resolution_id: str
    case_id: str
    left_id: str
    right_id: str
    decision: str
    status: str  # applied | pending_second_review | escalated | reversed
    decided_by: str
    decided_at: str
    decided_by_chain: tuple[str, ...]
    score: float
    impact: str
    rationale: str = ""
    reversible: bool = True
    undo_of: str | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "resolution_id": self.resolution_id,
            "case_id": self.case_id,
            "left_id": self.left_id,
            "right_id": self.right_id,
            "decision": self.decision,
            "status": self.status,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "decided_by_chain": list(self.decided_by_chain),
            "score": self.score,
            "impact": self.impact,
            "rationale": self.rationale,
            "reversible": self.reversible,
            "undo_of": self.undo_of,
        }


# ---------------------------------------------------------------------------
# Store (persistence boundary)
# ---------------------------------------------------------------------------


class AdjudicationStore:
    """In-memory store for queue items and resolution records.

    Serialises to/from plain dicts (:meth:`to_dicts` / :meth:`from_dicts`)
    so the API layer can persist the same shapes to SQL without the queue
    or decision logic knowing about it.
    """

    def __init__(self) -> None:
        self._items: dict[str, QueueItemV1] = {}
        self._records: dict[str, ResolutionRecordV1] = {}

    # -- queue items ---------------------------------------------------

    def put(self, item: QueueItemV1) -> QueueItemV1:
        self._items[item.case_id] = item
        return item

    def get(self, case_id: str) -> QueueItemV1:
        try:
            return self._items[case_id]
        except KeyError:
            raise CaseNotFoundError(f"unknown adjudication case {case_id!r}") from None

    def open_items(self) -> list[QueueItemV1]:
        """Undecided cases, highest priority first (the queue ordering)."""
        items = [i for i in self._items.values() if i.status in DECIDABLE_STATUSES]
        return sorted(items, key=lambda i: (-i.priority, i.enqueued_at, i.case_id))

    def items(self) -> list[QueueItemV1]:
        return sorted(self._items.values(), key=lambda i: (-i.priority, i.case_id))

    # -- resolution records ---------------------------------------------

    def record(self, record: ResolutionRecordV1) -> ResolutionRecordV1:
        self._records[record.resolution_id] = record
        return record

    def record_for(self, resolution_id: str) -> ResolutionRecordV1:
        try:
            return self._records[resolution_id]
        except KeyError:
            raise CaseNotFoundError(
                f"unknown resolution record {resolution_id!r}"
            ) from None

    def records(self) -> list[ResolutionRecordV1]:
        return sorted(self._records.values(), key=lambda r: r.decided_at)

    def records_for_case(self, case_id: str) -> list[ResolutionRecordV1]:
        return [r for r in self.records() if r.case_id == case_id]

    # -- serialisation ---------------------------------------------------

    def to_dicts(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "items": [i.to_dict() for i in self.items()],
            "records": [r.to_dict() for r in self.records()],
        }

    @classmethod
    def from_dicts(cls, d: Mapping[str, Any]) -> "AdjudicationStore":
        store = cls()
        for item_d in d.get("items", ()):  # noqa: SIM118
            store.put(QueueItemV1.from_dict(item_d))
        for rec_d in d.get("records", ()):
            store.record(
                ResolutionRecordV1(
                    resolution_id=rec_d["resolution_id"],
                    case_id=rec_d["case_id"],
                    left_id=rec_d["left_id"],
                    right_id=rec_d["right_id"],
                    decision=rec_d["decision"],
                    status=rec_d["status"],
                    decided_by=rec_d["decided_by"],
                    decided_at=rec_d["decided_at"],
                    decided_by_chain=tuple(rec_d.get("decided_by_chain", ())),
                    score=float(rec_d.get("score", 0.0)),
                    impact=rec_d.get("impact", "low"),
                    rationale=rec_d.get("rationale", ""),
                    reversible=bool(rec_d.get("reversible", True)),
                    undo_of=rec_d.get("undo_of"),
                    schema_version=rec_d.get("schema_version", SCHEMA_VERSION),
                )
            )
        return store


# ---------------------------------------------------------------------------
# The adjudication queue
# ---------------------------------------------------------------------------


class AdjudicationQueue:
    """Prioritised human review queue for uncertain / high-impact candidates.

    Only candidates where **uncertainty or cost warrants** a human are
    enqueued; everything else stays with the automatic resolver:

    * ``auto_reject`` (score < :data:`AUTO_REJECT_BELOW`, low impact) —
      confident non-match; a human adds no value.
    * ``auto_merge`` (score ≥ :data:`AUTO_MERGE_AT_OR_ABOVE`, low impact) —
      confident match; a human adds no value.
    * anything involving a high-impact flag is **always** queued (cost
      warrants a human) even when the score is confident.
    * everything in the uncertain band is queued (uncertainty warrants a
      human).
    """

    def __init__(
        self,
        store: AdjudicationStore | None = None,
        *,
        impact_model_id: str = IMPACT_MODEL_V1_ID,
        clock: Any = None,
    ) -> None:
        if impact_model_id != IMPACT_MODEL_V1_ID:
            raise AdjudicationError(
                f"unknown impact model {impact_model_id!r}; known: {[IMPACT_MODEL_V1_ID]}"
            )
        self.impact_model_id = impact_model_id
        self.store = store or AdjudicationStore()
        # ``clock`` is injectable so tests are deterministic; it never
        # influences *which* cases are queued or their ordering inputs.
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- enqueueing ------------------------------------------------------

    def effective_impact(self, candidate: ScoredCandidateV1) -> str:
        """The stricter of the caller-declared impact tier and the tier the
        impact model derives from the candidate's impact flags."""
        derived = impact_tier(candidate.impact_flags)
        if _impact_weight(derived) > _impact_weight(candidate.impact):
            return derived
        return candidate.impact

    def route(self, candidate: ScoredCandidateV1) -> str:
        """Classify a scored candidate: ``auto_reject`` | ``auto_merge`` |
        ``uncertain`` | ``high_impact`` | ``uncertain_high_impact``."""
        high = self.effective_impact(candidate) == "high"
        if candidate.score < AUTO_REJECT_BELOW:
            return "high_impact" if high else "auto_reject"
        if candidate.score >= AUTO_MERGE_AT_OR_ABOVE:
            return "high_impact" if high else "auto_merge"
        return "uncertain_high_impact" if high else "uncertain"

    def priority(self, candidate: ScoredCandidateV1, tier: str) -> float:
        """``impact_weight + uncertainty_weight × uncertainty`` — cost first,
        uncertainty breaks ties."""
        return _impact_weight(tier) + DEFAULT_UNCERTAINTY_WEIGHT * candidate.uncertainty

    def enqueue(self, candidate: ScoredCandidateV1) -> QueueItemV1 | None:
        """Queue a candidate for humans, or return ``None`` when the
        automatic resolver should keep it (no uncertainty, no cost)."""
        routing = self.route(candidate)
        if routing in ("auto_reject", "auto_merge"):
            return None
        tier = self.effective_impact(candidate)
        item = QueueItemV1(
            case_id=f"adj-{uuid.uuid4().hex[:12]}",
            status="pending",
            queue_reason=routing,
            priority=self.priority(candidate, tier),
            pair=candidate.pair,
            score=float(candidate.score),
            score_explanation=candidate.score_explanation,
            impact=tier,
            impact_flags=tuple(candidate.impact_flags),
            left_evidence=dict(candidate.left_evidence),
            right_evidence=dict(candidate.right_evidence),
            requires_second_review=(tier == "high"),
            enqueued_at=self._clock().isoformat(),
        )
        return self.store.put(item)

    def enqueue_all(
        self, candidates: Iterable[ScoredCandidateV1]
    ) -> tuple[QueueItemV1, ...]:
        """Enqueue everything worth human attention; returns queued items in
        queue order (highest priority first)."""
        for candidate in candidates:
            self.enqueue(candidate)
        return tuple(self.store.open_items())

    # -- deciding ---------------------------------------------------------

    def decide(self, request: DecisionRequestV1) -> ResolutionRecordV1:
        """Apply a decision through the shared write contract.

        This is the *same* code path the automatic resolver uses — a
        human MatchCard click and an auto-resolution write differ only in
        ``decided_by``.

        Double review: a ``merge`` on a case with
        ``requires_second_review`` records the first reviewer's vote and
        returns a ``pending_second_review`` record; only a *second,
        distinct* reviewer repeating the decision applies it.  A
        conflicting second decision escalates the case.
        """
        item = self.store.get(request.case_id)
        if item.status not in DECIDABLE_STATUSES:
            raise InvalidTransitionError(
                f"case {item.case_id!r} is {item.status!r}; "
                f"only {sorted(DECIDABLE_STATUSES)} cases accept decisions"
            )

        now = self._clock().isoformat()
        chain = tuple(v["decided_by"] for v in item.votes) + (request.decided_by,)

        # -- double review for high-impact merges ------------------------
        if item.requires_second_review and request.decision == "merge":
            if request.decided_by in {v["decided_by"] for v in item.votes}:
                raise DoubleReviewError(
                    f"{request.decided_by!r} has already voted on case "
                    f"{item.case_id!r}; double review requires a distinct second reviewer"
                )
            votes = item.votes + (
                {
                    "decision": request.decision,
                    "decided_by": request.decided_by,
                    "decided_at": now,
                    "rationale": request.rationale,
                },
            )
            prior_merge_votes = [v for v in votes if v["decision"] == "merge"]
            if len(prior_merge_votes) < 2:
                # First vote: keep the case open, awaiting a second reviewer.
                self.store.put(
                    _replace_item(item, status="awaiting_second_review", votes=votes)
                )
                return self.store.record(
                    ResolutionRecordV1(
                        resolution_id=f"res-{uuid.uuid4().hex[:12]}",
                        case_id=item.case_id,
                        left_id=item.pair.left_id,
                        right_id=item.pair.right_id,
                        decision="merge",
                        status="pending_second_review",
                        decided_by=request.decided_by,
                        decided_at=now,
                        decided_by_chain=chain,
                        score=item.score,
                        impact=item.impact,
                        rationale=request.rationale,
                    )
                )
            # Second, distinct reviewer agreed: apply with the full chain.
            self.store.put(_replace_item(item, status="applied", votes=votes))
            return self.store.record(
                ResolutionRecordV1(
                    resolution_id=f"res-{uuid.uuid4().hex[:12]}",
                    case_id=item.case_id,
                    left_id=item.pair.left_id,
                    right_id=item.pair.right_id,
                    decision="merge",
                    status="applied",
                    decided_by=request.decided_by,
                    decided_at=now,
                    decided_by_chain=chain,
                    score=item.score,
                    impact=item.impact,
                    rationale=request.rationale,
                )
            )

        # -- conflicting second decision on a reviewed case escalates ------
        if item.votes and request.decision not in {
            v["decision"] for v in item.votes
        }:
            if request.decided_by in {v["decided_by"] for v in item.votes}:
                raise DoubleReviewError(
                    f"{request.decided_by!r} has already voted on case {item.case_id!r}"
                )
            votes = item.votes + (
                {
                    "decision": request.decision,
                    "decided_by": request.decided_by,
                    "decided_at": now,
                    "rationale": request.rationale,
                },
            )
            self.store.put(_replace_item(item, status="escalated", votes=votes))
            return self.store.record(
                ResolutionRecordV1(
                    resolution_id=f"res-{uuid.uuid4().hex[:12]}",
                    case_id=item.case_id,
                    left_id=item.pair.left_id,
                    right_id=item.pair.right_id,
                    decision=request.decision,
                    status="escalated",
                    decided_by=request.decided_by,
                    decided_at=now,
                    decided_by_chain=chain,
                    score=item.score,
                    impact=item.impact,
                    rationale=(
                        request.rationale
                        or "conflicting reviewer decisions; escalated for tie-break"
                    ),
                )
            )

        # -- ordinary single-review decision --------------------------------
        status = {
            "merge": "applied",
            "separate": "applied",
            "escalate": "escalated",
            "defer": "deferred",
        }[request.decision]
        votes = item.votes + (
            {
                "decision": request.decision,
                "decided_by": request.decided_by,
                "decided_at": now,
                "rationale": request.rationale,
            },
        )
        self.store.put(_replace_item(item, status=status, votes=votes))
        return self.store.record(
            ResolutionRecordV1(
                resolution_id=f"res-{uuid.uuid4().hex[:12]}",
                case_id=item.case_id,
                left_id=item.pair.left_id,
                right_id=item.pair.right_id,
                decision=request.decision,
                status=status,
                decided_by=request.decided_by,
                decided_at=now,
                decided_by_chain=chain,
                score=item.score,
                impact=item.impact,
                rationale=request.rationale,
            )
        )

    # -- reversal (reversible actions) -------------------------------------

    def reverse_resolution(
        self, resolution_id: str, *, decided_by: str, rationale: str = ""
    ) -> ResolutionRecordV1:
        """Undo an applied resolution — every action is reversible.

        Writes a *new* :class:`ResolutionRecordV1` whose ``undo_of`` points
        at the record being reversed, marks the original ``reversed``, and
        requeues the case as ``pending`` so it can be decided again.
        """
        original = self.store.record_for(resolution_id)
        if original.status == "reversed":
            raise InvalidTransitionError(
                f"resolution {resolution_id!r} is already reversed"
            )
        if original.status not in ("applied", "escalated"):
            raise InvalidTransitionError(
                f"only applied/escalated resolutions can be reversed, "
                f"got {original.status!r}"
            )
        if not original.reversible:
            raise InvalidTransitionError(
                f"resolution {resolution_id!r} is marked non-reversible"
            )

        now = self._clock().isoformat()
        self.store.record(
            ResolutionRecordV1(
                resolution_id=original.resolution_id,
                case_id=original.case_id,
                left_id=original.left_id,
                right_id=original.right_id,
                decision=original.decision,
                status="reversed",
                decided_by=original.decided_by,
                decided_at=original.decided_at,
                decided_by_chain=original.decided_by_chain,
                score=original.score,
                impact=original.impact,
                rationale=original.rationale,
                reversible=original.reversible,
                undo_of=original.undo_of,
            )
        )

        # Requeue the case so a human can decide again.
        item = self.store.get(original.case_id)
        self.store.put(_replace_item(item, status="pending", votes=()))

        return self.store.record(
            ResolutionRecordV1(
                resolution_id=f"res-{uuid.uuid4().hex[:12]}",
                case_id=original.case_id,
                left_id=original.left_id,
                right_id=original.right_id,
                decision=original.decision,
                status="applied",
                decided_by=decided_by,
                decided_at=now,
                decided_by_chain=original.decided_by_chain + (decided_by,),
                score=original.score,
                impact=original.impact,
                rationale=rationale or f"reversal of {original.resolution_id}",
                reversible=True,
                undo_of=original.resolution_id,
            )
        )


def _replace_item(
    item: QueueItemV1,
    *,
    status: str | None = None,
    votes: tuple[dict[str, Any], ...] | None = None,
) -> QueueItemV1:
    """Return a copy of *item* with status/votes replaced (items are frozen)."""
    return QueueItemV1(
        case_id=item.case_id,
        status=status if status is not None else item.status,
        queue_reason=item.queue_reason,
        priority=item.priority,
        pair=item.pair,
        score=item.score,
        score_explanation=item.score_explanation,
        impact=item.impact,
        impact_flags=item.impact_flags,
        left_evidence=item.left_evidence,
        right_evidence=item.right_evidence,
        actions=item.actions,
        requires_second_review=item.requires_second_review,
        votes=votes if votes is not None else item.votes,
        enqueued_at=item.enqueued_at,
        schema_version=item.schema_version,
    )


# ---------------------------------------------------------------------------
# Usability evaluation harness — adjudicate a labelled sample, measure
# error rate and time (the DP-04-05 verification)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelledCase:
    """One labelled sample case: a scored candidate plus its gold label."""

    candidate: ScoredCandidateV1
    gold_label: str  # merge | separate


@dataclass(frozen=True)
class AdjudicationEvent:
    """One adjudicator's decision on one case, with timing."""

    case_id: str
    gold_label: str
    decision: str
    correct: bool
    elapsed_seconds: float
    decided_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "gold_label": self.gold_label,
            "decision": self.decision,
            "correct": self.correct,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "decided_by": self.decided_by,
        }


@dataclass(frozen=True)
class UsabilityReportV1:
    """The verification contract: error rate and time over a labelled sample."""

    adjudicator_id: str
    n_cases: int
    n_errors: int
    error_rate: float
    total_seconds: float
    mean_seconds_per_case: float
    events: tuple[AdjudicationEvent, ...]
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adjudicator_id": self.adjudicator_id,
            "n_cases": self.n_cases,
            "n_errors": self.n_errors,
            "error_rate": round(self.error_rate, 6),
            "total_seconds": round(self.total_seconds, 6),
            "mean_seconds_per_case": round(self.mean_seconds_per_case, 6),
            "events": [e.to_dict() for e in self.events],
        }

    def fingerprint(self) -> str:
        """Stable digest of the *outcomes* (decisions, correctness, timing).

        Random case ids are excluded so identical adjudication runs
        fingerprint identically — that is what makes the digest usable as
        comparable evidence across runs."""
        payload = repr(
            [
                (e.gold_label, e.decision, e.correct, e.elapsed_seconds, e.decided_by)
                for e in self.events
            ]
            + [self.adjudicator_id, self.n_cases, self.n_errors]
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]


def adjudicate_labelled_sample(
    sample: Iterable[LabelledCase],
    policy: Any,
    *,
    adjudicator_id: str = "adjudicator:policy",
    clock: Any = None,
    time_per_case: float | None = None,
) -> UsabilityReportV1:
    """Adjudicate a labelled sample and measure error rate and time.

    ``policy`` is the adjudicator under test — a callable
    ``policy(QueueItemV1) -> decision`` standing in for the human at the
    MatchCard (the verification harness supplies scripted policies that
    read the same evidence view the human would see).  Each case is
    routed through the real :class:`AdjudicationQueue` so the measurement
    covers the production decision path, including double review: a
    high-impact merge is only counted ``merge`` once two distinct
    reviewers have voted, exactly as the UI behaves.

    ``clock`` is an injectable monotonic timer; when ``time_per_case`` is
    given it is added to the timer per case so runs are reproducible.
    """
    import time as _time

    timer = clock or _time.monotonic
    queue = AdjudicationQueue(clock=lambda: datetime.fromtimestamp(timer(), timezone.utc))
    events: list[AdjudicationEvent] = []

    for labelled in sample:
        item = queue.enqueue(labelled.candidate)
        if item is None:
            # The case never reaches a human — the auto-resolver's answer is
            # the decision, and it is measured like any other.
            auto = "merge" if labelled.candidate.score >= AUTO_MERGE_AT_OR_ABOVE else "separate"
            started = timer()
            elapsed = (time_per_case or 0.0) or (timer() - started)
            events.append(
                AdjudicationEvent(
                    case_id=f"auto-{labelled.candidate.pair.left_id}-{labelled.candidate.pair.right_id}",
                    gold_label=labelled.gold_label,
                    decision=auto,
                    correct=auto == labelled.gold_label,
                    elapsed_seconds=elapsed,
                    decided_by="system:resolver",
                )
            )
            continue

        started = timer()
        decision = policy(item)
        if time_per_case is not None:
            elapsed = float(time_per_case)
        else:
            elapsed = timer() - started

        # Drive the real decision path, including double review.
        final = decision
        if item.requires_second_review and decision == "merge":
            queue.decide(
                DecisionRequestV1(
                    case_id=item.case_id,
                    decision="merge",
                    decided_by=f"{adjudicator_id}#1",
                    rationale="first review",
                )
            )
            queue.decide(
                DecisionRequestV1(
                    case_id=item.case_id,
                    decision="merge",
                    decided_by=f"{adjudicator_id}#2",
                    rationale="second review",
                )
            )
        else:
            queue.decide(
                DecisionRequestV1(
                    case_id=item.case_id,
                    decision=decision,
                    decided_by=adjudicator_id,
                    rationale="adjudication",
                )
            )
        if decision in ("escalate", "defer"):
            # Escalated/deferred cases are not resolved — count them as
            # errors against the gold label (the queue failed to resolve).
            final = decision
        events.append(
            AdjudicationEvent(
                case_id=item.case_id,
                gold_label=labelled.gold_label,
                decision=final,
                correct=final == labelled.gold_label,
                elapsed_seconds=elapsed,
                decided_by=adjudicator_id,
            )
        )

    n = len(events)
    errors = sum(1 for e in events if not e.correct)
    total = sum(e.elapsed_seconds for e in events)
    return UsabilityReportV1(
        adjudicator_id=adjudicator_id,
        n_cases=n,
        n_errors=errors,
        error_rate=(errors / n) if n else 0.0,
        total_seconds=total,
        mean_seconds_per_case=(total / n) if n else 0.0,
        events=tuple(events),
    )
