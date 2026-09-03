"""SM-01-04 golden fixtures: rule / formula drift analysis (RuleDriftV1).

Golden fixtures pin:
- SF3300 stable-certificate cohort mean TCC drift = -0.005 since 2022.
- Fleet-wide stable-cohort mean TCC drift = -0.0021 for 2026 vs 2025.

The fixture is synthetic (SQLite, hermetic) so the numbers are pinned exactly.
A live-DB smoke test runs only when Postgres is reachable.

Contract pinned here: RuleDriftV1 (irc_data.analysis.rule_drift).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from irc_data.analysis.rule_drift import (
    RULE_DRIFT_VERSION,
    analyze_rule_drift,
    attribute_levers,
    get_boat_rule_drift,
    get_class_rule_drift,
)

# ---------------------------------------------------------------------------
# Golden fixture
# ---------------------------------------------------------------------------
#
# Boats 1-5  : SF3300, stable, snapshots at 2022/2025/2026.
#   - 2022->2025 deltas: -0.005 each           (class mean -0.005)
#   - 2025->2026 deltas: -0.004 -0.005 -0.005 -0.006 -0.005 (mean -0.005)
#   - class mean across both cycles            = -0.005  (the golden "since 2022")
#
# Boats 6-10 : J/109, stable, snapshots at 2025/2026.
#   - 2025->2026 deltas: +0.001 +0.001 +0.001 +0.001 0.000 (mean +0.0008)
#
# Fleet 2026 stable cohort = 5 SF3300 + 5 J/109 = 10 boats.
#   sum = 5*(-0.005) + 5*(+0.0008) = -0.025 + 0.004 = -0.021
#   mean = -0.0021  (the golden "2026 vs 2025")
#
# Boat 11   : TWEAKER (SF3300), 2025->2026, headsails 3->2 (unstable).
#   total_change = -0.005; rule_movement = fleet stable mean = -0.0021;
#   boat_movement = -0.005 - (-0.0021) = -0.0029.

SF3300_DELTAS_2025_2026 = [-0.004, -0.005, -0.005, -0.006, -0.005]
J109_DELTAS_2025_2026 = [+0.001, +0.001, +0.001, +0.001, 0.0]
SF3300_DELTA_2022_2025 = -0.005

SF3300_BASE_TCC = 1.025
J109_BASE_TCC = 1.000


def _seed_boat(conn, boat_id, name, sail, design, country):
    conn.execute(
        text(
            "INSERT INTO boats (id, boat_name, sail_number, design,"
            " design_canonical, country)"
            " VALUES (:id, :name, :sail, :design, :design, :country)"
        ),
        {"id": boat_id, "name": name, "sail": sail, "design": design,
         "country": country},
    )


def _seed_snapshot(conn, boat_id, date, tcc, lh=10.0, beam=3.5, draft=2.0,
                   headsails=3, spinnakers=2, crew=8, dlr=150):
    conn.execute(
        text(
            "INSERT INTO tcc_snapshots (boat_id, snapshot_date, tcc, lh, beam,"
            " draft, headsails, spinnakers, crew, dlr)"
            " VALUES (:id, :date, :tcc, :lh, :beam, :draft, :hs, :ss, :crew, :dlr)"
        ),
        {"id": boat_id, "date": date, "tcc": tcc, "lh": lh, "beam": beam,
         "draft": draft, "hs": headsails, "ss": spinnakers, "crew": crew,
         "dlr": dlr},
    )


@pytest.fixture()
def golden_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE boats (id INTEGER PRIMARY KEY, boat_name TEXT,"
            " sail_number TEXT, design TEXT, design_canonical TEXT,"
            " country TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE tcc_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " boat_id INTEGER, snapshot_date DATE, tcc NUMERIC(6,4),"
            " lh NUMERIC(6,2), beam NUMERIC(6,2), draft NUMERIC(6,2),"
            " headsails INTEGER, spinnakers INTEGER, crew INTEGER, dlr INTEGER)"
        ))

        # SF3300 stable cohort — three cycles (2022, 2025, 2026).
        for i, d26 in enumerate(SF3300_DELTAS_2025_2026):
            boat_id = i + 1
            _seed_boat(conn, boat_id, f"SF3300-{i}", f"SF{i:04d}",
                       "Sunfast 3300", "AUS")
            tcc_2022 = SF3300_BASE_TCC
            tcc_2025 = tcc_2022 + SF3300_DELTA_2022_2025
            tcc_2026 = tcc_2025 + d26
            _seed_snapshot(conn, boat_id, "2022-06-01", tcc_2022)
            _seed_snapshot(conn, boat_id, "2025-06-01", tcc_2025)
            _seed_snapshot(conn, boat_id, "2026-06-01", tcc_2026)

        # J/109 stable cohort — one cycle (2025 -> 2026).
        for i, d26 in enumerate(J109_DELTAS_2025_2026):
            boat_id = 6 + i
            _seed_boat(conn, boat_id, f"J109-{i}", f"J{i:04d}", "J/109", "GBR")
            _seed_snapshot(conn, boat_id, "2025-06-01", J109_BASE_TCC)
            _seed_snapshot(conn, boat_id, "2026-06-01", J109_BASE_TCC + d26)

        # TWEAKER — SF3300 whose headsail count changed 2025->2026.
        _seed_boat(conn, 11, "TWEAKER", "SF9999", "Sunfast 3300", "AUS")
        _seed_snapshot(conn, 11, "2025-06-01", SF3300_BASE_TCC, headsails=3)
        _seed_snapshot(conn, 11, "2026-06-01", SF3300_BASE_TCC - 0.005,
                       headsails=2)

    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Golden fixtures — fleet-wide and per-class drift
# ---------------------------------------------------------------------------


def test_rule_drift_version_and_scope(golden_engine):
    result = analyze_rule_drift(golden_engine)
    assert result is not None
    out = result.to_dict()
    assert out["version"] == RULE_DRIFT_VERSION
    assert out["scope"] == "fleet"
    assert "disclaimer" in out


def test_golden_fleet_2026_vs_2025_mean_minus_0_0021(golden_engine):
    """Golden fixture: fleet stable-cohort mean drift 2026 vs 2025 = -0.0021."""
    result = analyze_rule_drift(golden_engine)
    assert result is not None

    cycle_2026 = next(c for c in result.cycles if c.year_to == "2026-06-01")
    assert cycle_2026.year_from == "2025-06-01"
    assert cycle_2026.stats.n_stable == 10
    assert cycle_2026.stats.mean_drift == pytest.approx(-0.0021, abs=1e-9)

    out = cycle_2026.to_dict()
    assert out["mean_drift"] == pytest.approx(-0.0021, abs=1e-6)
    # p-values present for a cohort of 10.
    assert out["p_value_ttest"] is not None
    assert out["p_value_wilcoxon"] is not None


def test_golden_sf3300_mean_minus_0_005_since_2022(golden_engine):
    """Golden fixture: SF3300 stable-cohort mean drift since 2022 = -0.005."""
    result = analyze_rule_drift(golden_engine, design="Sunfast 3300")
    assert result is not None

    # Class rollup across both cycles (2022->2025 and 2025->2026).
    by_class = result.by_class["Sunfast 3300"]
    # 5 boats x 2 cycles = 10 stable certificate-pairs.
    assert by_class.n_stable == 10
    assert by_class.mean_drift == pytest.approx(-0.005, abs=1e-9)

    # Windowed query: since 2022 captures the whole history.
    windowed = analyze_rule_drift(
        golden_engine, design="Sunfast 3300", year_from="2022", year_to="2026"
    )
    assert windowed is not None
    assert windowed.by_class["Sunfast 3300"].mean_drift == pytest.approx(
        -0.005, abs=1e-9
    )


def test_by_class_rollup_excludes_small_cohorts(golden_engine):
    """J/109 has 5 stable boats in one cycle -> present; threshold honoured."""
    result = analyze_rule_drift(golden_engine)
    assert "Sunfast 3300" in result.by_class
    assert "J/109" in result.by_class
    assert result.by_class["J/109"].mean_drift == pytest.approx(0.0008, abs=1e-9)


def test_per_cycle_stats_have_pvalues(golden_engine):
    result = analyze_rule_drift(golden_engine)
    for cycle in result.cycles:
        s = cycle.stats
        # t-test p present whenever the cohort has non-zero variance; a
        # zero-variance cohort (all deltas identical) can't be t-tested.
        if s.std_drift > 0:
            assert s.p_value_ttest is not None
            assert 0.0 <= s.p_value_ttest <= 1.0


def test_zero_variance_cohort_pvalues_none(golden_engine):
    """The 2022->2025 SF3300 deltas are all identical (-0.005): p must be None
    (no t-test on a zero-variance cohort) rather than NaN."""
    result = analyze_rule_drift(golden_engine, design="Sunfast 3300")
    cycle_2025 = next(c for c in result.cycles if c.year_to == "2025-06-01")
    assert cycle_2025.stats.std_drift == pytest.approx(0.0, abs=1e-12)
    assert cycle_2025.stats.p_value_ttest is None
    assert cycle_2025.stats.p_value_wilcoxon is None
    # Mean drift still reported.
    assert cycle_2025.stats.mean_drift == pytest.approx(-0.005, abs=1e-9)


def test_measurements_unstable_boat_excluded_from_stable_cohort(golden_engine):
    """TWEAKER (headsails 3->2) must not enter the stable cohort."""
    result = analyze_rule_drift(golden_engine)
    cycle_2026 = next(c for c in result.cycles if c.year_to == "2026-06-01")
    # 11 boats in the cycle, 10 stable (TWEAKER excluded).
    assert cycle_2026.stats.n_total == 11
    assert cycle_2026.stats.n_stable == 10


# ---------------------------------------------------------------------------
# Per-boat 'rule movement vs boat movement' decomposition
# ---------------------------------------------------------------------------


def test_boat_decomposition_rule_vs_boat_movement(golden_engine):
    """TWEAKER: rule movement = cohort mean; boat movement = remainder."""
    result = analyze_rule_drift(golden_engine)
    tweaker = next(
        b for b in result.boat_decompositions if b.boat_name == "TWEAKER"
    )
    assert tweaker.measurements_stable is False
    assert tweaker.total_change == pytest.approx(-0.005, abs=1e-9)
    assert tweaker.rule_movement == pytest.approx(-0.0021, abs=1e-9)
    assert tweaker.boat_movement == pytest.approx(-0.005 - (-0.0021), abs=1e-9)
    assert tweaker.measurement_deltas == {"headsails": -1.0}
    # total = rule + boat
    assert tweaker.total_change == pytest.approx(
        tweaker.rule_movement + tweaker.boat_movement, abs=1e-9
    )


def test_stable_boat_decomposition_all_rule_movement(golden_engine):
    """A stable boat's whole TCC change is attributed to rule movement."""
    result = analyze_rule_drift(golden_engine)
    sf0 = next(
        b for b in result.boat_decompositions
        if b.boat_name == "SF3300-0" and b.year_to == "2026-06-01"
    )
    assert sf0.measurements_stable is True
    assert sf0.total_change == pytest.approx(-0.004, abs=1e-9)
    assert sf0.rule_movement == pytest.approx(-0.0021, abs=1e-9)
    assert sf0.boat_movement == pytest.approx(-0.004 - (-0.0021), abs=1e-9)
    assert sf0.measurement_deltas == {}


