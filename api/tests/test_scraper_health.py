"""Tests for the scraper health monitor (DP-00-02).

Acceptance criteria under test:

* the daily health check reports **fetch success**, **record counts** and
  **last-success timestamp** per source (all four active scrapers);
* a **simulated failure (bad URL) alerts within one cycle** — an alert is
  emitted in the same cycle the probe fails, and a failed run-log row is
  written for that source;
* **7 clean cycles** are logged (one run-log row per source per cycle);
* **scope guard** — only the four active sources are checked; no scraper
  code paths are invoked (the probe is a plain HTTP GET).

These tests use an in-memory SQLite engine with a hand-rolled schema mirror
of ``ingestion_log`` (+ the count tables) so they don't depend on Postgres
or Alembic state — the same pattern as ``test_scrape_watchdog`` /
``test_source_monitor``. The network probe is injected, so no real HTTP
calls are made.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text

from irc_data.scraper_health import (
    SOURCES,
    build_alert_message,
    format_report,
    run_health_check,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

T0 = datetime(2026, 9, 2, 6, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine():
    """In-memory SQLite with the tables the health check reads/writes."""
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE ingestion_log (
                id INTEGER PRIMARY KEY,
                source TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                status TEXT DEFAULT 'running',
                records_found INTEGER,
                records_new INTEGER,
                records_updated INTEGER,
                error_message TEXT,
                metadata TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE race_results (
                id INTEGER PRIMARY KEY, source TEXT, created_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE tcc_snapshots (
                id INTEGER PRIMARY KEY, snapshot_date DATE
            )
        """))
        conn.execute(text("""
            CREATE TABLE orc_certificates (
                id INTEGER PRIMARY KEY, cert_number TEXT
            )
        """))
    return eng


def _table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _make_probe(fail_urls: dict[str, tuple[int | None, str]]):
    """Build a probe; URLs in *fail_urls* fail with (status, error)."""
    async def probe(url: str, timeout: float):
        if url in fail_urls:
            status, err = fail_urls[url]
            return False, status, 42.0, err
        return True, 200, 123.0, None
    return probe


def _seed_prior_run(engine, source: str, completed_at: datetime, status: str = "completed"):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ingestion_log (source, started_at, completed_at, status) "
            "VALUES (:s, :st, :ct, :stat)"
        ), {"s": source, "st": completed_at, "ct": completed_at, "stat": status})


def _run_rows(engine, source: str | None = None):
    """Run-log rows written by the health check (identified by metadata blob)."""
    with engine.begin() as conn:
        q = "SELECT * FROM ingestion_log WHERE metadata LIKE '%health_check%'"
        if source:
            q += f" AND source = '{source}'"
        q += " ORDER BY id"
        return conn.execute(text(q)).fetchall()


# ---------------------------------------------------------------------------
# Scope guard
# ---------------------------------------------------------------------------

class TestScope:
    def test_exactly_the_four_active_sources(self):
        slugs = [t.source for t in SOURCES]
        assert sorted(slugs) == ["irc_tcc", "orc_api", "sailsys", "topyacht"]
        assert len(SOURCES) == 4, "scope expansion: only the four active scrapers"

    def test_each_target_has_probe_and_counts(self):
        for t in SOURCES:
            assert t.probe_url.startswith("https://"), t
            assert t.count_tables, f"{t.source} must report at least one record count"
            assert t.label and t.cadence_human


# ---------------------------------------------------------------------------
# Healthy cycle — report contract
# ---------------------------------------------------------------------------

class TestHealthyCycle:
    def test_reports_fetch_success_counts_and_last_success(self, engine):
        """All four sources probe OK; report carries the required fields."""
        # Seed a prior scraper run + some data so counts/last-success are real.
        _seed_prior_run(engine, "sailsys", T0 - timedelta(hours=1))
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO race_results (source, created_at) VALUES ('sailsys', :t)"),
                         {"t": T0 - timedelta(hours=1)})
            conn.execute(text("INSERT INTO race_results (source, created_at) VALUES ('topyacht', :t)"),
                         {"t": T0 - timedelta(hours=2)})
            conn.execute(text("INSERT INTO tcc_snapshots (snapshot_date) VALUES ('2026-09-01')"))
            conn.execute(text("INSERT INTO orc_certificates (cert_number) VALUES ('AUS123')"))

        report = run_health_check(
            engine, now=T0, probe=_make_probe({}),
            alert=False, existing_tables=_table_names(engine),
        )

        assert report.ok is True
        assert len(report.results) == 4
        by_source = {r.source: r for r in report.results}

        for src in ("topyacht", "sailsys", "irc_tcc", "orc_api"):
            r = by_source[src]
            assert r.fetch_success is True
            assert r.http_status == 200
            assert r.log_id is not None, "run-log row must be recorded per source"

        # Record counts flow through.
        assert by_source["sailsys"].record_counts["race_results"] == 1
        assert by_source["topyacht"].record_counts["race_results"] == 1
        assert by_source["irc_tcc"].record_counts["tcc_snapshots"] == 1
        assert by_source["orc_api"].record_counts["orc_certificates"] == 1

        # Last-success timestamp for the seeded sailsys run surfaces.
        assert by_source["sailsys"].last_success_at is not None

        # No failures → no alerts.
        assert report.alerts == []

        # Text report mentions every source and the key fields.
        out = format_report(report)
        for label in ("TopYacht", "SailSys", "IRC TCC", "ORC"):
            assert label in out
        assert "fetch_success" in out and "last_success" in out

    def test_run_log_rows_written_per_source(self, engine):
        report = run_health_check(
            engine, now=T0, probe=_make_probe({}),
            alert=False, existing_tables=_table_names(engine),
        )
        rows = _run_rows(engine)
        assert len(rows) == 4  # one per source, per cycle
        sources = {r.source for r in rows}
        assert sources == {"topyacht", "sailsys", "irc_tcc", "orc_api"}
        for r in rows:
            assert r.status == "completed"
            meta = json.loads(r.metadata)
            assert meta["health_check"] is True
            assert "probe_url" in meta


