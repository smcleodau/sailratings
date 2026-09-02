"""Fixtures for the migration compatibility suite (DP-03-05).

These tests require a reachable PostgreSQL with permission to CREATE/DROP
throwaway databases.  They are skipped (with a clear reason) when no database
is available, so a contributor without Postgres can still run the rest of the
suite.

Connection resolution order:
  1. ``DP03_ADMIN_DATABASE_URL`` (explicit admin/maintenance URL)
  2. ``IRC_DATABASE_URL`` / ``DATABASE_URL`` (we point at the same server's
     ``postgres`` maintenance DB)

Set ``DP03_SKIP_IF_NO_DB=0`` to turn the skip into a hard failure (useful in
CI where the database is expected to exist).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from irc_data.db import migration_verify as mv


def _admin_url() -> str:
    return mv.default_admin_url()


def _db_available(url: str) -> bool:
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except (OperationalError, Exception):  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def admin_url() -> str:
    """Admin URL; skips the whole module if no DB is reachable."""
    url = _admin_url()
    if not _db_available(url):
        if os.environ.get("DP03_SKIP_IF_NO_DB", "1") == "0":
            pytest.fail(f"PostgreSQL not reachable at {url!r} (required for DP-03-05)")
        pytest.skip(f"PostgreSQL not reachable at {url!r}; skipping migration tests")
    return url


@pytest.fixture(scope="session")
def migrated_db(admin_url):
    """A throwaway database migrated to the canonical head.

    Yields (url, evidence-ish dict).  Dropped on teardown.
    """
    url = mv.create_temp_database(admin_url, prefix="dp03_pytest")
    try:
        mv.upgrade(url, mv.CANONICAL_HEAD)
        yield url
    finally:
        mv.drop_temp_database(url)


@pytest.fixture(scope="session")
def upgraded_from_previous_db(admin_url):
    """A throwaway DB seeded at the previous supported schema then upgraded.

    Exercises the acceptance criterion "upgrade from previous supported
    schema" with a production-shaped dataset.  Yields a dict with the url and
    pre/post snapshots.
    """
    url = mv.create_temp_database(admin_url, prefix="dp03_prev")
    data = {}
    try:
        from sqlalchemy import create_engine

        engine = create_engine(url)
        mv.upgrade(url, mv.PREVIOUS_SUPPORTED_REVISION)
        data["seeded"] = mv.seed_synthetic_at_previous_schema(engine)
        data["pre"] = mv.snapshot_counts_hashes(engine)
        engine.dispose()
        mv.upgrade(url, mv.CANONICAL_HEAD)
        engine = create_engine(url)
        data["seeded"] = mv.seed_synthetic_post_head(engine, data["seeded"])
        data["post"] = mv.snapshot_counts_hashes(engine)
        engine.dispose()
        data["url"] = url
        yield data
    finally:
        mv.drop_temp_database(url)