def test_get_boat_rule_drift_history(golden_engine):
    out = get_boat_rule_drift(golden_engine, 11)
    assert out is not None
    assert out["version"] == RULE_DRIFT_VERSION
    assert out["boat_id"] == 11
    assert len(out["decompositions"]) == 1
    assert out["decompositions"][0]["rule_movement"] == pytest.approx(
        -0.0021, abs=1e-6
    )

    # Unknown boat -> None
    assert get_boat_rule_drift(golden_engine, 9999) is None


def test_get_class_rule_drift(golden_engine):
    out = get_class_rule_drift(golden_engine, "Sunfast 3300")
    assert out is not None
    assert out["scope"] == "Sunfast 3300"
    assert out["by_class"]["Sunfast 3300"]["mean_drift"] == pytest.approx(
        -0.005, abs=1e-6
    )

    # Unknown design -> None
    assert get_class_rule_drift(golden_engine, "Not A Design") is None


# ---------------------------------------------------------------------------
# Lever attribution ('taxed more' / 'eased' / 'stable')
# ---------------------------------------------------------------------------


def test_attribute_levers_labels():
    coef_from = {"lh": 0.10, "headsails": 0.05, "draft": -0.02}
    coef_to = {"lh": 0.14, "headsails": 0.02, "draft": -0.0205}
    levers = attribute_levers(coef_from, coef_to)
    by_lever = {la.lever: la for la in levers}

    assert by_lever["lh"].attribution == "taxed more"
    assert by_lever["headsails"].attribution == "eased"
    assert by_lever["draft"].attribution == "stable"  # |change| < epsilon

    # Sorted by |change| descending.
    assert levers[0].lever in {"lh", "headsails"}
    assert abs(levers[0].coefficient_change) >= abs(levers[-1].coefficient_change)


