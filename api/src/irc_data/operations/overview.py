"""Admin overview — "what needs a human today", in one call (AD-01-13).

This module is the read/aggregate model behind
``GET /v1/admin/overview`` and the admin ``/admin`` Today screen.  It
answers, in a single payload:

  * ``sources``   — every governed source from the register
                    (``data_sources``) joined to its
                    ``source_schedule_state`` mirror (cadence, paused,
                    schedule id) and its run-ledger truth from
                    ``ingestion_log`` (OPS-01-03): last run, last status,
                    ``stale_days`` (whole days since the last successful
                    run, vs the source's freshness budget) and ``last14``
                    (per-day run outcomes for the trailing 14 days, for
                    the sparkline).
  * ``today``     — aggregates for the current UTC day: runs, failures,
                    rows found, rows new (``today.new``).
  * ``runs_per_day`` — per-day run totals over the trailing 60 days
                    (zero-run days are emitted with ``runs: 0`` so the
                    Today screen can draw the zero-run bands).
  * ``dupes``     — ``dupe_review_queue`` pending counts by tier plus
                    ``pending_clusters`` (distinct pending cluster ids).
  * ``corrections`` — ``boat_corrections`` pending count.
  * ``fleet``     — boats census plus completeness meters (share of boats
                    carrying design / country / year-built data).
  * ``attention`` — the server-side attention rules (SPEC-22 §3.1): one
                    item per thing that needs a human *today* — stale
                    nightly (scheduled, unpaused) sources, failed latest
                    runs, sources that have never run, pending dupe
                    clusters, pending corrections.

Portability note (same discipline as ``db.run_ledger``): queries avoid
Postgres-only constructs — cutoffs are computed in Python and bound as
parameters, day buckets use ``CAST(... AS DATE)`` on Postgres and
``date(...)`` on SQLite, and conditional aggregation uses ``CASE WHEN``.
The layer therefore runs identically against SQLite (contract tests) and
Postgres (production).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

SCHEMA_VERSION = "admin-overview-v1"

#: Trailing window for the per-day runs series.
RUNS_PER_DAY_DAYS = 60

#: Trailing window for each source's ``last14`` sparkline.
LAST14_DAYS = 14

#: Default freshness budget (hours) by cadence when the register row does
#: not carry an explicit ``staleness_budget_hours``.  Mirrors the
#: OPS-01-01 scheduling-policy design defaults (``sources.scheduling``).
CADENCE_BUDGET_HOURS: dict[str, float] = {
    "nightly": 48.0,
    "daily": 48.0,
    "weekly": 8 * 24.0,
    "annual": 370 * 24.0,
    "manual": 10 * 365 * 24.0,
}
DEFAULT_BUDGET_HOURS = CADENCE_BUDGET_HOURS["nightly"]


# ---------------------------------------------------------------------------
# Small helpers (SQLite/Postgres portable)
# ---------------------------------------------------------------------------


def _table_exists(engine: Engine, table: str) -> bool:
    """True when ``table`` exists (SQLite + Postgres portable)."""
    with engine.connect() as conn:
        if conn.dialect.name == "sqlite":
            row = conn.execute(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = :t"
                ),
                {"t": table},
            ).first()
        else:
            row = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = :t"
                ),
                {"t": table},
            ).first()
    return row is not None


def _as_dt(value: Any) -> datetime | None:
    """Coerce a DB timestamp to naive UTC (see ``db.run_ledger._as_dt``)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _day_key(value: Any) -> str | None:
    """Normalise a day bucket (str on SQLite, date on Postgres) to YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _cadence_days(cadence: str | None) -> float:
    """Nominal cadence length in days (used for attention rules)."""
    c = (cadence or "nightly").strip().lower()
    if c in {"nightly", "daily"}:
        return 1.0
    if c == "weekly":
        return 7.0
    if c == "annual":
        return 365.0
    return 1.0


def _budget_hours(row: dict[str, Any]) -> float:
    """Freshness budget for a register row (explicit value wins)."""
    explicit = row.get("staleness_budget_hours")
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass
    return CADENCE_BUDGET_HOURS.get(
        (row.get("cadence") or "nightly").strip().lower(),
        DEFAULT_BUDGET_HOURS,
    )


# ---------------------------------------------------------------------------
# Sources — register ⋈ schedule mirror ⋈ ingestion_log
# ---------------------------------------------------------------------------


def _load_register(engine: Engine) -> dict[str, dict[str, Any]]:
    """Register rows (``data_sources``) keyed by slug.

    Every field is defensively optional — older dev databases predate
    some columns.  When the register table itself is absent (unit-test
    fixtures) the overview simply treats every ledger source as
    unregistered.
    """
    if not _table_exists(engine, "data_sources"):
        return {}
    with engine.connect() as conn:
        cols = {
            r[1]
            for r in conn.execute(text("PRAGMA table_info(data_sources)")).fetchall()
        } if conn.dialect.name == "sqlite" else {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'data_sources'"
                )
            ).fetchall()
        }
        wanted = [
            "slug",
            "display_name",
            "cadence",
            "enabled",
            "legal_status",
            "adapter_status",
            "staleness_budget_hours",
            "cadence_class",
        ]
        select_cols = [c for c in wanted if c in cols]
        if "slug" not in select_cols:  # pragma: no cover - pathological
            return {}
        rows = conn.execute(
            text(f"SELECT {', '.join(select_cols)} FROM data_sources")
        ).mappings().all()
    return {str(r["slug"]): dict(r) for r in rows}


def _load_schedule_state(engine: Engine) -> dict[str, dict[str, Any]]:
    """``source_schedule_state`` mirror keyed by source slug."""
    if not _table_exists(engine, "source_schedule_state"):
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT source_slug, schedule_id, cadence, paused, notes,
                       last_synced_at
                FROM source_schedule_state
                """
            )
        ).mappings().all()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        d["last_synced_at"] = _as_dt(d.get("last_synced_at"))
        out[str(d["source_slug"])] = d
    return out


