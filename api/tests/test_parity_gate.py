"""Tests for ``irc_data.diagnostics.parity_gate`` (OPS-02-06).

Covers the acceptance gate: "irc-data parity-gate --source X (14 days,
rows >= 95%, place-1 agreement >= 98%)".

Uses an in-memory SQLite engine with a minimal ``race_results`` /
``firecrawl_diffs`` schema mirror so no Postgres/Alembic state is needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from irc_data.diagnostics import parity_gate as pg

NOW = datetime.now(timezone.utc)

SCHEMA_SQL = """
CREATE TABLE race_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    event_name TEXT NOT NULL,
    event_date DATE,
    source_url TEXT,
    place INTEGER,
    transport TEXT,
    raw_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE firecrawl_diffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    event_name TEXT,
    event_date DATE,
    legacy_rows INTEGER NOT NULL,
    firecrawl_rows INTEGER NOT NULL,
    matched INTEGER NOT NULL,
    match_rate NUMERIC NOT NULL,
    confidence NUMERIC,
    missing_names TEXT,
    extra_names TEXT,
    notes TEXT
);
"""


@pytest.fixture()
def eng():
    e = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with e.begin() as conn:
        for stmt in SCHEMA_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    return e


def _insert_result(conn, source, day, transport, boat, place=1,
                   event_name="Test Event"):
    import json as _json
    conn.execute(text("""
        INSERT INTO race_results
          (source, event_name, event_date, source_url, place, transport,
           raw_data, created_at)
        VALUES
          (:s, :en, :ed, :url, :place, :t, :raw, :ca)
    """), {
        "s": source, "en": event_name, "ed": day, "url": "https://x/1",
        "place": place, "t": transport,
        "raw": _json.dumps({"boat_name": boat}),
        "ca": NOW,
    })


def _parallel_days(conn, source, n_days, fc_ratio=1.0, p1_match=True):
    """Insert n_days of matching legacy+firecrawl results."""
    for i in range(n_days):
        day = (NOW - timedelta(days=i)).date().isoformat()
        # legacy: 10 rows incl. a place-1 winner
        _insert_result(conn, source, day, "legacy", f"Winner {i}", place=1)
        for k in range(9):
            _insert_result(conn, source, day, "legacy", f"Boat {i}-{k}", place=k + 2)
        # firecrawl: fc_ratio of the rows, same winner
        fc_winner = f"Winner {i}" if p1_match else f"Other {i}"
        _insert_result(conn, source, day, "firecrawl", fc_winner, place=1)
        n_rest = max(0, int(round(9 * fc_ratio)))
        for k in range(n_rest):
            _insert_result(conn, source, day, "firecrawl", f"Boat {i}-{k}", place=k + 2)


# ---------------------------------------------------------------------------
# Parallel-run (transport) path
# ---------------------------------------------------------------------------

def test_gate_passes_when_parity_is_clean(eng):
    with eng.begin() as conn:
        _parallel_days(conn, "isora", 6, fc_ratio=1.0, p1_match=True)
    r = pg.evaluate_parity_gate(eng, "isora", days=14)
    assert r.method == "parallel"
    assert r.days_evaluated == 6
    assert r.row_capture == pytest.approx(1.0)
    assert r.place1_agreement == pytest.approx(1.0)
    assert r.passed is True


def test_gate_fails_when_row_capture_below_95(eng):
    with eng.begin() as conn:
        # Firecrawl only captures ~80% of rows each day.
        _parallel_days(conn, "isora", 6, fc_ratio=0.55, p1_match=True)
    r = pg.evaluate_parity_gate(eng, "isora", days=14)
    assert r.row_capture < 0.95
    assert r.passed is False
    assert "row_capture" in r.reason


def test_gate_fails_when_place1_disagrees(eng):
    with eng.begin() as conn:
        # Winners differ between transports.
        _parallel_days(conn, "isora", 6, fc_ratio=1.0, p1_match=False)
    r = pg.evaluate_parity_gate(eng, "isora", days=14)
    assert r.place1_checks == 6
    assert r.place1_agreement == pytest.approx(0.0)
    assert r.passed is False
    assert "place1_agreement" in r.reason


def test_gate_fails_with_insufficient_data(eng):
    with eng.begin() as conn:
        _parallel_days(conn, "isora", 3, fc_ratio=1.0, p1_match=True)
    r = pg.evaluate_parity_gate(eng, "isora", days=14, min_sample=5)
    assert r.passed is False
    assert "insufficient_data" in r.reason


def test_gate_reports_no_data(eng):
    r = pg.evaluate_parity_gate(eng, "isora", days=14)
    assert r.method == "none"
    assert r.passed is False
    assert "nothing to gate on" in r.reason


# ---------------------------------------------------------------------------
# firecrawl_diffs fallback path
# ---------------------------------------------------------------------------

def test_diffs_fallback_computes_row_capture(eng):
    with eng.begin() as conn:
        for i in range(6):
            conn.execute(text("""
                INSERT INTO firecrawl_diffs
                  (ran_at, source, source_url, legacy_rows, firecrawl_rows,
                   matched, match_rate, notes)
                VALUES (:ra, 'sailracehq', :url, 40, 40, 39, 0.975, :notes)
            """), {
                "ra": NOW,
                "url": f"https://sailracehq.com/e{i}",
                "notes": "parity-gate snapshot; row_capture=1.0; n=1",
            })
    r = pg.evaluate_parity_gate(eng, "sailracehq", days=14)
    assert r.method == "diffs"
    assert r.days_evaluated == 6
    assert r.row_capture == pytest.approx(1.0)
    assert r.passed is True


def test_diffs_fallback_skips_hollow_legacy(eng):
    # legacy_rows=0 snapshots must be excluded (they'd skew the metric).
    with eng.begin() as conn:
        for i in range(6):
            conn.execute(text("""
                INSERT INTO firecrawl_diffs
                  (ran_at, source, source_url, legacy_rows, firecrawl_rows,
                   matched, match_rate)
                VALUES (:ra, 'sailracehq', :url, 0, 12, 0, 0.0)
            """), {"ra": NOW, "url": f"https://sailracehq.com/e{i}"})
    r = pg.evaluate_parity_gate(eng, "sailracehq", days=14)
    assert r.method == "none"
    assert r.passed is False


def test_diffs_fallback_reads_row_capture_from_notes(eng):
    # A snapshot whose raw counts disagree with the notes ratio must use the
    # notes value (the authoritative per-URL row-capture).
    with eng.begin() as conn:
        for i in range(6):
            conn.execute(text("""
                INSERT INTO firecrawl_diffs
                  (ran_at, source, source_url, legacy_rows, firecrawl_rows,
                   matched, match_rate, notes)
                VALUES (:ra, 'isora', :url, 50, 30, 25, 0.5, :notes)
            """), {
                "ra": NOW, "url": f"https://isora.org/e{i}",
                "notes": "parity-gate snapshot; row_capture=0.99; n=1",
            })
    r = pg.evaluate_parity_gate(eng, "isora", days=14)
    assert r.row_capture == pytest.approx(0.99)
    assert r.passed is True


def test_name_normalisation_handles_case_and_spacing():
    assert pg._norm_name("Rampage 88") == pg._norm_name("RAMPAGE88")
    assert pg._norm_name("Black Jack (DH)") == pg._norm_name("BLACKJACK")
    assert pg._norm_name("") == ""
