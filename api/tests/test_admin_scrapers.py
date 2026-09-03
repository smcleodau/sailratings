"""Contract tests for the /admin/scrapers health page backend (AD-01-06).

Verification approach: a hand-built ledger fixture (in-memory SQLite) is
written through the OPS-01-03 ledger write path (``record_run``) and read
back through the real FastAPI app with the DB dependency overridden — the
same pattern as ``test_admin_overview.py`` (AD-01-13).

The fixture pins a known timeline so the page's acceptance numbers are
exact:

  * ``sailsys``   — healthy: completed run 30 min ago (found 12, new 3),
                    one failure and one older completed run inside the
                    7-day window → runs_7d=3, failed_7d=1, new_records_7d=8.
                    Has race_results rows → data tap fresh.
  * ``topyacht``  — cron breach: last successful run 5 days ago vs a 26 h
                    budget → run_state=stale. Has race_results rows 5 days
                    old → data_state=stale.
  * ``orc_api``   — never ran → run_state=never. Writes no race_results
                    rows and has no data budget → data_state=n/a.
  * ``cowesweek`` — optional annual source → state=optional regardless.
  * ``ghost``     — uncatalogued source (in the ledger, not in the
                    supervision registry) → surfaced with state=uncatalogued.

The watchdog fixture (OPS-01-04) carries one active run-signal alert for
``topyacht`` and one recovered data-signal alert for ``sailsys`` — the
page's "Cron health" banner reads ``alerts_active``.

Timestamps are relative to wall-clock now so no time-mocking is needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from irc_data.db.run_ledger import record_run

DAY = timedelta(days=1)
HOUR = timedelta(hours=1)


# ---------------------------------------------------------------------------
# Fixture DB — mirrors of the production tables the page's endpoints read
# ---------------------------------------------------------------------------

DDL = """
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
);
CREATE TABLE race_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    event_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
    return eng


