"""AD-01-15 — nightly ``admin_metrics`` job + data-health facts.

Goal: make completeness and empty-table facts visible **without running
heavy queries on request**.

Three moving parts:

* :func:`compute_nightly_metrics` — the nightly job.  One aggregate scan of
  ``boats`` (single pass, every column in one ``COUNT``), one scan of
  ``events`` (venue null rate + a small sample of *raw names* — the
  ``name`` values exactly as written, before any normalisation), then an
  append into ``admin_metrics``.  Everything downstream (the page) reads
  the *precomputed* stream, never the base tables.

* :func:`get_table_health` — the table census.  ``pg_stat_user_tables``
  counts + sizes (the stats collector maintains these; the request path
  never touches the base tables), with ``rows = 0`` (empty) tables
  flagged.  On PostgreSQL the census is joined against the
  ``health_tables_built_never_written`` view (alembic ``0033``) for the
  built-but-never-written list.  This is the only request-time query and
  it reads catalog/statistics views only — well under the 200 ms budget.

* :func:`get_completeness` — reads ``health_metric_latest`` (latest row
  per metric/scope/phase), so the page renders from ``admin_metrics``
  directly.  ``available=False`` is returned honestly when the nightly
  job has never run (or on a fixture DB without the view).

The ``admin_metrics`` table carries both naming conventions (alembic
``0033`` keeps them in lock-step by trigger):

  * ``recorded_at`` / ``value_num`` / ``value_text``  — 0029 (evidence stream)
  * ``computed_at`` / ``value``                       — AD-01-15 (spec shape)

Verification: fixture DB test (``tests/test_admin_metrics.py``) plus the
page smoke test in ``e2e_tests/tests/data-health.spec.ts``.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Nightly job — metric catalogue
# ---------------------------------------------------------------------------

#: boats columns whose non-null % the nightly job computes (spec list, in
#: order).  These are the columns a user cares about on a boat page; a
#: meter under the buoy threshold is the honest "we don't actually know
#: this yet" signal.
BOATS_COMPLETENESS_COLUMNS: tuple[str, ...] = (
    "design",
    "design_canonical",
    "country",
    "year_built",
    "builder",
    "designer",
    "loa",
    "lwl",
    "beam_max",
    "displacement_kg",
)

#: The spec's "Buoy under 40%" threshold — a completeness meter below this
#: is flagged on the page (and in the endpoint payload) as needing a buoy.
BUOY_THRESHOLD_PCT = 40.0

#: Metric-name prefixes.  Kept in one place so the endpoint, the page and
#: the tests all agree on the stream's vocabulary.
METRIC_PREFIX_COMPLETENESS = "data_health.completeness"
METRIC_EVENTS_VENUE_NULL_RATE = "data_health.events.venue_null_rate"
METRIC_EVENTS_RAW_NAME_SAMPLE = "data_health.events.raw_name_sample"
METRIC_NIGHTLY_RUN = "data_health.nightly_run"

#: How many raw event names the nightly job samples into the stream.
EVENT_NAME_SAMPLE_SIZE = 25


# ---------------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------------


def _is_postgres(engine: Engine) -> bool:
    return engine.url.get_backend_name() in ("postgresql", "postgres")


def _table_exists(conn, table: str) -> bool:
    if conn.dialect.name == "sqlite":
        row = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).first()
        return row is not None
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    ).first()
    return row is not None


def _view_exists(conn, view: str) -> bool:
    if conn.dialect.name == "sqlite":
        row = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='view' AND name=:t"),
            {"t": view},
        ).first()
        return row is not None
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.views "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": view},
    ).first()
    return row is not None


def _columns(conn, table: str) -> set[str]:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return {r[1] for r in rows}
    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    ).fetchall()
    return {r[0] for r in rows}


def ensure_admin_metrics_table(engine: Engine) -> None:
    """Create ``admin_metrics`` (and the read views on PG) if absent.

    Alembic ``0029``/``0033`` own the canonical definition on production;
    this exists so fixture databases (SQLite + bare-PG test schemas) can
    run the nightly job and the endpoints without replaying the full
    migration chain.  Every statement is ``IF NOT EXISTS`` / guarded.
    """
    with engine.begin() as conn:
        if conn.dialect.name == "sqlite":
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS admin_metrics (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                        metric      TEXT NOT NULL,
                        scope       TEXT NOT NULL DEFAULT '',
                        phase       TEXT NOT NULL DEFAULT '',
                        value_num   REAL,
                        value_text  TEXT,
                        meta        TEXT,
                        computed_at TEXT,
                        value       REAL
                    )
                    """
                )
            )
        else:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS admin_metrics (
                        id          BIGSERIAL PRIMARY KEY,
                        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        metric      TEXT NOT NULL,
                        scope       TEXT NOT NULL DEFAULT '',
                        phase       TEXT NOT NULL DEFAULT '',
                        value_num   DOUBLE PRECISION,
                        value_text  TEXT,
                        meta        JSONB,
                        computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        value       DOUBLE PRECISION
                    )
                    """
                )
            )
            # Read views from 0033 — recreated here only when missing so a
            # bare-schema fixture converges without running alembic.
            conn.execute(
                text(
                    """
                    CREATE OR REPLACE VIEW health_metric_latest AS
                    SELECT DISTINCT ON (metric, scope, phase)
                        metric, scope, phase,
                        value_num AS value, value_text, computed_at, meta
                    FROM admin_metrics
                    ORDER BY metric, scope, phase, computed_at DESC
                    """
                )
            )


# ---------------------------------------------------------------------------
# The nightly job
# ---------------------------------------------------------------------------


def _insert_metric(
    conn,
    *,
    metric: str,
    value: float | None,
    scope: str = "",
    phase: str = "",
    value_text: str | None = None,
    meta: dict[str, Any] | None = None,
    computed_at: _dt.datetime | None = None,
) -> None:
    """Append one evidence row to ``admin_metrics`` (both column conventions).

    On PG the 0033 trigger would sync the aliases for us; writing both
    explicitly keeps the fixture (SQLite, trigger-less) path identical.
    """
    is_sqlite = conn.dialect.name == "sqlite"
    ts = computed_at or _dt.datetime.now(_dt.timezone.utc)
    if is_sqlite:
        conn.execute(
            text(
                """
                INSERT INTO admin_metrics
                    (metric, scope, phase, value_num, value, value_text, meta,
                     recorded_at, computed_at)
                VALUES
                    (:metric, :scope, :phase, :value_num, :value, :value_text,
                     :meta, :ts, :ts)
                """
            ),
            {
                "metric": metric,
                "scope": scope,
                "phase": phase,
                "value_num": value,
                "value": value,
                "value_text": value_text,
                "meta": json.dumps(meta) if meta is not None else None,
                "ts": ts.isoformat(),
            },
        )
    else:
        from sqlalchemy import bindparam
        from sqlalchemy.dialects.postgresql import JSONB

        stmt = (
            text(
                """
                INSERT INTO admin_metrics
                    (metric, scope, phase, value_num, value, value_text, meta,
                     recorded_at, computed_at)
                VALUES
                    (:metric, :scope, :phase, :value_num, :value, :value_text,
                     :meta, :ts, :ts)
                """
            )
            .bindparams(bindparam("meta", type_=JSONB))
        )
        conn.execute(
            stmt,
            {
                "metric": metric,
                "scope": scope,
                "phase": phase,
                "value_num": value,
                "value": value,
                "value_text": value_text,
                "meta": meta,
                "ts": ts,
            },
        )


def compute_nightly_metrics(
    engine: Engine,
    *,
    now: _dt.datetime | None = None,
    sample_size: int = EVENT_NAME_SAMPLE_SIZE,
) -> dict[str, Any]:
    """The nightly job: compute completeness facts into ``admin_metrics``.

    Two base-table scans (``boats`` single-pass aggregate, ``events``
    aggregate + bounded sample), then the append.  Safe to run any time;
    each run appends a fresh, timestamped evidence point.

    Returns the summary dict the CLI prints and the tests assert on.
    """
    computed_at = now or _dt.datetime.now(_dt.timezone.utc)
    ensure_admin_metrics_table(engine)

    summary: dict[str, Any] = {
        "computed_at": computed_at.isoformat(),
        "completeness": {},
        "events": {},
        "rows_written": 0,
        "skipped": [],
    }

    with engine.begin() as conn:
        # -- boats: one scan, every column in a single COUNT(...) ------------
        boats_cols = _columns(conn, "boats") if _table_exists(conn, "boats") else set()
        wanted = [c for c in BOATS_COMPLETENESS_COLUMNS if c in boats_cols]
        missing = [c for c in BOATS_COMPLETENESS_COLUMNS if c not in boats_cols]
        if missing:
            summary["skipped"].append({"table": "boats", "missing_columns": missing})

        if boats_cols:
            total = conn.execute(text("SELECT COUNT(*) FROM boats")).scalar() or 0
            if wanted:
                # COUNT(col) ignores NULLs — one pass for every column at once.
                select_list = ", ".join(f"COUNT({c}) AS {c}" for c in wanted)
                row = conn.execute(
                    text(f"SELECT {select_list} FROM boats")
                ).first()._mapping
                for col in wanted:
                    non_null = int(row[col] or 0)
                    pct = (100.0 * non_null / total) if total else None
                    metric = f"{METRIC_PREFIX_COMPLETENESS}.boats.{col}"
                    _insert_metric(
                        conn,
                        metric=metric,
                        scope="boats",
                        phase="nightly",
                        value=round(pct, 3) if pct is not None else None,
                        value_text=None,
                        meta={
                            "rows_total": int(total),
                            "non_null": non_null,
                            "buoy": bool(pct is not None and pct < BUOY_THRESHOLD_PCT),
                        },
                        computed_at=computed_at,
                    )
                    summary["completeness"][f"boats.{col}"] = {
                        "pct_non_null": round(pct, 3) if pct is not None else None,
                        "non_null": non_null,
                        "rows_total": int(total),
                    }
                    summary["rows_written"] += 1

        # -- events: venue null rate + raw name sample ------------------------
        if _table_exists(conn, "events"):
            ev_cols = _columns(conn, "events")
            total_ev = conn.execute(text("SELECT COUNT(*) FROM events")).scalar() or 0
            if "venue" in ev_cols:
                null_venue = conn.execute(
                    text("SELECT COUNT(*) FROM events WHERE venue IS NULL")
                ).scalar() or 0
                null_rate = (100.0 * null_venue / total_ev) if total_ev else None
                _insert_metric(
                    conn,
                    metric=METRIC_EVENTS_VENUE_NULL_RATE,
                    scope="events",
                    phase="nightly",
                    value=round(null_rate, 3) if null_rate is not None else None,
                    meta={
                        "rows_total": int(total_ev),
                        "venue_null": int(null_venue),
                        "buoy": bool(
                            null_rate is not None
                            and (100.0 - null_rate) < BUOY_THRESHOLD_PCT
                        ),
                    },
                    computed_at=computed_at,
                )
                summary["rows_written"] += 1
                summary["events"]["venue_null_rate"] = (
                    round(null_rate, 3) if null_rate is not None else None
                )
                summary["events"]["rows_total"] = int(total_ev)

            # Raw names exactly as ingested (pre-normalisation) — a bounded
            # sample, so the page can show *what the sources actually wrote*
            # without a request-time scan.
            name_col = next(
                (c for c in ("name", "event_name", "raw_name") if c in ev_cols),
                None,
            )
            if name_col is not None:
                rows = conn.execute(
                    text(
                        f"SELECT {name_col} AS n FROM events "
                        f"WHERE {name_col} IS NOT NULL "
                        f"ORDER BY id DESC LIMIT :lim"
                    )
                , {"lim": sample_size}).fetchall()
                sample = [r[0] for r in rows]
                _insert_metric(
                    conn,
                    metric=METRIC_EVENTS_RAW_NAME_SAMPLE,
                    scope="events",
                    phase="nightly",
                    value=float(len(sample)),
                    value_text=None,
                    meta={"names": sample, "column": name_col},
                    computed_at=computed_at,
                )
                summary["rows_written"] += 1
                summary["events"]["raw_name_sample"] = sample

        # -- the run marker itself -------------------------------------------
        _insert_metric(
            conn,
            metric=METRIC_NIGHTLY_RUN,
            phase="nightly",
            value=float(summary["rows_written"]),
            value_text="ok",
            meta={"skipped": summary["skipped"]},
            computed_at=computed_at,
        )
        summary["rows_written"] += 1

    log.info(
        "nightly admin_metrics: %d rows written at %s",
        summary["rows_written"],
        computed_at.isoformat(),
    )
    return summary


# ---------------------------------------------------------------------------
# Request-time reads (cheap: stats views + the precomputed stream only)
# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, _decimal.Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def get_table_health(engine: Engine) -> dict[str, Any]:
    """``GET /v1/admin/health/tables`` — the pg_stat census.

    Every user table with its statistics-collector row estimate and size,
    ``rows = 0`` flagged, plus (on PG) the built-never-written list from
    the ``health_tables_built_never_written`` view.  Reads catalog/stat
    views only — never scans a base table.
    """
    if not _is_postgres(engine):
        # Honest degradation for fixture DBs: pg_stat doesn't exist, so the
        # census reports itself unavailable rather than fabricating counts.
        return {
            "available": False,
            "reason": "pg_stat census requires PostgreSQL",
            "tables": [],
            "empty_tables": [],
            "built_never_written": [],
        }

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  relname                                        AS name,
                  n_live_tup                                     AS rows,
                  pg_total_relation_size(relid)                  AS total_bytes,
                  pg_relation_size(relid)                        AS table_bytes,
                  pg_total_relation_size(relid) - pg_relation_size(relid)
                                                                 AS index_bytes
                FROM pg_stat_user_tables
                ORDER BY relname
                """
            )
        ).fetchall()

        built_never_written: list[dict[str, Any]] = []
        bnw_available = _view_exists(conn, "health_tables_built_never_written")
        if bnw_available:
            bnw_rows = conn.execute(
                text(
                    """
                    SELECT table_name, est_rows, total_bytes, data_cols
                    FROM health_tables_built_never_written
                    ORDER BY table_name
                    """
                )
            ).fetchall()
            built_never_written = [
                {
                    "name": r.table_name,
                    "rows": int(r.est_rows or 0),
                    "total_bytes": int(r.total_bytes or 0),
                    "data_cols": int(r.data_cols or 0),
                }
                for r in bnw_rows
            ]

    tables = [
        {
            "name": r.name,
            "rows": int(r.rows or 0),
            "total_bytes": int(r.total_bytes or 0),
            "table_bytes": int(r.table_bytes or 0),
            "index_bytes": int(r.index_bytes or 0),
            "empty": bool((r.rows or 0) == 0),
        }
        for r in rows
    ]
    empty_tables = [t["name"] for t in tables if t["empty"]]

    return {
        "available": True,
        "as_of": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "source": "pg_stat_user_tables",
        "table_count": len(tables),
        "tables": tables,
        "empty_tables": empty_tables,
        "built_never_written": built_never_written,
        "built_never_written_available": bnw_available,
    }


