"""Admin dashboard endpoints — at-a-glance metrics for the admin landing page.

Surfaces a consolidated snapshot of platform health in one call: row counts,
revenue/order summary, the moderation queue, scraper freshness & alerts, and
recent admin activity. The public ``/health`` and ``/pipeline/status`` routers
cover data-quality and ingestion in depth; this router adds the revenue and
activity picture that those endpoints lack, and ties everything together behind
the same Bearer admin gate as the rest of ``/admin/*``.
"""

import datetime as _dt
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(prefix="/admin/dashboard", tags=["Admin", "Dashboard"])

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def _verify_admin(authorization: str | None) -> None:
    expected = f"Bearer {ADMIN_PASSWORD}"
    if not ADMIN_PASSWORD or not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Section helpers ──────────────────────────────────────────────────────


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                (SELECT count(*) FROM boats)            AS boats,
                (SELECT count(*) FROM race_results)      AS race_results,
                (SELECT count(*) FROM race_results WHERE boat_id IS NOT NULL) AS matched_results,
                (SELECT count(*) FROM irc_certificates)  AS irc_certificates,
                (SELECT count(*) FROM orc_certificates)  AS orc_certificates,
                (SELECT count(*) FROM tcc_snapshots)     AS tcc_snapshots,
                (SELECT count(*) FROM design_classes)    AS design_classes,
                (SELECT count(*) FROM orders)            AS orders
        """)).first()
    return {k: (row._mapping[k] or 0) for k in row._mapping.keys()} if row else {}


def _revenue_summary(engine: Engine) -> dict[str, Any]:
    """Order counts by status + revenue totals (paid orders only)."""
    with engine.connect() as conn:
        status_rows = conn.execute(text("""
            SELECT status, count(*) AS n, coalesce(sum(amount_cents), 0) AS revenue
            FROM orders GROUP BY status ORDER BY status
        """)).fetchall()
        by_status = {
            r.status: {"count": r.n, "revenue_cents": int(r.revenue)}
            for r in status_rows
        }

        currency_rows = conn.execute(text("""
            SELECT currency, count(*) AS n, coalesce(sum(amount_cents), 0) AS revenue
            FROM orders WHERE status IN ('paid', 'generated')
            GROUP BY currency ORDER BY revenue DESC
        """)).fetchall()
        by_currency = [
            {"currency": r.currency, "count": r.n, "revenue_cents": int(r.revenue)}
            for r in currency_rows
        ]

        total = conn.execute(text("""
            SELECT count(*) AS n, coalesce(sum(amount_cents), 0) AS revenue
            FROM orders WHERE status IN ('paid', 'generated')
        """)).first()

    return {
        "orders_by_status": by_status,
        "revenue_by_currency": by_currency,
        "paid_orders": total.n if total else 0,
        "revenue_cents": int(total.revenue) if total else 0,
    }


def _moderation(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        corrections = conn.execute(text("""
            SELECT status, count(*) AS n
            FROM boat_corrections GROUP BY status
        """)).fetchall()
        pending = conn.execute(text(
            "SELECT count(*) FROM boat_corrections WHERE status = 'pending'"
        )).scalar() or 0
        edits = conn.execute(text("SELECT count(*) FROM admin_edits")).scalar() or 0
        conversations = conn.execute(text(
            "SELECT count(*) FROM admin_conversations"
        )).scalar() or 0
    return {
        "pending_corrections": pending,
        "corrections_by_status": {r.status: r.n for r in corrections},
        "total_admin_edits": edits,
        "conversations": conversations,
    }


def _ingestion(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        # Latest run per source.
        rows = conn.execute(text("""
            SELECT DISTINCT ON (source)
                   source, started_at, completed_at, status,
                   records_found, records_new, error_message
            FROM ingestion_log ORDER BY source, started_at DESC
        """)).fetchall()
        sources = [
            {
                "source": r.source,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
                "records_found": r.records_found,
                "records_new": r.records_new,
                "error": r.error_message,
            }
            for r in rows
        ]

        freshness = conn.execute(text("""
            SELECT
                (SELECT max(snapshot_date) FROM tcc_snapshots)    AS latest_irc_snapshot,
                (SELECT max(snapshot_date) FROM orc_certificates) AS latest_orc_snapshot,
                (SELECT max(event_date) FROM race_results)        AS latest_race_result
        """)).first()

    return {
        "sources": sources,
        "freshness": {k: (v.isoformat() if v else None) for k, v in dict(freshness._mapping).items()} if freshness else {},
    }


def _alerts(engine: Engine, sources: list[dict]) -> list[dict[str, Any]]:
    """Flag scrapers not seen in >48h and any source whose last run failed."""
    now = _dt.datetime.now(_dt.timezone.utc)
    alerts: list[dict[str, Any]] = []
    for src in sources:
        started = src.get("started_at")
        if not started:
            continue
        last = _dt.datetime.fromisoformat(started)
        if last.tzinfo is None:
            last = last.replace(tzinfo=_dt.timezone.utc)
        hours_ago = (now - last).total_seconds() / 3600
        if hours_ago > 48:
            alerts.append({
                "type": "stale_source",
                "source": src["source"],
                "last_run": started,
                "hours_ago": round(hours_ago, 1),
                "message": f"{src['source']} not scraped in {hours_ago:.0f} hours",
            })
        if src.get("status") and src["status"] != "completed":
            alerts.append({
                "type": "run_failed",
                "source": src["source"],
                "last_run": started,
                "status": src["status"],
                "error": src.get("error"),
            })
    return alerts


def _recent_activity(engine: Engine, limit: int = 10) -> dict[str, Any]:
    with engine.connect() as conn:
        edits = conn.execute(text("""
            SELECT id, edited_at, table_name, pk_value, column_name, old_value, new_value
            FROM admin_edits ORDER BY edited_at DESC LIMIT :lim
        """), {"lim": limit}).fetchall()

        orders = conn.execute(text("""
            SELECT o.id, o.order_token, o.boat_id, b.boat_name, o.email,
                   o.amount_cents, o.currency, o.status, o.created_at, o.paid_at
            FROM orders o LEFT JOIN boats b ON b.id = o.boat_id
            ORDER BY o.created_at DESC LIMIT :lim
        """), {"lim": limit}).fetchall()

    return {
        "recent_edits": [
            {
                "id": r.id,
                "edited_at": r.edited_at.isoformat() if r.edited_at else None,
                "table": r.table_name,
                "pk_value": r.pk_value,
                "column": r.column_name,
                "old_value": r.old_value,
                "new_value": r.new_value,
            }
            for r in edits
        ],
        "recent_orders": [
            {
                "id": r.id,
                "order_token": str(r.order_token) if r.order_token else None,
                "boat_id": r.boat_id,
                "boat_name": r.boat_name,
                "email": r.email,
                "amount_cents": r.amount_cents,
                "currency": r.currency,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "paid_at": r.paid_at.isoformat() if r.paid_at else None,
            }
            for r in orders
        ],
    }


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get("")
def dashboard_overview(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Consolidated admin dashboard snapshot.

    Returns everything needed to render the admin landing page in a single
    request: row counts, revenue/order summary, moderation queue, scraper
    freshness + alerts, and recent activity.
    """
    _verify_admin(authorization)

    counts = _counts(engine)
    revenue = _revenue_summary(engine)
    moderation = _moderation(engine)
    ingestion = _ingestion(engine)
    alerts = _alerts(engine, ingestion["sources"])

    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "counts": counts,
        "revenue": revenue,
        "moderation": moderation,
        "ingestion": ingestion,
        "alerts": alerts,
    }