def test_attribute_levers_disjoint_keys_ignored():
    levers = attribute_levers({"lh": 0.1}, {"lh": 0.1, "crew": 0.3})
    assert [la.lever for la in levers] == ["lh"]


# ---------------------------------------------------------------------------
# Window filtering
# ---------------------------------------------------------------------------


def test_year_window_restricts_cycles(golden_engine):
    result = analyze_rule_drift(golden_engine, year_from="2026", year_to="2026")
    assert result is not None
    assert len(result.cycles) == 1
    assert result.cycles[0].year_to == "2026-06-01"
    assert result.cycles[0].stats.mean_drift == pytest.approx(-0.0021, abs=1e-9)


def test_no_pairs_returns_none(golden_engine):
    # A design with no snapshot pairs at all.
    assert analyze_rule_drift(golden_engine, design="Nonexistent") is None


# ---------------------------------------------------------------------------
# Live-DB smoke test (skipped without Postgres)
# ---------------------------------------------------------------------------


def _live_engine() -> Engine | None:
    try:
        from irc_data.db.connection import get_engine

        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:
        return None


@pytest.mark.skipif(_live_engine() is None, reason="live Postgres unavailable")
def test_live_fleet_rule_drift_smoke():
    """Live fleet RuleDriftV1 returns the contract with at least one cycle."""
    engine = _live_engine()
    result = analyze_rule_drift(engine)
    assert result is not None
    out = result.to_dict()
    assert out["version"] == RULE_DRIFT_VERSION
    assert out["scope"] == "fleet"
    assert len(out["cycles"]) >= 1
    first = out["cycles"][0]
    assert "mean_drift" in first
    assert "p_value_ttest" in first
    assert "lever_attribution" in first
