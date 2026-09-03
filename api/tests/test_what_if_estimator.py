"""Golden-fixture tests for the Δ TCC estimator / what-if simulator (SM-01-05).

Verification contract (from the issue board):

    Golden fixtures (CP: headsail −0.004, kite −0.003, crew −0.002,
    combined ≈ −0.006 / 22 s/hr).

"CP" is the Chilli Pepper case from the SM-01 epic's evaluation harness.
The fixture encodes a class regression in which one fewer declared headsail
is worth −0.004 TCC, one fewer spinnaker −0.003, and one fewer crew −0.002.
The combined scenario is damped for lever interaction (diminishing
returns): naive sum −0.009 × 2/3 = −0.006 ≈ 22 s/hr.

All tests are pure-Python (no database) so they run in CI.
"""

from __future__ import annotations

import pytest

from irc_data.analysis.what_if import (
    COMBINATION_FACTOR,
    ESTIMATE_DISCLAIMER,
    ESTIMATE_FLAG,
    ClassModelContext,
    RecommendationV1,
    WhatIfEstimateV1,
    build_trial_certificate_suggestion,
    default_cost_provider,
    estimate_delta_tcc,
    generate_candidate_deltas,
    rank_recommendations,
    tcc_to_seconds_per_hour,
)

# ---------------------------------------------------------------------------
# Golden fixture: the "CP" (Chilli Pepper) class regression
# ---------------------------------------------------------------------------


@pytest.fixture()
def cp_model() -> ClassModelContext:
    """Class regression for the CP golden fixture.

    beta_per_unit == raw beta here because headsails/spinnakers/crew all have
    SCALE_FACTORS of 1.0 (per-sail / per-person units).
    """
    return ClassModelContext(
        coefficients={
            "headsails": {"beta_per_unit": 0.004, "std_beta": 0.42},
            "spinnakers": {"beta_per_unit": 0.003, "std_beta": 0.31},
            "crew": {"beta_per_unit": 0.002, "std_beta": 0.25},
        },
        current_values={"headsails": 4.0, "spinnakers": 3.0, "crew": 8.0},
        r_squared=0.91,
        model_tier="A",
        design="Chilli Pepper 40",
        boat_name="CHILLI PEPPER",
        current_tcc=1.062,
    )


# ---------------------------------------------------------------------------
# Golden fixture: individual lever deltas
# ---------------------------------------------------------------------------


def test_golden_headsail_delta(cp_model):
    est = estimate_delta_tcc(cp_model, {"headsails": -1})
    assert est.delta_tcc == pytest.approx(-0.004, abs=1e-6)
    assert est.sec_per_hour == pytest.approx(-14.4, abs=0.05)


def test_golden_kite_delta(cp_model):
    est = estimate_delta_tcc(cp_model, {"spinnakers": -1})
    assert est.delta_tcc == pytest.approx(-0.003, abs=1e-6)
    assert est.sec_per_hour == pytest.approx(-10.8, abs=0.05)


def test_golden_crew_delta(cp_model):
    est = estimate_delta_tcc(cp_model, {"crew": -1})
    assert est.delta_tcc == pytest.approx(-0.002, abs=1e-6)
    assert est.sec_per_hour == pytest.approx(-7.2, abs=0.05)


def test_golden_combined_delta(cp_model):
    """Combined headsail+kite+crew scenario ≈ −0.006 TCC / 22 s/hr.

    Naive sum is −0.009; lever-interaction damping (2/3) gives −0.006
    and 21.6 s/hr which rounds to the golden 22 s/hr.
    """
    est = estimate_delta_tcc(
        cp_model, {"headsails": -1, "spinnakers": -1, "crew": -1}
    )
    assert est.delta_tcc == pytest.approx(-0.006, abs=5e-4)
    assert abs(est.sec_per_hour) == pytest.approx(22.0, abs=1.0)
    assert est.combination_factor == pytest.approx(COMBINATION_FACTOR)
    # Estimated new rating = base − 0.006.
    assert est.estimated_tcc == pytest.approx(cp_model.current_tcc - 0.006, abs=5e-4)


def test_single_lever_is_undamped(cp_model):
    """One moving lever → combination factor must be exactly 1.0."""
    est = estimate_delta_tcc(cp_model, {"headsails": -1})
    assert est.combination_factor == 1.0


def test_two_levers_are_damped(cp_model):
    est = estimate_delta_tcc(cp_model, {"headsails": -1, "spinnakers": -1})
    naive = -0.004 + -0.003
    assert est.delta_tcc == pytest.approx(naive * COMBINATION_FACTOR, abs=1e-6)
    assert est.combination_factor == pytest.approx(COMBINATION_FACTOR)


