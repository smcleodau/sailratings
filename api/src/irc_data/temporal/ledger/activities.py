"""OPS-01-02 — activities for the SourceRunWorkflow + schedule sync.

Every activity here is idempotent:

* ``open_source_run`` / ``close_source_run`` use ``(source_slug, run_key)``
  as the idempotency key (upsert / update).
* ``run_registered_adapter`` delegates to registered scrapers which use
  ``INSERT … ON CONFLICT`` (SPEC-13 §3.1).
* ``sync_schedules_from_register`` reconciles Temporal schedules with the
  register (create / update / pause) — safe to run on every cycle.
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
# Adapter dispatch — interim DP-00 CLI jobs + DP-01 SDK adapters
# ---------------------------------------------------------------------------


def _dispatch_table() -> dict[str, Callable[[], Any]]:
    """Map register slug → interim DP-00 collection callable.

    These wrap the existing scrapers.  Each underlying scraper already uses
    ``INSERT … ON CONFLICT`` (idempotent).  DP-01 SDK adapters resolve via
    ``adapter_class`` on the register row when present.
    """
    from irc_data.temporal.activities import scrape_activities

    return {
        "orc": scrape_activities.scrape_orc,
        "irc-tcc": scrape_activities.scrape_tcc,
        "sailsys": scrape_activities.scrape_sailsys,
        "topyacht": scrape_activities.scrape_topyacht,
        "sailing-news": scrape_activities.scrape_boat_news,
        "irc-certs": scrape_activities.scrape_certs_exhaustive,
    }


def _load_dotted(path: str) -> Callable[[], Any]:
    import importlib

    module_name, _, attr = path.rpartition(".")
    if not module_name:
        raise ImportError(f"invalid dotted path: {path!r}")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


@activity.defn
async def run_registered_adapter(record: dict, run_key: str) -> dict:
    """Execute the adapter for *record* under the per-domain concurrency cap.

    *record* is the dict returned by :func:`fetch_source_record`.
    """
    slug = record["slug"]
    domain = domain_for_url(record.get("base_url"))
    sem = _semaphore_for(domain)

    activity.logger.info(
        "source run start slug=%s domain=%s cap=%s run_key=%s",
        slug, domain or "(none)", max_concurrency_for_domain(domain), run_key,
    )

    async with sem:
        result: Any = None
        if record.get("adapter_class"):
            # DP-01 SDK adapter path — instantiate + collect().
            adapter_callable = _load_dotted(record["adapter_class"])
            result = await adapter_callable(record) if _iscoro_fn(adapter_callable) else adapter_callable(record)
        else:
            # Interim DP-00 path — dispatch by slug to the legacy scraper.
            fn = _dispatch_table().get(slug)
            if fn is None:
                activity.logger.warning(
                    "no DP-00 adapter registered for slug=%s; recording run only", slug
                )
                return {"records_written": 0, "adapter": "none", "note": "no adapter mapped"}
            result = await fn() if _iscoro_fn(fn) else fn()

    # Normalise the (loosely-typed) scraper return into a JSON-able dict.
    if isinstance(result, dict):
        return {"records_written": int(result.get("records_written", 0) or 0), **{k: v for k, v in result.items() if k != "records_written"}}
    return {"records_written": 0, "adapter_output": str(result)[:500]}


def _iscoro_fn(fn: Callable[..., Any]) -> bool:
    import inspect

    return inspect.iscoroutinefunction(fn)


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
    """
    from sqlalchemy import text

    engine = _resolve_engine(db_url)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
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
    activity.logger.info("ledger open slug=%s run_key=%s", source_slug, run_key)
    return {"source_slug": source_slug, "run_key": run_key, "status": "running"}


@activity.defn
async def close_source_run(
    source_slug: str,
    run_key: str,
    status: str,
    detail: str | None = None,
    stats: dict | None = None,
    workflow_run_id: str | None = None,
    db_url: str | None = None,
) -> dict:
    """Close the ledger row for this run (idempotent on (slug, run_key))."""
    import json

    from sqlalchemy import text

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
