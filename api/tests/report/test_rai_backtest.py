"""SM-01-08 — backtest RAI predictive value on held-out seasons.

For each fixture boat the RAI engine is re-run with one season hidden at a
time. The full-history RAI must stay within ``RAI_STABILITY_TOL`` of every
hold-one-season-out value — a metric that collapses when a season is
removed has no predictive value and the model change that caused the
collapse must not ship.
"""
from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.model_regression

from irc_data.analysis.backtest import (
    GOLDEN_BOATS,
    RAI_MIN_RACES_AFTER_HOLDOUT,
    RAI_STABILITY_TOL,
    backtest_rai_held_out_seasons,
    backtest_rating_model_holdout,
    fb_boat_id,
    RATING_MODEL_HOLDOUT_MAE_MAX,
    RATING_MODEL_HOLDOUT_R2_MIN,
)

ALL_SLUGS = [b.slug for b in GOLDEN_BOATS]


@pytest.mark.parametrize(
    "golden_engine", GOLDEN_BOATS, indirect=True, ids=ALL_SLUGS
)
class TestRAIHeldOutSeasons:
    def test_rai_stable_when_season_held_out(self, golden_engine):
        fixture, engine = golden_engine
        report = backtest_rai_held_out_seasons(engine, fb_boat_id(engine, fixture))

        assert "error" not in report
        # Every fixture boat has at least 3 seasons with enough racing to test.
        assert report["n_seasons_tested"] >= 3
        assert report["rai_full_history"] is not None

        # The headline acceptance number: stability across held-out seasons.
        assert report["max_stability_gap"] is not None
        assert report["max_stability_gap"] <= RAI_STABILITY_TOL, (
            f"{fixture.slug}: RAI moves {report['max_stability_gap']} points "
            f"when a season is held out (tolerance {RAI_STABILITY_TOL}) — "
            f"the metric is too fragile to cite in reports"
        )

        # Every tested season left enough racing behind to be meaningful.
        for season in report["seasons"]:
            assert season["n_races"] >= 3
            assert report["n_races"] - season["n_races"] >= RAI_MIN_RACES_AFTER_HOLDOUT

    def test_rai_backtest_is_deterministic(self, golden_engine):
        fixture, engine = golden_engine
        boat_id = fb_boat_id(engine, fixture)
        a = backtest_rai_held_out_seasons(engine, boat_id)
        b = backtest_rai_held_out_seasons(engine, boat_id)
        assert a == b

    def test_predictive_signal_reported(self, golden_engine):
        """The eval report must surface the prior-season → next-season
        predictive correlation whenever enough seasons exist. We don't gate
        on its sign (one boat can be genuinely streaky) but the number must
        be present and finite for multi-season histories."""
        fixture, engine = golden_engine
        report = backtest_rai_held_out_seasons(engine, fb_boat_id(engine, fixture))
        rho = report["predictive_spearman"]
        if report["n_seasons_tested"] >= 4:
            assert rho is not None and np.isfinite(rho)
            assert -1.0 <= rho <= 1.0


@pytest.mark.parametrize(
    "golden_engine", [GOLDEN_BOATS[0]], indirect=True, ids=["chilli_pepper"]
)
class TestRatingModelHoldout:
    def test_holdout_metrics_within_thresholds(self, golden_engine):
        """The fleet-wide rating model, refit on a deterministic 80/20
        split of the fixture universe, must retain predictive skill."""
        _fixture, engine = golden_engine
        result = backtest_rating_model_holdout(engine)

        assert result["n_holdout"] > 0
        assert result["holdout_mae"] <= RATING_MODEL_HOLDOUT_MAE_MAX, (
            f"held-out MAE {result['holdout_mae']} exceeds "
            f"{RATING_MODEL_HOLDOUT_MAE_MAX} — the rating model got worse"
        )
        assert result["holdout_r2"] >= RATING_MODEL_HOLDOUT_R2_MIN

    def test_holdout_split_is_deterministic(self, golden_engine):
        _fixture, engine = golden_engine
        a = backtest_rating_model_holdout(engine)
        b = backtest_rating_model_holdout(engine)
        assert a == b
