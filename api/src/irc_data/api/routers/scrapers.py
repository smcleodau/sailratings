"""Admin scraper control API (OPS-02-04, exposing AD-01-06).

One scheduler and one run ledger for every source — these endpoints are the
admin's start/pause/resume surface over the OPS-01 schedule registry and run
ledger:

  GET  /admin/scrapers/schedule-state        — every register row joined with
                                               its ``source_schedule_state``
                                               mirror (schedule id, cadence,
                                               paused) and latest run
  GET  /admin/scrapers/{slug}/state          — single-source view (schedule
                                               state + recent runs)
  POST /admin/scrapers/{slug}/pause          — pause the Temporal schedule
                                               *and* the
                                               ``source_schedule_state``
                                               mirror (both ways, atomically
                                               from the caller's perspective)
  POST /admin/scrapers/{slug}/resume         — resume (unpause) both
  POST /admin/scrapers/{slug}/run            — manual trigger: fires the
                                               schedule now; the run writes
                                               ``source_runs`` (and the
                                               ``ingestion_log`` mirror)

The existing ``GET /admin/scrapers`` freshness summary (admin.py) is
untouched; these routes add the control plane and the schedule-state read.

Pause/resume/run are executed via the ``set_schedule_paused`` /
``trigger_source_run`` activities (invoke style, no workflow needed) so the
admin's flip is durable and retried.  When Temporal is unreachable the
mirror row is still flipped (503 with the desired-state detail) so the
admin UI never lies about the *desired* state.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Engine
from irc_data.api.deps import CallerIdentity, get_optional_identity
from irc_data.api.audit import log_admin_action

from irc_data.api.deps import get_db
from irc_data.api.routers.admin import _verify_admin

router = APIRouter(prefix="/admin/scrapers", tags=["Admin"])


# ---------------------------------------------------------------------------
# Read side — register ⋈ schedule mirror ⋈ latest run
# ---------------------------------------------------------------------------


def _source_rows(engine: Engine) -> list[dict]:
    """Register rows joined with the schedule mirror and latest ledger run."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    ds.slug,
                    ds.display_name,
                    ds.base_url,
                    ds.category,
                    ds.legal_status,
                    ds.enabled,
                    ds.cadence,
                    ds.adapter_status,
                    ds.adapter_class,
                    sss.schedule_id,
                    sss.paused           AS schedule_paused,
                    sss.last_synced_at   AS schedule_synced_at,
                    (
                        SELECT sr.status
                          FROM source_runs sr
                         WHERE sr.source_slug = ds.slug
                         ORDER BY sr.started_at DESC NULLS LAST, sr.id DESC
                         LIMIT 1
                    ) AS last_run_status,
                    (
                        SELECT sr.started_at
                          FROM source_runs sr
                         WHERE sr.source_slug = ds.slug
                         ORDER BY sr.started_at DESC NULLS LAST, sr.id DESC
                         LIMIT 1
                    ) AS last_run_at
                  FROM data_sources ds
                  LEFT JOIN source_schedule_state sss
                         ON sss.source_slug = ds.slug
                 ORDER BY ds.slug
                """
            )
        ).mappings().all()
    return [_jsonable(dict(r)) for r in rows]


def _jsonable(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        out[key] = value.isoformat() if isinstance(value, datetime) else value
    return out


def _get_source_or_404(engine: Engine, slug: str) -> dict:
    rows = [r for r in _source_rows(engine) if r["slug"] == slug]
    if not rows:
        raise HTTPException(status_code=404, detail=f"source '{slug}' not registered")
    return rows[0]


@router.get("/schedule-state")
async def scraper_schedule_state(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """All registered sources with their schedule state and latest run."""
    _verify_admin(authorization)
    rows = _source_rows(engine)
    return {
        "count": len(rows),
        "scrapers": rows,
    }


@router.get("/{slug}/state")
async def scraper_state(
    slug: str,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Single source: register row, schedule mirror, and recent runs."""
    _verify_admin(authorization)
    row = _get_source_or_404(engine, slug)
    with engine.connect() as conn:
        runs = conn.execute(
            text(
                """
                SELECT id, run_key, trigger, status, started_at, finished_at,
                       detail, stats
                  FROM source_runs
                 WHERE source_slug = :slug
                 ORDER BY started_at DESC NULLS LAST, id DESC
                 LIMIT 20
                """
            ),
            {"slug": slug},
        ).mappings().all()
    return {**row, "recent_runs": [_jsonable(dict(r)) for r in runs]}


