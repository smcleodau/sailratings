"""Contract tests for GET /v1/admin/overview (AD-01-13).

Verification approach: the "2 Sep 2026 snapshot" — a hand-built fixture
database (in-memory SQLite) loaded through the real write paths and read
through the real FastAPI app with the DB dependency overridden.

The fixture pins ``now = 2026-09-02 09:00 UTC`` so the acceptance numbers
are exact:

  * ``orc_api`` last successful run is 2026-07-26 09:00 UTC →
    ``stale_days = 38`` (2026-07-26 → 2026-09-02), cadence nightly,
    budget 48h → stale, and yields exactly one
    ``attention[kind=source_stale, source=orc_api]`` item.
  * ``dupe_review_queue`` carries 174 distinct pending clusters →
    ``dupes.pending_clusters = 174``.
  * No ingestion runs start on 2026-09-02 → ``today.new = 0`` (and
    ``today.runs = 0``).
  * One attention item per stale nightly source: the fixture has exactly
    two stale nightly sources (``orc_api`` 38d, ``topyacht`` 2d); the
    annual stale source and the paused nightly source produce none.
  * ``sailsys`` runs every one of the trailing 60 days → the runs_per_day
    series sums to ≥ 60 and carries no zero-run bands; the 38 days of
    ``orc_api`` silence are visible as zero-run bands in its ``last14``.

The overview SQL is dialect-portable, so behaviour is identical on SQLite
(here) and Postgres (production).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from irc_data.db import run_ledger
from irc_data.operations import overview as overview_mod

NOW = datetime(2026, 9, 2, 9, 0, 0)  # the "2 Sep 2026 snapshot"
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)

#: Acceptance numbers from the issue (2 Sep 2026 snapshot).
ORC_API_STALE_DAYS = 38
DUPES_PENDING_CLUSTERS = 174
TODAY_NEW = 0
STALE_NIGHTLY_SOURCES = {"orc_api", "topyacht"}


# ---------------------------------------------------------------------------
# Fixture DB — mirrors of the production tables the overview reads
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
CREATE TABLE data_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    cadence TEXT NOT NULL DEFAULT 'nightly',
    enabled BOOLEAN NOT NULL DEFAULT 1,
    legal_status TEXT NOT NULL DEFAULT 'approved',
    adapter_status TEXT NOT NULL DEFAULT 'live',
    staleness_budget_hours FLOAT
);
CREATE TABLE source_schedule_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_slug TEXT NOT NULL UNIQUE,
    schedule_id TEXT NOT NULL,
    cadence TEXT NOT NULL,
    paused BOOLEAN NOT NULL DEFAULT 0,
    notes TEXT,
    last_synced_at TIMESTAMP
);
CREATE TABLE dupe_review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    boat_id INTEGER NOT NULL,
    boat_name TEXT,
    cluster_size INTEGER NOT NULL DEFAULT 2,
    verdict TEXT NOT NULL DEFAULT 'PENDING'
);
CREATE TABLE boat_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_id INTEGER,
    field_name TEXT,
    current_value TEXT,
    proposed_value TEXT,
    submitted_email TEXT,
    submitted_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE boats (
    id INTEGER PRIMARY KEY,
    boat_name TEXT NOT NULL,
    sail_number TEXT,
    design TEXT,
    design_canonical TEXT,
    country TEXT,
    year_built INTEGER
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
def snapshot_engine(engine):
    """Load the 2 Sep 2026 snapshot through the ledger write path."""
    _write_register(engine)
    _write_schedule_state(engine)
    _write_ingestion_history(engine)
    _write_dupe_queue(engine)
    _write_corrections(engine)
    _write_boats(engine)
    return engine


def _write_register(engine) -> None:
    rows = [
        # slug, display_name, cadence, enabled, legal_status, budget_hours
        ("sailsys", "SailSys", "nightly", True, "approved", 48.0),
        ("orc_api", "ORC API", "nightly", True, "approved", 48.0),
        ("topyacht", "TopYacht", "nightly", True, "approved", 48.0),
        ("irc_certificates", "IRC Certificates", "annual", True, "approved", 370 * 24.0),
        ("legacy_manual", "Legacy Manual", "nightly", True, "approved", 48.0),
    ]
    with engine.begin() as conn:
        for slug, name, cadence, enabled, legal, budget in rows:
            conn.execute(
                text(
                    "INSERT INTO data_sources "
                    "(slug, display_name, cadence, enabled, legal_status, "
                    " staleness_budget_hours) "
                    "VALUES (:slug, :name, :cadence, :enabled, :legal, :budget)"
                ),
                {
                    "slug": slug,
                    "name": name,
                    "cadence": cadence,
                    "enabled": enabled,
                    "legal": legal,
                    "budget": budget,
                },
            )


def _write_schedule_state(engine) -> None:
    rows = [
        # source_slug, schedule_id, cadence, paused
        ("sailsys", "source-sailsys", "nightly", False),
        ("orc_api", "source-orc_api", "nightly", False),
        ("topyacht", "source-topyacht", "nightly", False),
        ("irc_certificates", "source-irc_certificates", "annual", False),
        # legacy_manual is paused — deliberate, so it must NOT produce
        # attention items even though it is long past its budget.
        ("legacy_manual", "source-legacy_manual", "nightly", True),
    ]
    with engine.begin() as conn:
        for slug, schedule_id, cadence, paused in rows:
            conn.execute(
                text(
                    "INSERT INTO source_schedule_state "
                    "(source_slug, schedule_id, cadence, paused, last_synced_at) "
                    "VALUES (:slug, :sid, :cadence, :paused, :now)"
                ),
                {
                    "slug": slug,
                    "sid": schedule_id,
                    "cadence": cadence,
                    "paused": paused,
                    "now": NOW,
                },
            )


def _write_ingestion_history(engine) -> None:
    """Ledger history for the snapshot.

      sailsys          — healthy nightly: one completed run per day for the
                         trailing 60 days (last run today 03:12, new rows
                         every day *except* the acceptance window so
                         ``today.new`` stays at the snapshot value 0).
      orc_api          — nightly, last success 2026-07-26 09:00 UTC
                         (38 days before ``now``) → stale_days=38, stale.
      topyacht         — nightly, last success 2026-08-31 09:00 UTC
                         (2 days before ``now``) → stale vs the 48h budget
                         by whole-days arithmetic.
      irc_certificates — annual cadence, last success 40 days ago: stale
                         by *time* but exempt from the nightly attention
                         rule.
      legacy_manual    — nightly but paused; last success 90 days ago.
                         Must not produce attention items.
    """
    # sailsys: daily completed runs for the trailing 60 days, ending today
    # before `now`. rows_new is 0 for the most recent runs so today.new
    # stays at the acceptance value; the older runs carry new rows so the
    # freshness / new-data signal is exercised.
    for i in range(60):
        started = NOW - timedelta(days=i, hours=6)  # 03:00-ish each day
        run_ledger.record_run(
            engine,
            "sailsys",
            status=run_ledger.STATUS_COMPLETED,
            records_found=12,
            records_new=0 if i < 5 else 3,
            records_updated=9,
            started_at=started,
            completed_at=started + timedelta(minutes=4),
        )

    # orc_api: healthy until 38 days before the snapshot, then silence.
    for i in range(ORC_API_STALE_DAYS, ORC_API_STALE_DAYS + 22):
        started = NOW - timedelta(days=i, hours=6)
        run_ledger.record_run(
            engine,
            "orc_api",
            status=run_ledger.STATUS_COMPLETED,
            records_found=14000,
            records_new=0 if i > ORC_API_STALE_DAYS else 11,
            records_updated=14000,
            started_at=started,
            completed_at=started + timedelta(minutes=9),
        )

    # topyacht: last success 2 days before the snapshot.
    for i in (2, 3, 4, 9, 10):
        started = NOW - timedelta(days=i, hours=5)
        run_ledger.record_run(
            engine,
            "topyacht",
            status=run_ledger.STATUS_COMPLETED,
            records_found=320,
            records_new=1 if i == 2 else 0,
            records_updated=300,
            started_at=started,
            completed_at=started + timedelta(minutes=3),
        )

    # irc_certificates: annual cadence; last success 40 days ago.
    started = NOW - timedelta(days=40)
    run_ledger.record_run(
        engine,
        "irc_certificates",
        status=run_ledger.STATUS_COMPLETED,
        records_found=3800,
        records_new=25,
        records_updated=3775,
        started_at=started,
        completed_at=started + timedelta(minutes=30),
    )

    # legacy_manual: paused nightly; last success 90 days ago.
    started = NOW - timedelta(days=90)
    run_ledger.record_run(
        engine,
        "legacy_manual",
        status=run_ledger.STATUS_COMPLETED,
        records_found=10,
        records_new=0,
        records_updated=10,
        started_at=started,
        completed_at=started + timedelta(minutes=1),
    )


def _write_dupe_queue(engine) -> None:
    """174 distinct pending clusters + a few decided rows.

    Tier mix matches the dedup pipeline (A exact-ish, B strong, C weak).
    """
    tiers = ["A", "B", "C"]
    with engine.begin() as conn:
        for i in range(DUPES_PENDING_CLUSTERS):
            tier = tiers[i % len(tiers)]
            # two member rows per cluster → pending boat rows = 348
            for member in range(2):
                conn.execute(
                    text(
                        "INSERT INTO dupe_review_queue "
                        "(cluster_id, tier, boat_id, boat_name, cluster_size, "
                        " verdict) "
                        "VALUES (:cid, :tier, :bid, :name, 2, 'PENDING')"
                    ),
                    {
                        "cid": f"cluster-{i:04d}",
                        "tier": tier,
                        "bid": 100000 + i * 2 + member,
                        "name": f"Boat {i}-{member}",
                    },
                )
        # decided rows must not count towards the pending figures
        conn.execute(
            text(
                "INSERT INTO dupe_review_queue "
                "(cluster_id, tier, boat_id, boat_name, cluster_size, verdict) "
                "VALUES ('cluster-decided', 'A', 200001, 'Decided', 2, 'MERGED')"
            )
        )


def _write_corrections(engine) -> None:
    with engine.begin() as conn:
        for i in range(7):
            conn.execute(
                text(
                    "INSERT INTO boat_corrections "
                    "(boat_id, field_name, proposed_value, submitted_email, "
                    " submitted_at, status) "
                    "VALUES (:bid, 'designer', 'Judel/Vrolijk', 'owner@example.com', "
                    " :at, 'pending')"
                ),
                {"bid": 5000 + i, "at": NOW - timedelta(hours=i + 1)},
            )
        conn.execute(
            text(
                "INSERT INTO boat_corrections "
                "(boat_id, field_name, proposed_value, submitted_email, "
                " submitted_at, status) "
                "VALUES (9999, 'builder', 'X-Yachts', 'x@example.com', :at, "
                " 'approved')"
            ),
            {"at": NOW - timedelta(days=3)},
        )


def _write_boats(engine) -> None:
    """40 boats: completeness meters at exact, hand-counted percentages."""
    with engine.begin() as conn:
        for i in range(40):
            conn.execute(
                text(
                    "INSERT INTO boats "
                    "(id, boat_name, sail_number, design, design_canonical, "
                    " country, year_built) "
                    "VALUES (:id, :name, :sail, :design, :canon, :country, :year)"
                ),
                {
                    "id": i + 1,
                    "name": f"Boat {i}",
                    # 40/40 have sail numbers (100%)
                    "sail": f"GBR {1000 + i}",
                    # 36/40 have a design string (90%)
                    "design": "J/111" if i < 36 else None,
                    # 30/40 have a canonical design (75%)
                    "canon": "J/111" if i < 30 else None,
                    # 20/40 have a country (50%)
                    "country": "GBR" if i < 20 else None,
                    # 10/40 have a build year (25%)
                    "year": 2015 if i < 10 else None,
                },
            )


# ---------------------------------------------------------------------------
# Data-layer contract (no HTTP)
# ---------------------------------------------------------------------------


class TestOverviewContract:
    """The acceptance numbers, asserted against the data layer directly."""

    def test_orc_api_stale_days_is_38(self, snapshot_engine):
        ov = overview_mod.get_overview(snapshot_engine, now=NOW)
        by_slug = {s["slug"]: s for s in ov["sources"]}
        orc = by_slug["orc_api"]
        assert orc["stale_days"] == ORC_API_STALE_DAYS
        assert orc["stale"] is True
        assert orc["cadence"] == "nightly"
        assert orc["paused"] is False
        assert orc["schedule_id"] == "source-orc_api"
        assert orc["last_status"] == "completed"

    def test_dupes_pending_clusters_is_174(self, snapshot_engine):
        ov = overview_mod.get_overview(snapshot_engine, now=NOW)
        assert ov["dupes"]["pending_clusters"] == DUPES_PENDING_CLUSTERS
        assert ov["dupes"]["pending"] == DUPES_PENDING_CLUSTERS * 2
        assert ov["dupes"]["by_tier"] == {"A": 116, "B": 116, "C": 116}

    def test_today_new_is_zero(self, snapshot_engine):
        ov = overview_mod.get_overview(snapshot_engine, now=NOW)
        assert ov["today"]["date"] == "2026-09-02"
        assert ov["today"]["new"] == TODAY_NEW
        # sailsys ran this morning before `now` (3:00 UTC)
        assert ov["today"]["runs"] == 1
        assert ov["today"]["failed"] == 0

    def test_one_attention_item_per_stale_nightly_source(self, snapshot_engine):
        ov = overview_mod.get_overview(snapshot_engine, now=NOW)
        stale_items = [
            i for i in ov["attention"]
            if i["kind"] == overview_mod.ATTENTION_STALE_SOURCE
        ]
        assert {i["source"] for i in stale_items} == STALE_NIGHTLY_SOURCES
        # exactly one item per stale nightly source
        assert len(stale_items) == len(STALE_NIGHTLY_SOURCES)
        # worst offender first
        assert stale_items[0]["source"] == "orc_api"
        assert stale_items[0]["stale_days"] == ORC_API_STALE_DAYS
        # annual source is stale by time but exempt from the nightly rule
        assert "irc_certificates" not in {i["source"] for i in ov["attention"]}
        # paused source never produces attention
        assert "legacy_manual" not in {
            i["source"] for i in ov["attention"] if i["source"]
        }

    def test_attention_includes_backlog_items(self, snapshot_engine):
        ov = overview_mod.get_overview(snapshot_engine, now=NOW)
        kinds = {i["kind"] for i in ov["attention"]}
        assert overview_mod.ATTENTION_DUPE_BACKLOG in kinds
        assert overview_mod.ATTENTION_CORRECTIONS_BACKLOG in kinds
        dupe_item = next(
            i for i in ov["attention"]
            if i["kind"] == overview_mod.ATTENTION_DUPE_BACKLOG
        )
        assert "174" in dupe_item["title"]

    def test_last14_sparkline_is_zero_filled(self, snapshot_engine):
        """Zero-run bands: orc_api's last14 is all zeros (38d silent); the
        sailsys last14 shows one run per day."""
        ov = overview_mod.get_overview(snapshot_engine, now=NOW)
        by_slug = {s["slug"]: s for s in ov["sources"]}

        orc_last14 = by_slug["orc_api"]["last14"]
        assert len(orc_last14) == 14
        assert all(day["runs"] == 0 for day in orc_last14)

        sailsys_last14 = by_slug["sailsys"]["last14"]
        assert all(day["runs"] == 1 for day in sailsys_last14)
        # series is a continuous 14-day calendar ending today
        assert sailsys_last14[-1]["day"] == "2026-09-02"
        assert sailsys_last14[0]["day"] == "2026-08-20"

    def test_runs_per_day_60d_series(self, snapshot_engine):
        ov = overview_mod.get_overview(snapshot_engine, now=NOW)
        series = ov["runs_per_day"]["series"]
        assert ov["runs_per_day"]["days"] == 60
        assert len(series) == 60
        assert series[-1]["day"] == "2026-09-02"
        assert series[0]["day"] == "2026-07-05"
        # sailsys ran every day; orc_api went silent 38 days ago; topyacht
        # contributes on five days; irc/legacy add one each.
        total = sum(d["runs"] for d in series)
        assert total == 60 + 22 + 5 + 1
        # zero-run band: 2026-08-30 has sailsys only? No — topyacht ran
        # 2026-08-31; 08-28..08-30 have sailsys only. A genuinely empty day
        # never occurs in the window (sailsys is daily), so assert the
        # *shape* of the bands: every day has >= 1 run and days before the
        # orc silence have >= 2.
        by_day = {d["day"]: d for d in series}
        assert by_day["2026-07-26"]["runs"] >= 2  # sailsys + orc_api
        assert by_day["2026-08-20"]["runs"] == 1  # sailsys only (zero-band for orc)

    def test_fleet_completeness_meters(self, snapshot_engine):
        ov = overview_mod.get_overview(snapshot_engine, now=NOW)
        fleet = ov["fleet"]
        assert fleet["boats"] == 40
        comp = fleet["completeness"]
        assert comp["sail_number"] == {"count": 40, "pct": 100.0}
        assert comp["design"] == {"count": 36, "pct": 90.0}
        assert comp["design_canonical"] == {"count": 30, "pct": 75.0}
        assert comp["country"] == {"count": 20, "pct": 50.0}
        assert comp["year_built"] == {"count": 10, "pct": 25.0}

    def test_corrections_pending(self, snapshot_engine):
        ov = overview_mod.get_overview(snapshot_engine, now=NOW)
        assert ov["corrections"]["pending"] == 7

    def test_overview_rollup_counters(self, snapshot_engine):
        ov = overview_mod.get_overview(snapshot_engine, now=NOW)
        roll = ov["overview"]
        assert roll["sources_tracked"] == 5
        # stale by time: orc_api (38d), topyacht (2d) and legacy_manual
        # (90d, paused). irc_certificates (annual, 40d) is inside its 370d
        # budget — fresh. sources_stale counts unpaused only.
        assert roll["sources_stale"] == 2
        assert {s["slug"] for s in ov["sources"] if s["stale"]} == {
            "orc_api",
            "topyacht",
            "legacy_manual",
        }
        assert roll["sources_paused"] == 1
        assert roll["dupes_pending_clusters"] == DUPES_PENDING_CLUSTERS
        assert roll["corrections_pending"] == 7
        assert roll["boats"] == 40
        assert roll["attention_count"] == len(ov["attention"])

    def test_overview_under_300ms(self, snapshot_engine):
        """Acceptance: < 300 ms on dev. The SQLite fixture is far smaller
        than dev Postgres; this is the latency smoke bound."""
        start = time.perf_counter()
        overview_mod.get_overview(snapshot_engine, now=NOW)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 300, f"overview took {elapsed_ms:.1f} ms"

    def test_default_now_uses_wall_clock(self, snapshot_engine):
        """Without the as_of override the layer uses wall-clock UTC."""
        ov = overview_mod.get_overview(snapshot_engine)
        assert ov["schema_version"] == overview_mod.SCHEMA_VERSION
        assert ov["sources"]  # fixture data still resolves


class TestEmptyDatabase:
    """The overview degrades gracefully when stacks are absent."""

    def test_minimal_schema(self, engine):
        """Only ingestion_log exists — register/schedule/dupe/corrections/
        boats tables are absent; the payload still validates."""
        with engine.begin() as conn:
            for table in (
                "data_sources",
                "source_schedule_state",
                "dupe_review_queue",
                "boat_corrections",
                "boats",
            ):
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        ov = overview_mod.get_overview(engine, now=NOW)
        assert ov["sources"] == []
        assert ov["today"]["new"] == 0
        assert ov["dupes"]["available"] is False
        assert ov["corrections"]["available"] is False
        assert ov["fleet"]["available"] is False
        assert ov["attention"] == []
        assert len(ov["runs_per_day"]["series"]) == 60


# ---------------------------------------------------------------------------
# HTTP contract — the real FastAPI app with the DB overridden
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(snapshot_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from irc_data.api import app as app_module
    from irc_data.api.deps import get_db
    from irc_data.api.routers import admin as admin_module

    monkeypatch.setattr(admin_module, "ADMIN_PASSWORD", "test-secret")

    app_module.app.dependency_overrides[get_db] = lambda: snapshot_engine
    try:
        yield TestClient(app_module.app)
    finally:
        app_module.app.dependency_overrides.pop(get_db, None)


def _auth():
    return {"Authorization": "Bearer test-secret"}


class TestOverviewAPI:
    def test_requires_admin_auth(self, client):
        assert client.get("/v1/admin/overview").status_code == 401

    def test_snapshot_acceptance_over_http(self, client):
        """The full 2 Sep 2026 snapshot contract, end-to-end over HTTP."""
        resp = client.get(
            "/v1/admin/overview",
            params={"as_of": "2026-09-02T09:00:00Z"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["schema_version"] == "admin-overview-v1"

        by_slug = {s["slug"]: s for s in body["sources"]}
        assert by_slug["orc_api"]["stale_days"] == 38
        assert body["dupes"]["pending_clusters"] == 174
        assert body["today"]["new"] == 0

        stale_items = [
            i for i in body["attention"] if i["kind"] == "source_stale"
        ]
        assert {i["source"] for i in stale_items} == {"orc_api", "topyacht"}
        assert len(stale_items) == 2

        # Every source carries the joined schedule state + sparkline.
        for slug in ("sailsys", "orc_api", "topyacht"):
            row = by_slug[slug]
            assert row["schedule_id"] == f"source-{slug}"
            assert row["paused"] is False
            assert len(row["last14"]) == 14
            assert row["last_run_at"] is not None
            assert row["last_status"] == "completed"

        # Fleet + meters + rollup
        assert body["fleet"]["boats"] == 40
        assert body["fleet"]["completeness"]["design"]["pct"] == 90.0
        assert body["overview"]["boats"] == 40
        assert body["overview"]["attention_count"] == len(body["attention"])

    def test_api_under_300ms(self, client):
        start = time.perf_counter()
        resp = client.get(
            "/v1/admin/overview",
            params={"as_of": "2026-09-02T09:00:00Z"},
            headers=_auth(),
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert resp.status_code == 200
        assert elapsed_ms < 300, f"GET /v1/admin/overview took {elapsed_ms:.1f} ms"

    def test_invalid_as_of_is_422(self, client):
        resp = client.get(
            "/v1/admin/overview", params={"as_of": "not-a-date"}, headers=_auth()
        )
        assert resp.status_code == 422

    def test_runs_days_bounds(self, client):
        resp = client.get(
            "/v1/admin/overview",
            params={"as_of": "2026-09-02T09:00:00Z", "runs_days": 14},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert len(resp.json()["runs_per_day"]["series"]) == 14
