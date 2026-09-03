"""OPS-02-03 — Alerts that reach a human.

Extends the OPS-01-04 watchdog tests with the acceptance criteria for this
issue:

* **Freshness budgets** match the spec: ORC 26h, TCC 26h, SailSys 2h
  (run) / 26h (data), TopYacht 26h, weekly sources 8d.
* **Multi-channel alert** — a breach fans out to Slack *and* email; a single
  dead transport does not silence the other channel.
* **watchdog_alerts row** is written for every breach.
* **Recovery** sends a recovery message on return.
* **Acceptance drill**: pausing ``orc_api`` for 27 h produces a Slack
  message and a ``watchdog_alerts`` row; resuming produces a recovery.

All sends are injected — no network, no secrets.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from irc_data.scrape_supervision import SOURCES, SourceConfig, by_source
from irc_data.scrape_watchdog import (
    build_alert_text,
    build_recovery_text,
    ensure_watchdog_table,
    get_active_alerts,
    get_alert_history,
    run_watchdog,
)

T0 = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Freshness budgets (the OPS-02-03 contract)
# ---------------------------------------------------------------------------


class TestFreshnessBudgets:
    """Pin the per-source budgets the watchdog enforces."""

    def test_orc_budget_26h(self):
        assert by_source("orc_api").run_within == timedelta(hours=26)

    def test_tcc_budget_26h(self):
        assert by_source("irc_tcc").run_within == timedelta(hours=26)

    def test_topyacht_budget_26h(self):
        assert by_source("topyacht").run_within == timedelta(hours=26)

    def test_sailsys_run_budget_2h(self):
        assert by_source("sailsys").run_within == timedelta(hours=2)

    def test_sailsys_data_budget_26h(self):
        assert by_source("sailsys").data_within == timedelta(hours=26)

    def test_weekly_sources_budget_8d(self):
        for src in ("sailracehq", "isora", "rhkyc"):
            assert by_source(src).run_within == timedelta(days=8), src

    def test_budgets_cover_all_monitored_sources(self):
        """Every non-optional source has a finite run budget — nothing the
        watchdog monitors can be silently un-budgeted."""
        for cfg in SOURCES:
            if cfg.optional:
                continue
            assert cfg.run_within is not None and cfg.run_within.total_seconds() > 0, cfg.source


# ---------------------------------------------------------------------------
# Fixtures + capture senders
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE ingestion_log (
                id INTEGER PRIMARY KEY, source TEXT,
                started_at TIMESTAMP, completed_at TIMESTAMP, status TEXT)
        """))
        conn.execute(text("""
            CREATE TABLE race_results (
                id INTEGER PRIMARY KEY, source TEXT, created_at TIMESTAMP)
        """))
        ensure_watchdog_table(conn)
    return eng


def _run(engine, source, completed_at, status="completed"):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ingestion_log (source, started_at, completed_at, status)"
            " VALUES (:s, :st, :ct, :stat)"
        ), {"s": source, "st": completed_at, "ct": completed_at, "stat": status})


class _Capture:
    """Capture both channels: emails (subj, html) and slack (url, text)."""
    def __init__(self):
        self.emails: list[tuple[str, str]] = []
        self.slacks: list[tuple[str, str]] = []

    @property
    def send_email(self):
        return lambda subj, html: self.emails.append((subj, html))

    @property
    def send_slack(self):
        return lambda url, msg: self.slacks.append((url, msg)) or True


SLACK_URL = "https://hooks.slack.com/services/T/B/secret"


# ---------------------------------------------------------------------------
# Multi-channel fan-out
# ---------------------------------------------------------------------------


