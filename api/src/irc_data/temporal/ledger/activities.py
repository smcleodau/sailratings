"""OPS-01-02 / OPS-02-04 — activities for the SourceRunWorkflow + schedule sync.

Every activity here is idempotent:

* ``open_source_run`` / ``close_source_run`` use ``(source_slug, run_key)``
  as the idempotency key (upsert / update), and — until the admin reads
  ``source_runs`` — mirror the run into ``ingestion_log`` (OPS-02-04
  dual-write bridge).
* ``run_registered_adapter`` routes every legacy CLI scraper through the
  OPS-02-04 legacy adapter registry
  (:mod:`irc_data.temporal.legacy_adapters`) and falls back to DP-01 SDK
  adapters via ``adapter_class``.  Underlying scrapers use
  ``INSERT … ON CONFLICT`` (SPEC-13 §3.1).
* ``sync_schedules_from_register`` reconciles Temporal schedules with the
  register (create / update / pause) — safe to run on every cycle.
* ``set_schedule_paused`` / ``trigger_source_run`` are the admin-facing
  start/pause/resume helpers (AD-01-06): pause flips both the Temporal
  schedule and the ``source_schedule_state`` mirror, either direction.
"""

from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from temporalio import activity
from temporalio.exceptions import ApplicationError

from irc_data.temporal.schedules.cadence import (
    domain_for_url,
    max_concurrency_for_domain,
)


# ---------------------------------------------------------------------------
# Engine resolution (activities run outside the request context)
# ---------------------------------------------------------------------------


_ACTIVITY_ENGINE: Any = None


def _resolve_engine(db_url: str | None = None) -> Any:
    """Resolve the SQLAlchemy engine used by the ledger activities.

    Caches a single engine so repeated activity invocations don't create a
    fresh connection pool each time (which can starve the DB in tests).
    """
    global _ACTIVITY_ENGINE
    if db_url is not None:
        from sqlalchemy import create_engine

        return create_engine(db_url, future=True)
    if _ACTIVITY_ENGINE is None:
        url = os.environ.get("DATABASE_URL")
        if url:
            from sqlalchemy import create_engine

            _ACTIVITY_ENGINE = create_engine(url, future=True)
        else:
            from irc_data.db.connection import get_engine

            _ACTIVITY_ENGINE = get_engine()
    return _ACTIVITY_ENGINE


# ---------------------------------------------------------------------------
# Register lookup (fail-fast, non-retryable)
# ---------------------------------------------------------------------------


class SourceDisabledError(Exception):
    """Raised when a source run is attempted on a disabled/unapproved source."""


@activity.defn
async def fetch_source_record(source_slug: str) -> dict:
    """Load + validate the register row for *source_slug*.

    Raises a non-retryable ``ApplicationError`` when the source is missing,
    disabled, or not approved — this is the enforcement point for "nothing
    runs that isn't registered and enabled".
    """
    from irc_data.sources.registry import DataSource, SourceNotApprovedError

    engine = _resolve_engine()
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        row = session.execute(
            select(DataSource).where(DataSource.slug == source_slug)
        ).scalar_one_or_none()

    if row is None:
        raise ApplicationError(
            f"source '{source_slug}' is not registered",
            type="SourceNotApprovedError",
            non_retryable=True,
        )
    if not row.enabled:
        raise ApplicationError(
            f"source '{source_slug}' is disabled",
            type="SourceDisabledError",
            non_retryable=True,
        )
    if row.legal_status != "approved":
        raise ApplicationError(
            f"source '{source_slug}' legal_status='{row.legal_status}' (not approved)",
            type="SourceNotApprovedError",
            non_retryable=True,
        )

    return {
        "slug": row.slug,
        "display_name": row.display_name,
        "base_url": row.base_url,
        "category": row.category,
        "cadence": getattr(row, "cadence", None) or "nightly",
        "adapter_class": row.adapter_class,
        "legal_status": row.legal_status,
        "enabled": row.enabled,
        # OPS-02-04: optional per-source run timeout (seconds) from the
        # register row; the workflow falls back to a cadence-scaled default.
        "run_timeout_seconds": getattr(row, "run_timeout_seconds", None),
    }


# ---------------------------------------------------------------------------
# Per-domain concurrency caps (worker-process-local semaphores)
# ---------------------------------------------------------------------------
#
# The authoritative global cap is enforced at the *schedule* layer by Temporal
# (one schedule per source ⇒ one in-flight run per source).  These semaphores
# bound the concurrent in-flight *activities* per domain within a single
# worker process, which is where several sources can share one host (e.g. two
# register rows against app.sailsys.com.au).