def get_completeness(engine: Engine) -> dict[str, Any]:
    """``GET /v1/admin/health/completeness`` — the page's meters.

    Reads ``health_metric_latest`` (or, on a fixture DB without the view, a
    DISTINCT-ON-free fallback over ``admin_metrics``) so every number on the
    page comes from the precomputed stream.
    """
    with engine.connect() as conn:
        if not _table_exists(conn, "admin_metrics"):
            return {
                "available": False,
                "reason": "admin_metrics not present — run the nightly job",
                "computed_at": None,
                "buoy_threshold_pct": BUOY_THRESHOLD_PCT,
                "meters": [],
                "events": {"venue_null_rate": None, "raw_name_sample": []},
                "last_run": None,
            }

        is_sqlite = conn.dialect.name == "sqlite"
        if is_sqlite or not _view_exists(conn, "health_metric_latest"):
            # Fixture fallback: latest row per metric without DISTINCT ON.
            rows = conn.execute(
                text(
                    """
                    SELECT m.metric, m.scope, m.phase,
                           m.value_num AS value, m.value_text,
                           m.recorded_at AS computed_at, m.meta
                    FROM admin_metrics m
                    JOIN (
                        SELECT metric, scope, phase, MAX(recorded_at) AS mx
                        FROM admin_metrics GROUP BY metric, scope, phase
                    ) latest
                      ON latest.metric = m.metric
                     AND latest.scope = m.scope
                     AND latest.phase = m.phase
                     AND latest.mx = m.recorded_at
                    """
                )
            ).fetchall()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT metric, scope, phase, value, value_text, computed_at, meta
                    FROM health_metric_latest
                    """
                )
            ).fetchall()

    meters: list[dict[str, Any]] = []
    events_venue_null_rate: float | None = None
    events_raw_names: list[str] = []
    last_run: dict[str, Any] | None = None
    computed_at_max: str | None = None

    for r in rows:
        meta: dict[str, Any] = {}
        if r.meta:
            if isinstance(r.meta, str):
                try:
                    meta = json.loads(r.meta)
                except Exception:
                    meta = {}
            else:
                meta = dict(r.meta)
        ts = _jsonable(r.computed_at)
        if ts and (computed_at_max is None or str(ts) > computed_at_max):
            computed_at_max = str(ts)

        metric = r.metric
        value = None if r.value is None else float(r.value)

        if metric.startswith(f"{METRIC_PREFIX_COMPLETENESS}."):
            # data_health.completeness.boats.<col>
            parts = metric.split(".")
            col = parts[-1]
            pct = value
            meters.append(
                {
                    "table": "boats",
                    "column": col,
                    "pct_non_null": pct,
                    "rows_total": meta.get("rows_total"),
                    "non_null": meta.get("non_null"),
                    "buoy": bool(
                        pct is not None and pct < BUOY_THRESHOLD_PCT
                    ),
                    "computed_at": ts,
                }
            )
        elif metric == METRIC_EVENTS_VENUE_NULL_RATE:
            events_venue_null_rate = value
        elif metric == METRIC_EVENTS_RAW_NAME_SAMPLE:
            events_raw_names = list(meta.get("names") or [])
        elif metric == METRIC_NIGHTLY_RUN:
            last_run = {
                "computed_at": ts,
                "rows_written": int(value or 0),
                "status": r.value_text or "ok",
            }

    # Stable, spec-order meters: any column the nightly job knows about but
    # hasn't written (e.g. table missing) is simply absent.
    meters.sort(
        key=lambda m: (
            BOATS_COMPLETENESS_COLUMNS.index(m["column"])
            if m["column"] in BOATS_COMPLETENESS_COLUMNS
            else len(BOATS_COMPLETENESS_COLUMNS)
        )
    )

    return {
        "available": True,
        "computed_at": computed_at_max,
        "buoy_threshold_pct": BUOY_THRESHOLD_PCT,
        "meters": meters,
        "events": {
            "venue_null_rate": events_venue_null_rate,
            "venue_pct_non_null": (
                round(100.0 - events_venue_null_rate, 3)
                if events_venue_null_rate is not None
                else None
            ),
            "raw_name_sample": events_raw_names,
        },
        "last_run": last_run,
    }
