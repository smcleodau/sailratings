"""AD-01-15 — migration ``0033`` verification (PostgreSQL, scratch schema).

Acceptance criteria under test (the DB-backed half of the issue; the fixture
DB job test lives in ``tests/test_admin_metrics.py``):

* ``0033`` adds the spec-named ``computed_at`` / ``value`` columns to
  ``admin_metrics`` and keeps them in lock-step with the 0029
  ``recorded_at`` / ``value_num`` columns (both write directions verified
  through the BEFORE INSERT trigger, plus the backfill of a pre-existing
  0029-shape evidence row).
* ``health_metric_latest`` projects the newest row per (metric, scope,
  phase) — the read the page's meters come from.
* ``health_tables_built_never_written`` lists a table that was built with a
  real data column and never written, and excludes tables that hold rows or
  are empty-but-written.

These tests build a *scratch schema* on the dev PostgreSQL (no base-table
replay of the whole migration chain, which has pre-existing inter-revision
dependencies) and skip cleanly when PostgreSQL is unreachable.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from irc_data.ops import admin_metrics as adm


def _pg_url() -> str | None:
    url = os.environ.get("IRC_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        return None
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _reachable(url: str) -> bool:
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


PG_URL = _pg_url()
PG_AVAILABLE = bool(PG_URL) and _reachable(PG_URL)

pytestmark = pytest.mark.skipif(
    not PG_AVAILABLE, reason="PostgreSQL not reachable"
)

# The migration's SQL, loaded from the revision file so the test exercises
# exactly what deploys (no copy-paste).
import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0033_nightly_admin_metrics.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("m0033", _MIGRATION_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def scratch():
    """A throwaway schema with the 0033 substrate (boats, events,
    admin_metrics in its 0029 shape, one written + one never-written demo
    table) and the migration applied on top."""
    if not PG_AVAILABLE:
        pytest.skip("PostgreSQL not reachable")
    schema = f"ad0115_{uuid.uuid4().hex[:10]}"
    eng = create_engine(PG_URL)
    m = _load_migration()
    with eng.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(text(f'SET search_path TO "{schema}", public'))
        conn.execute(
            text(
                """
                CREATE TABLE boats (
                    id SERIAL PRIMARY KEY, boat_name TEXT, design TEXT,
                    design_canonical TEXT, country TEXT, year_built INT,
                    builder TEXT, designer TEXT,
                    loa NUMERIC(6,2), lwl NUMERIC(6,2),
                    beam_max NUMERIC(6,2), displacement_kg NUMERIC(10,1)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE TABLE events (id SERIAL PRIMARY KEY, name TEXT, "
                "venue TEXT, start_date DATE)"
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE admin_metrics (
                    id BIGSERIAL PRIMARY KEY,
                    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    metric TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT '',
                    phase TEXT NOT NULL DEFAULT '',
                    value_num DOUBLE PRECISION,
                    value_text TEXT,
                    meta JSONB
                )
                """
            )
        )
        # a pre-existing 0029-shape evidence row to prove the backfill
        conn.execute(
            text(
                "INSERT INTO admin_metrics (metric, scope, phase, value_num) "
                "VALUES ('boats.design_canonical.null_rate','boats','before',47.4)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE zz_never_written (id UUID PRIMARY KEY DEFAULT "
                "gen_random_uuid(), payload JSONB)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE zz_written (id UUID PRIMARY KEY DEFAULT "
                "gen_random_uuid(), payload JSONB)"
            )
        )
        conn.execute(text("INSERT INTO zz_written (payload) VALUES ('{}')"))
        # seed boats/events so the nightly job has real numbers
        conn.execute(
            text(
                "INSERT INTO boats (boat_name, design, country) VALUES "
                "('Alpha','J/109','GBR'),('Beta','J/109',NULL),"
                "('Gamma',NULL,NULL),('Delta','J/109','GBR')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO events (name, venue) VALUES "
                "('Cowes Week 2026','Cowes'),('RORC  Fastnet',NULL),"
                "('  spi ouest ',NULL)"
            )
        )
        # Apply the migration's SQL in order (all statements are static).
        for stmt in (
            m._ADD_ALIAS_COLUMNS,
            m._SYNC_FUNCTION,
            m._TRIGGER,
            m._BACKFILL,
            m._ALIAS_INDEX,
            m._LATEST_VIEW,
            m._BUILT_NEVER_WRITTEN_VIEW,
            m._ANALYZE,
        ):
            conn.execute(text(stmt))
    try:
        yield eng, schema
    finally:
        with eng.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        eng.dispose()


class TestAliasColumns:
    def test_spec_columns_present(self, scratch):
        eng, schema = scratch
        with eng.connect() as conn:
            cols = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = 'admin_metrics'"
                    ),
                    {"s": schema},
                )
            }
        assert {"metric", "value", "computed_at"} <= cols

    def test_backfill_of_preexisting_row(self, scratch):
        eng, schema = scratch
        with eng.connect() as conn:
            r = conn.execute(
                text(
                    f'SELECT value, value_num, computed_at, recorded_at '
                    f'FROM "{schema}".admin_metrics '
                    f"WHERE metric = 'boats.design_canonical.null_rate'"
                )
            ).fetchone()
        assert float(r[0]) == 47.4 and float(r[1]) == 47.4
        assert r[2] is not None and r[3] is not None

    def test_trigger_syncs_both_write_directions(self, scratch):
        eng, schema = scratch
        with eng.begin() as conn:
            conn.execute(
                text(
                    f'INSERT INTO "{schema}".admin_metrics (metric, value_num) '
                    "VALUES ('via_value_num', 1.5)"
                )
            )
            conn.execute(
                text(
                    f'INSERT INTO "{schema}".admin_metrics (metric, value) '
                    "VALUES ('via_value', 2.5)"
                )
            )
            r1 = conn.execute(
                text(
                    f'SELECT value, value_num FROM "{schema}".admin_metrics '
                    "WHERE metric = 'via_value_num'"
                )
            ).fetchone()
            r2 = conn.execute(
                text(
                    f'SELECT value, value_num FROM "{schema}".admin_metrics '
                    "WHERE metric = 'via_value'"
                )
            ).fetchone()
        assert float(r1[0]) == 1.5 and float(r1[1]) == 1.5
        assert float(r2[0]) == 2.5 and float(r2[1]) == 2.5


class TestViews:
    def test_latest_view_projects_newest(self, scratch):
        eng, schema = scratch
        with eng.begin() as conn:
            # two evidence points for one metric; the view must show the newer
            conn.execute(
                text(
                    f'INSERT INTO "{schema}".admin_metrics '
                    "(metric, scope, phase, value_num, recorded_at, computed_at) "
                    "VALUES ('m','s','p', 1.0, now() - interval '1 day', "
                    "now() - interval '1 day')"
                )
            )
            conn.execute(
                text(
                    f'INSERT INTO "{schema}".admin_metrics '
                    "(metric, scope, phase, value_num, recorded_at, computed_at) "
                    "VALUES ('m','s','p', 9.0, now(), now())"
                )
            )
            r = conn.execute(
                text(
                    f'SELECT value FROM "{schema}".health_metric_latest '
                    "WHERE metric = 'm'"
                )
            ).fetchone()
        assert float(r[0]) == 9.0

    def test_built_never_written(self, scratch):
        eng, schema = scratch
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    f'SELECT table_name, data_cols FROM '
                    f'"{schema}".health_tables_built_never_written'
                )
            ).fetchall()
        names = {r[0] for r in rows}
        # the never-written demo table is listed …
        assert "zz_never_written" in names
        # … the written one is not …
        assert "zz_written" not in names
        # … and tables with rows are never listed.
        assert "boats" not in names
        assert "events" not in names


class TestEndToEndOnPG:
    """The whole pipeline on the scratch schema: nightly job → reads."""

    def test_nightly_then_reads(self, scratch):
        import time

        eng, schema = scratch
        with eng.begin() as conn:
            conn.execute(text(f'SET search_path TO "{schema}", public'))
        # The module uses plain (unqualified) names, so run with the search
        # path set via a dedicated connection options on a new engine.
        eng2 = create_engine(
            PG_URL,
            connect_args={"options": f"-csearch_path={schema},public"},
        )
        try:
            summary = adm.compute_nightly_metrics(eng2)
            assert summary["rows_written"] == 13
            t0 = time.perf_counter()
            tables = adm.get_table_health(eng2)
            t1 = time.perf_counter()
            comp = adm.get_completeness(eng2)
            t2 = time.perf_counter()
        finally:
            eng2.dispose()
        # 200 ms budget per page query.
        assert (t1 - t0) * 1000 < 200
        assert (t2 - t1) * 1000 < 200

        assert tables["available"] is True
        names = {t["name"] for t in tables["tables"]}
        assert {"boats", "events", "zz_never_written", "zz_written"} <= names
        assert "zz_never_written" in tables["empty_tables"]

        meters = {m["column"]: m for m in comp["meters"]}
        assert meters["design"]["pct_non_null"] == 75.0
        assert meters["design"]["buoy"] is False
        assert meters["design_canonical"]["pct_non_null"] == 0.0
        assert meters["design_canonical"]["buoy"] is True
        assert abs(comp["events"]["venue_null_rate"] - 66.667) < 0.01
        # raw names exactly as ingested
        assert "  spi ouest " in comp["events"]["raw_name_sample"]
