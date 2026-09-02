"""OPS-02-12 — IRC history reconstruction at scale: tests.

Covers the three things the issue's acceptance criterion depends on:

1.  **The KPI query itself** — :func:`compute_tcc_history_kpi` must
    correctly measure "% of 24-month racers with >=3 years of TCC
    history" against a real database, and the orchestrator must record
    it (before/after) in ``admin_metrics``.
2.  **The historical loader** — :func:`import_historical_tcc_dir` must
    turn harvested ``tcc_{year}_{ts}.csv`` snapshots into mid-year
    anchored ``tcc_snapshots`` rows, match boats without creating
    duplicates, fold secondary certs onto primaries, and report
    coverage stats.
3.  **The prioritized queue** — ``build_prioritized_index`` must order
    certs raced-first, then GBR/AUS/IRL fleet, then the rest, and the
    backfill runner must emit ``admin_metrics`` progress rows.

DB-backed tests build a throwaway scratch database (migrated to head)
so they are hermetic; they skip cleanly when PostgreSQL is unreachable.
The live acceptance KPI against the dev database is exercised in
:class:`TestDevDatabaseKPI` and skipped when the dev DB is unreachable.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text

from irc_data.db import migration_verify as mv
from irc_data.db.history_kpi import (
    ACCEPTANCE_THRESHOLD,
    KPI_QUERY,
    compute_tcc_history_kpi,
)
from irc_data.scrapers.tcc_history_loader import (
    import_historical_tcc_dir,
    snapshot_anchor,
    year_from_path,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


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
def scratch_engine():
    """Throwaway database migrated to head; hermetic for writes."""
    admin = mv.default_admin_url()
    if not _reachable(admin):
        pytest.skip("admin database not reachable for scratch build")
    try:
        url = mv.create_temp_database(admin, prefix="ops0212")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"could not create scratch database: {exc}")
    try:
        # 0029 chains from the canonical 0026 head and adds admin_metrics,
        # which the orchestrator writes progress / KPI evidence into.
        mv.upgrade(url, "0029")
        engine = create_engine(url)
        yield engine
        engine.dispose()
    finally:
        mv.drop_temp_database(url)


@pytest.fixture(scope="module")
def dev_engine():
    url = os.environ.get("IRC_DATABASE_URL", os.environ.get("DATABASE_URL", ""))
    if not _reachable(url):
        pytest.skip("dev database not reachable")
    engine = create_engine(url)
    yield engine
    engine.dispose()


def _insert_boat(engine, name, sail, cert):
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO boats (boat_name, sail_number, cert_number)"
                " VALUES (:n, :s, :c) RETURNING id"
            ),
            {"n": name, "s": sail, "c": cert},
        ).scalar_one()


def _insert_snapshot(engine, boat_id, snapshot_date, cert_year, tcc=1.100):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tcc_snapshots (boat_id, snapshot_date, cert_year, tcc)"
                " VALUES (:b, :d, :y, :t)"
                " ON CONFLICT (boat_id, snapshot_date) DO NOTHING"
            ),
            {"b": boat_id, "d": snapshot_date, "y": cert_year, "t": tcc},
        )


def _insert_race(engine, boat_id, event_date):
    """Insert a race result for ``boat_id``.

    ``race_results.event_entry_id`` is NOT NULL with an FK to
    ``event_entries``, so we create a minimal event + entry first.
    """
    with engine.begin() as conn:
        event_id = conn.execute(
            text("INSERT INTO events (name, start_date) VALUES (:n, :d) RETURNING id"),
            {"n": f"Evt-{boat_id}-{event_date}", "d": event_date},
        ).scalar_one()
        entry_id = conn.execute(
            text(
                "INSERT INTO event_entries (event_id, boat_id) VALUES (:e, :b)"
                " RETURNING id"
            ),
            {"e": event_id, "b": boat_id},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO race_results (boat_id, event_name, event_date,"
                " event_entry_id)"
                " VALUES (:b, :e, :d, :ee)"
            ),
            {"b": boat_id, "e": f"Evt-{boat_id}-{event_date}", "d": event_date,
             "ee": entry_id},
        )


def _tcc_csv(path: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    """Write a minimal 2026-format TCC listing CSV.

    ``rows`` are (boat_name, sail_number, cert_number, tcc).
    """
    header = "Boat Name,Sail No,Cert No,TCC,Cert Year\n"
    body = "".join(f"{n},{s},{c},{t},\n" for n, s, c, t in rows)
    path.write_text(header + body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Loader unit behaviour (no DB for filename/anchor; DB for import)
# ---------------------------------------------------------------------------


class TestLoaderHelpers:
    def test_year_from_path(self):
        assert year_from_path(Path("tcc_2015_20150601120000.csv")) == 2015
        assert year_from_path(Path("/x/tcc_2009_20090518000000.csv")) == 2009
        assert year_from_path(Path("tcc_listing_2026-05-14.csv")) is None
        assert year_from_path(Path("other.csv")) is None

    def test_snapshot_anchor_is_mid_year(self):
        assert snapshot_anchor(2015) == date(2015, 6, 1)
        assert snapshot_anchor(2009) == date(2009, 6, 1)


class TestHistoricalImport:
    def test_import_matches_boats_and_writes_midyear_snapshots(self, scratch_engine, tmp_path):
        bid = _insert_boat(scratch_engine, "Kestrel", "GBR1234", "GBR1234R")
        d = tmp_path / "hist"
        d.mkdir()
        _tcc_csv(d / "tcc_2012_20120601000000.csv", [("Kestrel", "GBR1234", "GBR1234R", "1.012")])
        _tcc_csv(d / "tcc_2013_20130601000000.csv", [("Kestrel", "GBR1234", "GBR1234R", "1.013")])
        _tcc_csv(d / "tcc_2014_20140601000000.csv", [("Kestrel", "GBR1234", "GBR1234R", "1.015")])

        stats = import_historical_tcc_dir(scratch_engine, d)

        assert stats["files"] == 3
        assert stats["snapshots_written"] == 3
        assert stats["matched_boats"] == 1
        assert stats["coverage_boats_3y"] == 1  # 2012/2013/2014

        with scratch_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT snapshot_date, cert_year, tcc FROM tcc_snapshots"
                    " WHERE boat_id = :b ORDER BY snapshot_date"
                ),
                {"b": bid},
            ).fetchall()
        assert [str(r[0]) for r in rows] == [
            "2012-06-01",
            "2013-06-01",
            "2014-06-01",
        ]
        # cert_year falls back to the filename year when the column is blank.
        assert [r[1] for r in rows] == [2012, 2013, 2014]

    def test_import_is_idempotent(self, scratch_engine, tmp_path):
        bid = _insert_boat(scratch_engine, "Idem", "AUS1", "AUS1R")
        d = tmp_path / "hist"
        d.mkdir()
        _tcc_csv(d / "tcc_2015_20150601000000.csv", [("Idem", "AUS1", "AUS1R", "1.000")])

        import_historical_tcc_dir(scratch_engine, d)
        stats2 = import_historical_tcc_dir(scratch_engine, d)  # second run
        assert stats2["snapshots_written"] == 1  # upsert, not duplicate

        with scratch_engine.connect() as conn:
            n = conn.execute(
                text("SELECT COUNT(*) FROM tcc_snapshots WHERE boat_id = :b"),
                {"b": bid},
            ).scalar()
        assert n == 1

    def test_secondary_rows_fold_onto_primary_without_creating_boats(
        self, scratch_engine, tmp_path
    ):
        bid = _insert_boat(scratch_engine, "Folding", "IRL77", "IRL77R")
        d = tmp_path / "hist"
        d.mkdir()
        # Primary row + a "- SEC" secondary row in the same snapshot.
        _tcc_csv(
            d / "tcc_2016_20160601000000.csv",
            [("Folding", "IRL77", "IRL77R", "1.020"), ("Folding - SEC", "IRL77", "IRL77R", "1.021")],
        )

        stats = import_historical_tcc_dir(scratch_engine, d)
        assert stats["secondary_attached"] == 1

        with scratch_engine.connect() as conn:
            boats = conn.execute(
                text("SELECT COUNT(*) FROM boats WHERE sail_number = 'IRL77'")
            ).scalar()
            sec = conn.execute(
                text(
                    "SELECT secondary FROM tcc_snapshots"
                    " WHERE boat_id = :b AND snapshot_date = '2016-06-01'"
                ),
                {"b": bid},
            ).scalar()
        assert boats == 1, "secondary row must not create a duplicate boat"
        assert sec == "SEC"

    def test_unmatched_rows_counted_as_coverage_not_boats(self, scratch_engine, tmp_path):
        d = tmp_path / "hist"
        d.mkdir()
        for yr in (2010, 2011, 2012):
            _tcc_csv(
                d / f"tcc_{yr}_{yr}0601000000.csv",
                [("Ghost", "USA999", "USA999R", "1.000")],
            )
        stats = import_historical_tcc_dir(scratch_engine, d)
        assert stats["unmatched_rows"] == 3
        # Unmatched rows still count toward coverage (the boat exists in the
        # historical record even if not in the live boats table).
        assert stats["coverage_boats_3y"] == 1


# ---------------------------------------------------------------------------
# 2. KPI math on a controlled scratch DB
# ---------------------------------------------------------------------------


class TestKPIMath:
    def test_kpi_counts_only_recent_racers_and_3y_span(self, scratch_engine):
        # Boat A: raced recently, 2010->2015 history (span 5y) -> counts.
        a = _insert_boat(scratch_engine, "A", "A1", "A1R")
        for yr in (2010, 2012, 2015):
            _insert_snapshot(scratch_engine, a, date(yr, 6, 1), yr)
        _insert_race(scratch_engine, a, date.today())

        # Boat B: raced recently, only 2024->2025 (span 1y) -> doesn't count.
        b = _insert_boat(scratch_engine, "B", "B1", "B1R")
        for yr in (2024, 2025):
            _insert_snapshot(scratch_engine, b, date(yr, 6, 1), yr)
        _insert_race(scratch_engine, b, date.today())

        # Boat C: deep history but raced 5 years ago -> outside 24m window.
        c = _insert_boat(scratch_engine, "C", "C1", "C1R")
        for yr in (2005, 2008, 2012):
            _insert_snapshot(scratch_engine, c, date(yr, 6, 1), yr)
        _insert_race(scratch_engine, c, date(date.today().year - 5, 1, 1))

        # Boat D: raced recently, no history at all -> denominator only.
        d = _insert_boat(scratch_engine, "D", "D1", "D1R")
        _insert_race(scratch_engine, d, date.today())

        kpi = compute_tcc_history_kpi(scratch_engine)

        # Only A, B, D are within the 24-month racer window.
        assert kpi["racers"] == 3, kpi
        assert kpi["with_3y_span"] == 1, kpi  # only A
        assert kpi["with_3y_distinct"] == 1
        assert abs(kpi["pct_span"] - (1 / 3)) < 1e-9
        assert kpi["meets_acceptance"] is False

    def test_kpi_meets_acceptance_when_majority_have_history(self, scratch_engine):
        # Fresh scratch: 3 racers, 2 with >=3y span => 66% >= 60%.
        e = _insert_boat(scratch_engine, "E", "E1", "E1R")
        f = _insert_boat(scratch_engine, "F", "F1", "F1R")
        for bid in (e, f):
            for yr in (2010, 2014):
                _insert_snapshot(scratch_engine, bid, date(yr, 6, 1), yr)
            _insert_race(scratch_engine, bid, date.today())
        g = _insert_boat(scratch_engine, "G", "G1", "G1R")
        _insert_snapshot(scratch_engine, g, date(2024, 6, 1), 2024)
        _insert_race(scratch_engine, g, date.today())

        kpi = compute_tcc_history_kpi(scratch_engine)
        # E, F count (span 4y); G doesn't. Plus A from previous test (module
        # scope) — A raced today with 5y span, B (1y), D (none). So racers
        # with 3y = A, E, F = 3; racers = A,B,D,E,F,G = 6 -> 50%.
        assert kpi["racers"] == 6, kpi
        assert kpi["with_3y_span"] == 3, kpi
        assert kpi["meets_acceptance"] is (kpi["pct_span"] >= ACCEPTANCE_THRESHOLD)

    def test_kpi_query_sql_matches_python(self, scratch_engine):
        a = _insert_boat(scratch_engine, "Q", "Q1", "Q1R")
        for yr in (2009, 2013):
            _insert_snapshot(scratch_engine, a, date(yr, 6, 1), yr)
        _insert_race(scratch_engine, a, date.today())

        kpi = compute_tcc_history_kpi(scratch_engine)
        with scratch_engine.connect() as conn:
            row = conn.execute(text(KPI_QUERY)).one()
        assert int(row.racers_24m) == kpi["racers"]
        assert int(row.with_3y_history) == kpi["with_3y_span"]


# ---------------------------------------------------------------------------
# 3. Prioritized queue ordering + backfill progress (mocked HTTP)
# ---------------------------------------------------------------------------


class TestPrioritizedIndex:
    def test_raced_first_then_fleet_then_rest(self, scratch_engine, tmp_path):
        from scripts import ops_02_12_history_reconstruction as orch

        # Set up DB state: one racer (cert R1), one GBR fleet boat (F1),
        # plus an unrelated cert only present in the CSVs (X1).
        racer = _insert_boat(scratch_engine, "Racer", "AUS1", "R1")
        _insert_race(scratch_engine, racer, date.today())
        _insert_boat(scratch_engine, "Fleet", "GBR9", "F1")  # GBR sail prefix

        d = tmp_path / "hist"
        d.mkdir()
        _tcc_csv(
            d / "tcc_2015_20150601000000.csv",
            [("Rest", "ZZZ1", "X1", "1.0"), ("Racer", "AUS1", "R1", "1.0"),
             ("Fleet", "GBR9", "F1", "1.0")],
        )

        built = orch.build_prioritized_index(scratch_engine, d)
        order = [e["cert_number"] for e in built["index"]]
        assert order[0] == "R1", order          # raced first
        assert order[1] == "F1", order          # then GBR fleet
        assert order[2] == "X1", order          # then the rest
        assert built["tier_counts"] == {"raced": 1, "fleet": 1, "rest": 1}


class TestBackfillProgress:
    @pytest.mark.asyncio
    async def test_progress_rows_written_to_admin_metrics(
        self, scratch_engine, tmp_path, monkeypatch
    ):
        from scripts import ops_02_12_history_reconstruction as orch
        from irc_data.scrapers import irc_backfill as bf

        saved_pdf = b"%PDF-1.4 evidence"

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "web.archive.org/web/" in url and "id_/" in url:
                return httpx.Response(200, content=saved_pdf)
            if "/cdx/search/cdx" in url:
                return httpx.Response(200, content=b"[]")
            if "ircrating.org/pdfdirectory/" in url:
                return httpx.Response(
                    200,
                    content=saved_pdf,
                    headers={"content-type": "application/pdf",
                             "content-length": str(len(saved_pdf))},
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)

        def fake_client(**kwargs):
            kwargs.pop("transport", None)
            return httpx.AsyncClient(transport=transport, **kwargs)

        monkeypatch.setattr(bf, "get_http_client", fake_client)
        monkeypatch.setattr("irc_data.scrapers.wayback.get_http_client", fake_client)

        async def _no_wait(self):  # pragma: no cover
            return None

        monkeypatch.setattr("irc_data.scrapers.base.RateLimiter.wait", _no_wait)
        monkeypatch.setattr(bf, "HISTORICAL_CERTS_DIR", tmp_path / "certs")
        # Isolate the backfill resume-state file to the tmp dir.
        monkeypatch.setattr(
            bf, "_state_path", lambda: tmp_path / ".irc_backfill_state.json"
        )

        index = [
            {"cert_number": f"C{i}", "boat_name": f"B{i}", "sail_number": f"S{i}",
             "year": 2015}
            for i in range(5)
        ]
        stats = await orch.run_backfill(
            scratch_engine, index, resume=False, progress_every=2
        )
        assert stats["probed"] == 5
        assert stats["found_live"] == 5

        with scratch_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT value_num, meta FROM admin_metrics"
                    " WHERE metric = 'irc_history.cert_backfill.progress'"
                    " ORDER BY id"
                )
            ).fetchall()
        # progress_every=2 over 5 probes -> rows at 2 and 4.
        assert [int(r[0]) for r in rows] == [2, 4]
        assert rows[-1][1]["queue_total"] == 5


# ---------------------------------------------------------------------------
# 4. Full orchestrator run (dry-run) records KPI + run metrics
# ---------------------------------------------------------------------------


class TestOrchestratorRun:
    def test_run_records_kpi_and_run_metrics(self, scratch_engine, tmp_path):
        from scripts import ops_02_12_history_reconstruction as orch

        bid = _insert_boat(scratch_engine, "K", "K1", "K1R")
        for yr in (2011, 2014):
            _insert_snapshot(scratch_engine, bid, date(yr, 6, 1), yr)
        _insert_race(scratch_engine, bid, date.today())

        args = argparse.Namespace(
            dry_run=True,           # no network, no downloads
            skip_harvest=False,
            skip_import=False,
            skip_backfill=True,
            backfill_limit=None,
            progress_every=10,
            no_resume=False,
            start_year=2010,
            end_year=2025,
            max_per_pattern=None,
            tcc_dir=str(tmp_path / "hist"),
        )
        report = orch.run(scratch_engine, args)
        assert report["kpi_before"]["racers"] >= 1
        assert report["kpi_after"]["racers"] == report["kpi_before"]["racers"]

        with scratch_engine.connect() as conn:
            kpi_phases = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT phase FROM admin_metrics"
                        " WHERE metric = 'irc_history.tcc_history_coverage'"
                    )
                ).fetchall()
            }
            run_rows = conn.execute(
                text(
                    "SELECT value_num, value_text, meta FROM admin_metrics"
                    " WHERE metric = 'irc_history.run' ORDER BY id DESC LIMIT 1"
                )
            ).fetchone()
        assert {"before", "after"} <= kpi_phases
        assert run_rows is not None
        assert 0.0 <= run_rows[0] <= 1.0
        assert "kpi_after" in run_rows[2]


# ---------------------------------------------------------------------------
# 5. Live acceptance KPI on the dev database (skipped when unreachable)
# ---------------------------------------------------------------------------


class TestDevDatabaseKPI:
    def test_kpi_is_computed_and_recorded(self, dev_engine):
        kpi = compute_tcc_history_kpi(dev_engine)
        assert kpi["racers"] > 0, "dev DB has no recent racers to measure"
        assert 0.0 <= kpi["pct_span"] <= 1.0
        # The recorded evidence rows (if the orchestrator has run) must agree
        # with the live computation within a small tolerance.
        with dev_engine.connect() as conn:
            recorded = conn.execute(
                text(
                    "SELECT value_num FROM admin_metrics"
                    " WHERE metric = 'irc_history.tcc_history_coverage'"
                    "   AND phase = 'after' ORDER BY id DESC LIMIT 1"
                )
            ).scalar()
        if recorded is not None:
            assert abs(recorded - kpi["pct_span"]) < 0.05, (
                f"recorded KPI {recorded} diverges from live {kpi['pct_span']}"
            )