def _load_ledger_rollups(
    engine: Engine, now: datetime, last14_start: datetime
) -> dict[str, dict[str, Any]]:
    """Per-source rollups from ``ingestion_log`` keyed by source."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    source,
                    MAX(started_at) AS last_started_at,
                    MAX(CASE WHEN status = 'completed' THEN completed_at END)
                        AS last_completed_at,
                    MAX(CASE WHEN status = 'completed' THEN started_at END)
                        AS last_success_at,
                    MAX(CASE WHEN COALESCE(records_new, 0) > 0
                             THEN started_at END) AS last_new_data_at,
                    COUNT(*) AS runs_total,
                    SUM(CASE WHEN started_at >= :cutoff14 THEN 1 ELSE 0 END)
                        AS runs_14d,
                    SUM(CASE WHEN started_at >= :cutoff14
                              AND status = 'failed' THEN 1 ELSE 0 END)
                        AS failed_14d
                FROM ingestion_log
                GROUP BY source
                """
            ),
            {"cutoff14": last14_start},
        ).mappings().all()

        # Latest *terminal* status per source: the status of the most
        # recently started run that has finished (running runs are open,
        # not failed).  Python-side pick keeps this portable.
        latest_rows = conn.execute(
            text(
                """
                SELECT source, status, started_at
                FROM ingestion_log
                ORDER BY source, started_at DESC, id DESC
                """
            )
        ).mappings().all()

    latest_status: dict[str, str] = {}
    for r in latest_rows:
        slug = str(r["source"])
        if slug in latest_status:
            continue
        latest_status[slug] = str(r["status"]) if r["status"] else "unknown"

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        slug = str(r["source"])
        out[slug] = {
            "last_started_at": _as_dt(r["last_started_at"]),
            "last_completed_at": _as_dt(r["last_completed_at"]),
            "last_success_at": _as_dt(r["last_success_at"]),
            "last_new_data_at": _as_dt(r["last_new_data_at"]),
            "runs_total": int(r["runs_total"] or 0),
            "runs_14d": int(r["runs_14d"] or 0),
            "failed_14d": int(r["failed_14d"] or 0),
            "last_status": latest_status.get(slug),
        }
    return out


