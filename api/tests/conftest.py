"""Pytest configuration and fixtures for the news workflow tests.

The integration tests run against a throwaway PostgreSQL database
(``irc_data_news_test``) so the shared dev DB is never mutated. The schema is
applied via Alembic (``upgrade head``) once per session, and each test
truncates the news/mention tables (and its own seeded boats) for isolation.

The LLM extraction step is exercised with a deterministic extractor (a real
function implementing the same ``(title, body_text) -> ExtractionResult``
contract). This is intentional and justified: the LLM call is non-deterministic,
costly, and needs network + an API key; the goal of these tests is the real
parsing, linking, and workflow code paths, which all run against the real DB.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Ensure the worktree src/ is importable (we run with system python3, not the
# main repo's editable venv which points at a different src tree).
API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR / "src"))

TEST_DB_URL = os.environ.get(
    "NEWS_TEST_DB_URL",
    "postgresql+psycopg://irc:irc@localhost:5433/irc_data_news_test",
)


@pytest.fixture(scope="session")
def engine() -> Engine:
    eng = create_engine(TEST_DB_URL, echo=False)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean_news_tables(engine: Engine):
    """Truncate news/mention tables (and any test boats) before each test."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE boat_news_mentions, raw_news RESTART IDENTITY CASCADE"))
        # Remove boats created by tests (id >= a high watermark) but never
        # touch the live boat corpus by name.
        conn.execute(text("DELETE FROM boat_identities WHERE boat_id >= 9000000"))
        conn.execute(text("DELETE FROM boats WHERE id >= 9000000"))
    yield


@pytest.fixture()
def seed_boat(engine: Engine):
    """Return a helper that inserts a test boat + identity with a fixed high id."""
    def _seed(name: str, sail: str, *, boat_id: int) -> int:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO boats (id, boat_name, sail_number) "
                    "VALUES (:id, :name, :sail) ON CONFLICT DO NOTHING"
                ),
                {"id": boat_id, "name": name, "sail": sail},
            )
            conn.execute(
                text(
                    "INSERT INTO boat_identities (boat_id, boat_name, sail_number, source) "
                    "VALUES (:id, :name, :sail, 'test') ON CONFLICT DO NOTHING"
                ),
                {"id": boat_id, "name": name, "sail": sail},
            )
        return boat_id
    return _seed
