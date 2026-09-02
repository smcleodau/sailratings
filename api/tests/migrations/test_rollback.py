"""Rollback / restore strategy tests (DP-03-05).

The acceptance criterion requires a *tested rollback or restore strategy*.
The canonical capstone revision (``0026``) is deliberately **additive** — it
only creates compatibility views and two bookkeeping tables — so its
downgrade/upgrade pair is a safe, reversible operation.  These tests prove
that:

  * downgrading the capstone drops the views + bookkeeping tables,
  * user data is untouched by the downgrade, and
  * re-upgrading (restore) recreates the views and leaves data identical.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from irc_data.db import migration_verify as mv

CAPSTONE_DOWN_TARGET = "20260526a"  # the revision directly below 0026


def _views(engine) -> set:
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                text("SELECT viewname FROM pg_views WHERE schemaname='public'")
            )
        }


def _table_exists(engine, table: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_tables "
                    "WHERE schemaname='public' AND tablename=:t)"
                ),
                {"t": table},
            ).scalar()
        )


def test_rollback_and_restore_capstone(admin_url):
    url = mv.create_temp_database(admin_url, prefix="dp03_rollback")
    try:
        engine = create_engine(url)
        mv.upgrade(url, mv.PREVIOUS_SUPPORTED_REVISION)
        mv.seed_synthetic_at_previous_schema(engine)
        mv.upgrade(url, mv.CANONICAL_HEAD)
        engine.dispose()

        # baseline hashes at head
        engine = create_engine(url)
        pre = mv.snapshot_counts_hashes(engine)
        assert {"v1_boat_ratings", "v1_race_results", "v1_fact_assertions_current"} <= _views(engine)
        assert _table_exists(engine, "schema_migrations")
        assert _table_exists(engine, "backup_checks")
        engine.dispose()

        # --- rollback the additive capstone ---
        mv.downgrade(url, CAPSTONE_DOWN_TARGET)
        engine = create_engine(url)
        # views + bookkeeping gone
        assert not ({"v1_boat_ratings", "v1_race_results", "v1_fact_assertions_current"} & _views(engine))
        assert not _table_exists(engine, "schema_migrations")
        assert not _table_exists(engine, "backup_checks")
        # user data untouched
        with engine.connect() as conn:
            boats = conn.execute(text("SELECT count(*) FROM boats")).scalar()
        assert boats == pre["boats"]["count"]
        engine.dispose()

        # --- restore (re-upgrade) ---
        mv.upgrade(url, mv.CANONICAL_HEAD)
        engine = create_engine(url)
        post = mv.snapshot_counts_hashes(engine)
        assert {"v1_boat_ratings", "v1_race_results", "v1_fact_assertions_current"} <= _views(engine)
        # data identical after rollback+restore
        for t in mv.PRESERVED_TABLES:
            assert pre[t]["hash"] == post[t]["hash"], f"{t} changed across rollback/restore"
        engine.dispose()
    finally:
        mv.drop_temp_database(url)


def test_migration_and_backup_evidence_tables(admin_url):
    """The migration-evidence and backup-check tables must accept rows."""
    url = mv.create_temp_database(admin_url, prefix="dp03_evid")
    try:
        mv.upgrade(url, mv.CANONICAL_HEAD)
        engine = create_engine(url)
        mv.write_schema_migration_row(engine, mv.CANONICAL_HEAD, 123, notes="pytest")
        mv.record_backup_check(engine, "dp03_evid", status="verified", notes="pytest")
        with engine.connect() as conn:
            n_mig = conn.execute(text("SELECT count(*) FROM schema_migrations")).scalar()
            n_bak = conn.execute(text("SELECT count(*) FROM backup_checks")).scalar()
            bak_status = conn.execute(
                text("SELECT status FROM backup_checks ORDER BY id DESC LIMIT 1")
            ).scalar()
        engine.dispose()
        assert n_mig >= 1
        assert n_bak >= 1
        assert bak_status == "verified"
    finally:
        mv.drop_temp_database(url)
