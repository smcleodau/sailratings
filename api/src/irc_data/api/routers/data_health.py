"""Data health dashboard & incident workflow API (DP-05-04).

Endpoints behind the admin credential, backing the AD-01 admin console's
data-health page:

  GET  /admin/data-health/dashboard                      — the aggregated
                                                           dashboard (source
                                                           freshness, pipeline
                                                           yields, quarantine,
                                                           lineage gaps,
                                                           identity
                                                           uncertainty, SLO
                                                           breaches + active
                                                           incidents)
  GET  /admin/data-health/incidents                      — incident queue
                                                           (filterable by
                                                           status/source/kind)
  POST /admin/data-health/incidents                      — create an incident
                                                           (synthetic /
                                                           manual; the same
                                                           path detectors use)
  GET  /admin/data-health/incidents/reconcile            — every incident's
                                                           evidence checked
                                                           against the quality
                                                           event tables
  GET  /admin/data-health/incidents/{incident_id}        — incident detail
  POST /admin/data-health/incidents/{incident_id}/acknowledge
  POST /admin/data-health/incidents/{incident_id}/mitigate
  POST /admin/data-health/incidents/{incident_id}/resolve
  POST /admin/data-health/incidents/{incident_id}/notes
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db
from irc_data.api.routers.admin import _verify_admin
from irc_data.quality import health

router = APIRouter(prefix="/admin/data-health", tags=["Admin"])


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def data_health_dashboard(
    window_days: int = Query(
        default=health.DEFAULT_WINDOW_DAYS, ge=1, le=90
    ),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """The aggregated data-health dashboard.

    Reconciles live against the quality-event tables: source freshness
    (run ledger), pipeline yields (reconciliation), quarantine,
    lineage gaps, identity uncertainty and SLO breaches, plus the active
    incident queue.
    """
    _verify_admin(authorization)
    return health.get_health_dashboard(engine, window_days=window_days)


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


class IncidentCreateIn(BaseModel):
    """Create a (synthetic/manual) incident — the verification path."""

    kind: str = Field(default=health.KIND_MANUAL)
    title: str
    severity: str = Field(default=health.SEVERITY_WARNING)
    source_slug: str | None = None
    dataset: str | None = None
    summary: str = ""
    affected_batches: list[str] = Field(default_factory=list)
    affected_consumers: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    recommended_action: dict[str, Any] | None = None
    alert: bool = True


class WorkflowIn(BaseModel):
    actor: str
    note: str | None = None


class ResolveIn(BaseModel):
    actor: str
    resolution: str


class NoteIn(BaseModel):
    actor: str
    note: str


@router.get("/incidents")
async def list_data_incidents(
    status: str | None = Query(
        default=None,
        description="open | acknowledged | mitigating | resolved | active",
    ),
    source: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """The incident queue, newest first."""
    _verify_admin(authorization)
    incidents = health.list_incidents(
        engine, status=status, source_slug=source, kind=kind, limit=limit
    )
    return {
        "count": len(incidents),
        "incidents": [i.to_dict() for i in incidents],
    }


@router.get("/incidents/reconcile")
async def reconcile_incidents(
    limit: int = Query(default=200, ge=1, le=1000),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Reconcile the dashboard's incidents against the quality events.

    Every incident's evidence refs must resolve to real rows in the
    quality-event tables (health events, reconciliation reports, source
    incidents, quarantine, batches).
    """
    _verify_admin(authorization)
    return health.reconcile_incidents_to_events(engine, limit=limit)


@router.post("/incidents", status_code=201)
async def create_data_incident(
    body: IncidentCreateIn,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Create an incident — synthetic (verification) or manual.

    Goes through the same ownership / evidence / recommended-action /
    alerting path as detector-created incidents.
    """
    _verify_admin(authorization)
    if body.kind not in {
        health.KIND_SOURCE_DEVIATION,
        health.KIND_SILENT_LOSS,
        health.KIND_QUARANTINE,
        health.KIND_FRESHNESS,
        health.KIND_LINEAGE_GAP,
        health.KIND_IDENTITY_UNCERTAINTY,
        health.KIND_SLO_BREACH,
        health.KIND_MANUAL,
    }:
        raise HTTPException(status_code=422, detail=f"unknown kind {body.kind!r}")
    if body.severity not in {
        health.SEVERITY_INFO,
        health.SEVERITY_WARNING,
        health.SEVERITY_CRITICAL,
    }:
        raise HTTPException(
            status_code=422, detail=f"unknown severity {body.severity!r}"
        )
    incident = health.create_incident(
        engine,
        kind=body.kind,
        title=body.title,
        severity=body.severity,
        source_slug=body.source_slug,
        dataset=body.dataset,
        summary=body.summary,
        affected_batches=body.affected_batches,
        affected_consumers=body.affected_consumers,
        evidence=body.evidence,
        recommended_action=body.recommended_action,
        alert=body.alert,
    )
    return incident.to_dict()


@router.get("/incidents/{incident_id}")
async def get_data_incident(
    incident_id: str,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Incident detail: owner, evidence, affected batches/consumers,
    recommended action, workflow notes."""
    _verify_admin(authorization)
    incident = health.get_incident(engine, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=404, detail=f"incident {incident_id!r} not found"
        )
    return incident.to_dict()


def _workflow_or_404(
    engine: Engine, incident_id: str, action: str, **kwargs: Any
) -> dict[str, Any]:
    try:
        if action == "acknowledge":
            incident = health.acknowledge_incident(engine, incident_id, **kwargs)
        elif action == "mitigate":
            incident = health.start_mitigation(engine, incident_id, **kwargs)
        elif action == "resolve":
            incident = health.resolve_incident(engine, incident_id, **kwargs)
        else:  # pragma: no cover - guarded by call sites
            raise ValueError(action)
    except health.IncidentWorkflowError as exc:
        detail = str(exc)
        code = 404 if "not found" in detail else 409
        raise HTTPException(status_code=code, detail=detail)
    return incident.to_dict()


@router.post("/incidents/{incident_id}/acknowledge")
async def acknowledge_data_incident(
    incident_id: str,
    body: WorkflowIn,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """The owner acknowledges the incident — recovery work is claimed."""
    _verify_admin(authorization)
    return _workflow_or_404(
        engine, incident_id, "acknowledge", actor=body.actor, note=body.note
    )


@router.post("/incidents/{incident_id}/mitigate")
async def mitigate_data_incident(
    incident_id: str,
    body: WorkflowIn,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """The owner starts executing the recommended action."""
    _verify_admin(authorization)
    return _workflow_or_404(
        engine, incident_id, "mitigate", actor=body.actor, note=body.note
    )


@router.post("/incidents/{incident_id}/resolve")
async def resolve_data_incident(
    incident_id: str,
    body: ResolveIn,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Resolve the incident (resolution note required)."""
    _verify_admin(authorization)
    return _workflow_or_404(
        engine, incident_id, "resolve", actor=body.actor, resolution=body.resolution
    )


@router.post("/incidents/{incident_id}/notes")
async def note_data_incident(
    incident_id: str,
    body: NoteIn,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Append a workflow note without changing the incident state."""
    _verify_admin(authorization)
    try:
        incident = health.add_incident_note(
            engine, incident_id, actor=body.actor, note=body.note
        )
    except health.IncidentWorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return incident.to_dict()
