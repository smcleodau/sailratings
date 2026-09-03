"""Integration test: the DB-backed what-if bridge (SM-01-05).

Mirrors the style of ``test_regression_class_baseline.py`` — runs against
the live dev database using the known Sunfast 3300 (SUN FISH, id=12330).

Skips automatically when no database is reachable so the unit suite stays
green in CI/offline environments.
"""

from __future__ import annotations

import pytest

from irc_data.analysis.what_if import (
    ESTIMATE_DISCLAIMER,
    get_what_if_model_for_boat,
    recommend_for_boat,
    simulate_what_if,
)


@pytest.fixture(scope="module")
def engine():
    try:
        from irc_data.db.connection import get_engine

        eng = get_engine()
        with eng.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return eng
    except Exception as exc:  # pragma: no cover - offline CI
        pytest.skip(f"database unavailable: {exc}")


def test_what_if_model_for_sunfast_3300(engine):
    model = get_what_if_model_for_boat(engine, 12330)
    assert model is not None
    assert model.design == "Sunfast 3300"
    assert model.model_tier in ("A", "B", "C")
    assert model.current_tcc is not None
    # The Sunfast 3300 class has enough data for real coefficients.
    assert model.coefficients, "expected class regression coefficients"


def test_simulate_what_if_combined_scenario(engine):
    est = simulate_what_if(engine, 12330, {"headsails": -1, "spinnakers": -1})
    assert est is not None
    # Combined scenario: damped, uncertainty present, disclaimer flagged.
    assert est.combination_factor < 1.0
    assert est.uncertainty["low"] < est.delta_tcc < est.uncertainty["high"]
    assert est.disclaimer == ESTIMATE_DISCLAIMER
    assert est.trial_certificate is not None
    # Deltas are clamped to class-legal bounds (headsails ≥ 1, spinnakers ≥ 0).
    for lever in est.levers:
        if lever.field == "headsails":
            assert lever.new_value >= 1.0
        if lever.field == "spinnakers":
            assert lever.new_value >= 0.0


def test_recommend_for_boat_returns_ranked_list(engine):
    recs = recommend_for_boat(engine, 12330, top_n=5)
    assert recs is not None
    assert len(recs) <= 5
    # Ranks are dense and ordered by composite score.
    scores = [r.composite_score for r in recs]
    assert scores == sorted(scores, reverse=True)
    assert [r.rank for r in recs] == list(range(1, len(recs) + 1))
    # Every recommendation reduces TCC and carries the disclaimer.
    for r in recs:
        assert r.delta_tcc < 0
        assert r.disclaimer == ESTIMATE_DISCLAIMER
