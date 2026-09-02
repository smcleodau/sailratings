"""Run ledger API (OPS-01-03) — one truthful record of what every source did, when.

Endpoints behind the admin credential, backing the Admin design's source
health summary and recent-runs table:

  GET /admin/ledger/sources              — per-source latest-run and
                                           latest-new-data timestamps plus
                                           7-day aggregates (runs/fails/rows)
  GET /admin/ledger/runs                 — per-run records, queryable by
                                           source and time window
  GET /admin/ledger/runs/{run_id}        — run detail
  GET /admin/ledger/aggregates/daily     — per-day aggregates over a window
  GET /admin/ledger/reconcile            — ledger counts vs target table
                                           (DP-05-03 reconciliation seam)
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db
from irc_data.api.routers.admin import _verify_admin
from irc_data.db import run_ledger

router = APIRouter(prefix="/admin/ledger", tags=["Admin"])


def _parse_dt(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        # Accept both date and datetime ISO strings; "Z" for UTC.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"{name}: invalid ISO-8601 datetime {value!r}"
        )


@router.get("/sources")
async def ledger_sources(
    days: int = Query(default=run_ledger.DEFAULT_AGGREGATE_DAYS, ge=1, le=90),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Source health summary from the run ledger.

    Every source that has ever written a ledger row, with its latest-run
    and latest-new-data timestamps and trailing-window aggregates.
    """
    _verify_admin(authorization)
    now = datetime.now().astimezone()
    return {
        "as_of": now.isoformat(),
        "aggregate_days": days,
        "sources": run_ledger.get_source_health_summary(
            engine, now=now, aggregate_days=days
        ),
    }


@router.get("/runs")
async def ledger_runs(
    source: str | None = None,
    since: str | None = None,
    until: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Recent ledger runs, newest first — the recent-runs table.

    Filterable by ``source``, ``status`` and a ``since``/``until`` time
    window (ISO-8601). Each row carries started/completed/duration, status,
    records found/new/updated and the error message.
    """
    _verify_admin(authorization)
    runs = run_ledger.list_runs(
        engine,
        source=source,
        since=_parse_dt(since, "since"),
        until=_parse_dt(until, "until"),
        status=status,
        limit=limit,
    )
    return {
        "source": source,
        "since": since,
        "until": until,
        "count": len(runs),
        "runs": runs,
    }


@router.get("/runs/{run_id}")
async def ledger_run_detail(
    run_id: int,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Single run detail — drill-down behind a recent-runs table row."""
    _verify_admin(authorization)
    run = run_ledger.get_run(engine, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return run


@router.get("/aggregates/daily")
async def ledger_daily_aggregates(
    source: str | None = None,
    days: int = Query(default=run_ledger.DEFAULT_AGGREGATE_DAYS, ge=1, le=90),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Per-day aggregates (runs, fails, rows found/new) over ``days`` days.

    Days with no runs are returned with zero counts so the series is
    continuous. Pass ``source`` to scope to a single source.
    """
    _verify_admin(authorization)
    now = datetime.now().astimezone()
    return {
        "as_of": now.isoformat(),
        "source": source,
        "days": days,
        "series": run_ledger.get_daily_aggregates(
            engine, source=source, days=days, now=now
        ),
    }


@router.get("/reconcile")
async def ledger_reconcile(
    source: str,
    table: str,
    since: str | None = None,
    until: str | None = None,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Reconcile ledger-reported ``records_new`` against actual table rows.

    ``table`` must be a registered reconcile target (see
    ``run_ledger.RECONCILE_TARGETS``) — this is where DP-05-03 counts are
    checked against the ledger when that lands.
    """
    _verify_admin(authorization)
    try:
        return run_ledger.reconcile_counts(
            engine,
            source=source,
            table=table,
            since=_parse_dt(since, "since"),
            until=_parse_dt(until, "until"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