def _load_last14(
    engine: Engine, last14_start: datetime
) -> dict[str, dict[str, dict[str, int]]]:
    """Per-source, per-day run outcomes over the trailing 14 days.

    Returns ``{source: {day: {"runs": n, "failed": n, "new": n}}}``.
    """
    day_expr = (
        "date(started_at)"
        if engine.dialect.name == "sqlite"
        else "CAST(started_at AS DATE)"
    )
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT
                    source,
                    {day_expr} AS day,
                    COUNT(*) AS runs,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)
                        AS failed,
                    SUM(COALESCE(records_new, 0)) AS rows_new
                FROM ingestion_log
                WHERE started_at >= :cutoff
                GROUP BY source, {day_expr}
                """
            ),
            {"cutoff": last14_start},
        ).mappings().all()
    out: dict[str, dict[str, dict[str, int]]] = {}
    for r in rows:
        day = _day_key(r["day"])
        if day is None:
            continue
        out.setdefault(str(r["source"]), {})[day] = {
            "runs": int(r["runs"] or 0),
            "failed": int(r["failed"] or 0),
            "new": int(r["rows_new"] or 0),
        }
    return out


def _build_sources(
    engine: Engine, now: datetime
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Assemble the sources[] section.

    Returns the source rows plus a small context dict the attention rules
    re-use (so we never query twice).
    """
    today = now.date()
    last14_start = datetime.combine(today - timedelta(days=LAST14_DAYS - 1), time.min)

    register = _load_register(engine)
    schedule = _load_schedule_state(engine)
    rollups = _load_ledger_rollups(engine, now, last14_start)
    last14 = _load_last14(engine, last14_start)

    slugs = sorted(set(register) | set(schedule) | set(rollups))
    sources: list[dict[str, Any]] = []
    for slug in slugs:
        reg = register.get(slug, {})
        sch = schedule.get(slug, {})
        roll = rollups.get(slug, {})

        cadence = (sch.get("cadence") or reg.get("cadence") or "nightly")
        cadence = str(cadence).strip().lower() or "nightly"
        paused = bool(sch.get("paused", False))
        enabled = bool(reg.get("enabled", True))
        budget_hours = _budget_hours({**reg, "cadence": cadence})

        last_started = roll.get("last_started_at")
        last_success = roll.get("last_success_at")
        last_completed = roll.get("last_completed_at")
        last_new_data = roll.get("last_new_data_at")

        # stale_days: whole days since the last *successful* run (or since
        # the last run at all when nothing ever succeeded).  ``None`` when
        # the source has never run — the UI renders that as "never".
        anchor = last_success or last_completed or last_started
        if anchor is None:
            stale_days: int | None = None
        else:
            stale_days = max(0, (now - anchor).days)

        stale = (
            True
            if anchor is None
            else (now - anchor).total_seconds() > budget_hours * 3600
        )

        day_map = last14.get(slug, {})
        spark: list[dict[str, Any]] = []
        for offset in range(LAST14_DAYS):
            day = (today - timedelta(days=LAST14_DAYS - 1 - offset)).isoformat()
            cell = day_map.get(day, {"runs": 0, "failed": 0, "new": 0})
            spark.append(
                {
                    "day": day,
                    "runs": cell["runs"],
                    "failed": cell["failed"],
                    "new": cell["new"],
                }
            )

        sources.append(
            {
                "slug": slug,
                "display_name": reg.get("display_name") or slug,
                "cadence": cadence,
                "enabled": enabled,
                "paused": paused,
                "schedule_id": sch.get("schedule_id"),
                "legal_status": reg.get("legal_status"),
                "adapter_status": reg.get("adapter_status"),
                "last_run_at": (
                    last_started.isoformat() if last_started else None
                ),
                "last_completed_at": (
                    last_completed.isoformat() if last_completed else None
                ),
                "last_status": roll.get("last_status"),
                "runs_total": roll.get("runs_total", 0),
                "runs_14d": roll.get("runs_14d", 0),
                "failed_14d": roll.get("failed_14d", 0),
                "stale_days": stale_days,
                "budget_hours": budget_hours,
                "stale": stale,
                "last14": spark,
            }
        )

    ctx = {
        "register": register,
        "schedule": schedule,
        "last14_start": last14_start,
    }
    return sources, ctx