_domain_semaphores: dict[str, asyncio.Semaphore] = {}
_domain_lock = threading.Lock()


def _semaphore_for(domain: str) -> asyncio.Semaphore:
    with _domain_lock:
        sem = _domain_semaphores.get(domain)
        if sem is None:
            sem = asyncio.Semaphore(max_concurrency_for_domain(domain))
            _domain_semaphores[domain] = sem
        return sem


# ---------------------------------------------------------------------------
# Adapter dispatch — OPS-02-04 legacy CLI registry + DP-01 SDK adapters
# ---------------------------------------------------------------------------


def _dispatch_table() -> dict[str, Callable[[], Any]]:
    """Map register slug → interim DP-00 collection callable.

    Superseded by :mod:`irc_data.temporal.legacy_adapters` (OPS-02-04) — kept
    as a fallback for any caller that still imports it directly, and to
    preserve the historical slug→activity mapping in one place.
    """
    from irc_data.temporal.activities import scrape_activities

    return {
        "orc": scrape_activities.scrape_orc,
        "irc-tcc": scrape_activities.scrape_tcc,
        "sailsys": scrape_activities.scrape_sailsys,
        "topyacht": scrape_activities.scrape_topyacht,
        "sailing-news": scrape_activities.scrape_boat_news,
        "irc-certs": scrape_activities.scrape_certs_exhaustive,
        # OPS-02-04 additions (previously unmapped legacy scrapers)
        "isora": scrape_activities.scrape_isora,
        "rhkyc": scrape_activities.scrape_rhkyc,
        "sailracehq": scrape_activities.scrape_sailracehq,
        "wayback-irc": scrape_activities.scrape_wayback,
    }


@activity.defn
async def run_registered_adapter(record: dict, run_key: str) -> dict:
    """Execute the adapter for *record* under the per-domain concurrency cap.

    *record* is the dict returned by :func:`fetch_source_record`.

    OPS-02-04: dispatch goes through the legacy adapter registry first
    (every legacy CLI scraper — orc, tcc, sailsys, topyacht, isora, rhkyc,
    sailracehq, cert discovery/parse, wayback), then DP-01 SDK adapters via
    ``adapter_class``.  A slug with no adapter records a ledger-only run so
    every register row still produces a run-ledger entry.
    """
    from irc_data.temporal import legacy_adapters

    slug = record["slug"]
    domain = domain_for_url(record.get("base_url"))
    sem = _semaphore_for(domain)

    activity.logger.info(
        "source run start slug=%s domain=%s cap=%s run_key=%s",
        slug, domain or "(none)", max_concurrency_for_domain(domain), run_key,
    )

    async with sem:
        result = await legacy_adapters.run_legacy_source(record)
        if result is None:
            activity.logger.warning(
                "no adapter registered for slug=%s; recording run only", slug
            )
            return {
                "records_written": 0,
                "adapter": "none",
                "note": "no adapter mapped",
            }

    # Normalise the (loosely-typed) scraper return into a JSON-able dict.
    if isinstance(result, dict):
        return {"records_written": int(result.get("records_written", 0) or 0), **{k: v for k, v in result.items() if k != "records_written"}}
    return {"records_written": 0, "adapter_output": str(result)[:500]}


# ---------------------------------------------------------------------------
# Run ledger (idempotent)
# ---------------------------------------------------------------------------


