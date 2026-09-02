"""Reconciliation & silent-loss detection API (DP-05-03).

Endpoints behind the admin credential, backing the reconciliation
dashboard and the promotion gate:

  POST /admin/reconciliation/check             — reconcile one run's stage
                                                 counts; blocks + alerts on
                                                 unexplained variance or
                                                 abrupt yield change
  GET  /admin/reconciliation/reports           — recent reconciliation
                                                 reports (filterable)
  GET  /admin/reconciliation/reports/{run_id}  — report for one run
  GET  /admin/reconciliation/baseline/{source} — trailing yield band for a
                                                 source
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db
from irc_data.api.routers.admin import _verify_admin
from irc_data.diagnostics import reconciliation as recon

router = APIRouter(prefix="/admin/reconciliation", tags=["Admin"])


class PipelineCountsIn(BaseModel):
    """Request body for the reconcile endpoint (PipelineCountsV1)."""

    run_id: int
    source_id: str
    discovered: int = Field(default=0, ge=0)
    fetched: int = Field(default=0, ge=0)
    parsed: int = Field(default=0, ge=0)
    transformed: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    quarantined: int = Field(default=0, ge=0)
    published: int = Field(default=0, ge=0)
    duplicate_suppressed: int = Field(default=0, ge=0)
    reason_counts: dict[str, int] = Field(default_factory=dict)


@router.post("/check")
async def reconcile_check(
    body: PipelineCountsIn,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Reconcile one pipeline run's stage counts.

    Returns the :class:`ReconciliationReportV1`.  When the variance is
    unexplained or the yield changed abruptly, ``decision`` is ``block``,
    ``promotion_allowed`` is ``false``, the source is quarantined, and an
    alert is fired — all within this call (one cycle).
    """
    _verify_admin(authorization)
    counts = recon.PipelineCountsV1(**body.model_dump())
    report = recon.reconcile_run(engine, counts)
    return report.to_dict()


@router.get("/reports")
async def list_reconciliation_reports(
    source: str | None = None,
    decision: str | None = Query(default=None, pattern="^(allow|block)$"),
    limit: int = Query(default=50, ge=1, le=500),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Recent reconciliation reports, newest first."""
    _verify_admin(authorization)
    reports = recon.list_reports(
        engine, source_id=source, decision=decision, limit=limit
    )
    return {
        "source": source,
        "decision": decision,
        "count": len(reports),
        "reports": [r.to_dict() for r in reports],
    }


@router.get("/reports/{run_id}")
async def get_run_report(
    run_id: int,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """The reconciliation report for one pipeline run."""
    _verify_admin(authorization)
    report = recon.get_report_for_run(engine, run_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail=f"no reconciliation report for run {run_id}"
        )
    return report.to_dict()


@router.get("/baseline/{source_id}")
async def get_source_baseline(
    source_id: str,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """The trailing yield band for a source."""
    _verify_admin(authorization)
    baseline = recon.get_yield_baseline(engine, source_id)
    if baseline is None:
        return {"source_id": source_id, "baseline": None, "samples": 0}
    return {"source_id": source_id, "baseline": baseline, "samples": baseline["samples"]}
