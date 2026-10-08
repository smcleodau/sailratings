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
  GET  /admin/data-health/tables                        — AD-01-15 /
                                                           SPEC-22 §3.4 table
                                                           census (latest
                                                           ``tables.*``
                                                           ``admin_metrics``
                                                           snapshot; empty
                                                           tables flagged)
  GET  /admin/data-health/completeness                   — AD-01-15 /
                                                           SPEC-22 §3.4
                                                           completeness
                                                           (latest
                                                           ``completeness.*``
                                                           ``admin_metrics``
                                                           snapshot)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
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


# ---------------------------------------------------------------------------
# AD-01-15 / SPEC-22 §3.4 — tables census + completeness snapshot
#
# Both routes read ONLY the nightly ``admin_metrics`` snapshot written by
# ``irc-data compute-admin-metrics`` (api/src/irc_data/cli.py). Neither
# issues a query against ``boats`` or ``events`` — the whole point of the
# card is that the heavy COUNT(*) / non-null scans run once a night in the
# CLI, not on every page load of /admin/data-health.
# ---------------------------------------------------------------------------


def _metric_value(row: dict[str, Any]) -> float | None:
    value = row.get("value_num")
    if value is None:
        value = row.get("value")
    return float(value) if value is not None else None


def _metric_computed_at(row: dict[str, Any]) -> str | None:
    value = row.get("computed_at") or row.get("recorded_at")
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _latest_admin_metrics(engine: Engine, like: str) -> list[dict[str, Any]]:
    """Every ``admin_metrics`` row for the latest ``computed_at`` per metric
    name matching the SQL ``LIKE`` pattern ``like``.

    Dialect-portable (SQLite in tests, Postgres in prod): a plain ordered
    SELECT, deduplicated in Python by taking the last row per metric —
    avoids ``DISTINCT ON``/window-function differences between engines.
    """
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT metric, value, value_num, value_text, "
                    "computed_at, recorded_at FROM admin_metrics "
                    "WHERE metric LIKE :like ORDER BY metric, computed_at"
                ),
                {"like": like},
            )
            .mappings()
            .all()
        )
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest[row["metric"]] = dict(row)
    return list(latest.values())


@router.get("/tables")
async def data_health_tables(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Table census: the latest ``tables.<name>.{rows,bytes}`` snapshot from
    ``admin_metrics`` — written nightly by ``compute-admin-metrics`` from
    ``pg_stat_user_tables`` / ``pg_total_relation_size``. Tables with
    ``rows == 0`` are flagged ``empty`` and sorted first so they surface
    without a manual query."""
    _verify_admin(authorization)
    metrics = _latest_admin_metrics(engine, "tables.%")

    by_table: dict[str, dict[str, Any]] = {}
    for row in metrics:
        metric = row["metric"]
        body = metric[len("tables."):]
        if body.endswith(".rows"):
            name, field = body[: -len(".rows")], "rows"
        elif body.endswith(".bytes"):
            name, field = body[: -len(".bytes")], "bytes"
        else:
            continue
        entry = by_table.setdefault(
            name,
            {"name": name, "rows": None, "bytes": None, "computed_at": None},
        )
        value = _metric_value(row)
        entry[field] = int(value) if value is not None else None
        computed_at = _metric_computed_at(row)
        if computed_at and (
            entry["computed_at"] is None or computed_at > entry["computed_at"]
        ):
            entry["computed_at"] = computed_at

    for entry in by_table.values():
        entry["empty"] = (entry["rows"] or 0) == 0

    tables = sorted(
        by_table.values(), key=lambda t: (0 if t["empty"] else 1, t["name"])
    )
    return {"count": len(tables), "tables": tables}


@router.get("/completeness")
async def data_health_completeness(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Completeness snapshot: the latest ``completeness.*`` metrics from
    ``admin_metrics`` — % non-NULL for every boats identity column plus the
    events venue null-rate, written nightly by ``compute-admin-metrics``."""
    _verify_admin(authorization)
    metrics = _latest_admin_metrics(engine, "completeness.%")

    out = [
        {
            "metric": row["metric"],
            "value": _metric_value(row),
            "computed_at": _metric_computed_at(row),
        }
        for row in metrics
    ]
    out.sort(key=lambda r: r["metric"])
    return {"count": len(out), "metrics": out}