@pytest.fixture()
def ledger_engine(engine):
    """Load the fixture timeline through the ledger write path."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # sailsys — healthy, three runs inside the window.
    record_run(
        engine, "sailsys", status="completed",
        records_found=12, records_new=3, records_updated=9,
        started_at=now - timedelta(minutes=30),
        completed_at=now - timedelta(minutes=30) + timedelta(seconds=42.5),
    )
    record_run(
        engine, "sailsys", status="failed",
        records_found=0, records_new=0, error_message="HTTP 503 from club site",
        started_at=now - 1 * DAY, completed_at=now - 1 * DAY + timedelta(seconds=8),
    )
    record_run(
        engine, "sailsys", status="completed",
        records_found=10, records_new=5, records_updated=5,
        started_at=now - 2 * DAY, completed_at=now - 2 * DAY + timedelta(seconds=31),
    )

    # topyacht — cron breach: last success 5 days ago (budget 26 h).
    record_run(
        engine, "topyacht", status="completed",
        records_found=7, records_new=7,
        started_at=now - 5 * DAY, completed_at=now - 5 * DAY + timedelta(seconds=95),
    )

    # ghost — uncatalogued: ledger rows but no supervision registry entry.
    record_run(
        engine, "ghost", status="completed",
        records_found=4, records_new=1,
        started_at=now - 3 * HOUR, completed_at=now - 3 * HOUR + timedelta(seconds=12),
    )

    # Data-tap rows: sailsys fresh (30 min), topyacht stale (5 days).
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO race_results (source, event_date, created_at) "
                "VALUES (:src, :ev, :created)"
            ),
            [
                {
                    "src": "sailsys",
                    "ev": (now - 1 * DAY).date().isoformat(),
                    "created": now - timedelta(minutes=30),
                },
                {
                    "src": "topyacht",
                    "ev": (now - 6 * DAY).date().isoformat(),
                    "created": now - 5 * DAY,
                },
            ],
        )

    # Watchdog alert log (OPS-01-04): one active run alert (topyacht),
    # one recovered data alert (sailsys).
    from irc_data.scrape_watchdog import ensure_watchdog_table

    with engine.begin() as conn:
        ensure_watchdog_table(conn)
        conn.execute(
            text(
                "INSERT INTO watchdog_alerts "
                "(alert_key, source, signal, label, cadence, reason, "
                " age_hours, budget_hours, status, first_seen_at, "
                " alerted_at, cooldown_until, recovered_at) "
                "VALUES (:key, :src, :signal, :label, :cadence, :reason, "
                "        :age, :budget, :status, :first, :alerted, "
                "        :cooldown, :recovered)"
            ),
            [
                {
                    "key": "topyacht",
                    "src": "topyacht",
                    "signal": "run",
                    "label": "TopYacht (AU/regattas)",
                    "cadence": "daily 02:30 UTC",
                    "reason": "cron stopped (no successful run)",
                    "age": 120.0,
                    "budget": 26.0,
                    "status": "active",
                    "first": now - 2 * HOUR,
                    "alerted": now - 2 * HOUR,
                    "cooldown": now + 2 * HOUR,
                    "recovered": None,
                },
                {
                    "key": "sailsys:data",
                    "src": "sailsys",
                    "signal": "data",
                    "label": "SailSys (AU clubs) (no new data)",
                    "cadence": "every 30 min",
                    "reason": "no new race rows beyond seasonal lull",
                    "age": 30.0,
                    "budget": 26.0,
                    "status": "recovered",
                    "first": now - 3 * DAY,
                    "alerted": now - 3 * DAY,
                    "cooldown": None,
                    "recovered": now - 2 * DAY,
                },
            ],
        )

    return engine


@pytest.fixture()
def client(ledger_engine, monkeypatch):
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


def _auth():
    return {"Authorization": "Bearer test-secret"}


def _by_source(body) -> dict:
    return {s["source"]: s for s in body["sources"]}


# ---------------------------------------------------------------------------
# GET /admin/scrapers — the page's summary payload
# ---------------------------------------------------------------------------


class TestScrapersSummary:
    def test_requires_admin_auth(self, client):
        assert client.get("/v1/admin/scrapers").status_code == 401

    def test_renders_every_supervised_source(self, client):
        """Acceptance: '/admin/scrapers renders every source'."""
        body = client.get("/v1/admin/scrapers", headers=_auth()).json()
        by_src = _by_source(body)
        for slug in (
            "sailsys", "orc_api", "irc_tcc", "topyacht", "sailracehq",
            "isora", "rhkyc", "cowesweek", "sydneyhobart", "rorc",
        ):
            assert slug in by_src, f"missing supervised source {slug}"
        # …plus the uncatalogued ledger source.
        assert "ghost" in by_src

    def test_last_run_and_7d_aggregates(self, client):
        """Acceptance: last run + 7-day runs/fails/rows per source."""
        body = client.get("/v1/admin/scrapers", headers=_auth()).json()
        sailsys = _by_source(body)["sailsys"]
        assert sailsys["last_success"] is not None
        assert sailsys["last_started"] is not None
        # 30-minute-old success — fresh against the 2 h budget.
        assert sailsys["run_state"] == "fresh"
        assert sailsys["run_age_seconds"] is not None
        assert 0 < sailsys["run_age_seconds"] < 2 * 3600
        # Three runs in the window: 2 completed (3 + 5 new) + 1 failed.
        assert sailsys["runs_7d"] == 3
        assert sailsys["failed_7d"] == 1
        assert sailsys["new_records_7d"] == 8

    def test_last_new_data_data_tap(self, client):
        """Acceptance: last new data per source (the race_results tap)."""
        body = client.get("/v1/admin/scrapers", headers=_auth()).json()
        by_src = _by_source(body)
        assert by_src["sailsys"]["data_state"] == "fresh"
        assert by_src["sailsys"]["last_new_data"] is not None
        assert by_src["sailsys"]["latest_event_date"] is not None
        # topyacht's tap is 5 days dry vs a 26 h budget → stale.
        assert by_src["topyacht"]["data_state"] == "stale"
        # orc_api writes no race_results and has no data budget → n/a.
        assert by_src["orc_api"]["data_state"] == "n/a"
        assert by_src["orc_api"]["last_new_data"] is None

    def test_stale_never_optional_uncatalogued_states(self, client):
        body = client.get("/v1/admin/scrapers", headers=_auth()).json()
        by_src = _by_source(body)
        assert by_src["topyacht"]["run_state"] == "stale"
        assert by_src["topyacht"]["state"] == "stale"
        assert by_src["orc_api"]["run_state"] == "never"
        assert by_src["orc_api"]["state"] == "never"
        assert by_src["cowesweek"]["state"] == "optional"
        assert by_src["ghost"]["state"] == "uncatalogued"
        assert by_src["ghost"]["runs_7d"] == 1
        assert by_src["ghost"]["new_records_7d"] == 1

    def test_watchdog_alert_stream_for_banner(self, client):
        """Acceptance: 'Cron health' banner is fed by watchdog alerts."""
        body = client.get("/v1/admin/scrapers", headers=_auth()).json()
        active = body["alerts_active"]
        assert len(active) == 1
        assert active[0]["source"] == "topyacht"
        assert active[0]["signal"] == "run"
        assert active[0]["status"] == "active"
        # History retains the recovered sailsys:data alert too.
        keys = {a["alert_key"] for a in body["alerts_history"]}
        assert keys == {"topyacht", "sailsys:data"}


# ---------------------------------------------------------------------------
# GET /admin/scrapers/{source}/runs — the expandable recent-runs drawer
# ---------------------------------------------------------------------------


class TestScraperRuns:
    def test_requires_admin_auth(self, client):
        assert client.get("/v1/admin/scrapers/sailsys/runs").status_code == 401

    def test_recent_runs_newest_first(self, client):
        """Acceptance: expandable recent-runs table per source."""
        resp = client.get("/v1/admin/scrapers/sailsys/runs", headers=_auth())
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert len(runs) == 3
        assert runs[0]["status"] == "completed"
        assert runs[0]["records_found"] == 12
        assert runs[0]["records_new"] == 3
        assert runs[0]["duration_seconds"] == pytest.approx(42.5)
        # The failed run carries its error message for the ERROR column.
        failed = [r for r in runs if r["status"] == "failed"]
        assert failed and failed[0]["error_message"] == "HTTP 503 from club site"
        # Newest first.
        starts = [r["started_at"] for r in runs]
        assert starts == sorted(starts, reverse=True)

    def test_runs_limit_is_bounded(self, client):
        resp = client.get(
            "/v1/admin/scrapers/sailsys/runs", params={"limit": 2}, headers=_auth()
        )
        assert len(resp.json()["runs"]) == 2
        # Absurd limits are clamped, not honoured.
        resp = client.get(
            "/v1/admin/scrapers/sailsys/runs",
            params={"limit": 99999},
            headers=_auth(),
        )
        assert resp.status_code == 200

    def test_unknown_source_returns_empty_run_list(self, client):
        resp = client.get(
            "/v1/admin/scrapers/no-such-source/runs", headers=_auth()
        )
        assert resp.status_code == 200
        assert resp.json()["runs"] == []
