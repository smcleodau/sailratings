"""Run ledger — one truthful record of what every source did, when (OPS-01-03).

This module is the canonical data layer over the ``ingestion_log`` table.
Every scraper run writes a row capturing:

  - ``started_at`` / ``completed_at`` (duration is derived)
  - ``status``            — running | completed | failed
  - ``records_found``     — rows the source presented
  - ``records_new``       — rows actually ingested as new
  - ``records_updated``   — rows re-confirmed/updated
  - ``error_message``     — failure detail when status != completed

On top of the per-run records this layer computes:

  - per-source health summary: latest-run and latest-new-data timestamps
    plus 7-day aggregates (runs, fails, rows found/new)
  - daily aggregates over a trailing window (default 7 days)
  - reconciliation of ledger counts against a target table (so the ledger
    can be checked against DP-05-03 counts when that lands)

Portability note: queries avoid Postgres-only constructs (``interval``,
``FILTER``) — cutoffs are computed in Python and bound as parameters, and
conditional aggregation uses ``CASE WHEN`` — so the whole layer runs
identically against SQLite (tests) and Postgres (production).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.db.models import IngestionLog

_INGESTION_LOG = IngestionLog.__table__

# Status values written by scrapers (ingestion_log.status is free-text; these
# are the canonical values the ledger understands).
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

DEFAULT_AGGREGATE_DAYS = 7

# Whitelist of tables the ledger can be reconciled against. DP-05-03 (and any
# other pipeline producing per-source row counts) registers its target here:
# table -> (column holding the source slug, column holding the row created
# timestamp). Keeping this explicit prevents arbitrary-table SQL injection
# through the reconcile API.
RECONCILE_TARGETS: dict[str, dict[str, str]] = {
    "race_results": {"source_col": "source", "created_col": "created_at"},
    "irc_certificates": {"source_col": "source", "created_col": "scraped_at"},
    "orc_certificates": {"source_col": "source", "created_col": "scraped_at"},
    "tcc_snapshots": {"source_col": None, "created_col": "created_at"},
}


# ---------------------------------------------------------------------------
# Write path — every run writes started/duration/status/found/new/error rows
# ---------------------------------------------------------------------------


def record_run_start(
    engine: Engine,
    source: str,
    metadata: dict[str, Any] | None = None,
    started_at: datetime | None = None,
) -> int:
    """Open a ledger row for a scraper run. Returns the run id.

    ``started_at`` is injectable for fixtures/replays; production callers
    leave it as "now".
    """
    with engine.begin() as conn:
        result = conn.execute(
            _INGESTION_LOG.insert().values(
                source=source,
                started_at=started_at or datetime.now(timezone.utc),
                status=STATUS_RUNNING,
                metadata=metadata,
            )
        )
        run_id = result.inserted_primary_key[0]
    return int(run_id)


def record_run_end(
    engine: Engine,
    run_id: int,
    status: str = STATUS_COMPLETED,
    records_found: int | None = None,
    records_new: int | None = None,
    records_updated: int | None = None,
    error_message: str | None = None,
    completed_at: datetime | None = None,
) -> None:
    """Close a ledger row with the outcome of the run."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE ingestion_log
                SET completed_at = :completed_at,
                    status = :status,
                    records_found = :records_found,
                    records_new = :records_new,
                    records_updated = :records_updated,
                    error_message = :error_message
                WHERE id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "completed_at": completed_at or datetime.now(timezone.utc),
                "status": status,
                "records_found": records_found,
                "records_new": records_new,
                "records_updated": records_updated,
                "error_message": error_message,
            },
        )


