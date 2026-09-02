"""Upgrade-from-previous-schema compatibility tests (DP-03-05).

The acceptance criterion: *"CI exercises upgrade from previous supported
schema and validates counts, hashes and queries."*

These tests build a throwaway database at the previous supported schema,
seed a production-shaped synthetic dataset, upgrade to the canonical head,
and assert that user data is preserved (counts + content hashes), that the
risky 3NF backfill links every race_result, and that the compatibility views
answer representative consumer queries.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, text

from irc_data.db import migration_verify as mv


@pytest.mark.usefixtures("upgraded_from_previous_db")
class TestUpgradeFromPrevious:
    def test_counts_preserved(self, upgraded_from_previous_db):
        data = upgraded_from_previous_db
        for t in mv.PRESERVED_TABLES:
            assert data["pre"][t]["count"] == data["post"][t]["count"], (
                f"{t} count changed: {data['pre'][t]['count']} -> {data['post'][t]['count']}"
            )

    def test_hashes_preserved(self, upgraded_from_previous_db):
        data = upgraded_from_previous_db
        for t in mv.PRESERVED_TABLES:
            assert data["pre"][t]["hash"] == data["post"][t]["hash"], (
                f"{t} content hash changed across migration"
            )

    def test_no_unlinked_race_results(self, upgraded_from_previous_db):
        """The 0022 3NF backfill must link every race_result to an entry."""
        engine = create_engine(upgraded_from_previous_db["url"])
        with engine.connect() as conn:
            unlinked = conn.execute(
                text("SELECT count(*) FROM race_results WHERE event_entry_id IS NULL")
            ).scalar()
        engine.dispose()
        assert unlinked == 0, f"{unlinked} race_results left unlinked"

    def test_derived_tables_populated(self, upgraded_from_previous_db):
        data = upgraded_from_previous_db
        assert data["seeded"]["event_entries"] > 0
        assert data["seeded"]["events"] > 0
        assert data["seeded"]["data_sources"] > 0

    def test_consumer_queries_run(self, upgraded_from_previous_db):
        engine = create_engine(upgraded_from_previous_db["url"])
        results = mv.run_consumer_queries(engine)
        engine.dispose()
        assert results["v1_boat_ratings"] > 0
        assert results["v1_race_results"] > 0
        assert results["v1_fact_assertions_current"] > 0
        assert results["avg_tcc_by_country"] > 0


def test_scratch_build_reaches_head(admin_url):
    """A from-scratch ``upgrade head`` must succeed and land on 0026."""
    url = mv.create_temp_database(admin_url, prefix="dp03_scratch")
    try:
        mv.upgrade(url, "head")
        engine = create_engine(url)
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            views = {
                r[0]
                for r in conn.execute(
                    text("SELECT viewname FROM pg_views WHERE schemaname='public'")
                )
            }
        engine.dispose()
        assert rev == "0026"
        assert {"v1_boat_ratings", "v1_race_results", "v1_fact_assertions_current"} <= views
    finally:
        mv.drop_temp_database(url)


def test_migration_within_budget(admin_url):
    """The previous-schema -> head migration must complete within budget on a
    production-sized synthetic dataset."""
    url = mv.create_temp_database(admin_url, prefix="dp03_budget")
    try:
        engine = create_engine(url)
        mv.upgrade(url, mv.PREVIOUS_SUPPORTED_REVISION)
        mv.seed_synthetic_at_previous_schema(engine)
        engine.dispose()
        t0 = time.monotonic()
        mv.upgrade(url, mv.CANONICAL_HEAD)
        elapsed = time.monotonic() - t0
        assert elapsed <= mv.DEFAULT_BUDGET_SECONDS, (
            f"migration took {elapsed:.1f}s, over budget {mv.DEFAULT_BUDGET_SECONDS:.0f}s"
        )
    finally:
        mv.drop_temp_database(url)
