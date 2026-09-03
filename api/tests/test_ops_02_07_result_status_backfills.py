"""OPS-02-07 — count assertions for the result-status backfills.

The issue's verification step is "count assertions in the PR". This module
encodes the acceptance criteria as pytest assertions against the dev
database (the same pattern as ``TestDevDatabaseKPI`` in
``test_ops_02_12_history_reconstruction.py`` — skipped cleanly when the dev
DB is unreachable, so the suite stays hermetic in CI).

Acceptance criteria covered
---------------------------
1. Counts were recorded — the ops script writes before/after snapshots into
   ``admin_metrics`` under ``ops_02_07.*`` (and prints them to stdout for
   the Notion evidence block).
2. ``race_results`` has no empty-name rows — literal empty-string
   ``boat_name`` values are gone, and the broader "no boat identity of any
   kind" garbage population (163-row cluster called out in the issue, plus
   the same failure mode in rorc/isora/cowesweek/rhkyc/sydneyhobart) is
   swept to zero.
3. DNF statuses are present for the affected events — topyacht, sailracehq
   and sailsys all carry non-trivial DNF populations after the run, and no
   unprocessed candidates remain for any of the four rules (idempotence).
4. The SailSys ~21k finished-no-place decision (flag as DNF, reversible via
   the ``raw_data.ops_02_07_prev_status`` marker) is fully applied.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") and not os.environ.get("IRC_DATABASE_URL"),
    reason="dev database URL not configured",
)

NO_IDENTITY_WHERE = """
    NOT (
        coalesce(btrim(raw_data->>'boat_name'), '') <> ''
        OR coalesce(btrim(raw_data->>'name'), '') <> ''
        OR coalesce(btrim(raw_data->>'boat'), '') <> ''
        OR coalesce(btrim(raw_data->>'sail_number'), '') <> ''
        OR coalesce(btrim(raw_data->>'sail_no'), '') <> ''
        OR coalesce(btrim(raw_data->>'sailno'), '') <> ''
        OR coalesce(btrim(raw_data->>'sail no'), '') <> ''
    )
"""


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
    url = os.environ.get("IRC_DATABASE_URL", os.environ.get("DATABASE_URL", ""))
    if not _reachable(url):
        pytest.skip("dev database not reachable")
    engine = create_engine(url)
    yield engine
    engine.dispose()


def _scalar(conn, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar() or 0)


class TestNoEmptyNameRows:
    """AC: race_results has no empty-name rows."""

    def test_no_empty_string_boat_name(self, dev_engine):
        with dev_engine.connect() as conn:
            n = _scalar(
                conn,
                "SELECT COUNT(*) FROM race_results WHERE raw_data->>'boat_name' = ''",
            )
        assert n == 0, f"{n} race_results rows still carry boat_name = ''"

    def test_no_rows_without_any_boat_identity(self, dev_engine):
        """The garbage sweep: rows with no name AND no sail identity of any
        kind can never be matched or trusted — none may remain."""
        with dev_engine.connect() as conn:
            n = _scalar(
                conn, f"SELECT COUNT(*) FROM race_results WHERE {NO_IDENTITY_WHERE}"
            )
        assert n == 0, f"{n} no-identity garbage rows remain in race_results"


class TestDnfStatusesPresent:
    """AC: DNF statuses present for affected events (topyacht / sailracehq /
    sailsys) and zero unprocessed candidates remain for each rule."""

    def test_topyacht_dnf_present(self, dev_engine):
        with dev_engine.connect() as conn:
            n = _scalar(
                conn,
                "SELECT COUNT(*) FROM race_results "
                "WHERE source = 'topyacht' AND status = 'DNF'",
            )
        # The backfill labelled the ~680-row DNF population (662 on dev).
        assert n >= 600, f"topyacht DNF population unexpectedly small: {n}"

    def test_topyacht_no_remaining_candidates(self, dev_engine):
        with dev_engine.connect() as conn:
            n = _scalar(
                conn,
                "SELECT COUNT(*) FROM race_results "
                "WHERE source = 'topyacht' AND transport = 'legacy' "
                "  AND status = 'finished' AND place IS NULL "
                "  AND coalesce(raw_data->>'finish_time', '') = ''",
            )
        assert n == 0, f"{n} topyacht DNF candidates left unprocessed"

    def test_sailracehq_dnf_present(self, dev_engine):
        with dev_engine.connect() as conn:
            n = _scalar(
                conn,
                "SELECT COUNT(*) FROM race_results "
                "WHERE source = 'sailracehq' AND status = 'DNF'",
            )
        assert n >= 400, f"sailracehq DNF population unexpectedly small: {n}"

    def test_sailracehq_no_remaining_candidates(self, dev_engine):
        with dev_engine.connect() as conn:
            n = _scalar(
                conn,
                "SELECT COUNT(*) FROM race_results "
                "WHERE source = 'sailracehq' AND transport = 'legacy' "
                "  AND status = 'finished' AND place IS NULL "
                "  AND coalesce(raw_data->>'finish_time', '') = '' "
                "  AND coalesce(raw_data->>'boat_name', '') <> ''",
            )
        assert n == 0, f"{n} sailracehq DNF candidates left unprocessed"


class TestSailSysHollowRows:
    """AC: the ~21k SailSys finished-no-place rows are decided (flagged DNF)
    and the flag is reversible via the ops_02_07_prev_status marker."""

    def test_no_hollow_finished_rows_remain(self, dev_engine):
        with dev_engine.connect() as conn:
            n = _scalar(
                conn,
                "SELECT COUNT(*) FROM race_results "
                "WHERE source = 'sailsys' AND status = 'finished' AND place IS NULL",
            )
        assert n == 0, f"{n} sailsys finished-no-place rows remain unflagged"

    def test_sailsys_dnf_population_includes_hollow_rows(self, dev_engine):
        with dev_engine.connect() as conn:
            n = _scalar(
                conn,
                "SELECT COUNT(*) FROM race_results "
                "WHERE source = 'sailsys' AND status = 'DNF'",
            )
        # 10,944 pre-existing DNF + 21,054 flagged hollow rows.
        assert n >= 30_000, f"sailsys DNF population unexpectedly small: {n}"

    def test_flag_marker_is_present_and_reversible(self, dev_engine):
        with dev_engine.connect() as conn:
            n = _scalar(
                conn,
                "SELECT COUNT(*) FROM race_results "
                "WHERE source = 'sailsys' AND status = 'DNF' AND place IS NULL "
                "  AND raw_data->>'ops_02_07_prev_status' = 'finished'",
            )
        assert n >= 20_000, (
            f"only {n} flagged rows carry the ops_02_07_prev_status marker"
        )


class TestCountsRecorded:
    """AC: counts recorded — the apply run must have written before/after
    evidence rows into admin_metrics."""

    def test_admin_metrics_evidence_present(self, dev_engine):
        with dev_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT metric, phase, value_num FROM admin_metrics "
                    "WHERE metric LIKE 'ops_02_07.%' ORDER BY id"
                )
            ).fetchall()
        metrics = {(r[0], r[1]) for r in rows}
        assert (f"ops_02_07.counts", "before") in metrics, (
            "ops script never recorded its BEFORE counts"
        )
        assert (f"ops_02_07.counts", "after") in metrics, (
            "ops script never recorded its AFTER counts"
        )
        assert any(m == "ops_02_07.garbage_sweep" for m, _ in metrics), (
            "garbage sweep step recorded no evidence"
        )
        assert any(m == "ops_02_07.sailsys_hollow_flag" for m, _ in metrics), (
            "sailsys hollow-flag step recorded no evidence"
        )