@activity.defn
async def open_source_run(
    source_slug: str,
    run_key: str,
    trigger: str = "schedule",
    workflow_id: str | None = None,
    schedule_id: str | None = None,
    db_url: str | None = None,
) -> dict:
    """Open (idempotently) a ledger row for this run.

    ``INSERT … ON CONFLICT (source_slug, run_key) DO UPDATE`` — re-opening an
    existing row just bumps the status back to ``running``.

    OPS-02-04 dual-write: mirrors the open into ``ingestion_log`` (one row
    per legacy source alias) and returns the mirrored ids so the close
    activity can update them.  The mirror is skipped when the run is already
    closed in ``source_runs`` (a re-open would resurrect a finished run).
    """
    from sqlalchemy import text

    from irc_data.temporal import legacy_adapters

    engine = _resolve_engine(db_url)
    now = datetime.now(timezone.utc)
    prior_status: str | None = None
    with engine.begin() as conn:
        prior_status = conn.execute(
            text(
                "SELECT status FROM source_runs "
                "WHERE source_slug = :slug AND run_key = :run_key"
            ),
            {"slug": source_slug, "run_key": run_key},
        ).scalar()
        conn.execute(
            text(
                """
                INSERT INTO source_runs
                    (source_slug, run_key, trigger, schedule_id, workflow_id,
                     status, started_at, created_at)
                VALUES (:slug, :run_key, :trigger, :schedule_id, :workflow_id,
                        'running', :now, :now)
                ON CONFLICT (source_slug, run_key) DO UPDATE SET
                    status = 'running',
                    workflow_id = EXCLUDED.workflow_id,
                    started_at = EXCLUDED.started_at
                """
            ),
            {
                "slug": source_slug,
                "run_key": run_key,
                "trigger": trigger,
                "schedule_id": schedule_id,
                "workflow_id": workflow_id,
                "now": now,
            },
        )
    ingestion_log_ids: dict[str, int] = {}
    if prior_status not in ("success", "failed"):
        ingestion_log_ids = legacy_adapters.mirror_run_open_to_ingestion_log(
            engine,
            source_slug,
            run_key=run_key,
            trigger=trigger,
            workflow_id=workflow_id,
            started_at=now,
        )
    activity.logger.info("ledger open slug=%s run_key=%s", source_slug, run_key)
    return {
        "source_slug": source_slug,
        "run_key": run_key,
        "status": "running",
        "ingestion_log_ids": ingestion_log_ids,
    }


@activity.defn
async def close_source_run(
    source_slug: str,
    run_key: str,
    status: str,
    detail: str | None = None,
    stats: dict | None = None,
    workflow_run_id: str | None = None,
    ingestion_log_ids: dict | None = None,
    db_url: str | None = None,
) -> dict:
    """Close the ledger row for this run (idempotent on (slug, run_key)).

    OPS-02-04 dual-write: also closes the mirrored ``ingestion_log`` rows
    (opened by :func:`open_source_run` and passed through the workflow as
    *ingestion_log_ids*; looked up by ``run_key`` when absent).
    """
    import json

    from sqlalchemy import text

    from irc_data.temporal import legacy_adapters

    engine = _resolve_engine(db_url)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE source_runs
                   SET status = :status,
                       detail = :detail,
                       stats = CAST(:stats AS json),
                       workflow_run_id = COALESCE(:workflow_run_id, workflow_run_id),
                       finished_at = :now
                 WHERE source_slug = :slug AND run_key = :run_key
                """
            ),
            {
                "slug": source_slug,
                "run_key": run_key,
                "status": status,
                "detail": detail,
                "stats": json.dumps(stats or {}),
                "workflow_run_id": workflow_run_id,
                "now": now,
            },
        )
    legacy_adapters.mirror_run_close_to_ingestion_log(
        engine,
        source_slug,
        run_key=run_key,
        status=status,
        detail=detail,
        stats=stats,
        log_ids=ingestion_log_ids,
    )
    activity.logger.info("ledger close slug=%s run_key=%s status=%s", source_slug, run_key, status)
    return {"source_slug": source_slug, "run_key": run_key, "status": status}


# ---------------------------------------------------------------------------
# Schedule sync (drives the reconciliation loop)
# ---------------------------------------------------------------------------


@activity.defn
async def sync_schedules_from_register(db_url: str | None = None) -> dict:
    """Reconcile Temporal schedules with the ``data_sources`` register.

    Safe to run every cycle: creates schedules for new enabled+approved
    sources, updates changed cadences, pauses disabled/unapproved ones.
    """
    from irc_data.temporal.schedules.registry import ScheduleRegistry

    engine = _resolve_engine(db_url)
    registry = await ScheduleRegistry.connect()
    summary = await registry.sync_from_register(engine)
    activity.logger.info("schedule sync complete: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# Admin start / pause / resume (AD-01-06) — paused mirrored both ways
# ---------------------------------------------------------------------------


def mirror_paused_state(
    engine: Any,
    slug: str,
    paused: bool,
    *,
    notes: str | None = None,
) -> None:
    """Upsert the ``source_schedule_state`` mirror for *slug*.

    This is the DB side of "paused mirrored both ways" — Temporal's schedule
    is authoritative at runtime; this mirror is what the admin (and
    ``psql``) reads, and what :func:`set_schedule_paused` flips together
    with the Temporal schedule.
    """
    from sqlalchemy import text

    from irc_data.temporal.schedules.cadence import schedule_id_for_slug

    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO source_schedule_state
                    (source_slug, schedule_id, cadence, paused, notes, last_synced_at)
                VALUES (:slug, :schedule_id, :cadence, :paused, :notes, :now)
                ON CONFLICT (source_slug) DO UPDATE SET
                    paused = EXCLUDED.paused,
                    notes = EXCLUDED.notes,
                    last_synced_at = EXCLUDED.last_synced_at
                """
            ),
            {
                "slug": slug,
                "schedule_id": schedule_id_for_slug(slug),
                "cadence": _cadence_for_slug(engine, slug),
                "paused": paused,
                "notes": notes or ("paused via admin" if paused else "resumed via admin"),
                "now": now,
            },
        )


