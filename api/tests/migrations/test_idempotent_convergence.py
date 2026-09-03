"""Idempotency & legacy-convergence tests (DP-03-05).

Databases that were stamped under the old multi-head graph carry several
``alembic_version`` rows and already have some of the DP tables.  These tests
prove that (a) the canonical migrations are idempotent (safe to re-run over
existing tables) and (b) ``scripts/converge_legacy_heads.py`` + ``alembic
upgrade head`` converges such a database to the canonical head.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from irc_data.db import migration_verify as mv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _versions(engine):
    with engine.connect() as conn:
        return sorted(r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version")))


def test_reupgrade_head_is_idempotent(admin_url):
    """Running ``upgrade head`` twice must be a no-op the second time."""
    url = mv.create_temp_database(admin_url, prefix="dp03_idem")
    try:
        mv.upgrade(url, "head")
        mv.upgrade(url, "head")  # second run
        engine = create_engine(url)
        assert _versions(engine) == [mv.CANONICAL_HEAD]
        engine.dispose()
    finally:
        mv.drop_temp_database(url)


def test_converge_from_legacy_multi_head(admin_url):
    """Simulate a legacy DB stamped at ('0024','0025') with the pre-0025
    schema, then converge + upgrade and assert it lands on the canonical head.

    Only the head revision (``0027_payments_auth``) is idempotent; the older
    chain is not re-runnable, so the legacy simulation stops at the schema the
    convergence script expects for rows ('0024','0025') — i.e. migrated to
    canonical ``0025`` (``fact_assertions`` present) but stamped with the
    historical multi-head pair.
    """
    url = mv.create_temp_database(admin_url, prefix="dp03_conv")
    try:
        mv.upgrade(url, "0025")  # canonical pre-0026 state (has fact_assertions)
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM alembic_version"))
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0024')"))
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0025')"))
        engine.dispose()
        assert _versions(create_engine(url)) == ["0024", "0025"]

        # Repair the version table via the convergence script.
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "converge_legacy_heads.py"), url],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(PROJECT_ROOT / "src")},
        )
        assert proc.returncode == 0, proc.stderr

        # Now the canonical upgrade must succeed and reach the single head.
        mv.upgrade(url, "head")
        engine = create_engine(url)
        assert _versions(engine) == [mv.CANONICAL_HEAD]
        # compatibility views present
        with engine.connect() as conn:
            views = {
                r[0]
                for r in conn.execute(
                    text("SELECT viewname FROM pg_views WHERE schemaname='public'")
                )
            }
        assert {"v1_boat_ratings", "v1_race_results", "v1_fact_assertions_current", "v_admin_users"} <= views
        engine.dispose()
    finally:
        mv.drop_temp_database(url)
