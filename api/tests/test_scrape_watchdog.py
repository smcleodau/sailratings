"""Tests for the staleness watchdog (OPS-01-04).

Acceptance criteria under test:

* a source past its budget raises ONE alert (then respects a 4 h cooldown)
* recovery clears the alert (and sends a recovery email)
* alert history is retained

These tests use an in-memory SQLite engine with a hand-rolled schema mirror
of ``ingestion_log`` / ``race_results`` / ``watchdog_alerts`` so they don't
depend on Postgres or Alembic state (same pattern as test_source_monitor).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from irc_data.scrape_supervision import SourceConfig
from irc_data.scrape_watchdog import (
    STATUS_ACTIVE,
    STATUS_RECOVERED,
    ensure_watchdog_table,
    get_active_alerts,
    get_alert_history,
    run_watchdog,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

T0 = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)

# A single source on a 2 h run budget and a 24 h data budget.
SRC = SourceConfig(
    source="sailsys",
    label="SailSys (AU clubs)",
    cadence_human="every 30 min",
    run_within=timedelta(hours=2),
    data_within=timedelta(hours=24),
)


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE ingestion_log (
                id INTEGER PRIMARY KEY,
                source TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                status TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE race_results (
                id INTEGER PRIMARY KEY,
                source TEXT,
                created_at TIMESTAMP
            )
        """))
        ensure_watchdog_table(conn)
    return eng


def _record_run(engine, source: str, completed_at: datetime, status: str = "completed"):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ingestion_log (source, started_at, completed_at, status) "
            "VALUES (:s, :st, :ct, :stat)"
        ), {"s": source, "st": completed_at, "ct": completed_at, "stat": status})


def _record_data(engine, source: str, created_at: datetime):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO race_results (source, created_at) VALUES (:s, :ct)"
        ), {"s": source, "ct": created_at})


def _emails_sent():
    """A mailbox capturing (subject, html) pairs."""
    box: list[tuple[str, str]] = []
    return box, lambda subj, html: box.append((subj, html))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStaleSourceAlertsOnce:
    def test_stale_source_raises_one_alert(self, engine):
        """Induced stale source: one alert email, one active alert row."""
        # Last successful run 5 h ago — beyond the 2 h budget.
        _record_run(engine, "sailsys", T0 - timedelta(hours=5))
        _record_data(engine, "sailsys", T0 - timedelta(hours=5))
        box, send = _emails_sent()

        result = run_watchdog(engine, now=T0, sources=[SRC], send_email=send)

        assert len(result.breaches) == 1
        assert result.breaches[0].source == "sailsys"
        assert len(result.alerts_sent) == 1
        assert result.email_sent is True
        assert len(box) == 1
        assert "stale" in box[0][0]

        with engine.begin() as conn:
            active = get_active_alerts(conn)
        assert len(active) == 1
        assert active[0]["alert_key"] == "sailsys"
        assert active[0]["status"] == STATUS_ACTIVE

    def test_fresh_source_no_alert(self, engine):
        _record_run(engine, "sailsys", T0 - timedelta(minutes=30))
        _record_data(engine, "sailsys", T0 - timedelta(minutes=30))
        box, send = _emails_sent()

        result = run_watchdog(engine, now=T0, sources=[SRC], send_email=send)

        assert result.breaches == []
        assert result.alerts_sent == []
        assert not result.email_sent
        assert box == []