# ---------------------------------------------------------------------------
# Today aggregates + runs_per_day (60d)
# ---------------------------------------------------------------------------


def _today_aggregates(engine: Engine, now: datetime) -> dict[str, Any]:
    """Runs / failures / rows for the current UTC day."""
    today_start = datetime.combine(now.date(), time.min)
    with engine.connect() as conn:
        r = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS runs,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)
                        AS failed,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)
                        AS completed,
                    SUM(COALESCE(records_found, 0)) AS found,
                    SUM(COALESCE(records_new, 0)) AS new,
                    SUM(COALESCE(records_updated, 0)) AS updated
                FROM ingestion_log
                WHERE started_at >= :today_start
                """
            ),
            {"today_start": today_start},
        ).mappings().first()
    return {
        "date": now.date().isoformat(),
        "runs": int(r["runs"] or 0),
        "completed": int(r["completed"] or 0),
        "failed": int(r["failed"] or 0),
        "found": int(r["found"] or 0),
        "new": int(r["new"] or 0),
        "updated": int(r["updated"] or 0),
    }


def _runs_per_day(engine: Engine, now: datetime, days: int) -> list[dict[str, Any]]:
    """Per-day run totals over the trailing ``days`` days (zero-filled)."""
    days = max(1, min(int(days), 120))
    today = now.date()
    cutoff = datetime.combine(today - timedelta(days=days - 1), time.min)
    day_expr = (
        "date(started_at)"
        if engine.dialect.name == "sqlite"
        else "CAST(started_at AS DATE)"
    )
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT
                    {day_expr} AS day,
                    COUNT(*) AS runs,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)
                        AS failed
                FROM ingestion_log
                WHERE started_at >= :cutoff
                GROUP BY {day_expr}
                """
            ),
            {"cutoff": cutoff},
        ).mappings().all()
    by_day = {}
    for r in rows:
        key = _day_key(r["day"])
        if key is not None:
            by_day[key] = r
    out: list[dict[str, Any]] = []
    for offset in range(days):
        day = (today - timedelta(days=days - 1 - offset)).isoformat()
        r = by_day.get(day)
        out.append(
            {
                "day": day,
                "runs": int(r["runs"] or 0) if r else 0,
                "failed": int(r["failed"] or 0) if r else 0,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Dupes / corrections / fleet
# ---------------------------------------------------------------------------


def _dupe_summary(engine: Engine) -> dict[str, Any]:
    """``dupe_review_queue`` pending counts by tier + pending clusters."""
    empty = {"available": False, "pending": 0, "pending_clusters": 0, "by_tier": {}}
    if not _table_exists(engine, "dupe_review_queue"):
        return empty
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT tier, COUNT(*) AS pending
                FROM dupe_review_queue
                WHERE verdict = 'PENDING'
                GROUP BY tier
                ORDER BY tier
                """
            )
        ).mappings().all()
        clusters = conn.execute(
            text(
                "SELECT COUNT(DISTINCT cluster_id) AS c "
                "FROM dupe_review_queue WHERE verdict = 'PENDING'"
            )
        ).scalar_one()
    by_tier = {str(r["tier"]): int(r["pending"] or 0) for r in rows}
    return {
        "available": True,
        "pending": sum(by_tier.values()),
        "pending_clusters": int(clusters or 0),
        "by_tier": by_tier,
    }


def _corrections_summary(engine: Engine) -> dict[str, Any]:
    """``boat_corrections`` pending count."""
    if not _table_exists(engine, "boat_corrections"):
        return {"available": False, "pending": 0}
    with engine.connect() as conn:
        pending = conn.execute(
            text("SELECT COUNT(*) FROM boat_corrections WHERE status = 'pending'")
        ).scalar_one()
    return {"available": True, "pending": int(pending or 0)}


def _fleet_summary(engine: Engine) -> dict[str, Any]:
    """Boats census + completeness meters.

    Completeness is the share of boats carrying the field; the Today
    screen renders these as meters.
    """
    if not _table_exists(engine, "boats"):
        return {"available": False, "boats": 0, "completeness": {}}
    with engine.connect() as conn:
        r = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS boats,
                    SUM(CASE WHEN design IS NOT NULL AND design <> ''
                             THEN 1 ELSE 0 END) AS with_design,
                    SUM(CASE WHEN design_canonical IS NOT NULL
                               AND design_canonical <> ''
                             THEN 1 ELSE 0 END) AS with_design_canonical,
                    SUM(CASE WHEN country IS NOT NULL AND country <> ''
                             THEN 1 ELSE 0 END) AS with_country,
                    SUM(CASE WHEN year_built IS NOT NULL
                             THEN 1 ELSE 0 END) AS with_year_built,
                    SUM(CASE WHEN sail_number IS NOT NULL AND sail_number <> ''
                             THEN 1 ELSE 0 END) AS with_sail_number
                FROM boats
                """
            )
        ).mappings().first()
    boats = int(r["boats"] or 0)

    def meter(n: Any) -> dict[str, Any]:
        count = int(n or 0)
        return {
            "count": count,
            "pct": round(100.0 * count / boats, 1) if boats else 0.0,
        }

    return {
        "available": True,
        "boats": boats,
        "completeness": {
            "design": meter(r["with_design"]),
            "design_canonical": meter(r["with_design_canonical"]),
            "country": meter(r["with_country"]),
            "year_built": meter(r["with_year_built"]),
            "sail_number": meter(r["with_sail_number"]),
        },
    }


