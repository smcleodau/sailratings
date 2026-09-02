"""Tests for the run ledger (OPS-01-03).

Verification approach: fixture runs with controlled timestamps are written
through the ledger write path, then the ledger rows and aggregates are
asserted against hand-computed expectations.

Like ``test_ingest_log.py``, these run against an in-memory SQLite engine
with a hand-rolled ``ingestion_log`` schema mirror, so no Postgres or
Alembic state is required. The data layer deliberately uses portable SQL
(cutoffs bound as parameters, CASE WHEN aggregation) so behaviour is
identical on Postgres in production.

Fixture timeline (now = 2025-06-08 12:00 UTC, 7-day window from 2025-06-01):

  sailsys    2025-06-01 09:00  completed  found=10 new=2   <- in window, has new
  sailsys    2025-06-03 09:00  failed     error="boom"     <- in window
  sailsys    2025-05-20 09:00  completed  found=99 new=99  <- outside window
  orc_api    2025-06-02 03:00  completed  found=5  new=0   <- in window, no new
  topyacht   2025-06-04 02:30  running                       <- open run
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from irc_data.db import run_ledger
from irc_data.db.run_ledger import (
    get_daily_aggregates,
    get_run,
    get_source_health_summary,
    list_runs,
    reconcile_counts,
    record_run,
    record_run_end,
    record_run_start,
)

NOW = datetime(2025, 6, 8, 12, 0, 0)
DAY = timedelta(days=1)


@pytest.fixture()
def engine():
    """Fresh SQLite engine with ingestion_log + a reconcile target table.

    ``StaticPool`` pins the whole engine to a single connection so the
    in-memory database persists across the separate connections the API
    layer opens (each request gets its own ``engine.connect()``).
    """
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE ingestion_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    status TEXT DEFAULT 'running',
                    records_found INTEGER,
                    records_new INTEGER,
                    records_updated INTEGER,
                    error_message TEXT,
                    metadata TEXT
                )
                """
            )
        )
        # Minimal stand-in for the race_results table so reconcile_counts has
        # a registered target with rows to count.
        conn.execute(
            text(
                """
                CREATE TABLE race_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    return eng


def _write_fixture_runs(engine) -> dict[str, list[int]]:
    """Write the fixture timeline through the public write path."""
    ids: dict[str, list[int]] = {"sailsys": [], "orc_api": [], "topyacht": []}

    ids["sailsys"].append(
        record_run(
            engine,
            "sailsys",
            status="completed",
            records_found=10,
            records_new=2,
            records_updated=8,
            started_at=datetime(2025, 6, 1, 9, 0, 0),
            completed_at=datetime(2025, 6, 1, 9, 2, 30),
            metadata={"trigger": "cron"},
        )
    )
    ids["sailsys"].append(
        record_run(
            engine,
            "sailsys",
            status="failed",
            records_found=0,
            records_new=0,
            error_message="boom",
            started_at=datetime(2025, 6, 3, 9, 0, 0),
            completed_at=datetime(2025, 6, 3, 9, 0, 45),
        )
    )
    ids["sailsys"].append(
        record_run(
            engine,
            "sailsys",
            status="completed",
            records_found=99,
            records_new=99,
            started_at=datetime(2025, 5, 20, 9, 0, 0),
            completed_at=datetime(2025, 5, 20, 9, 5, 0),
        )
    )
    ids["orc_api"].append(
        record_run(
            engine,
            "orc_api",
            status="completed",
            records_found=5,
            records_new=0,
            records_updated=5,
            started_at=datetime(2025, 6, 2, 3, 0, 0),
            completed_at=datetime(2025, 6, 2, 3, 1, 0),
        )
    )
    # Open run: start written, never completed.
    ids["topyacht"].append(
        record_run_start(
            engine, "topyacht", started_at=datetime(2025, 6, 4, 2, 30, 0)
        )
    )
    return ids


@pytest.fixture()
def ledger_engine(engine):
    _write_fixture_runs(engine)
    return engine


# ---------------------------------------------------------------------------
# Write path — every run writes started/duration/status/found/new/error rows
# ---------------------------------------------------------------------------


class TestWritePath:
    def test_record_run_writes_complete_row(self, engine):
        run_id = record_run(
            engine,
            "sailsys",
            status="completed",
            records_found=12,
            records_new=3,
            records_updated=9,
            started_at=datetime(2025, 6, 1, 9, 0, 0),
            completed_at=datetime(2025, 6, 1, 9, 10, 0),
            metadata={"trigger": "cron"},
        )
        run = get_run(engine, run_id)
        assert run["source"] == "sailsys"
        assert run["started_at"] == "2025-06-01T09:00:00"
        assert run["completed_at"] == "2025-06-01T09:10:00"
        assert run["duration_seconds"] == 600.0
        assert run["status"] == "completed"
        assert run["records_found"] == 12
        assert run["records_new"] == 3
        assert run["records_updated"] == 9
        assert run["error_message"] is None
        assert run["metadata"] == {"trigger": "cron"}

    def test_failed_run_writes_error_row(self, engine):
        run_id = record_run(
            engine,
            "sailsys",
            status="failed",
            records_found=0,
            records_new=0,
            error_message="boom",
            started_at=datetime(2025, 6, 3, 9, 0, 0),
            completed_at=datetime(2025, 6, 3, 9, 0, 45),
        )
        run = get_run(engine, run_id)
        assert run["status"] == "failed"
        assert run["error_message"] == "boom"
        assert run["records_found"] == 0
        assert run["records_new"] == 0

    def test_start_then_end_lifecycle(self, engine):
        run_id = record_run_start(
            engine, "topyacht", started_at=datetime(2025, 6, 4, 2, 30, 0)
        )
        open_run = get_run(engine, run_id)
        assert open_run["status"] == "running"
        assert open_run["completed_at"] is None
        assert open_run["duration_seconds"] is None

        record_run_end(
            engine,
            run_id,
            status="completed",
            records_found=7,
            records_new=1,
            completed_at=datetime(2025, 6, 4, 2, 45, 0),
        )
        closed = get_run(engine, run_id)
        assert closed["status"] == "completed"
        assert closed["duration_seconds"] == 900.0
        assert closed["records_new"] == 1

    def test_get_run_unknown_id_returns_none(self, engine):
        assert get_run(engine, 99999) is None


# ---------------------------------------------------------------------------
# Read path — queryable by source and time
# ---------------------------------------------------------------------------


class TestListRuns:
    def test_lists_all_sources_newest_first(self, ledger_engine):
        runs = list_runs(ledger_engine)
        assert len(runs) == 5
        assert [r["source"] for r in runs] == [
            "topyacht",
            "sailsys",
            "orc_api",
            "sailsys",
            "sailsys",
        ]

    def test_filter_by_source(self, ledger_engine):
        runs = list_runs(ledger_engine, source="sailsys")
        assert len(runs) == 3
        assert all(r["source"] == "sailsys" for r in runs)

    def test_filter_by_time_window(self, ledger_engine):
        runs = list_runs(
            ledger_engine,
            source="sailsys",
            since=datetime(2025, 6, 1, 0, 0, 0),
            until=datetime(2025, 6, 2, 0, 0, 0),
        )
        assert len(runs) == 1
        assert runs[0]["records_new"] == 2

    def test_filter_by_status(self, ledger_engine):
        failed = list_runs(ledger_engine, status="failed")
        assert len(failed) == 1
        assert failed[0]["error_message"] == "boom"

    def test_limit_is_respected(self, ledger_engine):
        runs = list_runs(ledger_engine, limit=2)
        assert len(runs) == 2


# ---------------------------------------------------------------------------
# Source health summary — latest-run, latest-new-data, 7-day aggregates
# ---------------------------------------------------------------------------


class TestSourceHealthSummary:
    def test_sailsys_summary(self, ledger_engine):
        summary = {s["source"]: s for s in get_source_health_summary(ledger_engine, now=NOW)}
        s = summary["sailsys"]
        # Latest-run timestamp = most recent start, even for the failed run.
        assert s["last_started_at"] == "2025-06-03T09:00:00"
        # Latest successful completion.
        assert s["last_completed_at"] == "2025-06-01T09:02:30"
        # Latest run that actually ingested new rows.
        assert s["last_new_data_at"] == "2025-06-01T09:00:00"
        # Trailing 7 calendar days from now (2025-06-08) = 2025-06-02..06-08.
        # The 2025-06-01 and 2025-05-20 runs fall outside; only the failed
        # 2025-06-03 run is in-window.
        assert s["runs_total"] == 3
        assert s["runs_7d"] == 1
        assert s["failed_7d"] == 1
        assert s["rows_found_7d"] == 0
        assert s["rows_new_7d"] == 0
        assert s["seconds_since_last_run"] == (NOW - datetime(2025, 6, 3, 9, 0, 0)).total_seconds()

    def test_orc_api_no_new_data(self, ledger_engine):
        summary = {s["source"]: s for s in get_source_health_summary(ledger_engine, now=NOW)}
        o = summary["orc_api"]
        assert o["last_new_data_at"] is None  # records_new = 0 → tap dry
        assert o["runs_7d"] == 1
        assert o["failed_7d"] == 0
        assert o["rows_new_7d"] == 0

    def test_topyacht_open_run(self, ledger_engine):
        summary = {s["source"]: s for s in get_source_health_summary(ledger_engine, now=NOW)}
        t = summary["topyacht"]
        assert t["last_started_at"] == "2025-06-04T02:30:00"
        assert t["last_completed_at"] is None
        assert t["runs_7d"] == 1
        assert t["failed_7d"] == 0

    def test_empty_ledger(self, engine):
        assert get_source_health_summary(engine, now=NOW) == []

    def test_rows_aggregated_within_window(self, ledger_engine):
        # Add an in-window completed run with real row counts for sailsys.
        record_run(
            ledger_engine,
            "sailsys",
            status="completed",
            records_found=20,
            records_new=6,
            records_updated=14,
            started_at=datetime(2025, 6, 7, 9, 0, 0),
            completed_at=datetime(2025, 6, 7, 9, 3, 0),
        )
        summary = {s["source"]: s for s in get_source_health_summary(ledger_engine, now=NOW)}
        s = summary["sailsys"]
        # In-window completed run now contributes its rows to the 7d totals.
        assert s["runs_7d"] == 2
        assert s["rows_found_7d"] == 20
        assert s["rows_new_7d"] == 6
        # Latest-new-data moves to the new run.
        assert s["last_new_data_at"] == "2025-06-07T09:00:00"


# ---------------------------------------------------------------------------
# Daily aggregates — 7-day runs/fails/rows per day
# ---------------------------------------------------------------------------


class TestDailyAggregates:
    def test_series_covers_full_window_with_zero_fill(self, ledger_engine):
        series = get_daily_aggregates(ledger_engine, days=7, now=NOW)
        assert [d["day"] for d in series] == [
            "2025-06-02",
            "2025-06-03",
            "2025-06-04",
            "2025-06-05",
            "2025-06-06",
            "2025-06-07",
            "2025-06-08",
        ]
        by_day = {d["day"]: d for d in series}
        # 2025-06-01 run is just outside the trailing 7 calendar days.
        assert by_day["2025-06-02"]["runs"] == 1  # orc_api
        assert by_day["2025-06-03"]["runs"] == 1  # sailsys failed
        assert by_day["2025-06-03"]["failed"] == 1
        assert by_day["2025-06-04"]["runs"] == 1  # topyacht running
        assert by_day["2025-06-05"]["runs"] == 0
        assert by_day["2025-06-05"]["rows_new"] == 0

    def test_scoped_to_source(self, ledger_engine):
        series = get_daily_aggregates(ledger_engine, source="sailsys", days=7, now=NOW)
        by_day = {d["day"]: d for d in series}
        assert by_day["2025-06-02"]["runs"] == 0  # orc_api run excluded
        assert by_day["2025-06-03"]["failed"] == 1

    def test_window_includes_today(self, ledger_engine):
        record_run(
            ledger_engine,
            "sailsys",
            status="completed",
            records_found=4,
            records_new=4,
            started_at=datetime(2025, 6, 8, 6, 0, 0),
            completed_at=datetime(2025, 6, 8, 6, 1, 0),
        )
        series = get_daily_aggregates(ledger_engine, source="sailsys", days=7, now=NOW)
        by_day = {d["day"]: d for d in series}
        assert by_day["2025-06-08"]["runs"] == 1
        assert by_day["2025-06-08"]["rows_new"] == 4


# ---------------------------------------------------------------------------
# Reconciliation — ledger vs DP-05-03-style target table counts
# ---------------------------------------------------------------------------


class TestReconcile:
    def _add_race_rows(self, engine, source, n, created_at):
        with engine.begin() as conn:
            for _ in range(n):
                conn.execute(
                    text(
                        "INSERT INTO race_results (source, created_at) "
                        "VALUES (:source, :created_at)"
                    ),
                    {"source": source, "created_at": created_at},
                )

    def test_reconciles_when_counts_match(self, ledger_engine):
        # sailsys: 2 new rows on 2025-06-01 + 99 on 2025-05-20 = 101 total.
        self._add_race_rows(ledger_engine, "sailsys", 2, datetime(2025, 6, 1, 9, 5, 0))
        self._add_race_rows(ledger_engine, "sailsys", 99, datetime(2025, 5, 20, 9, 10, 0))

        result = reconcile_counts(ledger_engine, source="sailsys", table="race_results")
        assert result["ledger_runs"] == 2  # failed run excluded
        assert result["ledger_rows_new"] == 101
        assert result["actual_rows"] == 101
        assert result["new_rows_difference"] == 0
        assert result["reconciled"] is True

    def test_reconcile_scoped_by_window(self, ledger_engine):
        self._add_race_rows(ledger_engine, "sailsys", 2, datetime(2025, 6, 1, 9, 5, 0))
        self._add_race_rows(ledger_engine, "sailsys", 99, datetime(2025, 5, 20, 9, 10, 0))

        result = reconcile_counts(
            ledger_engine,
            source="sailsys",
            table="race_results",
            since=datetime(2025, 6, 1, 0, 0, 0),
        )
        assert result["ledger_rows_new"] == 2
        assert result["actual_rows"] == 2
        assert result["reconciled"] is True

    def test_drift_is_visible(self, ledger_engine):
        # Only 1 of the 101 ledger-claimed rows actually landed.
        self._add_race_rows(ledger_engine, "sailsys", 1, datetime(2025, 6, 1, 9, 5, 0))
        result = reconcile_counts(ledger_engine, source="sailsys", table="race_results")
        assert result["reconciled"] is False
        assert result["new_rows_difference"] == 100

    def test_unknown_target_rejected(self, ledger_engine):
        with pytest.raises(ValueError, match="unknown reconcile target"):
            reconcile_counts(ledger_engine, source="sailsys", table="boats; DROP TABLE boats")


# ---------------------------------------------------------------------------
# API layer — the /admin/ledger endpoints serve the same truthful records
# ---------------------------------------------------------------------------


class TestLedgerAPI:
    """End-to-end over FastAPI TestClient with the DB dependency overridden
    to the fixture engine. Proves the API returns the same ledger the data
    layer writes (the recent-runs table in the Admin design)."""

    @pytest.fixture()
    def client(self, ledger_engine, monkeypatch):
        from fastapi.testclient import TestClient

        from irc_data.api import app as app_module
        from irc_data.api.deps import get_db
        from irc_data.api.routers import admin as admin_module

        monkeypatch.setattr(admin_module, "ADMIN_PASSWORD", "test-secret")

        app_module.app.dependency_overrides[get_db] = lambda: ledger_engine
        try:
            yield TestClient(app_module.app)
        finally:
            app_module.app.dependency_overrides.pop(get_db, None)

    def _auth(self):
        return {"Authorization": "Bearer test-secret"}

    def test_requires_admin_auth(self, client):
        assert client.get("/v1/admin/ledger/runs").status_code == 401

    def test_runs_endpoint_lists_fixture_runs(self, client):
        resp = client.get("/v1/admin/ledger/runs", headers=self._auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 5
        # Newest first.
        assert body["runs"][0]["source"] == "topyacht"
        # Each row carries started/duration/status/found/new/error.
        row = next(r for r in body["runs"] if r["status"] == "failed")
        assert row["error_message"] == "boom"
        assert row["records_found"] == 0
        assert row["duration_seconds"] == 45.0

    def test_runs_endpoint_filters_by_source_and_time(self, client):
        resp = client.get(
            "/v1/admin/ledger/runs",
            params={
                "source": "sailsys",
                "since": "2025-06-01T00:00:00",
                "until": "2025-06-02T00:00:00",
            },
            headers=self._auth(),
        )
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert len(runs) == 1
        assert runs[0]["records_new"] == 2

    def test_run_detail_endpoint(self, client, ledger_engine):
        runs = list_runs(ledger_engine, source="sailsys")
        run_id = runs[0]["id"]
        resp = client.get(f"/v1/admin/ledger/runs/{run_id}", headers=self._auth())
        assert resp.status_code == 200
        assert resp.json()["id"] == run_id

    def test_run_detail_404(self, client):
        resp = client.get("/v1/admin/ledger/runs/99999", headers=self._auth())
        assert resp.status_code == 404

    def test_sources_summary_endpoint(self, client):
        resp = client.get("/v1/admin/ledger/sources", headers=self._auth())
        assert resp.status_code == 200
        body = resp.json()
        by_src = {s["source"]: s for s in body["sources"]}
        assert set(by_src) == {"sailsys", "orc_api", "topyacht"}
        assert "runs_7d" in by_src["sailsys"]
        assert by_src["sailsys"]["last_new_data_at"] == "2025-06-01T09:00:00"

    def test_daily_aggregates_endpoint(self, client):
        resp = client.get(
            "/v1/admin/ledger/aggregates/daily",
            params={"source": "sailsys", "days": 7},
            headers=self._auth(),
        )
        assert resp.status_code == 200
        series = resp.json()["series"]
        assert len(series) == 7
        assert all("runs" in d and "failed" in d and "rows_new" in d for d in series)

    def test_reconcile_endpoint_rejects_unknown_table(self, client):
        resp = client.get(
            "/v1/admin/ledger/reconcile",
            params={"source": "sailsys", "table": "boats"},
            headers=self._auth(),
        )
        assert resp.status_code == 422

    def test_reconcile_endpoint_runs(self, client):
        resp = client.get(
            "/v1/admin/ledger/reconcile",
            params={"source": "sailsys", "table": "race_results"},
            headers=self._auth(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ledger_rows_new"] == 101
        assert body["reconciled"] is False  # no race_results rows in fixture
