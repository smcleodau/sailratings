from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

from irc_data.db.connection import get_engine
from irc_data.temporal.legacy_adapters import _last_successful_run_since

SLUG = "pytest_since_probe"


@pytest.fixture()
def engine():
    e = get_engine()
    yield e
    with e.begin() as conn:
        conn.execute(text("DELETE FROM ingestion_log WHERE source = :s"), {"s": SLUG})
        conn.execute(text("DELETE FROM source_runs WHERE source_slug = :s"), {"s": SLUG})


def _log(engine, completed_at, status="completed", trigger="mirror-close"):
    meta = '{"trigger": "%s", "run_key": "scheduled"}' % trigger if trigger else '{"club": "X"}'
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO ingestion_log (source, started_at, completed_at, status, metadata) "
                 "VALUES (:s, :t, :t, :st, CAST(:m AS json))"),
            {"s": SLUG, "t": completed_at, "st": status, "m": meta},
        )


def test_no_history_means_full_scrape(engine):
    assert _last_successful_run_since(SLUG) is None


def test_uses_latest_completed_run_close_minus_one_day(engine):
    _log(engine, datetime(2026, 10, 1, 12, tzinfo=timezone.utc))
    _log(engine, datetime(2026, 10, 11, 9, tzinfo=timezone.utc))
    assert _last_successful_run_since(SLUG) == date(2026, 10, 10)


def test_ignores_failed_runs_and_per_club_import_rows(engine):
    _log(engine, datetime(2026, 10, 5, tzinfo=timezone.utc))
    _log(engine, datetime(2026, 10, 9, tzinfo=timezone.utc), status="failed")
    _log(engine, datetime(2026, 10, 10, tzinfo=timezone.utc), trigger=None)
    assert _last_successful_run_since(SLUG) == date(2026, 10, 4)
