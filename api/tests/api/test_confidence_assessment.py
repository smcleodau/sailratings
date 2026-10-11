"""IN-01-03 - ConfidenceAssessmentV1 (SPEC-25 §3)."""
from __future__ import annotations

import pytest

from irc_data.api.schemas.intelligence import ConfidenceAssessmentV1, IntelligenceAnswerV1
from irc_data.api.services.intelligence.confidence import (
    WEIGHTS,
    ConfidenceInputs,
    assess,
    band_for,
)

# Fixtures (and the registry URL table) are shared with the IN-01-02 test module.
from tests.api.test_intelligence_answers import CASES, client, engine  # noqa: F401

FULL = dict(
    seasons_with_results=3,
    tcc_readings=(1.020, 1.0205, 1.020),
    pending_dupe_cluster=False,
    days_since_latest_fact=0,
    model_r_squared=0.9,
    model_n=20,
)
EMPTY = dict(
    seasons_with_results=0,
    tcc_readings=(),
    pending_dupe_cluster=True,
    days_since_latest_fact=365,
    model_r_squared=0.0,
    model_n=0,
)
FACTOR_KEYS = {
    "coverage": ("seasons_with_results",),
    "source_agreement": ("tcc_readings",),
    "identity_certainty": ("pending_dupe_cluster",),
    "recency": ("days_since_latest_fact",),
    "model_evidence": ("model_r_squared", "model_n"),
}


def _factor(a: ConfidenceAssessmentV1, name: str):
    return next(f for f in a.factors if f.name == name)


def test_each_factor_at_zero_and_one():
    for name, keys in FACTOR_KEYS.items():
        one = assess(ConfidenceInputs(**{**EMPTY, **{k: FULL[k] for k in keys}}))
        zero = assess(ConfidenceInputs(**{**FULL, **{k: EMPTY[k] for k in keys}}))
        assert _factor(one, name).value == 1.0, name
        assert _factor(zero, name).value == 0.0, name
        # only the factor under test moves the score, by exactly its weight
        assert one.score == pytest.approx(WEIGHTS[name]), name
        assert zero.score == pytest.approx(1.0 - WEIGHTS[name]), name


def test_weights_are_fixed_and_sum_to_one():
    assert WEIGHTS == {
        "coverage": 0.25,
        "source_agreement": 0.20,
        "identity_certainty": 0.20,
        "recency": 0.20,
        "model_evidence": 0.15,
    }
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)
    a = assess(ConfidenceInputs(**FULL))
    assert [f.name for f in a.factors] == list(WEIGHTS)
    assert all(f.weight == WEIGHTS[f.name] and f.note for f in a.factors)


def test_all_factors_one_is_high_and_all_zero_is_low():
    top, bottom = assess(ConfidenceInputs(**FULL)), assess(ConfidenceInputs(**EMPTY))
    assert (top.score, top.band, top.limitations) == (1.0, "high", [])
    assert (bottom.score, bottom.band) == (0.0, "low")
    assert len(bottom.limitations) == 5


def test_band_boundaries():
    assert band_for(0.75) == "high"
    assert band_for(0.7499) == "medium"
    assert band_for(0.45) == "medium"
    assert band_for(0.4499) == "low"
    assert band_for(0.0) == "low" and band_for(1.0) == "high"


def test_limitation_listed_for_every_factor_below_half():
    for name, keys in FACTOR_KEYS.items():
        a = assess(ConfidenceInputs(**{**FULL, **{k: EMPTY[k] for k in keys}}))
        assert len(a.limitations) == 1, name
        assert name in a.limitations[0]


def test_no_limitation_at_or_above_half():
    # one TCC reading is exactly 0.5 (uncorroborated, not "bad"): no limitation
    a = assess(ConfidenceInputs(**{**FULL, "tcc_readings": (1.02,)}))
    assert _factor(a, "source_agreement").value == 0.5
    assert a.limitations == []
    # model: N>=15 but R² under the floor -> 0.5, no limitation
    b = assess(ConfidenceInputs(**{**FULL, "model_r_squared": 0.1}))
    assert _factor(b, "model_evidence").value == 0.5 and b.limitations == []


def test_source_agreement_uses_0_001_tolerance():
    a = assess(ConfidenceInputs(**{**FULL, "tcc_readings": (1.000, 1.010, 1.0195, 1.020)}))
    # latest 1.020: 1.0195 and 1.020 agree (<= 0.001), 1.000 and 1.010 do not
    assert _factor(a, "source_agreement").value == 0.5


def test_deterministic_for_same_inputs():
    inputs = ConfidenceInputs(**{**FULL, "seasons_with_results": 2, "days_since_latest_fact": 100})
    runs = [assess(inputs).model_dump() for _ in range(5)]
    assert all(r == runs[0] for r in runs)


def test_every_intelligence_query_carries_confidence(client):
    for key, (ok_url, bad_url, _code, extra) in CASES.items():
        urls = [ok_url, bad_url] + [u for u, _c in extra]
        for url in urls:
            resp = client.get(url)
            assert resp.status_code == 200, (key, url)
            answer = IntelligenceAnswerV1.model_validate(resp.json())
            conf = ConfidenceAssessmentV1.model_validate(answer.confidence)
            assert 0.0 <= conf.score <= 1.0
            assert conf.band == band_for(conf.score)
            assert [f.name for f in conf.factors] == list(WEIGHTS)


# --- holdout: three golden boats, bands stated in SPEC-25 §3.4 ---------------

def test_golden_boat_a_well_documented_j109_is_high():
    a = assess(ConfidenceInputs(
        seasons_with_results=4, tcc_readings=(1.020, 1.020, 1.0205),
        pending_dupe_cluster=False, days_since_latest_fact=30,
        model_r_squared=0.72, model_n=40,
    ))
    assert (a.score, a.band) == (pytest.approx(0.9836, abs=1e-4), "high")
    assert a.limitations == []


def test_golden_boat_b_thin_one_off_is_medium():
    a = assess(ConfidenceInputs(
        seasons_with_results=2, tcc_readings=(0.985, 0.985),
        pending_dupe_cluster=False, days_since_latest_fact=400,
        model_r_squared=None, model_n=None,
    ))
    assert (a.score, a.band) == (pytest.approx(0.5667, abs=1e-4), "medium")
    assert len(a.limitations) == 2  # recency, model_evidence


def test_golden_boat_c_stale_pending_duplicate_is_low():
    a = assess(ConfidenceInputs(
        seasons_with_results=1, tcc_readings=(0.97,),
        pending_dupe_cluster=True, days_since_latest_fact=700,
        model_r_squared=0.2, model_n=4,
    ))
    assert (a.score, a.band) == (pytest.approx(0.1833, abs=1e-4), "low")
    assert len(a.limitations) == 4
