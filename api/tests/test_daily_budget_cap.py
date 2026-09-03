"""Tests for the OPS-02-06 daily hard-stop credit cap.

Covers the acceptance criterion: "the cap stops calls when hit."

- ``check_throttle`` refuses non-manual callers with ``daily_block`` once the
  credits spent since UTC midnight reach ``daily_credit_cap``.
- The daily cap is independent of (and evaluated before) the period
  soft/hard caps.
- Manual callers are still allowed over the daily cap (with a warn), so a
  human poking the CLI is never hard-blocked.
- ``daily_credit_cap`` of ``None``/``0`` disables the daily cap entirely.
- ``credit_balance`` surfaces the daily state for the admin Firecrawl page.

Uses the same in-memory SQLite + hand-rolled schema pattern as
``test_crawl_telemetry.py`` so no Postgres/Alembic state is needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from irc_data.discovery import crawl_telemetry as tele
from irc_data.discovery.crawl_telemetry import (
    CrawlBudgetExhausted,
    check_throttle,
    credit_balance,
    log_call,
    upsert_settings,
)
from irc_data.discovery import firecrawl_client as fc

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
PERIOD_START = datetime(2026, 9, 1, tzinfo=timezone.utc)

SCHEMA_SQL = """
CREATE TABLE firecrawl_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    mode TEXT NOT NULL,
    url TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER,
    credits INTEGER,
    duration_ms INTEGER,
    response_chars INTEGER,
    links_found INTEGER,
    error_message TEXT,
    caller TEXT
);
CREATE TABLE crawl_budget_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL UNIQUE,
    period_credits INTEGER NOT NULL,
    soft_cap_frac REAL NOT NULL DEFAULT 0.80,
    hard_cap_frac REAL NOT NULL DEFAULT 0.95,
    daily_credit_cap INTEGER,
    period_start TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE crawl_throttle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    provider TEXT NOT NULL,
    action TEXT NOT NULL,
    caller TEXT,
    mode TEXT,
    url TEXT,
    used_credits INTEGER,
    soft_cap INTEGER,
    hard_cap INTEGER,
    utilization REAL,
    message TEXT
);
"""


@pytest.fixture()
def eng():
    """SQLite engine with telemetry schema + a 100k budget for 'firecrawl'
    with a daily cap of 50 credits."""
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with eng.begin() as conn:
        for stmt in SCHEMA_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.execute(text("""
            INSERT INTO crawl_budget_settings
              (provider, period_credits, soft_cap_frac, hard_cap_frac,
               daily_credit_cap, period_start)
            VALUES ('firecrawl', 100000, 0.80, 0.95, 50, :ps)
        """), {"ps": PERIOD_START})
    tele.set_engine(eng)
    try:
        yield eng
    finally:
        tele.set_engine(None)


def _spend(eng, credits: int, *, hours_ago: float = 0.0, caller="discover-and-ingest"):
    """Log one call spending ``credits`` credits, anchored relative to now."""
    called_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    log_call(mode="scrape", url="https://example.com/x", status="ok",
             duration_ms=100, credits=credits, caller=caller, called_at=called_at)


# ---------------------------------------------------------------------------
# Daily hard stop
# ---------------------------------------------------------------------------

def test_under_daily_cap_allows(eng):
    _spend(eng, 10)
    d = check_throttle(caller="discover-and-ingest", mode="scrape",
                       url="https://example.com")
    assert d.allowed is True
    assert d.action == "allow"
    assert d.used_today == 10
    assert d.daily_cap == 50


def test_daily_cap_blocks_discovery_when_hit(eng):
    # Spend right up to the daily cap (50) today.
    _spend(eng, 30)
    _spend(eng, 20)
    d = check_throttle(caller="discover-and-ingest", mode="scrape",
                       url="https://example.com")
    assert d.allowed is False
    assert d.action == "daily_block"
    assert d.used_today == 50
    assert d.daily_cap == 50
    assert "daily credit cap reached" in d.reason


def test_daily_cap_recorded_in_throttle_events(eng):
    _spend(eng, 60)
    check_throttle(caller="discover-and-ingest", mode="scrape",
                   url="https://example.com")
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT action, message FROM crawl_throttle_events"
        )).fetchall()
    assert len(rows) == 1
    assert rows[0].action == "daily_block"
    assert "daily credit cap" in rows[0].message


def test_daily_cap_still_allows_manual_caller(eng):
    _spend(eng, 60)
    d = check_throttle(caller="manual", mode="scrape", url="https://example.com")
    assert d.allowed is True
    assert d.action == "warn"
    assert "daily cap" in d.reason


def test_daily_cap_disabled_when_none(eng):
    # Set the daily cap column to NULL (disabled).
    with eng.begin() as conn:
        conn.execute(text(
            "UPDATE crawl_budget_settings SET daily_credit_cap = NULL "
            "WHERE provider = 'firecrawl'"
        ))
    _spend(eng, 500)  # way over any plausible daily cap
    d = check_throttle(caller="discover-and-ingest", mode="scrape",
                       url="https://example.com")
    # 500 period credits is far below the 80k soft cap, so allowed.
    assert d.allowed is True


def test_daily_cap_only_counts_today(eng):
    # Spend a lot *yesterday* (outside today's window, inside the period).
    _spend(eng, 1000, hours_ago=30)
    # Today's spend is zero → daily cap must not fire even though period
    # spend is high but still under the 80k soft cap.
    d = check_throttle(caller="discover-and-ingest", mode="scrape",
                       url="https://example.com")
    assert d.allowed is True
    assert d.used_today == 0


def test_daily_cap_takes_precedence_over_period_soft_cap(eng):
    # Push period spend over the soft cap (80k) with *old* calls, then hit
    # the daily cap today. The daily block must win (it's checked first).
    _spend(eng, 81000, hours_ago=48)  # old, period spend over soft cap
    _spend(eng, 55)                    # today, over daily cap
    d = check_throttle(caller="discover-and-ingest", mode="scrape",
                       url="https://example.com")
    assert d.action == "daily_block"
    assert d.allowed is False


def test_credit_balance_surfaces_daily_state(eng):
    _spend(eng, 60)
    bal = credit_balance(eng, provider_balance=lambda: None)
    assert bal["daily_credit_cap"] == 50
    assert bal["used_today"] == 60
    assert bal["daily_capped"] is True


def test_credit_balance_daily_not_capped_when_under(eng):
    _spend(eng, 10)
    bal = credit_balance(eng, provider_balance=lambda: None)
    assert bal["daily_capped"] is False
    assert bal["used_today"] == 10


# ---------------------------------------------------------------------------
# End-to-end: the firecrawl client refuses before hitting the API
# ---------------------------------------------------------------------------

def test_firecrawl_client_raises_budget_exhausted_at_daily_cap(eng, monkeypatch):
    """scrape_url must raise CrawlBudgetExhausted before any provider call
    once the daily cap is hit — this is the hard stop on the API path."""
    _spend(eng, 60)

    # If the client were constructed, that would be a bug (no key in tests).
    def _boom():
        raise AssertionError("provider client must not be constructed when capped")
    monkeypatch.setattr(fc, "_client", _boom)

    with pytest.raises(CrawlBudgetExhausted):
        fc.scrape_url("https://example.com/race", caller="discover-and-ingest")
