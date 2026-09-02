"""Tests for ``irc_data.discovery.crawl_telemetry`` (OPS-01-05).

Covers the acceptance criteria:

1. Every crawl/map/scrape call is logged with mode, URL, status, credits,
   latency, caller (per-call ledger).
2. Per-domain 7-day aggregates are computed from fixture calls.
3. Balance, projected monthly burn and headroom are computed.
4. Soft/hard credit caps throttle discovery before exhaustion.

Uses an in-memory SQLite eng with a hand-rolled schema mirror (same
pattern as ``test_ingest_log.py``) so no Postgres / Alembic state is
needed. Provider calls are faked — no Firecrawl key required.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

from irc_data.discovery import crawl_telemetry as tele
from irc_data.discovery.crawl_telemetry import (
    CrawlBudgetExhausted,
    check_throttle,
    credit_balance,
    domain_aggregates,
    log_call,
    recent_throttle_events,
    window_aggregates,
)
from irc_data.discovery import firecrawl_client as fc

NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
PERIOD_START = datetime(2026, 5, 1, tzinfo=timezone.utc)

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
    """Fresh SQLite eng with the telemetry schema + a 100k-credit budget
    for 'firecrawl' whose period started 2026-05-01."""
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with eng.begin() as conn:
        for stmt in SCHEMA_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.execute(text("""
            INSERT INTO crawl_budget_settings
              (provider, period_credits, soft_cap_frac, hard_cap_frac, period_start)
            VALUES ('firecrawl', 100000, 0.80, 0.95, :ps)
        """), {"ps": PERIOD_START})
    tele.set_engine(eng)
    try:
        yield eng
    finally:
        tele.set_engine(None)


@pytest.fixture()
def fixture_calls(eng):
    """A deterministic spread of crawl/map/scrape calls across domains,
    statuses and days. 'Old' calls sit outside the 7-day window but inside
    the billing period so window and budget maths differ."""
    rows = [
        # Recent: sailracehq — 5 scrapes (1 empty), 1 map, 1 crawl
        dict(mode="scrape", url="https://sailracehq.com/r1", status="ok",
             credits=1, duration_ms=900, caller="discover-and-ingest",
             days_ago=1),
        dict(mode="scrape", url="https://sailracehq.com/r2", status="ok",
             credits=1, duration_ms=1100, caller="discover-and-ingest",
             days_ago=1),
        dict(mode="scrape", url="https://sailracehq.com/r3", status="error",
             credits=None, duration_ms=300, caller="discover-and-ingest",
             days_ago=2, error_message="timeout"),
        dict(mode="scrape", url="https://sailracehq.com/r4", status="empty",
             credits=1, duration_ms=700, caller="discover-and-ingest",
             days_ago=3),
        dict(mode="scrape", url="https://www.sailracehq.com/r5", status="ok",
             credits=2, duration_ms=1500, caller="discover-and-ingest",
             days_ago=4),
        dict(mode="map", url="https://sailracehq.com/", status="ok",
             credits=1, duration_ms=2500, caller="discover-and-ingest",
             days_ago=1, links_found=42),
        dict(mode="crawl", url="https://sailracehq.com/series", status="ok",
             credits=9, duration_ms=8000, caller="discovery",
             days_ago=2, links_found=9),
        # Recent: isora.org — 2 ok scrapes
        dict(mode="scrape", url="https://www.isora.org/results/a", status="ok",
             credits=1, duration_ms=1200, caller="discover-and-ingest",
             days_ago=1),
        dict(mode="scrape", url="https://www.isora.org/results/b", status="ok",
             credits=1, duration_ms=1300, caller="discover-and-ingest",
             days_ago=5),
        # Recent: rhkyc.org.hk — 1 error
        dict(mode="scrape", url="https://rhkyc.org.hk/res/x", status="error",
             credits=1, duration_ms=200, caller="cli",
             days_ago=6, error_message="403"),
        # Old: inside billing period but outside the 7-day window
        dict(mode="scrape", url="https://sailracehq.com/old1", status="ok",
             credits=3, duration_ms=900, caller="discovery", days_ago=10),
        dict(mode="map", url="https://sailracehq.com/", status="ok",
             credits=2, duration_ms=2000, caller="discovery", days_ago=12,
             links_found=30),
        # Ancient: outside the 30-day window AND before the period start —
        # must not appear in any aggregate or the budget.
        dict(mode="scrape", url="https://sailracehq.com/prehistoric",
             status="ok", credits=50, duration_ms=900, caller="discovery",
             days_ago=45),
    ]
    for r in rows:
        r = dict(r)
        # +5 minutes keeps fixtures strictly *inside* their intended window
        # regardless of whether a boundary comparison is > or >=.
        called_at = NOW - timedelta(days=r.pop("days_ago")) + timedelta(minutes=5)
        log_call(mode=r.pop("mode"), url=r.pop("url"),
                 status=r.pop("status"), duration_ms=r.pop("duration_ms"),
                 credits=r.pop("credits"), caller=r.pop("caller"),
                 called_at=called_at, **r)
    return rows


# ---------------------------------------------------------------------------
# 1. Per-call ledger
# ---------------------------------------------------------------------------

def test_every_call_logged_with_mode_url_status_credits_latency_caller(eng):
    log_call(mode="scrape", url="https://www.example.com/page",
             status="ok", duration_ms=1234, credits=3, caller="cli")
    log_call(mode="map", url="https://example.com/", status="empty",
             duration_ms=2000, credits=None, caller="discovery")
    log_call(mode="crawl", url="https://example.com/series", status="error",
             duration_ms=500, credits=5, caller="discover-and-ingest",
             error_message="boom")

    with eng.connect() as conn:
        rows = conn.execute(text("""
            SELECT mode, url, domain, status, credits, duration_ms, caller,
                   error_message
            FROM firecrawl_calls ORDER BY id
        """)).fetchall()

    assert [r.mode for r in rows] == ["scrape", "map", "crawl"]
    scrape, map_, crawl = rows
    # mode, URL, status, credits, latency, caller all persisted
    assert scrape.url == "https://www.example.com/page"
    assert scrape.domain == "example.com"          # www. stripped
    assert scrape.status == "ok"
    assert scrape.credits == 3
    assert scrape.duration_ms == 1234
    assert scrape.caller == "cli"
    # credits fallback: unreported calls still count as 1
    assert map_.credits == 1
    assert map_.status == "empty"
    assert crawl.status == "error"
    assert crawl.error_message == "boom"


def test_log_call_never_raises_on_broken_engine():
    broken = create_engine("sqlite+pysqlite:////nonexistent-dir/x.db")
    # Should not raise — telemetry must never break a scrape.
    log_call(broken, mode="scrape", url="https://x.com", status="ok",
             duration_ms=1, credits=1, caller="cli")


# ---------------------------------------------------------------------------
# 2. Aggregates
# ---------------------------------------------------------------------------

def test_window_aggregates_from_fixture(fixture_calls):
    agg = window_aggregates(now=NOW)

    # 7d window: 10 recent calls, credits = 1+1+1+1+2+1+9+1+1+1 = 19
    w7 = agg["7d"]
    assert w7["calls"] == 10
    assert w7["credits"] == 19
    assert w7["ok"] == 7
    assert w7["empty"] == 1
    assert w7["errored"] == 2
    assert w7["scrapes"] == 8
    assert w7["maps"] == 1
    assert w7["crawls"] == 1
    assert w7["domains"] == 3

    # 30d window additionally includes the two 10/12-day-old calls
    w30 = agg["30d"]
    assert w30["calls"] == 12
    assert w30["credits"] == 24

    # today = last 24h: the 4 calls from days_ago=1 (3 scrapes + 1 map)
    assert agg["today"]["calls"] == 4
    assert agg["today"]["credits"] == 4


def test_domain_aggregates_seven_day(fixture_calls):
    doms = {d["domain"]: d for d in domain_aggregates(days=7, now=NOW)}

    # 7-day window, sorted by calls: sailracehq (7), isora (2), rhkyc (1)
    assert set(doms) == {"sailracehq.com", "isora.org", "rhkyc.org.hk"}

    srhq = doms["sailracehq.com"]
    assert srhq["calls"] == 7           # 5 scrapes + map + crawl; www. folded
    # 1+1+(error fallback 1)+1+2+1+9 = 16 — an error call with unreported
    # credits still costs 1 against the domain budget.
    assert srhq["credits"] == 16
    # ok = 3 scrapes + map + crawl; empty = 1 scrape; errored = 1 scrape
    assert srhq["ok"] == 5
    assert srhq["empty"] == 1
    assert srhq["errored"] == 1
    assert srhq["success_rate"] == pytest.approx(5 / 7)
    assert srhq["avg_ms"] == round((900 + 1100 + 300 + 700 + 1500 + 2500 + 8000) / 7)

    isora = doms["isora.org"]
    assert isora["calls"] == 2
    assert isora["success_rate"] == 1.0

    assert doms["rhkyc.org.hk"]["errored"] == 1
    assert doms["rhkyc.org.hk"]["success_rate"] == 0.0


def test_domain_aggregates_window_respected(fixture_calls):
    # A 1-day window only sees the 3 calls from days_ago=1.
    doms = {d["domain"]: d for d in domain_aggregates(days=1, now=NOW)}
    assert set(doms) == {"sailracehq.com", "isora.org"}
    assert doms["sailracehq.com"]["calls"] == 3


# ---------------------------------------------------------------------------
# 3. Balance / projected burn / headroom
# ---------------------------------------------------------------------------

def test_credit_balance_from_ledger(fixture_calls):
    bal = credit_balance(now=NOW)

    # Period spend (since 2026-05-01) excludes the 45-day-old call.
    assert bal["period_spend"] == 24
    assert bal["period_credits"] == 100000
    assert bal["balance_source"] == "ledger"
    assert bal["balance_credits"] == 100000 - 24

    # Projected monthly burn: 7-day burn (19) scaled to 30 days.
    assert bal["burn_7d"] == 19
    assert bal["projected_monthly_burn"] == round(19 * 30 / 7)   # 81
    assert bal["headroom_credits"] == bal["balance_credits"] - bal["projected_monthly_burn"]

    # Caps derived from settings fractions.
    assert bal["soft_cap"] == 80000
    assert bal["hard_cap"] == 95000


def test_credit_balance_prefers_provider_reported(fixture_calls):
    probe = lambda: {"remaining_credits": 42000, "plan_credits": 100000}
    bal = credit_balance(provider_balance=probe, now=NOW)
    assert bal["balance_credits"] == 42000
    assert bal["balance_source"] == "provider"
    assert bal["provider_reported"] == {"remaining_credits": 42000,
                                        "plan_credits": 100000}
    assert bal["headroom_credits"] == 42000 - bal["projected_monthly_burn"]


def test_credit_balance_survives_dead_probe(fixture_calls):
    def dead():
        raise RuntimeError("firecrawl api down")
    bal = credit_balance(provider_balance=dead, now=NOW)
    assert bal["balance_source"] == "ledger"
    assert bal["balance_credits"] == 100000 - 24


# ---------------------------------------------------------------------------
# 4. Soft / hard caps throttle discovery before exhaustion
# ---------------------------------------------------------------------------

def _spend(eng, credits: int, caller: str = "discovery") -> None:
    """Backfill prior period spend straight into the ledger."""
    log_call(eng, mode="scrape", url="https://burn.example/",
             status="ok", duration_ms=10, credits=credits, caller=caller,
             called_at=NOW - timedelta(days=1))


def test_under_soft_cap_allows(fixture_calls):
    d = check_throttle(caller="discover-and-ingest", mode="map",
                       url="https://sailracehq.com/")
    assert d.allowed and d.action == "allow"
    # 'allow' decisions are not ledgered — the event log exists so the
    # onset of throttling is visible, not to mirror every call.
    assert recent_throttle_events() == []


def test_soft_cap_blocks_discovery_callers(fixture_calls):
    _spend(eng=None, credits=80000 - 24)   # 24 already spent → exactly at cap
    d = check_throttle(caller="discover-and-ingest", mode="map",
                       url="https://sailracehq.com/")
    assert not d.allowed
    assert d.action == "soft_block"
    assert d.used_credits == 80000
    assert d.soft_cap == 80000

    events = recent_throttle_events(blocked_only=True)
    assert len(events) == 1
    assert events[0]["action"] == "soft_block"
    assert events[0]["caller"] == "discover-and-ingest"
    assert "soft credit cap" in events[0]["message"]


def test_soft_cap_only_warns_non_discovery(fixture_calls):
    _spend(eng=None, credits=80000 - 24)
    d = check_throttle(caller="cli", mode="scrape", url="https://x.com/")
    assert d.allowed and d.action == "warn"


def test_hard_cap_blocks_everything_but_manual(fixture_calls):
    _spend(eng=None, credits=95000 - 24)

    d = check_throttle(caller="cli", mode="scrape", url="https://x.com/")
    assert not d.allowed and d.action == "hard_block"
    assert "hard credit cap" in d.reason

    d = check_throttle(caller="discover-and-ingest", mode="map",
                       url="https://x.com/")
    assert not d.allowed and d.action == "hard_block"

    # A human at the CLI keeps emergency access (warned, ledgered).
    d = check_throttle(caller="manual", mode="scrape", url="https://x.com/")
    assert d.allowed and d.action == "warn"

    actions = [e["action"] for e in recent_throttle_events()]
    assert actions.count("hard_block") == 2
    assert actions.count("warn") == 1


def test_reserve_would_tip_hard_cap(fixture_calls):
    # Just under the hard cap, but a 100-credit crawl would push us over.
    _spend(eng=None, credits=94950 - 24)
    d = check_throttle(caller="cli", mode="crawl", url="https://x.com/",
                       reserve_credits=100)
    assert not d.allowed and d.action == "hard_block"


# ---------------------------------------------------------------------------
# 5. Provider-client integration: gate fires before the SDK is touched
# ---------------------------------------------------------------------------

def test_scrape_url_throttles_without_calling_sdk(fixture_calls):
    _spend(eng=None, credits=95000 - 24)   # at hard cap

    def _boom():
        raise AssertionError("provider SDK must not be touched when throttled")

    with patch.object(fc, "_client", side_effect=_boom):
        with pytest.raises(CrawlBudgetExhausted, match="hard credit cap"):
            fc.scrape_url("https://sailracehq.com/r9", caller="discover-and-ingest")

    # The refused call consumed nothing — ledger spend unchanged.
    assert credit_balance(now=NOW)["period_spend"] == 95000


def test_map_site_throttles_at_soft_cap_for_discovery(fixture_calls):
    _spend(eng=None, credits=80000 - 24)
    with pytest.raises(CrawlBudgetExhausted, match="soft credit cap"):
        fc.map_site("https://sailracehq.com/", caller="discover-and-ingest")


def test_scrape_url_allowed_call_still_logs_ledger(fixture_calls, monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    fake_resp = SimpleNamespace(
        markdown="# hello", metadata={"title": "Hello"}, credits_used=2,
    )
    fake_client = SimpleNamespace(scrape=lambda url, formats: fake_resp)
    with patch.object(fc, "_client", return_value=fake_client):
        result = fc.scrape_url("https://sailracehq.com/r9", caller="cli")

    assert result.markdown == "# hello"
    agg = window_aggregates(now=NOW + timedelta(seconds=1))
    assert agg["7d"]["calls"] == 11       # 10 fixtures + this one
    assert agg["7d"]["credits"] == 21     # 19 + 2


def test_crawl_site_mode_logged(fixture_calls, eng, monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    pages = [
        {"markdown": "# a", "metadata": {"sourceURL": "https://x.com/a", "title": "A"}},
        {"markdown": "# b", "metadata": {"sourceURL": "https://x.com/b", "title": "B"}},
    ]
    fake_client = SimpleNamespace(
        crawl=lambda url, limit, scrape_options: {"data": pages, "credits_used": 2},
    )
    with patch.object(fc, "_client", return_value=fake_client):
        out = fc.crawl_site("https://x.com/", limit=2, caller="discovery")

    assert out["page_count"] == 2
    assert out["pages"][0]["url"] == "https://x.com/a"
    with eng.connect() as conn:
        row = conn.execute(text("""
            SELECT mode, status, credits, links_found FROM firecrawl_calls
            WHERE mode = 'crawl' AND url = 'https://x.com/'
        """)).fetchone()
    assert row is not None
    assert row.mode == "crawl" and row.status == "ok"
    assert row.credits == 2 and row.links_found == 2


def test_budget_settings_upsert(eng):
    tele.upsert_settings(provider="firecrawl", period_credits=200000,
                         soft_cap_frac=0.5, hard_cap_frac=0.9)
    settings = tele._get_settings(eng, "firecrawl")
    assert settings["period_credits"] == 200000
    assert settings["soft_cap_frac"] == 0.5
    assert settings["hard_cap_frac"] == 0.9

    # The gate honours the new caps: 100k spend is now exactly the soft cap.
    _spend(eng=None, credits=100000)
    d = check_throttle(caller="discover-and-ingest", mode="map",
                       url="https://x.com/")
    assert not d.allowed and d.action == "soft_block"


def test_check_throttle_fails_open_without_settings(eng):
    """No settings row → env defaults, gate still functional."""
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM crawl_budget_settings"))
    d = check_throttle(caller="cli", mode="scrape", url="https://x.com/")
    assert d.allowed and d.action == "allow"
    assert d.soft_cap == 80000 and d.hard_cap == 95000
