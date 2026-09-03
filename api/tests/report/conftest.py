"""Fixtures for the SM-01-08 golden-fixture / backtesting suite.

Each fixture boat gets a self-contained scratch PostgreSQL database seeded
from ``tests/report/golden/<slug>/dataset.json`` — the tests are therefore
fully deterministic and independent of whatever happens to be in the dev
database.

Skipped (with a reason) when no PostgreSQL is reachable, mirroring the
DP-03-05 migration suite. Set ``SM01_REQUIRE_DB=1`` to turn the skip into
a hard failure (CI should do this).
"""
from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from irc_data.analysis.backtest import (
    GOLDEN_BOATS,
    create_scratch_db,
    drop_scratch_db,
    golden_dataset_path,
    load_fixture_dataset,
)
from irc_data.config import DATABASE_URL


def _admin_url() -> str:
    return os.environ.get("SM01_ADMIN_DATABASE_URL") or DATABASE_URL.rpartition("/")[0] + "/postgres"


def _db_reachable(url: str) -> bool:
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except SQLAlchemyError:
        return False


@pytest.fixture(scope="session")
def sm01_admin_engine():
    url = _admin_url()
    if not _db_reachable(url):
        if os.environ.get("SM01_REQUIRE_DB", "0") == "1":
            pytest.fail(f"PostgreSQL not reachable at {url!r} (required for SM-01-08)")
        pytest.skip(f"PostgreSQL not reachable at {url!r}; skipping SM-01-08 golden tests")
    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def golden_engine(request, sm01_admin_engine):
    """Session-scoped scratch DB, seeded once per fixture boat.

    Indirect parametrised on the FixtureBoat; yields (fixture, engine).
    """
    fixture = request.param
    ds_path = golden_dataset_path(fixture.slug)
    if not ds_path.exists():
        pytest.skip(f"no dataset for {fixture.slug} at {ds_path}")

    db = create_scratch_db(sm01_admin_engine, f"sm0108_test_{fixture.slug}")
    base = DATABASE_URL.rpartition("/")[0]
    eng = create_engine(f"{base}/{db}")
    try:
        load_fixture_dataset(eng, json.loads(ds_path.read_text()))
        yield fixture, eng
    finally:
        eng.dispose()
        drop_scratch_db(sm01_admin_engine, db)


GOLDEN_BOAT_IDS = {b.slug: b for b in GOLDEN_BOATS}