# ---------------------------------------------------------------------------
# Combined estimate carries uncertainty
# ---------------------------------------------------------------------------


def test_combined_estimate_has_uncertainty_interval(cp_model):
    est = estimate_delta_tcc(
        cp_model, {"headsails": -1, "spinnakers": -1, "crew": -1}
    )
    assert "low" in est.uncertainty and "high" in est.uncertainty
    low, high = est.uncertainty["low"], est.uncertainty["high"]
    assert low < est.delta_tcc < high
    assert high - low > 0.0005  # non-trivial band


def test_uncertainty_widens_for_weaker_model(cp_model):
    strong = estimate_delta_tcc(cp_model, {"headsails": -1})
    weak_model = ClassModelContext(
        coefficients=cp_model.coefficients,
        current_values=cp_model.current_values,
        r_squared=0.45,
        model_tier="C",
    )
    weak = estimate_delta_tcc(weak_model, {"headsails": -1})
    strong_width = strong.uncertainty["high"] - strong.uncertainty["low"]
    weak_width = weak.uncertainty["high"] - weak.uncertainty["low"]
    assert weak_width > strong_width


# ---------------------------------------------------------------------------
# Mandatory disclaimer on every output
# ---------------------------------------------------------------------------


def test_every_output_carries_estimate_flag(cp_model):
    est = estimate_delta_tcc(
        cp_model, {"headsails": -1, "spinnakers": -1, "crew": -1}
    )
    payload = est.to_dict()
    assert payload["disclaimer"] == ESTIMATE_DISCLAIMER
    assert payload["estimate_flag"] == ESTIMATE_FLAG
    assert "not an official rating" in payload["disclaimer"]
    for lever in payload["levers"]:
        assert lever["disclaimer"] == ESTIMATE_DISCLAIMER
        assert lever["estimate_flag"] == ESTIMATE_FLAG
    # Trial certificate payload carries it too.
    assert payload["trial_certificate"]["disclaimer"] == ESTIMATE_DISCLAIMER
    assert ESTIMATE_DISCLAIMER in payload["trial_certificate"]["notes"]


# ---------------------------------------------------------------------------
# Class-legal bound enforcement
# ---------------------------------------------------------------------------


def test_headsails_cannot_go_below_one(cp_model):
    """Requesting to remove 4 headsails from a boat carrying 4 must clamp
    to the class-legal minimum of 1 (applied delta −3, not −4)."""
    est = estimate_delta_tcc(cp_model, {"headsails": -4})
    lever = est.levers[0]
    assert lever.applied_delta == -3.0
    assert lever.new_value == 1.0
    assert lever.clamped is True
    assert "limit" in lever.clamp_reason or "minimum" in lever.clamp_reason
    assert est.delta_tcc == pytest.approx(-3 * 0.004, abs=1e-6)


def test_spinnakers_cannot_go_below_zero(cp_model):
    est = estimate_delta_tcc(cp_model, {"spinnakers": -5})
    lever = est.levers[0]
    assert lever.applied_delta == -3.0
    assert lever.new_value == 0.0
    assert lever.clamped is True


def test_crew_cannot_go_below_one(cp_model):
    est = estimate_delta_tcc(cp_model, {"crew": -20})
    lever = est.levers[0]
    # Step limit kicks in first (max_step_down=6), then absolute floor.
    assert lever.applied_delta >= -6.0
    assert lever.new_value >= 1.0
    assert lever.clamped is True


def test_declaration_levers_round_to_integers(cp_model):
    est = estimate_delta_tcc(cp_model, {"headsails": -0.5})
    lever = est.levers[0]
    assert lever.applied_delta == -1.0
    assert lever.clamped is True
    assert "whole numbers" in lever.clamp_reason


def test_caller_supplied_class_bounds_override(cp_model):
    """One-design style band: headsails fixed at 4 → no change possible."""
    est = estimate_delta_tcc(
        cp_model,
        {"headsails": -1},
        class_bounds={"headsails": (4.0, 4.0)},
    )
    lever = est.levers[0]
    assert lever.applied_delta == 0.0
    assert lever.clamped is True
    assert est.delta_tcc == 0.0


# ---------------------------------------------------------------------------
# Trial-certificate suggestion payload
# ---------------------------------------------------------------------------


