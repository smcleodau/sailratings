"""AD-01-15 — nightly ``admin_metrics`` job tests (fixture DB).

Acceptance criteria under test:

* **Nightly job computes the spec metrics** — one pass over ``boats``
  (design, design_canonical, country, year_built, builder, designer, loa,
  lwl, beam_max, displacement_kg % non-null) plus the ``events`` venue-null
  rate and a bounded raw-name sample; each lands in ``admin_metrics`` with
  both the 0029 (``recorded_at``/``value_num``) and AD-01-15
  (``computed_at``/``value``) column conventions populated.
* **Completeness endpoint renders from admin_metrics** — ``get_completeness``
  reflects exactly what the nightly job wrote (and honestly reports
  ``available=False`` before the first run).
* **Buoy threshold** — a meter under 40% non-null is flagged ``buoy=True``,
  at/above it is not.
* **Tables census degrades honestly off PostgreSQL** — the fixture DB has no
  ``pg_stat_user_tables``, so ``get_table_health`` reports itself
  unavailable rather than fabricating counts.  (The pg_stat census itself is
  asserted shape-only: on PG it must include every user table; that check
  lives in the PG-backed variant below, skipped when no PG is reachable.)

Verification style: fixture DB (SQLite in-memory) for the job logic, per the
issue ("Fixture DB test for the nightly job").
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from irc_data.ops import admin_metrics as adm


# ---------------------------------------------------------------------------
# Fixture DB
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    """In-memory SQLite fixture with a small, known boats/events population."""
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE boats (
                    id INTEGER PRIMARY KEY,
                    boat_name TEXT,
                    sail_number TEXT,
                    design TEXT,
                    design_canonical TEXT,
                    country TEXT,
                    year_built INTEGER,
                    builder TEXT,
                    designer TEXT,
                    loa REAL,
                    lwl REAL,
                    beam_max REAL,
                    displacement_kg REAL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    venue TEXT,
                    start_date TEXT
                )
                """
            )
        )
        # 4 boats.  Known-null pattern:
        #   design           4/4   100%
        #   design_canonical 1/4    25%   -> buoy (<40%)
        #   country          2/4    50%
        #   year_built       0/4     0%   -> buoy
        #   builder          1/4    25%   -> buoy
        #   designer         4/4   100%
        #   loa/lwl/beam/displacement: 0/4 0% -> buoy
        boats = [
            ("Alpha", "GBR1", "J/109", "j109", "GBR", None, "J Boats", "Johnstone", None, None, None, None),
            ("Beta",  "GBR2", "J/109", None,   "GBR", None, None, "Johnstone", None, None, None, None),
            ("Gamma", "IRL1", "J/109", None,   None,  None, None, "Johnstone", None, None, None, None),
            ("Delta", "IRL2", "J/109", None,   None,  None, None, "Johnstone", None, None, None, None),
        ]
        conn.execute(
            text(
                """
                INSERT INTO boats
                  (boat_name, sail_number, design, design_canonical, country,
                   year_built, builder, designer, loa, lwl, beam_max, displacement_kg)
                VALUES
                  (:boat_name, :sail_number, :design, :design_canonical, :country,
                   :year_built, :builder, :designer, :loa, :lwl, :beam_max, :displacement_kg)
                """
            ),
            [
                {
                    "boat_name": r[0], "sail_number": r[1], "design": r[2],
                    "design_canonical": r[3], "country": r[4], "year_built": r[5],
                    "builder": r[6], "designer": r[7], "loa": r[8], "lwl": r[9],
                    "beam_max": r[10], "displacement_kg": r[11],
                }
                for r in boats
            ],
        )
        # events: 2 with venue, 2 without -> 50% null rate; names raw.
        evs = [
            ("Cowes Week 2026", "Cowes"),
            ("RORC  Fastnet", None),
            ("round the island race ", "Cowes"),
            ("  SPI OUEST-FRANCE", None),
        ]
        conn.execute(
            text("INSERT INTO events (name, venue) VALUES (:n, :v)"),
            [{"n": n, "v": v} for n, v in evs],
        )
    return eng