@router.get("/revenue")
def dashboard_revenue(
    days: int = Query(30, ge=1, le=365, description="Window in days for the daily series"),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Detailed revenue breakdown: order counts by status, revenue by currency,
    and a per-day paid-order series for the requested window."""
    _verify_admin(authorization)

    summary = _revenue_summary(engine)

    with engine.connect() as conn:
        series = conn.execute(text("""
            SELECT date_trunc('day', paid_at)::date AS day,
                   count(*) AS orders,
                   coalesce(sum(amount_cents), 0) AS revenue_cents
            FROM orders
            WHERE paid_at IS NOT NULL
              AND paid_at >= now() - make_interval(days => :days)
            GROUP BY day ORDER BY day
        """), {"days": days}).fetchall()

    summary["days"] = days
    summary["daily_series"] = [
        {
            "day": r.day.isoformat() if r.day else None,
            "orders": r.orders,
            "revenue_cents": int(r.revenue_cents),
        }
        for r in series
    ]
    return summary


@router.get("/recent-orders")
def dashboard_recent_orders(
    limit: int = Query(20, ge=1, le=200),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Most recent orders (activity feed) — newest first."""
    _verify_admin(authorization)

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT o.id, o.order_token, o.boat_id, b.boat_name, o.email,
                   o.amount_cents, o.currency, o.status, o.created_at, o.paid_at
            FROM orders o LEFT JOIN boats b ON b.id = o.boat_id
            ORDER BY o.created_at DESC LIMIT :lim
        """), {"lim": limit}).fetchall()

    return {
        "orders": [
            {
                "id": r.id,
                "order_token": str(r.order_token) if r.order_token else None,
                "boat_id": r.boat_id,
                "boat_name": r.boat_name,
                "email": r.email,
                "amount_cents": r.amount_cents,
                "currency": r.currency,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "paid_at": r.paid_at.isoformat() if r.paid_at else None,
            }
            for r in rows
        ],
    }
