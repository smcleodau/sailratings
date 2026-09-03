"""SM-01-08 — golden fixtures: the design reports for Chilli Pepper,
Diablo-J and Kestrel must reproduce every figure within stated tolerance.

Each test seeds a scratch database from the checked-in dataset, rebuilds
the ReportFactsV1 bundle with the current model code, and diffs every
figure against the checked-in golden bundle.

If a test here fails, either:
  * the model/builder code regressed — fix the code; or
  * the change was intentional — re-snapshot with
    ``python api/scripts/sm_01_08_build_golden.py`` and have the diff
    reviewed (that re-snapshot is the "model changes are tested like code"
    conversation).
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.model_regression

from irc_data.analysis.backtest import (
    GOLDEN_BOATS,
    compare_bundles,
    fb_boat_id,
    golden_bundle_path,
)
from irc_data.api.services.report.facts_bundle import (
    DEFAULT_ABS_TOL,
    build_report_facts,
    validate_report_facts_bundle,
)

ALL_SLUGS = [b.slug for b in GOLDEN_BOATS]


@pytest.mark.parametrize(
    "golden_engine", GOLDEN_BOATS, indirect=True, ids=ALL_SLUGS
)
class TestGoldenBundles:
    def test_bundle_is_valid_report_facts_v1(self, golden_engine):
        fixture, engine = golden_engine
        bundle = build_report_facts(engine, fb_boat_id(engine, fixture))
        assert validate_report_facts_bundle(bundle) == []
        assert bundle["boat"]["name"] == fixture.boat_name
        assert bundle["boat"]["design"] == fixture.design

    def test_golden_bundle_reproduces_within_tolerance(self, golden_engine):
        fixture, engine = golden_engine
        golden = json.loads(golden_bundle_path(fixture.slug).read_text())
        golden.pop("fixture", None)  # provenance block, not a figure

        actual = build_report_facts(engine, fb_boat_id(engine, fixture))
        diffs = compare_bundles(golden, actual)
        assert diffs == [], (
            f"{fixture.slug}: {len(diffs)} figures moved vs golden "
            f"(first: {diffs[0].to_dict() if diffs else None})"
        )

    def test_key_figures_have_sane_values(self, golden_engine):
        """Guard against a golden file snapshotting a broken pipeline: the
        headline figures must be non-degenerate."""
        fixture, engine = golden_engine
        bundle = build_report_facts(engine, fb_boat_id(engine, fixture))
        s01 = bundle["sections"]["s01_executive"]
        assert 0.8 < float(s01["tcc_now"]) < 1.3
        assert s01["finishes"] >= 40  # all three fixture boats race a lot

        s06 = bundle["sections"]["s06_performance"]
        assert s06["finishes"] == s01["finishes"]
        assert s06["rai_percentile"] is not None  # RAI must be computable

        engines = bundle["engines"]
        assert engines["rai"]["n_races"] == s06["finishes"] or engines["rai"]["n_races"] > 0
        assert engines["fleet_wide_model"]["n_boats"] >= 100


@pytest.mark.parametrize(
    "golden_engine", GOLDEN_BOATS, indirect=True, ids=ALL_SLUGS
)
class TestDeterminism:
    def test_rebuild_is_byte_identical(self, golden_engine):
        """Building the bundle twice over the same fixture data must give
        the same figures and the same content hash."""
        fixture, engine = golden_engine
        boat_id = fb_boat_id(engine, fixture)
        a = build_report_facts(engine, boat_id)
        b = build_report_facts(engine, boat_id)
        assert a["facts_sha256"] == b["facts_sha256"]
        assert compare_bundles(a, b, abs_tol=0.0, rel_tol=0.0) == []


def test_tolerance_is_stated():
    """The 'stated tolerance' from the acceptance criteria is a real,
    exported constant (5e-3 absolute on TCC-scale figures)."""
    assert DEFAULT_ABS_TOL == pytest.approx(5e-3)


def test_compare_bundles_flags_model_regression():
    """The comparator itself: a moved coefficient must be caught."""
    golden = {"a": {"b": 1.000, "c": "x"}, "facts_sha256": "z"}
    moved = {"a": {"b": 1.000 + 2 * DEFAULT_ABS_TOL, "c": "x"}, "facts_sha256": "y"}
    diffs = compare_bundles(golden, moved)
    assert len(diffs) == 1
    assert diffs[0].path == "a.b"
    assert diffs[0].abs_diff == pytest.approx(2 * DEFAULT_ABS_TOL)

    # within tolerance → clean
    ok = {"a": {"b": 1.000 + DEFAULT_ABS_TOL / 2, "c": "x"}, "facts_sha256": "w"}
    assert compare_bundles(golden, ok) == []

    # missing/extra leaves are caught too
    assert compare_bundles(golden, {"a": {"b": 1.0}}) != []
    assert compare_bundles(golden, {"a": {"b": 1.0, "c": "x", "d": 1}, "facts_sha256": "v"}) != []