def test_trial_certificate_payload(cp_model):
    est = estimate_delta_tcc(
        cp_model, {"headsails": -1, "spinnakers": -1, "crew": -1}
    )
    trial = est.trial_certificate
    assert trial is not None
    assert trial["design"] == "Chilli Pepper 40"
    assert trial["boat_name"] == "CHILLI PEPPER"
    assert trial["base_tcc"] == pytest.approx(1.062, abs=1e-4)
    assert trial["estimated_tcc"] == pytest.approx(1.056, abs=5e-4)
    assert trial["delta_tcc"] == pytest.approx(-0.006, abs=5e-4)
    fields = {c["field"] for c in trial["proposed_changes"]}
    assert fields == {"headsails", "spinnakers", "crew"}
    # Proposed new values reflect the applied deltas.
    headsail_change = next(c for c in trial["proposed_changes"] if c["field"] == "headsails")
    assert headsail_change["new_value"] == pytest.approx(3.0)
    assert "trial certificate" in trial["notes"].lower()


def test_trial_certificate_can_be_suppressed(cp_model):
    est = estimate_delta_tcc(cp_model, {"headsails": -1}, include_trial_certificate=False)
    assert est.trial_certificate is None


def test_trial_certificate_builder_direct(cp_model):
    est = estimate_delta_tcc(cp_model, {"crew": -2})
    trial = build_trial_certificate_suggestion(
        model=cp_model,
        lever_estimates=est.levers,
        base_tcc=est.base_tcc,
        delta_tcc=est.delta_tcc,
    )
    d = trial.to_dict()
    assert d["proposed_changes"][0]["field"] == "crew"
    assert d["proposed_changes"][0]["applied_delta"] == -2.0
    assert d["disclaimer"] == ESTIMATE_DISCLAIMER


# ---------------------------------------------------------------------------
# Recommendation ranking (impact × feasibility × evidence)
# ---------------------------------------------------------------------------


def test_rank_recommendations_orders_by_composite(cp_model):
    candidates = [
        {"lever": "crew", "delta": -1},
        {"lever": "headsails", "delta": -1},
        {"lever": "spinnakers", "delta": -1},
    ]
    recs = rank_recommendations(cp_model, candidates)
    assert len(recs) == 3
    # All feasibility 1, so ordering follows impact × evidence.
    assert recs[0].lever_field == "headsails"   # 0.004 × 1.0 × 1.0
    assert recs[1].lever_field == "spinnakers"  # 0.003 × 1.0 × 1.0
    assert recs[2].lever_field == "crew"        # 0.002 × 1.0 × 1.0
    assert [r.rank for r in recs] == [1, 2, 3]
    # RecommendationV1 contract keys.
    d = recs[0].to_dict()
    for key in ("change", "category", "delta_tcc", "sec_per_hour",
                "feasibility", "evidence_strength"):
        assert key in d
    assert d["disclaimer"] == ESTIMATE_DISCLAIMER
    assert d["estimate_flag"] == ESTIMATE_FLAG


def test_recommendations_exclude_tcc_increases(cp_model):
    """Increasing headsails raises TCC — must not be recommended."""
    recs = rank_recommendations(cp_model, [{"lever": "headsails", "delta": +1}])
    assert recs == []


def test_recommendation_feasibility_beats_impact(cp_model):
    """A higher-impact structural change should rank below a lower-impact
    admin change because feasibility dominates the composite."""
    model = ClassModelContext(
        coefficients={
            "headsails": {"beta_per_unit": 0.004, "std_beta": 0.42},
            # draft beta_per_unit is per 0.1m (SCALE_FACTORS["draft"]=0.1):
            # raw_beta = 0.05 / 0.1 = 0.5 TCC/m.
            "draft": {"beta_per_unit": 0.05, "std_beta": 0.55},
        },
        current_values={"headsails": 4.0, "draft": 2.4},
        r_squared=0.91,
        model_tier="A",
    )
    candidates = [
        # impact 0.004 × feas 1.0  × ev 1.0 = 0.00400
        {"lever": "headsails", "delta": -1},
        # −0.02 m → impact 0.5×0.02 = 0.010 × feas (9−7)/8 = 0.25 × ev 1.0
        # = 0.00250 — bigger rating win, worse composite.
        {"lever": "draft", "delta": -0.02},
    ]
    recs = rank_recommendations(model, candidates)
    assert recs[0].lever_field == "headsails"
    assert recs[0].feasibility == 1
    assert recs[1].lever_field == "draft"
    assert recs[1].feasibility == 7
    # Sanity: the draft change really did have the larger |Δ TCC|.
    assert abs(recs[1].delta_tcc) > abs(recs[0].delta_tcc)