class TestCooldownHonoured:
    def test_second_pass_within_cooldown_sends_nothing(self, engine):
        """Alert once; a repeat 15-min pass inside 4 h must not re-alert."""
        _record_run(engine, "sailsys", T0 - timedelta(hours=5))
        box, send = _emails_sent()

        # First pass — alerts.
        r1 = run_watchdog(engine, now=T0, sources=[SRC], send_email=send)
        assert len(r1.alerts_sent) == 1
        assert len(box) == 1

        # 15 min later, still stale — cooldown honoured, no second email,
        # and no duplicate alert-log row.
        r2 = run_watchdog(
            engine, now=T0 + timedelta(minutes=15), sources=[SRC], send_email=send
        )
        assert r2.alerts_sent == []
        assert len(r2.in_cooldown) == 1
        assert not r2.email_sent
        assert len(box) == 1  # unchanged

        with engine.begin() as conn:
            history = get_alert_history(conn)
        assert len(history) == 1  # still just the one incident

    def test_alert_repeats_after_cooldown_expires(self, engine):
        """A breach that outlives 4 h re-alerts on the same incident row."""
        _record_run(engine, "sailsys", T0 - timedelta(hours=5))
        box, send = _emails_sent()

        run_watchdog(engine, now=T0, sources=[SRC], send_email=send)
        assert len(box) == 1

        # 5 h later — beyond the 4 h cooldown, still stale: re-alert.
        r2 = run_watchdog(
            engine, now=T0 + timedelta(hours=5), sources=[SRC], send_email=send
        )
        assert len(r2.alerts_sent) == 1
        assert len(box) == 2

        with engine.begin() as conn:
            history = get_alert_history(conn)
        assert len(history) == 1  # same incident, re-alerted


class TestRecoveryClearsAlert:
    def test_recovery_closes_alert_and_emails(self, engine):
        _record_run(engine, "sailsys", T0 - timedelta(hours=5))
        box, send = _emails_sent()

        # Pass 1: stale → alert.
        run_watchdog(engine, now=T0, sources=[SRC], send_email=send)
        with engine.begin() as conn:
            assert len(get_active_alerts(conn)) == 1

        # Source recovers: a fresh successful run lands.
        t1 = T0 + timedelta(minutes=30)
        _record_run(engine, "sailsys", t1)
        _record_data(engine, "sailsys", t1)

        # Pass 2: recovery clears the alert + sends recovery email.
        r2 = run_watchdog(engine, now=t1, sources=[SRC], send_email=send)
        assert r2.breaches == []
        assert len(r2.recoveries) == 1
        assert r2.recovery_email_sent is True

        # alert email + recovery email
        assert len(box) == 2
        assert "recovered" in box[1][0]

        with engine.begin() as conn:
            assert get_active_alerts(conn) == []  # cleared
            history = get_alert_history(conn)
        assert history[0]["status"] == STATUS_RECOVERED
        assert history[0]["recovered_at"] is not None


class TestAlertHistoryRetained:
    def test_history_survives_recovery_and_new_incidents(self, engine):
        _record_run(engine, "sailsys", T0 - timedelta(hours=5))
        box, send = _emails_sent()

        # Incident 1: stale at T0, recovered at T0+30m.
        run_watchdog(engine, now=T0, sources=[SRC], send_email=send)
        t1 = T0 + timedelta(minutes=30)
        _record_run(engine, "sailsys", t1)
        run_watchdog(engine, now=t1, sources=[SRC], send_email=send)

        # Incident 2: goes stale again 10 h later.
        t2 = t1 + timedelta(hours=10)
        run_watchdog(engine, now=t2, sources=[SRC], send_email=send)

        with engine.begin() as conn:
            history = get_alert_history(conn)

        # Both incidents retained — one recovered, one active.
        assert len(history) == 2
        statuses = sorted(h["status"] for h in history)
        assert statuses == [STATUS_ACTIVE, STATUS_RECOVERED]

    def test_dry_run_still_logs_but_never_emails(self, engine):
        _record_run(engine, "sailsys", T0 - timedelta(hours=5))
        box, send = _emails_sent()

        result = run_watchdog(
            engine, now=T0, sources=[SRC], send_email=send, dry_run=True
        )
        assert result.skipped_send is True
        assert box == []
        with engine.begin() as conn:
            # Breach is still logged even in dry-run — cooldown state is real.
            assert len(get_active_alerts(conn)) == 1
