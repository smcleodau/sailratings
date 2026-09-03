"""Tests for the OPS-02-06 cutover machinery (operations/cutover.py).

Covers: "run ISORA and SailRaceHQ legacy + Firecrawl in parallel and cut
over when the gate passes (pause legacy, update data_sources.adapter_class)".

- The cutover is REFUSED when the parity gate does not pass.
- With a passing gate, the cutover repoints ``data_sources.adapter_class``
  at the Firecrawl pipeline, marks it active, and writes an audit row.
- ``--force`` overrides a failing gate (audited).
- ``get_cutover_state`` reports the 14-day transport split (the
  "rows arrive with transport='firecrawl'" evidence).

In-memory SQLite with a minimal ``data_sources`` / ``race_results`` /
``ingest_events`` schema mirror.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from irc_data.operations import cutover as co

NOW = datetime.now(timezone.utc)

SCHEMA_SQL = """
CREATE TABLE data_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT,
    base_url TEXT,
    category TEXT,
    adapter_class TEXT,
    adapter_status TEXT NOT NULL DEFAULT 'planned',
    enabled INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
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
CREATE TABLE ingest_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    reference TEXT,
    reason TEXT,
    meta TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture()
def eng():
    e = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with e.begin() as conn:
        for stmt in SCHEMA_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.execute(text("""
            INSERT INTO data_sources
              (slug, display_name, base_url, category, adapter_class,
               adapter_status, enabled)
            VALUES
              ('isora', 'ISORA', 'https://www.isora.org', 'results',
               'irc_data.scrapers.isora.ISORAScraper', 'unexplored', 1)
        """))
    return e


def _seed_parallel(conn, source, n_days=6, fc_ratio=1.0, p1_match=True):
    for i in range(n_days):
        day = (NOW - timedelta(days=i)).date().isoformat()
        for k in range(10):
            conn.execute(text("""
                INSERT INTO race_results
                  (source, event_name, event_date, source_url, place, transport,
                   raw_data, created_at)
                VALUES (:s, 'Evt', :ed, 'https://x/1', :place, 'legacy', :raw, :ca)
            """), {
                "s": source, "ed": day, "place": (1 if k == 0 else k + 1),
                "raw": json.dumps({"boat_name": f"Winner {i}" if k == 0 else f"B{i}-{k}"}),
                "ca": NOW,
            })
        fc_winner = f"Winner {i}" if p1_match else f"Other {i}"
        n_fc = max(1, int(round(10 * fc_ratio)))
        for k in range(n_fc):
            conn.execute(text("""
                INSERT INTO race_results
                  (source, event_name, event_date, source_url, place, transport,
                   raw_data, created_at)
                VALUES (:s, 'Evt', :ed, 'https://x/1', :place, 'firecrawl', :raw, :ca)
            """), {
                "s": source, "ed": day, "place": (1 if k == 0 else k + 1),
                "raw": json.dumps({"boat_name": fc_winner if k == 0 else f"B{i}-{k}"}),
                "ca": NOW,
            })


# ---------------------------------------------------------------------------

def test_cutover_refused_when_gate_fails(eng):
    with eng.begin() as conn:
        _seed_parallel(conn, "isora", n_days=6, fc_ratio=0.5, p1_match=False)
    result = co.cutover(eng, "isora", days=14)
    assert result["cut_over"] is False
    assert any("REFUSED" in a for a in result["actions"])
    # Adapter must be unchanged.
    state = co.get_cutover_state(eng, "isora")
    assert state.adapter_class == "irc_data.scrapers.isora.ISORAScraper"
    assert state.is_firecrawl is False


def test_cutover_applies_when_gate_passes(eng):
    with eng.begin() as conn:
        _seed_parallel(conn, "isora", n_days=6, fc_ratio=1.0, p1_match=True)
    result = co.cutover(eng, "isora", days=14)
    assert result["gate"]["passed"] is True
    assert result["cut_over"] is True

    state = co.get_cutover_state(eng, "isora")
    assert state.adapter_class == co.FIRECRAWL_ADAPTER
    assert state.adapter_status == "active"
    assert state.is_firecrawl is True
    assert state.legacy_paused is True
    assert "firecrawl" in state.transport_last_14d

    # Audit row written.
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT event_type, status, reason FROM ingest_events "
            "WHERE source='isora' AND event_type='cutover'"
        )).fetchall()
    assert len(rows) == 1
    assert rows[0].event_type == "cutover"
    assert "paused" in rows[0].reason


def test_cutover_is_idempotent(eng):
    with eng.begin() as conn:
        _seed_parallel(conn, "isora", n_days=6, fc_ratio=1.0, p1_match=True)
    first = co.cutover(eng, "isora", days=14)
    assert first["cut_over"] is True
    # Second run reports already-cut-over without another update/audit row.
    second = co.cutover(eng, "isora", days=14)
    assert second["cut_over"] is True
    assert any("already cut over" in a for a in second["actions"])


def test_cutover_force_overrides_failing_gate(eng):
    with eng.begin() as conn:
        _seed_parallel(conn, "isora", n_days=6, fc_ratio=0.5, p1_match=False)
    result = co.cutover(eng, "isora", days=14, force=True)
    assert result["cut_over"] is True
    assert result["forced"] is True
    assert any("WARNING" in a for a in result["actions"])


def test_cutover_dry_run_writes_nothing(eng):
    with eng.begin() as conn:
        _seed_parallel(conn, "isora", n_days=6, fc_ratio=1.0, p1_match=True)
    result = co.cutover(eng, "isora", days=14, dry_run=True)
    assert result["cut_over"] is True
    assert result["dry_run"] is True
    # Adapter unchanged, no audit row.
    state = co.get_cutover_state(eng, "isora")
    assert state.adapter_class == "irc_data.scrapers.isora.ISORAScraper"
    with eng.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM ingest_events")).scalar()
    assert n == 0


def test_cutover_rejects_unknown_source(eng):
    with pytest.raises(Exception):
        co.cutover(eng, "not-a-source", days=14)


def test_cutover_status_reports_transport_split(eng):
    with eng.begin() as conn:
        _seed_parallel(conn, "isora", n_days=3, fc_ratio=1.0)
    state = co.get_cutover_state(eng, "isora")
    assert state.transport_last_14d.get("legacy", 0) > 0
    assert state.transport_last_14d.get("firecrawl", 0) > 0