def test_indicative_cost_overlay_hook(cp_model):
    """Sail-programme cost overlay: admin changes are free, sails cost money."""
    model = ClassModelContext(
        coefficients={
            "headsails": {"beta_per_unit": 0.004, "std_beta": 0.42},
            "hlu": {"beta_per_unit": 0.01, "std_beta": 0.35},  # per 0.1m
        },
        current_values={"headsails": 4.0, "hlu": 12.5},
        r_squared=0.91,
        model_tier="A",
    )
    recs = rank_recommendations(model, [
        {"lever": "headsails", "delta": -1},
        {"lever": "hlu", "delta": -0.3},
    ])
    by_lever = {r.lever_field: r for r in recs}
    assert by_lever["headsails"].indicative_cost == 0.0
    assert by_lever["hlu"].indicative_cost is not None
    assert by_lever["hlu"].indicative_cost > 0
    # indicative_cost is an *optional* contract key — present only when set.
    assert "indicative_cost" in by_lever["headsails"].to_dict()


def test_cost_overlay_hook_is_pluggable(cp_model):
    """A sail-programme pricing service can replace the default hook."""
    def sail_programme_prices(lever_field, category, feasibility):
        return {"headsails": 0.0, "spinnakers": 6_200.0}.get(lever_field)

    recs = rank_recommendations(
        cp_model,
        [{"lever": "spinnakers", "delta": -1}],
        cost_provider=sail_programme_prices,
    )
    assert recs[0].indicative_cost == 6_200.0


def test_cost_overlay_can_be_suppressed(cp_model):
    recs = rank_recommendations(
        cp_model,
        [{"lever": "spinnakers", "delta": -1}],
        cost_provider=lambda *a: None,
    )
    assert recs[0].indicative_cost is None
    assert "indicative_cost" not in recs[0].to_dict()


def test_default_cost_provider_semantics():
    assert default_cost_provider("headsails", "admin", 1) == 0.0
    assert default_cost_provider("hlu", "sail", 3) > 0
    assert default_cost_provider("lh", "hardware", 8) is None


# ---------------------------------------------------------------------------
# Candidate generation + unit conversion
# ---------------------------------------------------------------------------


def test_generate_candidates_only_tcc_reducing(cp_model):
    candidates = generate_candidate_deltas(cp_model)
    assert candidates, "expected at least one candidate"
    for cand in candidates:
        raw_beta = cp_model.raw_beta(cand["lever"])
        assert cand["delta"] * raw_beta < 0, (
            f"candidate {cand} would not reduce TCC"
        )


def test_generate_candidates_empty_model():
    model = ClassModelContext(coefficients={}, current_values={})
    assert generate_candidate_deltas(model) == []


def test_tcc_to_seconds_per_hour_conversion():
    assert tcc_to_seconds_per_hour(-0.001) == pytest.approx(-3.6)
    assert tcc_to_seconds_per_hour(-0.006) == pytest.approx(-21.6)
    assert round(abs(tcc_to_seconds_per_hour(-0.006))) == 22  # golden


# ---------------------------------------------------------------------------
# Contract shape: WhatIfEstimateV1 / RecommendationV1 serialise cleanly
# ---------------------------------------------------------------------------


def test_what_if_estimate_v1_contract_shape(cp_model):
    payload = estimate_delta_tcc(
        cp_model, {"headsails": -1, "crew": -1}
    ).to_dict()
    expected_keys = {
        "base_tcc", "estimated_tcc", "delta_tcc", "sec_per_hour",
        "uncertainty", "levers", "model_tier", "r_squared", "design",
        "combination_factor", "disclaimer", "estimate_flag",
        "trial_certificate",
    }
    assert expected_keys <= set(payload.keys())
    lever_keys = {
        "field", "label", "unit", "requested_delta", "applied_delta",
        "clamped", "clamp_reason", "delta_tcc", "sec_per_hour", "new_value",
        "disclaimer", "estimate_flag",
    }
    assert lever_keys <= set(payload["levers"][0].keys())


def test_unknown_lever_is_ignored(cp_model):
    est = estimate_delta_tcc(cp_model, {"not_a_lever": -1})
    assert est.levers == []
    assert est.delta_tcc == 0.0


def test_zero_model_still_enforces_bounds():
    """With no regression coefficients, deltas estimate 0 but bounds apply."""
    model = ClassModelContext(
        coefficients={},
        current_values={"headsails": 2.0},
    )
    est = estimate_delta_tcc(model, {"headsails": -5})
    assert est.levers[0].applied_delta == -1.0  # floor at 1 headsail
    assert est.delta_tcc == 0.0
    assert est.disclaimer == ESTIMATE_DISCLAIMER