# ---------------------------------------------------------------------------
# Attention rules (SPEC-22 §3.1) — what needs a human *today*
# ---------------------------------------------------------------------------

ATTENTION_STALE_SOURCE = "source_stale"
ATTENTION_SOURCE_FAILED = "source_failed"
ATTENTION_SOURCE_NEVER_RUN = "source_never_run"
ATTENTION_DUPE_BACKLOG = "dupe_backlog"
ATTENTION_CORRECTIONS_BACKLOG = "corrections_backlog"


def _attention_items(
    sources: list[dict[str, Any]],
    dupes: dict[str, Any],
    corrections: dict[str, Any],
) -> list[dict[str, Any]]:
    """One attention item per thing that needs a human today.

    Rules (server-side, deterministic):

      * ``source_stale``       — a scheduled (unpaused, enabled) source on
        a *nightly/daily* cadence whose last successful run is older than
        its freshness budget — one item per stale nightly source.
      * ``source_never_run``   — a scheduled nightly source with no ledger
        rows at all.
      * ``source_failed``      — the source's latest run failed (any
        cadence, scheduled sources only).
      * ``dupe_backlog``       — pending dupe-review clusters > 0.
      * ``corrections_backlog``— pending owner corrections > 0.

    Items are ordered by severity then by descending ``stale_days`` so the
    worst offender is on top.
    """
    items: list[dict[str, Any]] = []
    for s in sources:
        if s["paused"] or not s["enabled"]:
            continue  # paused/disabled schedules are deliberate — not attention
        cadence_days = _cadence_days(s["cadence"])
        if cadence_days > 30:
            # annual/manual cadences are exempt from daily attention rules
            continue
        if s["stale_days"] is None:
            items.append(
                {
                    "kind": ATTENTION_SOURCE_NEVER_RUN,
                    "severity": "critical",
                    "source": s["slug"],
                    "title": f"{s['display_name']} has never run",
                    "detail": (
                        f"Scheduled {s['cadence']} but no ledger rows exist."
                    ),
                    "stale_days": None,
                    "href": "/admin/scrapers",
                }
            )
            continue
        if s["stale"]:
            items.append(
                {
                    "kind": ATTENTION_STALE_SOURCE,
                    "severity": "warning",
                    "source": s["slug"],
                    "title": f"{s['display_name']} is stale",
                    "detail": (
                        f"Last successful run {s['stale_days']}d ago "
                        f"(budget {s['budget_hours'] / 24:.0f}d, "
                        f"cadence {s['cadence']})."
                    ),
                    "stale_days": s["stale_days"],
                    "href": "/admin/scrapers",
                }
            )
        if s.get("last_status") == "failed":
            items.append(
                {
                    "kind": ATTENTION_SOURCE_FAILED,
                    "severity": "warning",
                    "source": s["slug"],
                    "title": f"{s['display_name']} latest run failed",
                    "detail": "The most recent ledger run for this source failed.",
                    "stale_days": s["stale_days"],
                    "href": "/admin/scrapers",
                }
            )

    if dupes.get("pending_clusters", 0) > 0:
        items.append(
            {
                "kind": ATTENTION_DUPE_BACKLOG,
                "severity": "info",
                "source": None,
                "title": f"{dupes['pending_clusters']} dupe clusters awaiting review",
                "detail": (
                    f"{dupes['pending']} boats across "
                    f"{dupes['pending_clusters']} clusters are pending a "
                    "merge verdict."
                ),
                "stale_days": None,
                "href": "/admin/identity",
            }
        )
    if corrections.get("pending", 0) > 0:
        items.append(
            {
                "kind": ATTENTION_CORRECTIONS_BACKLOG,
                "severity": "info",
                "source": None,
                "title": f"{corrections['pending']} corrections awaiting review",
                "detail": "Owner-submitted boat corrections need moderation.",
                "stale_days": None,
                "href": "/admin/corrections",
            }
        )

    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    items.sort(
        key=lambda i: (
            severity_rank.get(i["severity"], 3),
            -(i["stale_days"] or 0),
        )
    )
    return items