def record_run(
    engine: Engine,
    source: str,
    status: str = STATUS_COMPLETED,
    records_found: int | None = None,
    records_new: int | None = None,
    records_updated: int | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> int:
    """Write a complete ledger row in one shot.

    Convenience wrapper for runners that only know the outcome at the end
    (e.g. Temporal activities holding a run summary) and for fixture runs
    in tests. Returns the run id.
    """
    run_id = record_run_start(
        engine, source, metadata=metadata, started_at=started_at
    )
    record_run_end(
        engine,
        run_id,
        status=status,
        records_found=records_found,
        records_new=records_new,
        records_updated=records_updated,
        error_message=error_message,
        completed_at=completed_at,
    )
    return run_id


# ---------------------------------------------------------------------------
# Read path — per-run records, queryable by source and time
# ---------------------------------------------------------------------------


def _normalise_metadata(value: Any) -> dict | None:
    """JSON columns come back as dict on Postgres, str on SQLite."""
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {"raw": value}
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    return {"raw": str(value)}


def _as_dt(value: Any) -> datetime | None:
    """Coerce a DB timestamp to a naive-UTC ``datetime``.

    Raw ``text()`` queries bypass SQLAlchemy's column-type deserialisation,
    so on Postgres a ``timestamptz`` arrives as an offset-aware ``datetime``
    while on SQLite it arrives as the stored (offset-naive) string. To keep
    arithmetic and comparison consistent across dialects we normalise to
    naive UTC — every timestamp this ledger handles is UTC.
    """
    dt: datetime | None
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


def _run_row(row: Any) -> dict[str, Any]:
    started = _as_dt(row.started_at)
    completed = _as_dt(row.completed_at)
    duration = None
    if started is not None and completed is not None:
        duration = (completed - started).total_seconds()
    return {
        "id": row.id,
        "source": row.source,
        "started_at": started.isoformat() if started else None,
        "completed_at": completed.isoformat() if completed else None,
        "duration_seconds": duration,
        "status": row.status,
        "records_found": row.records_found,
        "records_new": row.records_new,
        "records_updated": row.records_updated,
        "error_message": row.error_message,
        "metadata": _normalise_metadata(row.metadata),
    }


_RUN_COLUMNS = (
    "id, source, started_at, completed_at, status, "
    "records_found, records_new, records_updated, error_message, metadata"
)


def list_runs(
    engine: Engine,
    source: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Recent ledger runs, newest first. Queryable by source and time window."""
    limit = max(1, min(int(limit), 500))
    clauses = []
    params: dict[str, Any] = {"limit": limit}
    if source is not None:
        clauses.append("source = :source")
        params["source"] = source
    if since is not None:
        clauses.append("started_at >= :since")
        params["since"] = since
    if until is not None:
        clauses.append("started_at <= :until")
        params["until"] = until
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT {_RUN_COLUMNS} FROM ingestion_log "
                f"{where} ORDER BY started_at DESC, id DESC LIMIT :limit"
            ),
            params,
        ).fetchall()
    return [_run_row(r) for r in rows]


def get_run(engine: Engine, run_id: int) -> dict[str, Any] | None:
    """Run detail for a single ledger row (None when unknown id)."""
    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT {_RUN_COLUMNS} FROM ingestion_log WHERE id = :run_id"),
            {"run_id": run_id},
        ).fetchone()
    return _run_row(row) if row else None


# ---------------------------------------------------------------------------
# Read path — source health summary and aggregates
# ---------------------------------------------------------------------------


def get_source_health_summary(
    engine: Engine,
    now: datetime | None = None,
    aggregate_days: int = DEFAULT_AGGREGATE_DAYS,
) -> list[dict[str, Any]]:
    """Per-source ledger summary.

    For every source that has ever written a ledger row:

    - ``last_started_at``    — latest run start (latest-run timestamp)
    - ``last_completed_at``  — latest successful completion
    - ``last_new_data_at``   — latest run that actually ingested new rows
      (``records_new`` > 0). Derived from the ledger itself, so it is a
      truthful per-source signal even for sources that don't write to
      ``race_results``.
    - ``runs_Nd`` / ``failed_Nd`` / ``rows_found_Nd`` / ``rows_new_Nd`` —
      aggregates over the trailing ``aggregate_days`` calendar days
      (default 7), consistent with :func:`get_daily_aggregates`.
    """
    now = _as_dt(now) or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = datetime.combine(
        now.date() - timedelta(days=aggregate_days - 1), time.min
    )
    stmt = text(
        """
        SELECT
            source,
            MAX(started_at) AS last_started_at,
            MAX(CASE WHEN status = :completed THEN completed_at END)
                AS last_completed_at,
            MAX(CASE WHEN COALESCE(records_new, 0) > 0 THEN started_at END)
                AS last_new_data_at,
            COUNT(*) AS runs_total,
            SUM(CASE WHEN started_at >= :cutoff THEN 1 ELSE 0 END) AS runs_window,
            SUM(CASE WHEN started_at >= :cutoff AND status = :failed
                     THEN 1 ELSE 0 END) AS failed_window,
            SUM(CASE WHEN started_at >= :cutoff
                     THEN COALESCE(records_found, 0) ELSE 0 END)
                AS rows_found_window,
            SUM(CASE WHEN started_at >= :cutoff
                     THEN COALESCE(records_new, 0) ELSE 0 END)
                AS rows_new_window
        FROM ingestion_log
        GROUP BY source
        ORDER BY source
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            stmt,
            {
                "completed": STATUS_COMPLETED,
                "failed": STATUS_FAILED,
                "cutoff": cutoff,
            },
        ).fetchall()

    out = []
    for r in rows:
        last_started = _as_dt(r.last_started_at)
        last_completed = _as_dt(r.last_completed_at)
        last_new_data = _as_dt(r.last_new_data_at)
        out.append(
            {
                "source": r.source,
                "last_started_at": last_started.isoformat() if last_started else None,
                "last_completed_at": (
                    last_completed.isoformat() if last_completed else None
                ),
                "last_new_data_at": (
                    last_new_data.isoformat() if last_new_data else None
                ),
                "seconds_since_last_run": (
                    (now - last_started).total_seconds() if last_started else None
                ),
                "runs_total": int(r.runs_total),
                f"runs_{aggregate_days}d": int(r.runs_window or 0),
                f"failed_{aggregate_days}d": int(r.failed_window or 0),
                f"rows_found_{aggregate_days}d": int(r.rows_found_window or 0),
                f"rows_new_{aggregate_days}d": int(r.rows_new_window or 0),
            }
        )
    return out


def get_daily_aggregates(
    engine: Engine,
    source: str | None = None,
    days: int = DEFAULT_AGGREGATE_DAYS,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Per-day run aggregates over the trailing ``days`` window.

    Buckets are calendar days (UTC) labelled ``YYYY-MM-DD``. Days with no
    runs are emitted with zero counts so dashboards can render a continuous
    series without gap-filling client-side.
    """
    days = max(1, min(int(days), 90))
    now = _as_dt(now) or datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.date()
    cutoff = datetime.combine(today - timedelta(days=days - 1), time.min)

    clauses = ["started_at >= :cutoff"]
    params: dict[str, Any] = {"cutoff": cutoff, "failed": STATUS_FAILED}
    if source is not None:
        clauses.append("source = :source")
        params["source"] = source
    where = "WHERE " + " AND ".join(clauses)

    # started_at::date is Postgres; date(started_at) is SQLite. Both produce
    # a day bucket for the trailing window.
    day_expr = (
        "date(started_at)"
        if engine.dialect.name == "sqlite"
        else "CAST(started_at AS DATE)"
    )
    stmt = text(
        f"""
        SELECT
            {day_expr} AS day,
            COUNT(*) AS runs,
            SUM(CASE WHEN status = :failed THEN 1 ELSE 0 END) AS failed,
            SUM(COALESCE(records_found, 0)) AS rows_found,
            SUM(COALESCE(records_new, 0)) AS rows_new
        FROM ingestion_log
        {where}
        GROUP BY {day_expr}
        ORDER BY {day_expr}
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt, params).fetchall()

    # Day bucket comes back as str on SQLite, datetime.date on Postgres.
    by_day = {}
    for r in rows:
        day_value = r.day
        if isinstance(day_value, (date, datetime)):
            day_key = day_value.isoformat()[:10]
        else:
            day_key = str(day_value)[:10]
        by_day[day_key] = r
    out = []
    for offset in range(days):
        day = (today - timedelta(days=days - 1 - offset)).isoformat()
        r = by_day.get(day)
        out.append(
            {
                "day": day,
                "source": source,
                "runs": int(r.runs) if r else 0,
                "failed": int(r.failed or 0) if r else 0,
                "rows_found": int(r.rows_found or 0) if r else 0,
                "rows_new": int(r.rows_new or 0) if r else 0,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Reconciliation — ledger vs actual row counts (DP-05-03 hook)
# ---------------------------------------------------------------------------


def reconcile_counts(
    engine: Engine,
    source: str,
    table: str,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Compare ledger-reported counts against actual rows in a target table.

    The ledger claims ``records_new`` rows were ingested per run; the target
    table holds the rows themselves. This compares the two over a window so
    drift (double-counts, swallowed rows, out-of-band writes) is visible.

    ``table`` must be registered in :data:`RECONCILE_TARGETS` — this is the
    seam DP-05-03 plugs into when its row counts land.
    """
    target = RECONCILE_TARGETS.get(table)
    if target is None:
        raise ValueError(
            f"unknown reconcile target {table!r}; "
            f"known: {sorted(RECONCILE_TARGETS)}"
        )

    params: dict[str, Any] = {"source": source}
    ledger_clauses = ["source = :source", f"status = '{STATUS_COMPLETED}'"]
    if since is not None:
        ledger_clauses.append("started_at >= :since")
        params["since"] = since
    if until is not None:
        ledger_clauses.append("started_at <= :until")
        params["until"] = until

    ledger_sql = text(
        f"""
        SELECT COUNT(*) AS runs,
               SUM(COALESCE(records_found, 0)) AS rows_found,
               SUM(COALESCE(records_new, 0)) AS rows_new,
               SUM(COALESCE(records_updated, 0)) AS rows_updated
        FROM ingestion_log
        WHERE {' AND '.join(ledger_clauses)}
        """
    )

    created_col = target["created_col"]
    source_col = target["source_col"]
    table_clauses = []
    if source_col:
        table_clauses.append(f"{source_col} = :source")
    if since is not None:
        table_clauses.append(f"{created_col} >= :since")
    if until is not None:
        table_clauses.append(f"{created_col} <= :until")
    table_where = ("WHERE " + " AND ".join(table_clauses)) if table_clauses else ""
    table_sql = text(f"SELECT COUNT(*) FROM {table} {table_where}")

    with engine.connect() as conn:
        ledger_row = conn.execute(ledger_sql, params).fetchone()
        actual_rows = conn.execute(table_sql, params).scalar_one()

    ledger_new = int(ledger_row.rows_new or 0)
    actual = int(actual_rows or 0)
    return {
        "source": source,
        "table": table,
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "ledger_runs": int(ledger_row.runs or 0),
        "ledger_rows_found": int(ledger_row.rows_found or 0),
        "ledger_rows_new": ledger_new,
        "ledger_rows_updated": int(ledger_row.rows_updated or 0),
        "actual_rows": actual,
        "new_rows_difference": ledger_new - actual,
        "reconciled": ledger_new == actual,
    }
