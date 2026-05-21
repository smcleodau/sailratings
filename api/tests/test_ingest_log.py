"""Tests for ``irc_data.db.ingest_log.log_event``.

These tests use an in-memory SQLite engine with a hand-rolled
``ingest_events`` schema mirror so we don't depend on Postgres or
Alembic state. The point is to verify (a) the INSERT shape is correct
and (b) failures never raise out of the logger.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text

from irc_data.db.ingest_log import log_event


@pytest.fixture()
def sqlite_engine():
    """Fresh SQLite engine with the ingest_events table."""
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE ingest_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reference TEXT,
                    reason TEXT,
                    meta TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    return eng


def test_log_event_inserts_basic_row(sqlite_engine):
    log_event(
        sqlite_engine,
        source="orc",
        event_type="parse",
        status="ok",
        reference="123ABC",
        reason=None,
    )
    with sqlite_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT source, event_type, status, reference, reason, meta "
                "FROM ingest_events"
            )
        ).fetchone()
    assert row.source == "orc"
    assert row.event_type == "parse"
    assert row.status == "ok"
    assert row.reference == "123ABC"
    assert row.reason is None
    # SQLAlchemy's JSON adapter serialises a Python ``None`` as the JSON
    # literal ``null`` (string ``"null"`` on SQLite, JSONB null on
    # Postgres) — both decode back to ``None``.
    if row.meta is not None:
        assert json.loads(row.meta) is None


def test_log_event_serialises_meta(sqlite_engine):
    log_event(
        sqlite_engine,
        source="orc",
        event_type="match",
        status="orphan",
        reference="ABC",
        reason="no boat match",
        meta={"country_id": "AUS", "class_name": "Sunfast 3300"},
    )
    with sqlite_engine.connect() as conn:
        meta = conn.execute(text("SELECT meta FROM ingest_events")).scalar()
    assert json.loads(meta) == {
        "country_id": "AUS",
        "class_name": "Sunfast 3300",
    }


def test_log_event_swallows_db_failures(sqlite_engine, capsys):
    """If the engine errors out, log_event must not raise."""
    # Drop the table so the INSERT fails.
    with sqlite_engine.begin() as conn:
        conn.execute(text("DROP TABLE ingest_events"))
    # Must not raise.
    log_event(
        sqlite_engine,
        source="orc",
        event_type="parse",
        status="error",
        reference="X",
        reason="boom",
    )
    captured = capsys.readouterr()
    assert "ingest_log" in captured.err