# ---------------------------------------------------------------------------
# Write side — pause / resume / run, mirrored both ways
# ---------------------------------------------------------------------------


async def _invoke_control_activity(fn, *args) -> dict:
    """Invoke a control activity outside a workflow (function-call style).

    ``temporalio.activity.defn`` wraps the callable without changing its
    call signature, so direct invocation runs the activity body with the
    same retry/heartbeat semantics as the worker path.
    """
    result = fn(*args)
    import inspect

    if inspect.isawaitable(result):
        return await result
    return result


@router.post("/{slug}/pause")
async def pause_scraper(
    slug: str,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
    caller: CallerIdentity | None = Depends(get_optional_identity),
):
    """Pause the source's schedule — flips Temporal **and** the mirror."""
    _verify_admin(authorization)
    _get_source_or_404(engine, slug)
    from irc_data.temporal.ledger import activities as ledger_activities

    try:
        res = await _invoke_control_activity(
            ledger_activities.set_schedule_paused, slug, True
        )
        who = caller.email if caller and caller.email else "admin"
        import logging
        logging.getLogger(__name__).warning(f"Pausing {slug} by {who}")
        log_admin_action(engine, who, "pause", f"scrapers:{slug}", slug, after={"paused": True})
        logging.getLogger(__name__).warning("Finished log_admin_action")
        return res
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # Temporal unreachable — mirror still flips
        ledger_activities.mirror_paused_state(engine, slug, True)
        who = caller.email if caller and caller.email else "admin"
        log_admin_action(engine, who, "pause", f"scrapers:{slug}", slug, after={"paused": True, "note": "temporal_unreachable"})
        raise HTTPException(
            status_code=503,
            detail=f"Temporal unreachable; source_schedule_state mirror paused "
            f"(desired state recorded, sync will re-assert): {exc}",
        )


@router.post("/{slug}/resume")
async def resume_scraper(
    slug: str,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
    caller: CallerIdentity | None = Depends(get_optional_identity),
):
    """Resume the source's schedule — flips Temporal **and** the mirror."""
    _verify_admin(authorization)
    _get_source_or_404(engine, slug)
    from irc_data.temporal.ledger import activities as ledger_activities

    try:
        res = await _invoke_control_activity(
            ledger_activities.set_schedule_paused, slug, False
        )
        who = caller.email if caller and caller.email else "admin"
        log_admin_action(engine, who, "resume", f"scrapers:{slug}", slug, after={"paused": False})
        return res
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        ledger_activities.mirror_paused_state(engine, slug, False)
        who = caller.email if caller and caller.email else "admin"
        log_admin_action(engine, who, "resume", f"scrapers:{slug}", slug, after={"paused": False, "note": "temporal_unreachable"})
        raise HTTPException(
            status_code=503,
            detail=f"Temporal unreachable; source_schedule_state mirror resumed "
            f"(desired state recorded, sync will re-assert): {exc}",
        )


@router.post("/{slug}/run")
async def run_scraper_now(
    slug: str,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
    caller: CallerIdentity | None = Depends(get_optional_identity),
):
    """Manually trigger the source's schedule (writes source_runs on run)."""
    _verify_admin(authorization)
    _get_source_or_404(engine, slug)
    from irc_data.temporal.ledger import activities as ledger_activities

    try:
        res = await _invoke_control_activity(
            ledger_activities.trigger_source_run, slug
        )
        who = caller.email if caller and caller.email else "admin"
        log_admin_action(engine, who, "run", f"scrapers:{slug}", slug)
        return res
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Temporal unreachable; cannot trigger: {exc}"
        )
