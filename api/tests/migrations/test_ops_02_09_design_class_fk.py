"""OPS-02-09 — design_classes sweep + boats.design_canonical FK tests.

Acceptance criteria under test:

* **FK exists and validates** — revision ``0029`` ensures
  ``fk_boats_design_canonical`` (boats.design_canonical ->
  design_classes.name_canonical) exists and is ``VALID`` whenever no orphan
  rows remain.  Verified twice: against the dev database (live state) and on
  a throwaway scratch database migrated ``0001 -> 0029`` (schema proof).
* **design_canonical NULL rate recorded before/after in admin_metrics** —
  the sweep runner (``scripts.ops_02_09_design_class_sweep``) writes
  ``boats.design_canonical.null_rate`` rows with phase=before/after (plus
  orphan counts and the FK validation outcome) into ``admin_metrics``.

Verification style: FK validation + count assertions, per the issue.

DB-backed tests skip cleanly when PostgreSQL is unreachable (set
``OPS02_REQUIRE_DB=0``... rather ``DP03_SKIP_IF_NO_DB=0`` semantics are
honoured through the same admin-URL resolution as the DP-03-05 suite).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from irc_data.db import migration_verify as mv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FK_NAME = "fk_boats_design_canonical"

_ORPHAN_SQL = """
SELECT COUNT(*)
FROM boats b
WHERE b.design_canonical IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM design_classes dc
      WHERE dc.name_canonical = b.design_canonical
  )