def _metric_rows(engine, metric: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT metric, scope, phase, value_num, value, value_text, "
                "recorded_at, computed_at, meta FROM admin_metrics "
                "WHERE metric = :m ORDER BY id"
            ),
            {"m": metric},
        ).fetchall()
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# The nightly job
# ---------------------------------------------------------------------------


class TestNightlyJob:
    def test_writes_all_spec_boats_columns(self, engine):
        summary = adm.compute_nightly_metrics(engine)
        cols = set(summary["completeness"].keys())
        expected = {
            "boats.design", "boats.design_canonical", "boats.country",
            "boats.year_built", "boats.builder", "boats.designer",
            "boats.loa", "boats.lwl", "boats.beam_max",
            "boats.displacement_kg",
        }
        assert expected <= cols

    def test_completeness_values_match_fixture(self, engine):
        summary = adm.compute_nightly_metrics(engine)
        c = summary["completeness"]
        assert c["boats.design"]["pct_non_null"] == 100.0
        assert c["boats.design_canonical"]["pct_non_null"] == 25.0
        assert c["boats.country"]["pct_non_null"] == 50.0
        assert c["boats.year_built"]["pct_non_null"] == 0.0
        assert c["boats.designer"]["pct_non_null"] == 100.0

    def test_both_column_conventions_populated(self, engine):
        """admin_metrics must carry metric/value/computed_at (spec shape) and
        the 0029 recorded_at/value_num evidence shape, in lock-step."""
        adm.compute_nightly_metrics(engine)
        rows = _metric_rows(engine, "data_health.completeness.boats.design")
        assert len(rows) == 1
        r = rows[0]
        assert r["value"] == 100.0
        assert r["value_num"] == 100.0
        assert r["recorded_at"] is not None
        assert r["computed_at"] is not None

    def test_events_venue_null_rate(self, engine):
        summary = adm.compute_nightly_metrics(engine)
        assert summary["events"]["venue_null_rate"] == 50.0
        assert summary["events"]["rows_total"] == 4

    def test_events_raw_name_sample_is_raw(self, engine):
        """The sample carries the names exactly as ingested (whitespace,
        case, accents) — no normalisation on the way in."""
        adm.compute_nightly_metrics(engine)
        rows = _metric_rows(engine, adm.METRIC_EVENTS_RAW_NAME_SAMPLE)
        assert len(rows) == 1
        import json

        meta = json.loads(rows[0]["meta"])
        names = set(meta["names"])
        # Newest-first sample of 25 -> all four fixture names present.
        assert "  SPI OUEST-FRANCE" in names
        assert "RORC  Fastnet" in names

    def test_run_marker_written(self, engine):
        summary = adm.compute_nightly_metrics(engine)
        rows = _metric_rows(engine, adm.METRIC_NIGHTLY_RUN)
        assert len(rows) == 1
        # 10 boats meters + venue rate + name sample = 12, plus the marker
        # itself is counted in rows_written before the marker insert.
        assert summary["rows_written"] == 13
        assert rows[0]["value"] == 12.0

    def test_idempotent_appends(self, engine):
        """Re-running appends fresh evidence; the stream keeps both runs."""
        adm.compute_nightly_metrics(engine)
        adm.compute_nightly_metrics(engine)
        rows = _metric_rows(engine, "data_health.completeness.boats.design")
        assert len(rows) == 2

    def test_missing_tables_skipped_honestly(self):
        """On a DB with no boats/events, the job writes only the run marker
        and reports the skips — it never invents numbers."""
        eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
        summary = adm.compute_nightly_metrics(eng)
        assert summary["completeness"] == {}
        assert summary["events"] == {}
        assert summary["rows_written"] == 1  # just the run marker


# ---------------------------------------------------------------------------
# The completeness read (what the page renders from)
# ---------------------------------------------------------------------------