# ---------------------------------------------------------------------------
# The overview
# ---------------------------------------------------------------------------


def get_overview(
    engine: Engine,
    now: datetime | None = None,
    runs_days: int = RUNS_PER_DAY_DAYS,
) -> dict[str, Any]:
    """The one-call admin overview (AD-01-13).

    ``now`` is injectable so contract tests can pin the "2 Sep 2026
    snapshot" fixture and assert exact stale_days / today aggregates.
    """
    if now is None:
        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        now_dt = _as_dt(now) or datetime.now(timezone.utc).replace(tzinfo=None)

    sources, _ctx = _build_sources(engine, now_dt)
    today = _today_aggregates(engine, now_dt)
    runs_series = _runs_per_day(engine, now_dt, runs_days)
    dupes = _dupe_summary(engine)
    corrections = _corrections_summary(engine)
    fleet = _fleet_summary(engine)
    attention = _attention_items(sources, dupes, corrections)

    stale_sources = [s for s in sources if s["stale"] and not s["paused"]]
    failed_today_sources = [
        s for s in sources if s.get("last_status") == "failed" and not s["paused"]
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": now_dt.isoformat(),
        "today": today,
        "overview": {
            "sources_tracked": len(sources),
            "sources_stale": len(stale_sources),
            "sources_failed": len(failed_today_sources),
            "sources_paused": len([s for s in sources if s["paused"]]),
            "attention_count": len(attention),
            "dupes_pending_clusters": dupes["pending_clusters"],
            "corrections_pending": corrections["pending"],
            "boats": fleet["boats"],
        },
        "sources": sources,
        "runs_per_day": {
            "days": runs_days,
            "series": runs_series,
        },
        "dupes": dupes,
        "corrections": corrections,
        "fleet": fleet,
        "attention": attention,
    }


__all__ = [
    "get_overview",
    "SCHEMA_VERSION",
    "RUNS_PER_DAY_DAYS",
    "LAST14_DAYS",
    "CADENCE_BUDGET_HOURS",
    "ATTENTION_STALE_SOURCE",
    "ATTENTION_SOURCE_FAILED",
    "ATTENTION_SOURCE_NEVER_RUN",
    "ATTENTION_DUPE_BACKLOG",
    "ATTENTION_CORRECTIONS_BACKLOG",
]