"""


def _dev_url() -> str:
    return os.environ.get(
        "IRC_DATABASE_URL",
        os.environ.get("DATABASE_URL", ""),
    )


def _reachable(url: str) -> bool:
    if not url:
        return False
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def dev_engine():
    """The dev database (already migrated to 0029 + swept by this issue)."""
    url = _dev_url()
    if not _reachable(url):
        pytest.skip("dev database not reachable")
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def scratch_db():
    """Throwaway database migrated 0001 -> 0029 (proves schema from scratch)."""
    admin = mv.default_admin_url()
    if not _reachable(admin):
        pytest.skip("admin database not reachable for scratch build")
    try:
        url = mv.create_temp_database(admin, prefix="ops0209")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"could not create scratch database: {exc}")
    try:
        mv.upgrade(url, "0029")
        yield url
    finally:
        mv.drop_temp_database(url)


def _fk_row(conn):
    return conn.execute(
        text(
            """
            SELECT convalidated, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = :name AND conrelid = 'boats'::regclass
            """
        ),
        {"name": FK_NAME},
    ).fetchone()


# ---------------------------------------------------------------------------
# 1. Migration-graph invariants (no DB required)
# ---------------------------------------------------------------------------


def test_revision_0029_declared():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0029")
    assert rev is not None, "revision 0029 missing"
    assert rev.down_revision == "0026", (
        f"0029 must extend the canonical chain from 0026; got {rev.down_revision}"
    )


def test_migration_0029_is_idempotent_sql():
    """Every mutating statement in 0029 must be guarded so a re-run converges."""
    from pathlib import Path

    text_of = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0029_admin_metrics_and_boats_design_fk.py"
    ).read_text()
    assert "CREATE TABLE IF NOT EXISTS admin_metrics" in text_of
    assert "CREATE INDEX IF NOT EXISTS" in text_of
    # FK re-creation is guarded by a pg_constraint existence check …
    assert "IF NOT EXISTS (" in text_of and "pg_constraint" in text_of
    # … and validation only happens when no orphans would block it.
    assert "VALIDATE CONSTRAINT" in text_of
    assert "n_orphans" in text_of


# ---------------------------------------------------------------------------
# 2. Scratch build 0001 -> 0029 (schema proof)
# ---------------------------------------------------------------------------


class TestScratchBuildTo0029:
    def test_admin_metrics_table_created(self, scratch_db):
        engine = create_engine(scratch_db)
        with engine.connect() as conn:
            cols = {
                r[0]
                for r in conn.execute(
                    text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name = 'admin_metrics'
                        """
                    )
                )
            }
        engine.dispose()
        assert {
            "id",
            "recorded_at",
            "metric",
            "scope",
            "phase",
            "value_num",
            "value_text",
            "meta",
        } <= cols, f"admin_metrics missing columns: {cols}"

    def test_fk_created_and_validated_on_scratch(self, scratch_db):
        """On an empty (0-orphan) build the FK must be present AND valid."""
        engine = create_engine(scratch_db)
        with engine.connect() as conn:
            row = _fk_row(conn)
            orphans = conn.execute(text(_ORPHAN_SQL)).scalar()
        engine.dispose()
        assert row is not None, "fk_boats_design_canonical missing after 0029"
        assert row[0] is True, "FK not validated on a clean scratch build"
        assert "REFERENCES design_classes(name_canonical)" in row[1]
        assert orphans == 0

    def test_upgrade_is_reentrant(self, scratch_db):
        """Running 0029's upgrade SQL twice must not error (idempotency)."""
        engine = create_engine(scratch_db)
        with engine.begin() as conn:
            # second application of the key statements
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS admin_metrics (
                        id          BIGSERIAL PRIMARY KEY,
                        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        metric      TEXT NOT NULL,
                        scope       TEXT NOT NULL DEFAULT '',
                        phase       TEXT NOT NULL DEFAULT '',
                        value_num   DOUBLE PRECISION,
                        value_text  TEXT,
                        meta        JSONB
                    )
                    """
                )
            )
        engine.dispose()


# ---------------------------------------------------------------------------
# 3. Dev-database acceptance state (live evidence)
# ---------------------------------------------------------------------------


class TestDevDatabaseAcceptance:
    def test_fk_exists_and_validates(self, dev_engine):
        with dev_engine.connect() as conn:
            row = _fk_row(conn)
        assert row is not None, "fk_boats_design_canonical does not exist"
        assert row[0] is True, (
            "fk_boats_design_canonical exists but is NOT VALID — "
            "the orphan sweep must finish, then VALIDATE CONSTRAINT"
        )
        assert "ON UPDATE CASCADE" in row[1]
        assert "ON DELETE SET NULL" in row[1]

    def test_fk_actually_enforced(self, dev_engine):
        """INSERT with a bogus design_canonical must be rejected."""
        with dev_engine.begin() as conn:
            with pytest.raises(Exception) as excinfo:
                conn.execute(
                    text(
                        """
                        INSERT INTO boats (boat_name, sail_number, design_canonical)
                        VALUES ('__ops0209_probe__', '__ops0209_probe__',
                                '__no_such_design_class__')
                        """
                    )
                )
            assert FK_NAME in str(excinfo.value)

    def test_orphan_count_zero(self, dev_engine):
        with dev_engine.connect() as conn:
            orphans = conn.execute(text(_ORPHAN_SQL)).scalar()
        assert orphans == 0, f"{orphans} orphan boats.design_canonical rows remain"

    def test_count_assertions(self, dev_engine):
        """The fleet must still be intact after the sweep."""
        with dev_engine.connect() as conn:
            boats = conn.execute(text("SELECT COUNT(*) FROM boats")).scalar()
            designs = conn.execute(text("SELECT COUNT(*) FROM design_classes")).scalar()
            linked = conn.execute(
                text("SELECT COUNT(design_canonical) FROM boats")
            ).scalar()
            merges = conn.execute(text("SELECT COUNT(*) FROM design_class_merges")).scalar()
        assert boats > 9000, f"boats count collapsed: {boats}"
        assert designs > 4000, f"design_classes count collapsed: {designs}"
        assert 0 < linked <= boats
        assert merges >= 150, "merge audit trail missing"

    def test_null_rate_recorded_before_after(self, dev_engine):
        with dev_engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT phase, value_num, meta
                    FROM admin_metrics
                    WHERE metric = 'boats.design_canonical.null_rate'
                      AND phase IN ('before', 'after')
                    ORDER BY id
                    """
                )
            ).fetchall()
            fk_rows = conn.execute(
                text(
                    """
                    SELECT value_text, value_num FROM admin_metrics
                    WHERE metric = 'boats.design_canonical.fk_validation'
                    ORDER BY id DESC LIMIT 1
                    """
                )
            ).fetchone()
        phases = {r[0] for r in rows}
        assert "before" in phases and "after" in phases, (
            f"admin_metrics lacks before/after null-rate rows: {phases}"
        )
        for _phase, value, meta in rows:
            assert value is not None and 0.0 <= value <= 1.0
            assert meta["total_boats"] > 0
            assert meta["null_boats"] >= 0
        assert fk_rows is not None, "no fk_validation metric recorded"
        assert fk_rows[0] in ("validated", "already_valid"), fk_rows
        assert fk_rows[1] == 1.0

    def test_latest_recorded_null_rate_matches_live(self, dev_engine):
        """The recorded 'after' rate must equal the live computed rate."""
        with dev_engine.connect() as conn:
            live_total, live_nulls = conn.execute(
                text(
                    "SELECT COUNT(*), COUNT(*) - COUNT(design_canonical) FROM boats"
                )
            ).one()
            recorded = conn.execute(
                text(
                    """
                    SELECT value_num FROM admin_metrics
                    WHERE metric = 'boats.design_canonical.null_rate'
                      AND phase = 'after'
                    ORDER BY id DESC LIMIT 1
                    """
                )
            ).scalar()
        live_rate = live_nulls / live_total
        assert abs(recorded - live_rate) < 1e-9, (
            f"recorded null rate {recorded} != live {live_rate}"
        )


# ---------------------------------------------------------------------------
# 4. Sweep runner behaviour on the dev DB (idempotent re-run)
# ---------------------------------------------------------------------------


class TestSweepRunner:
    def test_rerun_is_converged_and_records_metrics(self, dev_engine):
        """A second full run must apply zero changes but still record evidence."""
        from scripts import ops_02_09_design_class_sweep as sweep

        report = sweep.run(dev_engine, dry_run=False)

        assert report["merge"]["clusters_merged"] == 0, report["merge"]
        assert report["merge"]["rows_deleted"] == 0
        assert report["boat_sweep"]["updates_applied"] == 0
        assert report["after"]["orphan_boats"] == 0
        assert report["fk"]["validated"] is True

        with dev_engine.connect() as conn:
            latest = conn.execute(
                text(
                    """
                    SELECT value_text FROM admin_metrics
                    WHERE metric = 'design_classes.sweep' AND scope = 'merge'
                    ORDER BY id DESC LIMIT 1
                    """
                )
            ).scalar()
        assert latest == "applied"
