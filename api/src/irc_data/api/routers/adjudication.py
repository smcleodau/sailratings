"""Human adjudication queue API (DP-04-05).

Endpoints behind the admin credential, backing the ``/admin/identity``
MatchCard evidence view:

  GET  /admin/adjudication/queue           — prioritised open queue
                                             (high-impact / most-uncertain
                                             first)
  GET  /admin/adjudication/cases           — all cases, any status
  GET  /admin/adjudication/cases/{id}      — one case's evidence view
                                             (side-by-side source evidence,
                                             score explanation, downstream
                                             impact, reversible actions)
  POST /admin/adjudication/enqueue         — hand a scored candidate
                                             (ScoredCandidateV1) to the
                                             queue; returns null when the
                                             automatic resolver keeps it
  POST /admin/adjudication/decide          — write a decision through the
                                             shared DecisionRequestV1
                                             contract (the same contract the
                                             automatic resolver uses);
                                             double review is enforced for
                                             high-impact merges
  POST /admin/adjudication/reverse         — undo an applied resolution
                                             (reversible actions)
  GET  /admin/adjudication/resolutions     — the resolution audit trail

The queue state lives in a process-level :class:`AdjudicationQueue`
behind the persistence-agnostic :class:`AdjudicationStore` boundary; a
SQL-backed store can replace it without touching the decision logic.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from irc_data.api.routers.admin import _verify_admin
from irc_data.matching import adjudication as adj

from irc_data.api.deps import get_optional_identity, CallerIdentity, get_db
from irc_data.api.audit import log_admin_action
from sqlalchemy.engine import Engine
from fastapi import Depends
router = APIRouter(prefix="/admin/adjudication", tags=["Admin"])

# Process-level queue.  The AdjudicationStore serialisation boundary
# (to_dicts/from_dicts) is where a SQL-backed store plugs in.
_QUEUE: adj.AdjudicationQueue | None = None


def get_queue() -> adj.AdjudicationQueue:
    """The process-level adjudication queue, created on first use.

    Tests override this with :func:`set_queue` so each test gets an
    isolated queue.
    """
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = adj.AdjudicationQueue()
    return _QUEUE


def set_queue(queue: adj.AdjudicationQueue | None) -> None:
    """Replace the process-level queue (test isolation hook)."""
    global _QUEUE
    _QUEUE = queue


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ScoredCandidateIn(BaseModel):
    """ScoredCandidateV1 over the wire — the DP-04-03/04 handoff."""

    left_id: str
    right_id: str
    rules_fired: list[str] = Field(min_length=1)
    matching_keys: list[str] = Field(default_factory=list)
    ruleset_id: str = "blocking-rules-v1"
    score: float = Field(ge=0.0, le=1.0)
    score_explanation: list[str] = Field(min_length=1)
    impact: str = "low"
    impact_flags: list[str] = Field(default_factory=list)
    left_evidence: dict = Field(default_factory=dict)
    right_evidence: dict = Field(default_factory=dict)

    def to_domain(self) -> adj.ScoredCandidateV1:
        from irc_data.matching.blocking import CandidatePair

        return adj.ScoredCandidateV1(
            pair=CandidatePair(
                left_id=self.left_id,
                right_id=self.right_id,
                rules_fired=tuple(self.rules_fired),
                matching_keys=tuple(self.matching_keys),
                ruleset_id=self.ruleset_id,
            ),
            score=self.score,
            score_explanation=tuple(self.score_explanation),
            impact=self.impact,
            impact_flags=tuple(self.impact_flags),
            left_evidence=self.left_evidence,
            right_evidence=self.right_evidence,
        )


class DecisionIn(BaseModel):
    """DecisionRequestV1 over the wire — the shared write contract."""

    case_id: str
    decision: str  # merge | separate | escalate | defer
    decided_by: str
    rationale: str = ""


class ReverseIn(BaseModel):
    resolution_id: str
    decided_by: str
    rationale: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/queue")
async def get_queue_items(
    authorization: str = Header(None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """The prioritised open queue — high-impact / most-uncertain first."""
    _verify_admin(authorization)
    queue = get_queue()
    return [item.to_dict() for item in queue.store.open_items()[:limit]]


@router.get("/cases")
async def get_cases(
    authorization: str = Header(None),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """All adjudication cases, optionally filtered by status."""
    _verify_admin(authorization)
    queue = get_queue()
    items = queue.store.items()
    if status:
        items = [i for i in items if i.status == status]
    return [item.to_dict() for item in items[:limit]]


@router.get("/cases/{case_id}")
async def get_case(case_id: str, authorization: str = Header(None)):
    """One case's full evidence view (what the MatchCard renders)."""
    _verify_admin(authorization)
    queue = get_queue()
    try:
        item = queue.store.get(case_id)
    except adj.CaseNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown case {case_id!r}")
    d = item.to_dict()
    d["resolutions"] = [r.to_dict() for r in queue.store.records_for_case(case_id)]
    return d


@router.post("/enqueue", status_code=201)
async def enqueue_candidate(body: ScoredCandidateIn, authorization: str = Header(None)):
    """Hand a scored candidate to the queue.

    Returns the :class:`QueueItemV1` when uncertainty or cost warrants a
    human, or ``{"queued": false, "routing": ...}`` when the automatic
    resolver keeps the candidate.
    """
    _verify_admin(authorization)
    queue = get_queue()
    try:
        candidate = body.to_domain()
    except adj.AdjudicationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    item = queue.enqueue(candidate)
    if item is None:
        return {"queued": False, "routing": queue.route(candidate)}
    return {"queued": True, "item": item.to_dict()}


@router.post("/decide")
async def decide(
    body: DecisionIn,
    authorization: str = Header(None),
    caller: CallerIdentity | None = Depends(get_optional_identity),
    engine: Engine = Depends(get_db),
):
    """Write a decision through the shared DecisionRequestV1 contract.

    High-impact merges return ``status=pending_second_review`` on the
    first vote and require a second, distinct reviewer to apply.
    """
    _verify_admin(authorization)
    queue = get_queue()
    try:
        request = adj.DecisionRequestV1(
            case_id=body.case_id,
            decision=body.decision,
            decided_by=body.decided_by,
            rationale=body.rationale,
        )
        record = queue.decide(request)
        who = caller.email if caller and caller.email else "admin"
        log_admin_action(engine, who, "decide", "adjudication", body.case_id, after={"decision": body.decision, "rationale": body.rationale})
    except adj.CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except adj.DoubleReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except adj.InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except adj.AdjudicationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return record.to_dict()


@router.post("/reverse")
async def reverse(
    body: ReverseIn,
    authorization: str = Header(None),
    caller: CallerIdentity | None = Depends(get_optional_identity),
    engine: Engine = Depends(get_db),
):
    """Undo an applied resolution and requeue the case."""
    _verify_admin(authorization)
    queue = get_queue()
    try:
        record = queue.reverse_resolution(
            body.resolution_id, decided_by=body.decided_by, rationale=body.rationale
        )
        who = caller.email if caller and caller.email else "admin"
        log_admin_action(engine, who, "reverse", "adjudication", body.resolution_id, after={"rationale": body.rationale})
    except adj.CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except adj.InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return record.to_dict()


@router.get("/resolutions")
async def get_resolutions(
    authorization: str = Header(None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """The resolution audit trail (newest decision records last)."""
    _verify_admin(authorization)
    queue = get_queue()
    return [r.to_dict() for r in queue.store.records()][-limit:]