class TestCompletenessRead:
    def test_unavailable_before_first_run(self, engine):
        out = adm.get_completeness(engine)
        assert out["available"] is False
        assert out["meters"] == []

    def test_meters_rendered_from_admin_metrics(self, engine):
        adm.compute_nightly_metrics(engine)
        out = adm.get_completeness(engine)
        assert out["available"] is True
        by_col = {m["column"]: m for m in out["meters"]}
        assert by_col["design"]["pct_non_null"] == 100.0
        assert by_col["design"]["buoy"] is False
        assert by_col["design_canonical"]["pct_non_null"] == 25.0
        assert by_col["design_canonical"]["buoy"] is True  # under 40%
        assert by_col["year_built"]["buoy"] is True

    def test_buoy_threshold_boundary(self):
        """Exactly at 40% is *not* a buoy; just under is."""
        eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE boats (
                        id INTEGER PRIMARY KEY,
                        boat_name TEXT,
                        country TEXT
                    )
                    """
                )
            )
            # 10 boats, 4 with country => exactly 40%.
            for i in range(10):
                conn.execute(
                    text(
                        "INSERT INTO boats (boat_name, country) "
                        "VALUES (:n, :c)"
                    ),
                    {"n": f"B{i}", "c": "GBR" if i < 4 else None},
                )
        adm.compute_nightly_metrics(eng)
        out = adm.get_completeness(eng)
        by_col = {m["column"]: m for m in out["meters"]}
        assert by_col["country"]["pct_non_null"] == 40.0
        assert by_col["country"]["buoy"] is False

    def test_events_facts_rendered(self, engine):
        adm.compute_nightly_metrics(engine)
        out = adm.get_completeness(engine)
        assert out["events"]["venue_null_rate"] == 50.0
        assert out["events"]["venue_pct_non_null"] == 50.0
        assert len(out["events"]["raw_name_sample"]) == 4

    def test_latest_row_wins(self, engine):
        """Two runs: the endpoint reflects the *latest* evidence point."""
        adm.compute_nightly_metrics(engine)
        with engine.begin() as conn:
            conn.execute(text("UPDATE boats SET design_canonical = 'j109'"))
        adm.compute_nightly_metrics(engine)
        out = adm.get_completeness(engine)
        by_col = {m["column"]: m for m in out["meters"]}
        assert by_col["design_canonical"]["pct_non_null"] == 100.0

    def test_last_run_marker(self, engine):
        adm.compute_nightly_metrics(engine)
        out = adm.get_completeness(engine)
        assert out["last_run"] is not None
        assert out["last_run"]["status"] == "ok"


# ---------------------------------------------------------------------------
# The tables census
# ---------------------------------------------------------------------------


class TestTablesCensus:
    def test_unavailable_off_postgres(self, engine):
        """SQLite fixture: no pg_stat — the census says so, honestly."""
        out = adm.get_table_health(engine)
        assert out["available"] is False
        assert out["tables"] == []
        assert out["empty_tables"] == []


@pytest.mark.skipif(
    not os.environ.get("IRC_DATABASE_URL") and not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL not reachable; pg_stat census test skipped",
)
class TestTablesCensusPG:
    """pg_stat-backed census: every user table present, empties flagged."""

    def test_census_matches_pg_stat(self):
        url = os.environ.get("IRC_DATABASE_URL") or os.environ.get("DATABASE_URL")
        eng = create_engine(url, future=True)
        out = adm.get_table_health(eng)
        assert out["available"] is True
        names = {t["name"] for t in out["tables"]}
        with eng.connect() as conn:
            pg_names = {
                r[0]
                for r in conn.execute(
                    text("SELECT relname FROM pg_stat_user_tables")
                ).fetchall()
            }
        # The census must match pg_stat exactly — neither a subset nor
        # a superset.
        assert names == pg_names
        # Empty tables are exactly those pg_stat estimates at 0 rows.
        with eng.connect() as conn:
            pg_empties = {
                r[0]
                for r in conn.execute(
                    text("SELECT relname FROM pg_stat_user_tables WHERE n_live_tup = 0")
                ).fetchall()
            }
        assert set(out["empty_tables"]) == pg_empties