# ---------------------------------------------------------------------------
# Simulated failure (bad URL) — alert within one cycle
# ---------------------------------------------------------------------------

class TestFailureAlertsWithinOneCycle:
    def test_bad_url_fails_and_alerts_same_cycle(self, engine, monkeypatch):
        """A 404 probe (bad URL) → alert fired in the same cycle + failed run-log row."""
        bad = SOURCES[0].probe_url  # topyacht
        fail = {bad: (404, "HTTP 404")}

        sent: list[tuple[str, dict]] = []

        class _Resp:
            status_code = 204

        def fake_post(url, json=None, timeout=None):  # capture webhook instead of network
            sent.append((url, json))
            return _Resp()

        monkeypatch.setattr("irc_data.scraper_health.httpx.post", fake_post)

        report = run_health_check(
            engine, now=T0, probe=_make_probe(fail),
            alert=True, webhook_url="https://discord.example/webhook",
            existing_tables=_table_names(engine),
        )

        # Report reflects the failure.
        assert report.ok is False
        failing = report.failures
        assert len(failing) == 1
        assert failing[0].source == "topyacht"
        assert failing[0].fetch_success is False
        assert failing[0].http_status == 404
        assert report.alerts, "an alert line must be raised"

        # Alert fired *within this cycle* (webhook called synchronously).
        assert report.alert_sent is True
        assert "webhook" in report.alert_channels
        assert len(sent) == 1
        payload = sent[0][1]
        assert "topyacht" in json.dumps(payload).lower()

        # The failed source's run-log row is marked failed with the error.
        rows = _run_rows(engine, "topyacht")
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert "404" in (rows[0].error_message or "")

        # Other sources still logged as completed.
        ok_rows = _run_rows(engine, "sailsys")
        assert ok_rows and all(r.status == "completed" for r in ok_rows)

    def test_transport_error_alerts(self, engine, monkeypatch):
        """A network/DNS failure (no HTTP status) also alerts in-cycle."""
        bad = "https://ircrating.org/irc-racing/online-tcc-listings/"
        fail = {bad: (None, "ConnectError: name or service not known")}
        monkeypatch.setattr(
            "irc_data.scraper_health.httpx.post",
            lambda url, json=None, timeout=None: type("R", (), {"status_code": 204})(),
        )
        report = run_health_check(
            engine, now=T0, probe=_make_probe(fail),
            alert=True, webhook_url="https://slack.example/webhook",
            existing_tables=_table_names(engine),
        )
        assert report.ok is False
        assert any("irc_tcc" in a for a in report.alerts)
        assert report.alert_sent is True
        rows = _run_rows(engine, "irc_tcc")
        assert rows[0].status == "failed"

    def test_alert_message_contents(self, engine):
        report = run_health_check(
            engine, now=T0,
            probe=_make_probe({SOURCES[3].probe_url: (503, "HTTP 503")}),
            alert=False, existing_tables=_table_names(engine),
        )
        msg = build_alert_message(report)
        assert "ORC" in msg
        assert "503" in msg
        assert "orc_api" in msg


# ---------------------------------------------------------------------------
# Seven clean cycles logged
# ---------------------------------------------------------------------------

class TestSevenCleanCycles:
    def test_seven_cycles_all_logged(self, engine):
        """Simulate 7 consecutive daily cycles — all succeed, all logged."""
        for day in range(7):
            now = T0 + timedelta(days=day)
            report = run_health_check(
                engine, now=now, probe=_make_probe({}),
                alert=False, existing_tables=_table_names(engine),
            )
            assert report.ok is True, f"cycle {day} should be clean"

        rows = _run_rows(engine)
        assert len(rows) == 7 * 4  # 4 sources per cycle
        assert all(r.status == "completed" for r in rows)

        # Distinct per-cycle timestamps present in the log.
        ts = {str(r.started_at) for r in rows}
        assert len(ts) == 7

        # No alerts across 7 clean cycles.
        with engine.begin() as conn:
            failed = conn.execute(text(
                "SELECT COUNT(*) FROM ingestion_log "
                "WHERE metadata LIKE '%health_check%' AND status='failed'"
            )).scalar()
        assert failed == 0

    def test_last_success_advances_each_cycle(self, engine):
        """Last-success timestamp reported per source tracks the latest cycle."""
        for day in range(7):
            now = T0 + timedelta(days=day)
            run_health_check(
                engine, now=now, probe=_make_probe({}),
                alert=False, existing_tables=_table_names(engine),
            )
        # After 7 cycles, the reported last_success for each source is the
        # most recent cycle's timestamp (not "never").
        final = run_health_check(
            engine, now=T0 + timedelta(days=7), probe=_make_probe({}),
            alert=False, existing_tables=_table_names(engine),
        )
        for r in final.results:
            assert r.last_success_at is not None
            # Should match the day-6 cycle timestamp (the previous cycle).
            assert str(T0 + timedelta(days=6))[:10] in r.last_success_at