class TestMultiChannelAlert:
    def test_breach_reaches_slack_and_email(self, engine):
        cap = _Capture()
        _run(engine, "orc_api", T0 - timedelta(hours=27))  # 27h > 26h budget

        result = run_watchdog(
            engine, now=T0, sources=[by_source("orc_api")],
            send_email=cap.send_email, send_slack=cap.send_slack, slack_url=SLACK_URL,
        )

        assert result.email_sent and result.slack_sent
        assert set(result.channels) == {"email", "slack"}
        assert len(cap.emails) == 1 and len(cap.slacks) == 1
        assert cap.slacks[0][0] == SLACK_URL
        assert "stale" in cap.slacks[0][1].lower()

    def test_slack_failure_does_not_silence_email(self, engine):
        """A dead Slack webhook must not stop the email channel."""
        cap = _Capture()
        _run(engine, "orc_api", T0 - timedelta(hours=27))

        def boom(url, msg):
            raise RuntimeError("slack unreachable")

        result = run_watchdog(
            engine, now=T0, sources=[by_source("orc_api")],
            send_email=cap.send_email, send_slack=boom, slack_url=SLACK_URL,
        )
        assert result.email_sent is True
        assert result.slack_sent is False
        assert len(cap.emails) == 1

    def test_email_failure_does_not_silence_slack(self, engine):
        """A dead email transport must not stop the Slack channel."""
        cap = _Capture()
        _run(engine, "orc_api", T0 - timedelta(hours=27))

        def boom(subj, html):
            raise RuntimeError("resend down")

        result = run_watchdog(
            engine, now=T0, sources=[by_source("orc_api")],
            send_email=boom, send_slack=cap.send_slack, slack_url=SLACK_URL,
        )
        assert result.slack_sent is True
        assert result.email_sent is False
        assert len(cap.slacks) == 1
        # ... and the alert row is still committed despite the send failure.
        with engine.begin() as conn:
            assert len(get_active_alerts(conn)) == 1

    def test_no_channels_configured_marks_skipped_but_logs(self, engine):
        """With both senders disabled the breach is still logged (cooldown
        state is real) but nothing is sent."""
        _run(engine, "orc_api", T0 - timedelta(hours=27))
        result = run_watchdog(
            engine, now=T0, sources=[by_source("orc_api")],
            send_email=None, send_slack=None,
        )
        assert result.skipped_send is True
        with engine.begin() as conn:
            assert len(get_active_alerts(conn)) == 1


# ---------------------------------------------------------------------------
# Acceptance drill: pause orc_api 27h → Slack + watchdog_alerts row; resume
# → recovery.
# ---------------------------------------------------------------------------


class TestOrcPauseDrill:
    def test_pause_alert_resume_recovery(self, engine):
        cap = _Capture()
        cfg = by_source("orc_api")

        # -- PAUSE: last successful orc_api run was 27 h ago (> 26h budget).
        _run(engine, "orc_api", T0 - timedelta(hours=27))
        r1 = run_watchdog(
            engine, now=T0, sources=[cfg],
            send_email=cap.send_email, send_slack=cap.send_slack, slack_url=SLACK_URL,
        )
        # Acceptance: a Slack message ...
        assert r1.slack_sent and len(cap.slacks) == 1
        # ... and a watchdog_alerts row.
        with engine.begin() as conn:
            active = get_active_alerts(conn)
        assert len(active) == 1
        assert active[0]["source"] == "orc_api"
        assert active[0]["status"] == "active"

        # -- RESUME: orc_api runs successfully again.
        t1 = T0 + timedelta(minutes=15)
        _run(engine, "orc_api", t1)
        r2 = run_watchdog(
            engine, now=t1, sources=[cfg],
            send_email=cap.send_email, send_slack=cap.send_slack, slack_url=SLACK_URL,
        )
        # Acceptance: resuming produces a recovery (Slack + email + row close).
        assert r2.recovery_slack_sent and r2.recovery_email_sent
        assert len(r2.recoveries) == 1
        assert any("recovered" in m.lower() for _, m in cap.slacks[1:])
        with engine.begin() as conn:
            assert get_active_alerts(conn) == []
            history = get_alert_history(conn)
        assert history[0]["status"] == "recovered"
        assert history[0]["recovered_at"] is not None


# ---------------------------------------------------------------------------
# Message builders (content sanity)
# ---------------------------------------------------------------------------


class TestMessageBuilders:
    def test_alert_text_names_source_and_budget(self, engine):
        cap = _Capture()
        _run(engine, "topyacht", T0 - timedelta(hours=30))
        run_watchdog(
            engine, now=T0, sources=[by_source("topyacht")],
            send_email=cap.send_email, send_slack=cap.send_slack, slack_url=SLACK_URL,
        )
        text_msg = cap.slacks[0][1]
        assert "topyacht" in text_msg
        assert "26h" in text_msg or "26" in text_msg  # budget shown

    def test_recovery_text_says_recovered(self):
        recs = [{"source": "orc_api", "label": "ORC certificates", "cadence": "daily"}]
        msg = build_recovery_text(recs)
        assert "recovered" in msg.lower()
        assert "orc_api" in msg