def _cadence_for_slug(engine: Any, slug: str) -> str:
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            cadence = conn.execute(
                text("SELECT cadence FROM data_sources WHERE slug = :slug"),
                {"slug": slug},
            ).scalar()
        return cadence or "nightly"
    except Exception:
        return "nightly"


@activity.defn
async def set_schedule_paused(
    slug: str,
    paused: bool,
    note: str | None = None,
    db_url: str | None = None,
) -> dict:
    """Pause or resume a source's schedule — mirrored both ways (AD-01-06).

    1. Flips the Temporal schedule's paused bit (creating the schedule from
       the register row first when it doesn't exist yet, so the register →
       schedule mapping stays total).
    2. Flips the ``source_schedule_state`` mirror row.

    The reverse direction (Temporal schedule edited out-of-band → mirror) is
    handled by :func:`sync_schedules_from_register`, which re-asserts the
    register state and re-mirrors ``source_schedule_state`` every cycle.

    Raises ``KeyError`` when *slug* is not in the register.
    """
    from irc_data.temporal.schedules.registry import ScheduleRegistry

    engine = _resolve_engine(db_url)
    registry = await ScheduleRegistry.connect()

    source = _load_register_source(engine, slug)
    if source is None:
        raise KeyError(f"source '{slug}' is not registered")

    note = note or ("paused via admin" if paused else "resumed via admin")
    schedule_id = await registry.ensure_schedule_id(source)

    handle = registry.client.get_schedule_handle(schedule_id)
    if paused:
        await handle.pause(note=note)
    else:
        await handle.unpause(note=note)

    mirror_paused_state(engine, slug, paused, notes=note)
    activity.logger.info("schedule %s slug=%s", "paused" if paused else "resumed", slug)
    return {"slug": slug, "schedule_id": schedule_id, "paused": paused}


@activity.defn
async def trigger_source_run(
    slug: str,
    run_key: str | None = None,
    note: str = "manual trigger via admin",
    db_url: str | None = None,
) -> dict:
    """Fire a source's schedule immediately (the admin "start"/run-now).

    Creates the schedule from the register row first when missing so a
    manual trigger works even before the first sync cycle.  The triggered
    action starts a ``SourceRunWorkflow`` which writes the ``source_runs``
    ledger row (and the ``ingestion_log`` mirror).

    Raises ``KeyError`` when *slug* is not in the register.
    """
    from irc_data.temporal.schedules.registry import ScheduleRegistry

    engine = _resolve_engine(db_url)
    registry = await ScheduleRegistry.connect()

    source = _load_register_source(engine, slug)
    if source is None:
        raise KeyError(f"source '{slug}' is not registered")

    schedule_id = await registry.ensure_schedule_id(source)
    handle = registry.client.get_schedule_handle(schedule_id)
    await handle.trigger(note=note)
    activity.logger.info("schedule triggered slug=%s run_key=%s", slug, run_key)
    return {
        "slug": slug,
        "schedule_id": schedule_id,
        "triggered": True,
        "run_key": run_key,
    }


def _load_register_source(engine: Any, slug: str) -> Any | None:
    """Load a register row as a plain attribute object (detached)."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from irc_data.sources.registry import DataSource

    with Session(engine) as session:
        row = session.execute(
            select(DataSource).where(DataSource.slug == slug)
        ).scalar_one_or_none()
        if row is None:
            return None
        return type(
            "_Src",
            (),
            {
                "slug": row.slug,
                "base_url": row.base_url,
                "cadence": getattr(row, "cadence", None) or "nightly",
                "enabled": bool(row.enabled),
                "legal_status": row.legal_status,
                "retry_policy": getattr(row, "retry_policy", None),
            },
        )()
