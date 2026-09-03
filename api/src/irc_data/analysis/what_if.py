"""Engine 5: Δ TCC estimator, what-if simulator and recommendation ranking.

Powers the "what-if" simulator (SM-01-05): given proposed lever deltas
(headsail declaration, spinnaker count/size, crew number, draft,
displacement, …) estimate the resulting change in TCC using the class
regression from Engine 1 (SM-01-02), with uncertainty, class-legal bound
enforcement, and recommendation ranking by impact × feasibility × evidence.

Output contracts (consumed by the web what-if UI and report surfaces):

- ``WhatIfEstimateV1``  — per-scenario estimate:
    {base_tcc, estimated_tcc, delta_tcc, sec_per_hour,
     uncertainty: {low, high}, levers[], disclaimer, estimate_flag}
- ``RecommendationV1``  — ranked suggestion:
    {change, category, delta_tcc, sec_per_hour, feasibility,
     evidence_strength, indicative_cost?}
- ``TrialCertificateSuggestionV1`` — payload an owner can take to the
  IRC rating office to request a trial certificate.

EVERY output carries the mandatory disclaimer:
    "estimate from class regression — not an official rating"

The IRC formula is SECRET. All numbers are statistical estimates from
within-class regression (Engine 1) and are framed as "consistent with"
observed fleet patterns — never as formula mechanics.
"""

from __future__ import annotations

import itertools
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from irc_data.analysis.regression import SCALE_FACTORS, UNITS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mandatory disclaimer — MUST appear on every estimate / recommendation.
# ---------------------------------------------------------------------------

ESTIMATE_DISCLAIMER = "estimate from class regression — not an official rating"
ESTIMATE_FLAG = "class_regression_estimate"  # machine-readable flag

# Conversion: one TCC "point" (0.001) ≈ 3.6 s/hr on corrected time.
SECONDS_PER_HOUR_PER_TCC_POINT = 3.6

# Lever interaction damping (diminishing returns). Lever effects estimated
# from a within-class regression are *marginal* — changing several levers at
# once moves the boat through design space the regression only sampled
# locally, and real IRC rating effects interact (e.g. fewer headsails AND
# fewer spinnakers both act on the sail-plan term). A combined scenario
# therefore scales the naive sum by COMBINATION_FACTOR. Calibrated against
# the golden fixture: headsail −0.004 + kite −0.003 + crew −0.002 summed to
# −0.009, observed combined ≈ −0.006 → factor = 2/3. A single lever is
# undamped (factor 1.0).
COMBINATION_FACTOR = 2.0 / 3.0


# ---------------------------------------------------------------------------
# Lever definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeverSpec:
    """Static description of an estimable TCC lever."""

    field: str
    label: str
    unit: str
    category: str  # "admin" | "sail" | "hardware"
    feasibility: int  # 1 (trivial admin) .. 8 (practically impossible)
    integer_valued: bool = False
    min_legal: float | None = None  # absolute class-legal floor (raw units)
    max_legal: float | None = None  # absolute class-legal ceiling (raw units)
    max_step_down: float | None = None  # max single downward change (raw units)
    max_step_up: float | None = None  # max single upward change (raw units)


# Feasibility scale mirrors irc_data.analysis.optimizer.FEASIBILITY.
LEVER_SPECS: dict[str, LeverSpec] = {
    # Admin / declaration levers — free to change, bounded by IRC rules.
    "headsails": LeverSpec(
        field="headsails",
        label="Declared headsails",
        unit="sails",
        category="admin",
        feasibility=1,
        integer_valued=True,
        min_legal=1.0,          # IRC requires at least one headsail aboard
        max_legal=8.0,
        max_step_down=3.0,
        max_step_up=3.0,
    ),
    "spinnakers": LeverSpec(
        field="spinnakers",
        label="Declared spinnakers",
        unit="sails",
        category="admin",
        feasibility=1,
        integer_valued=True,
        min_legal=0.0,
        max_legal=8.0,
        max_step_down=4.0,
        max_step_up=4.0,
    ),
    "crew": LeverSpec(
        field="crew",
        label="Crew number",
        unit="people",
        category="admin",
        feasibility=1,
        integer_valued=True,
        min_legal=1.0,          # cannot race with fewer than 1
        max_legal=25.0,
        max_step_down=6.0,
        max_step_up=6.0,
    ),
    "dlr": LeverSpec(
        field="dlr",
        label="DLR (rated length/weight)",
        unit="units",
        category="admin",
        feasibility=2,
        integer_valued=False,
        max_step_down=5.0,
        max_step_up=5.0,
    ),
    # Sail wardrobe levers — new sail purchase.
    "hlu": LeverSpec(
        field="hlu",
        label="Headsail luff (HLU)",
        unit="m",
        category="sail",
        feasibility=3,
        min_legal=0.5,
        max_step_down=2.0,
        max_step_up=2.0,
    ),
    "hlp": LeverSpec(
        field="hlp",
        label="Headsail perpendicular (HLP)",
        unit="m",
        category="sail",
        feasibility=3,
        min_legal=0.5,
        max_step_down=2.0,
        max_step_up=2.0,
    ),
    "sym_slu": LeverSpec(
        field="sym_slu",
        label="Spinnaker luff (SLU)",
        unit="m",
        category="sail",
        feasibility=3,
        min_legal=1.0,
        max_step_down=3.0,
        max_step_up=3.0,
    ),
    "sym_sf": LeverSpec(
        field="sym_sf",
        label="Spinnaker foot (SF)",
        unit="m",
        category="sail",
        feasibility=3,
        min_legal=1.0,
        max_step_down=3.0,
        max_step_up=3.0,
    ),
    "stl": LeverSpec(
        field="stl",
        label="Spinnaker pole / bowsprit (STL)",
        unit="m",
        category="sail",
        feasibility=3,
        min_legal=0.0,
        max_step_down=2.0,
        max_step_up=2.0,
    ),
    # Rig / structural levers.
    "p": LeverSpec("p", "Mainsail hoist (P)", "m", "sail", 4, min_legal=1.0, max_step_down=1.0, max_step_up=1.0),
    "e": LeverSpec("e", "Mainsail foot (E)", "m", "sail", 4, min_legal=0.5, max_step_down=1.0, max_step_up=1.0),
    "j": LeverSpec("j", "Foretriangle base (J)", "m", "sail", 4, min_legal=0.5, max_step_down=1.0, max_step_up=1.0),
    "muw": LeverSpec("muw", "Mast weight (MUW)", "kg", "hardware", 5, min_legal=10.0, max_step_down=100.0, max_step_up=100.0),
    "mhw": LeverSpec("mhw", "Mast head weight (MHW)", "kg", "hardware", 5, min_legal=1.0, max_step_down=20.0, max_step_up=20.0),
    "displacement": LeverSpec(
        "displacement", "Displacement", "kg", "hardware", 6,
        min_legal=500.0, max_step_down=0.15, max_step_up=0.15,
    ),
    "draft": LeverSpec(
        "draft", "Draft", "m", "hardware", 7,
        min_legal=0.3, max_step_down=0.5, max_step_up=0.5,
    ),
    "lh": LeverSpec("lh", "Hull length (LH)", "m", "hardware", 8, min_legal=3.0),
    "beam": LeverSpec("beam", "Beam", "m", "hardware", 8, min_legal=1.0),
}

# Levers eligible for automatic recommendation generation (skip hull-fixed).
RECOMMENDABLE_LEVERS = [
    f for f, s in LEVER_SPECS.items() if s.feasibility <= 7
]

# Evidence-strength weights used by the ranking composite.
EVIDENCE_WEIGHTS = {"strong": 1.0, "moderate": 0.7, "limited": 0.4}


# ---------------------------------------------------------------------------
# Cost overlay hooks (sail programme)
# ---------------------------------------------------------------------------


def default_cost_provider(lever_field: str, category: str, feasibility: int) -> float | None:
    """Fallback indicative-cost hook (AUD, order-of-magnitude).

    Wired so a sail-programme / quote service can replace it later via
    the ``cost_provider`` parameter on the ranking functions. Returns
    ``None`` for hull-fixed levers where cost is meaningless.
    """
    if category == "admin":
        return 0.0
    if category == "sail":
        # New headsail/spinnaker, or rig tweak.
        return {3: 4_500.0, 4: 8_000.0}.get(feasibility, 8_000.0)
    if category == "hardware":
        return {5: 18_000.0, 6: 30_000.0, 7: 45_000.0}.get(feasibility)
    return None


# ---------------------------------------------------------------------------
# Output contracts
# ---------------------------------------------------------------------------


@dataclass
class LeverEstimate:
    """Per-lever Δ TCC contribution within a scenario."""

    field: str
    label: str
    unit: str
    requested_delta: float
    applied_delta: float
    clamped: bool
    clamp_reason: str | None
    delta_tcc: float
    sec_per_hour: float
    new_value: float | None
    disclaimer: str = ESTIMATE_DISCLAIMER
    estimate_flag: str = ESTIMATE_FLAG

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "label": self.label,
            "unit": self.unit,
            "requested_delta": round(self.requested_delta, 4),
            "applied_delta": round(self.applied_delta, 4),
            "clamped": self.clamped,
            "clamp_reason": self.clamp_reason,
            "delta_tcc": round(self.delta_tcc, 5),
            "sec_per_hour": round(self.sec_per_hour, 1),
            "new_value": round(self.new_value, 3) if self.new_value is not None else None,
            "disclaimer": self.disclaimer,
            "estimate_flag": self.estimate_flag,
        }


@dataclass
class WhatIfEstimateV1:
    """Combined what-if scenario estimate (the WhatIfEstimateV1 contract)."""

    base_tcc: float | None
    estimated_tcc: float | None
    delta_tcc: float
    sec_per_hour: float
    uncertainty: dict[str, float]  # {low, high} — Δ TCC interval
    levers: list[LeverEstimate]
    model_tier: str | None = None
    r_squared: float | None = None
    design: str | None = None
    combination_factor: float = 1.0
    disclaimer: str = ESTIMATE_DISCLAIMER
    estimate_flag: str = ESTIMATE_FLAG
    trial_certificate: dict | None = None  # TrialCertificateSuggestionV1 payload

    def to_dict(self) -> dict:
        return {
            "base_tcc": round(self.base_tcc, 4) if self.base_tcc is not None else None,
            "estimated_tcc": round(self.estimated_tcc, 4) if self.estimated_tcc is not None else None,
            "delta_tcc": round(self.delta_tcc, 5),
            "sec_per_hour": round(self.sec_per_hour, 1),
            "uncertainty": {k: round(v, 5) for k, v in self.uncertainty.items()},
            "levers": [le.to_dict() for le in self.levers],
            "model_tier": self.model_tier,
            "r_squared": round(self.r_squared, 4) if self.r_squared is not None else None,
            "design": self.design,
            "combination_factor": round(self.combination_factor, 6),
            "disclaimer": self.disclaimer,
            "estimate_flag": self.estimate_flag,
            "trial_certificate": self.trial_certificate,
        }


@dataclass
class RecommendationV1:
    """Ranked recommendation (the RecommendationV1 contract)."""

    change: str
    category: str
    delta_tcc: float
    sec_per_hour: float
    feasibility: int
    evidence_strength: str
    indicative_cost: float | None = None
    lever_field: str | None = None
    requested_delta: float | None = None
    applied_delta: float | None = None
    new_value: float | None = None
    unit: str | None = None
    feasibility_label: str = ""
    explanation: str = ""
    composite_score: float = 0.0
    rank: int = 0
    disclaimer: str = ESTIMATE_DISCLAIMER
    estimate_flag: str = ESTIMATE_FLAG

    def to_dict(self) -> dict:
        result = {
            "change": self.change,
            "category": self.category,
            "delta_tcc": round(self.delta_tcc, 5),
            "sec_per_hour": round(self.sec_per_hour, 1),
            "feasibility": self.feasibility,
            "feasibility_label": self.feasibility_label,
            "evidence_strength": self.evidence_strength,
            "lever_field": self.lever_field,
            "requested_delta": round(self.requested_delta, 4) if self.requested_delta is not None else None,
            "applied_delta": round(self.applied_delta, 4) if self.applied_delta is not None else None,
            "new_value": round(self.new_value, 3) if self.new_value is not None else None,
            "unit": self.unit,
            "explanation": self.explanation,
            "composite_score": round(self.composite_score, 6),
            "rank": self.rank,
            "disclaimer": self.disclaimer,
            "estimate_flag": self.estimate_flag,
        }
        # indicative_cost? — optional key, only present when a cost exists.
        if self.indicative_cost is not None:
            result["indicative_cost"] = round(self.indicative_cost, 2)
        return result


@dataclass
class TrialCertificateSuggestionV1:
    """Payload for requesting an IRC trial certificate."""

    boat_name: str | None
    design: str | None
    base_tcc: float | None
    estimated_tcc: float | None
    delta_tcc: float
    proposed_changes: list[dict]
    notes: str
    disclaimer: str = ESTIMATE_DISCLAIMER
    estimate_flag: str = ESTIMATE_FLAG

    def to_dict(self) -> dict:
        return {
            "boat_name": self.boat_name,
            "design": self.design,
            "base_tcc": round(self.base_tcc, 4) if self.base_tcc is not None else None,
            "estimated_tcc": round(self.estimated_tcc, 4) if self.estimated_tcc is not None else None,
            "delta_tcc": round(self.delta_tcc, 5),
            "proposed_changes": self.proposed_changes,
            "notes": self.notes,
            "disclaimer": self.disclaimer,
            "estimate_flag": self.estimate_flag,
        }


# ---------------------------------------------------------------------------
# Model context
# ---------------------------------------------------------------------------


@dataclass
class ClassModelContext:
    """A simplified view over the SM-01-02 class regression (Engine 1).

    Parameters
    ----------
    coefficients:
        Mapping lever field → standardised-effect estimate expressed as
        ``{beta_per_unit, std_beta}``. ``beta_per_unit`` is the Δ TCC per
        *display unit* (see ``irc_data.analysis.regression.SCALE_FACTORS``).
    current_values:
        This boat's current declared/measured values (raw units).
    r_squared, model_tier, design:
        Model quality + identity metadata for uncertainty scaling.
    """

    coefficients: dict[str, dict[str, float]]
    current_values: dict[str, float] = field(default_factory=dict)
    r_squared: float | None = None
    model_tier: str | None = None
    design: str | None = None
    boat_name: str | None = None
    class_means: dict[str, float] = field(default_factory=dict)
    smart_means: dict[str, float] = field(default_factory=dict)
    current_tcc: float | None = None

    # ------------------------------------------------------------------
    @classmethod
    def from_sensitivity_context(cls, ctx: dict | None, boat_data: dict | None = None) -> "ClassModelContext":
        """Build from ``get_boat_sensitivity_context(...)`` (SM-01-02) output.

        Accepts ``None`` (no regression available) → an empty model where
        every lever has zero estimated effect but bounds are still enforced.
        """
        ctx = ctx or {}
        coefficients = {
            c["field"]: {
                "beta_per_unit": float(c.get("beta_per_unit", 0.0)),
                "std_beta": float(c.get("std_beta", 0.0)),
            }
            for c in ctx.get("coefficients", [])
            if c.get("field")
        }
        current_values: dict[str, float] = {}
        for feat, pos in (ctx.get("boat_position") or {}).items():
            if pos.get("value") is not None:
                current_values[feat] = float(pos["value"])
        if boat_data:
            for k, v in boat_data.items():
                if v is not None and isinstance(v, (int, float)):
                    current_values.setdefault(k, float(v))
        class_baseline = ctx.get("class_baseline") or {}
        return cls(
            coefficients=coefficients,
            current_values=current_values,
            r_squared=ctx.get("r_squared"),
            model_tier=ctx.get("model_tier"),
            design=ctx.get("design"),
            class_means={},
            smart_means={},
            current_tcc=class_baseline.get("this_boat_tcc"),
        )

    # ------------------------------------------------------------------
    def raw_beta(self, lever_field: str) -> float:
        """Δ TCC per *raw* unit for a lever."""
        coef = self.coefficients.get(lever_field)
        if not coef:
            return 0.0
        scale = SCALE_FACTORS.get(lever_field, 1.0)
        return coef.get("beta_per_unit", 0.0) / scale if scale else 0.0

    def std_beta(self, lever_field: str) -> float:
        coef = self.coefficients.get(lever_field)
        return float(coef.get("std_beta", 0.0)) if coef else 0.0


# ---------------------------------------------------------------------------
# Bound enforcement
# ---------------------------------------------------------------------------


@dataclass
class _ClampResult:
    applied_delta: float
    clamped: bool
    reason: str | None


def _clamp_delta(
    spec: LeverSpec,
    requested_delta: float,
    current_value: float | None,
    class_min: float | None = None,
    class_max: float | None = None,
) -> _ClampResult:
    """Enforce class-legal bounds on a requested lever delta.

    Bounds applied (in order):
    1. Per-step magnitude limits (``max_step_down`` / ``max_step_up``).
    2. Absolute legal floor/ceiling (``min_legal`` / ``max_legal``) when the
       boat's current value is known.
    3. Optional caller-supplied class min/max (e.g. one-design bands).
    4. Integer rounding for declaration levers.
    """
    applied = requested_delta
    clamped = False
    reasons: list[str] = []

    # (1) Per-step magnitude
    if applied < 0 and spec.max_step_down is not None and abs(applied) > spec.max_step_down:
        applied = -spec.max_step_down
        clamped = True
        reasons.append(f"single-change limit −{spec.max_step_down:g} {spec.unit}")
    elif applied > 0 and spec.max_step_up is not None and applied > spec.max_step_up:
        applied = spec.max_step_up
        clamped = True
        reasons.append(f"single-change limit +{spec.max_step_up:g} {spec.unit}")

    # (2)(3) Absolute bounds — need current value
    new_value = None
    if current_value is not None:
        new_value = current_value + applied
        floor = spec.min_legal
        if class_min is not None:
            floor = max(floor, class_min) if floor is not None else class_min
        ceiling = spec.max_legal
        if class_max is not None:
            ceiling = min(ceiling, class_max) if ceiling is not None else class_max
        if floor is not None and new_value < floor:
            applied = floor - current_value
            new_value = floor
            clamped = True
            reasons.append(f"class-legal minimum {floor:g} {spec.unit}")
        if ceiling is not None and new_value > ceiling:
            applied = ceiling - current_value
            new_value = ceiling
            clamped = True
            reasons.append(f"class-legal maximum {ceiling:g} {spec.unit}")

    # (4) Integer rounding for declarations
    if spec.integer_valued:
        rounded = math.floor(applied) if applied < 0 else math.ceil(applied)
        if rounded != applied:
            applied = float(rounded)
            clamped = True
            reasons.append("declarations must be whole numbers")

    return _ClampResult(
        applied_delta=applied,
        clamped=clamped,
        reason="; ".join(reasons) if reasons else None,
    )


# ---------------------------------------------------------------------------
# Core estimator
# ---------------------------------------------------------------------------


def tcc_to_seconds_per_hour(delta_tcc: float) -> float:
    """Convert Δ TCC → seconds per hour on corrected time (signed)."""
    return delta_tcc / 0.001 * SECONDS_PER_HOUR_PER_TCC_POINT


def estimate_delta_tcc(
    model: ClassModelContext,
    lever_deltas: dict[str, float],
    *,
    base_tcc: float | None = None,
    class_bounds: dict[str, tuple[float | None, float | None]] | None = None,
    include_trial_certificate: bool = True,
) -> WhatIfEstimateV1:
    """Estimate Δ TCC for a what-if scenario.

    Parameters
    ----------
    model:
        Class model context (from SM-01-02 class regression).
    lever_deltas:
        Mapping lever field → requested change in *raw* units.
        Negative = reduce (e.g. ``{"headsails": -1, "crew": -1}``).
    base_tcc:
        Boat's current TCC (for estimated new TCC). Falls back to
        ``model.current_tcc`` if omitted.
    class_bounds:
        Optional per-lever ``(min, max)`` overrides (e.g. one-design bands).
    include_trial_certificate:
        Attach a trial-certificate suggestion payload.

    Returns
    -------
    WhatIfEstimateV1 with per-lever contributions, combined estimate and
    an uncertainty interval. Every output carries the mandatory disclaimer.
    """
    if base_tcc is None:
        base_tcc = model.current_tcc

    lever_estimates: list[LeverEstimate] = []
    total_delta = 0.0
    variance_terms: list[float] = []

    for lever_field, requested in lever_deltas.items():
        spec = LEVER_SPECS.get(lever_field)
        if spec is None:
            logger.warning("Unknown lever %r — ignored", lever_field)
            continue

        requested_f = float(requested)
        current = model.current_values.get(lever_field)
        bounds = (class_bounds or {}).get(lever_field, (None, None))
        clamp = _clamp_delta(spec, requested_f, current, bounds[0], bounds[1])

        raw_beta = model.raw_beta(lever_field)
        lever_delta_tcc = clamp.applied_delta * raw_beta
        total_delta += lever_delta_tcc

        new_value = (current + clamp.applied_delta) if current is not None else None
        lever_estimates.append(LeverEstimate(
            field=lever_field,
            label=spec.label,
            unit=spec.unit,
            requested_delta=requested_f,
            applied_delta=clamp.applied_delta,
            clamped=clamp.clamped,
            clamp_reason=clamp.reason,
            delta_tcc=lever_delta_tcc,
            sec_per_hour=tcc_to_seconds_per_hour(lever_delta_tcc),
            new_value=new_value,
        ))

        # Variance proxy: levers with weaker evidence contribute more noise.
        std_beta = abs(model.std_beta(lever_field))
        evidence_noise = 1.0 - min(std_beta, 1.0)  # 0 = strong lever, 1 = no evidence
        variance_terms.append((abs(lever_delta_tcc) * (0.5 + evidence_noise)) ** 2)

    # ---- Lever interaction damping --------------------------------------
    # Individual lever contributions above are exact (undamped). When two or
    # more levers move simultaneously, damp the naive sum to reflect
    # diminishing returns / interaction effects observed in the fleet.
    naive_delta = total_delta
    n_moving = sum(1 for le in lever_estimates if abs(le.applied_delta) > 1e-12)
    combination_factor = COMBINATION_FACTOR if n_moving >= 2 else 1.0
    total_delta = naive_delta * combination_factor

    # ---- Combined uncertainty interval ---------------------------------
    # Floor: model quality — lower R² → wider band.
    r2 = model.r_squared if model.r_squared is not None else 0.5
    model_noise = max(0.35, 1.0 - r2)  # R²=0.91 → 0.35 (floored); R²=0.5 → 0.5
    combined_sigma = math.sqrt(sum(variance_terms)) * model_noise * combination_factor
    # Minimum half-width of half a point so the band is never zero-width.
    half_width = max(combined_sigma, 0.0005)
    uncertainty = {"low": total_delta - half_width, "high": total_delta + half_width}

    estimated_tcc = (base_tcc + total_delta) if base_tcc is not None else None

    trial_payload = None
    if include_trial_certificate:
        trial = build_trial_certificate_suggestion(
            model=model,
            lever_estimates=lever_estimates,
            base_tcc=base_tcc,
            delta_tcc=total_delta,
        )
        trial_payload = trial.to_dict()

    return WhatIfEstimateV1(
        base_tcc=base_tcc,
        estimated_tcc=estimated_tcc,
        delta_tcc=total_delta,
        sec_per_hour=tcc_to_seconds_per_hour(total_delta),
        uncertainty=uncertainty,
        levers=lever_estimates,
        model_tier=model.model_tier,
        r_squared=model.r_squared,
        design=model.design,
        combination_factor=combination_factor,
        trial_certificate=trial_payload,
    )


# ---------------------------------------------------------------------------
# Trial-certificate suggestion payload
# ---------------------------------------------------------------------------


def build_trial_certificate_suggestion(
    model: ClassModelContext,
    lever_estimates: list[LeverEstimate],
    base_tcc: float | None,
    delta_tcc: float,
) -> TrialCertificateSuggestionV1:
    """Build a trial-certificate suggestion payload from applied changes."""
    proposed = [
        {
            "field": le.field,
            "label": le.label,
            "requested_delta": round(le.requested_delta, 4),
            "applied_delta": round(le.applied_delta, 4),
            "new_value": round(le.new_value, 3) if le.new_value is not None else None,
            "unit": le.unit,
        }
        for le in lever_estimates
        if abs(le.applied_delta) > 1e-12
    ]
    estimated_tcc = (base_tcc + delta_tcc) if base_tcc is not None else None
    direction = "reduction" if delta_tcc < 0 else "increase"
    notes = (
        f"Estimated TCC {direction} of {abs(delta_tcc):.4f} "
        f"({abs(tcc_to_seconds_per_hour(delta_tcc)):.0f} s/hr) "
        "from class regression. Request a trial certificate from the IRC "
        "rating office to confirm before committing to any change. "
        f"({ESTIMATE_DISCLAIMER}.)"
    )
    return TrialCertificateSuggestionV1(
        boat_name=model.boat_name,
        design=model.design,
        base_tcc=base_tcc,
        estimated_tcc=estimated_tcc,
        delta_tcc=delta_tcc,
        proposed_changes=proposed,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Recommendation ranking (impact × feasibility × evidence)
# ---------------------------------------------------------------------------


def _evidence_strength(std_beta: float, model_tier: str | None) -> str:
    mag = abs(std_beta)
    if model_tier == "A":
        return "strong" if mag > 0.3 else "moderate"
    if model_tier == "B":
        return "moderate" if mag > 0.3 else "limited"
    return "limited"


_FEASIBILITY_LABELS = {
    1: "Admin/config (no cost)",
    2: "Minor config change",
    3: "Sail purchase ($)",
    4: "Rig modification ($$)",
    5: "Major rig work ($$$)",
    6: "Structural — weight ($$$$)",
    7: "Keel/draft modification ($$$$)",
    8: "Not practically changeable",
}


def rank_recommendations(
    model: ClassModelContext,
    candidates: list[dict[str, Any]],
    *,
    cost_provider=None,
    top_n: int | None = None,
) -> list[RecommendationV1]:
    """Rank candidate lever changes by impact × feasibility × evidence.

    Parameters
    ----------
    model:
        Class model context (from SM-01-02 class regression).
    candidates:
        Each candidate is ``{"lever": <field>, "delta": <raw-unit change>}``
        with optional ``"class_min"`` / ``"class_max"`` bound overrides.
    cost_provider:
        Optional sail-programme cost overlay hook:
        ``callable(lever_field, category, feasibility) -> float | None``.
        Defaults to :func:`default_cost_provider`. Pass ``None`` explicitly
        to suppress ``indicative_cost`` entirely.
    top_n:
        Optionally truncate the ranked list.

    Returns
    -------
    Ranked ``RecommendationV1`` list (rank 1 = best composite score).
    Only changes that *reduce* TCC (negative delta_tcc) are recommended;
    a candidate that would raise the rating is excluded.
    """
    if cost_provider is None:
        cost_provider = default_cost_provider

    recommendations: list[RecommendationV1] = []

    for cand in candidates:
        lever_field = cand.get("lever") or cand.get("field")
        spec = LEVER_SPECS.get(lever_field or "")
        if spec is None:
            continue
        requested = float(cand.get("delta", 0.0))
        if requested == 0.0:
            continue

        current = model.current_values.get(lever_field)
        clamp = _clamp_delta(spec, requested, current, cand.get("class_min"), cand.get("class_max"))
        if clamp.applied_delta == 0.0:
            continue

        raw_beta = model.raw_beta(lever_field)
        delta_tcc = clamp.applied_delta * raw_beta

        # Only recommend rating reductions.
        if delta_tcc >= 0:
            continue

        std_beta = model.std_beta(lever_field)
        evidence = _evidence_strength(std_beta, model.model_tier)
        sec_hr = tcc_to_seconds_per_hour(delta_tcc)

        indicative_cost = cost_provider(lever_field, spec.category, spec.feasibility) if cost_provider else None

        direction_word = "Reduce" if clamp.applied_delta < 0 else "Increase"
        new_value = (current + clamp.applied_delta) if current is not None else None
        change_desc = (
            f"{direction_word} {spec.label.lower()} by {abs(clamp.applied_delta):g} {spec.unit}"
        )
        if new_value is not None:
            change_desc += f" (→ {new_value:g} {spec.unit})"

        explanation = (
            f"{change_desc}. Est. Δ TCC {delta_tcc:+.4f} "
            f"({sec_hr:+.1f} s/hr) — {evidence} evidence "
            f"(class regression, tier {model.model_tier or 'n/a'}). "
        )
        if clamp.clamped and clamp.reason:
            explanation += f"Clamped to class-legal bounds: {clamp.reason}. "
        explanation += "Confirm with a trial certificate."

        # Composite score: |impact| × feasibility × evidence
        impact = abs(delta_tcc)
        feas_score = (9 - spec.feasibility) / 8.0
        ev_score = EVIDENCE_WEIGHTS.get(evidence, 0.5)
        composite = impact * feas_score * ev_score

        recommendations.append(RecommendationV1(
            change=change_desc,
            category=spec.category,
            delta_tcc=delta_tcc,
            sec_per_hour=sec_hr,
            feasibility=spec.feasibility,
            feasibility_label=_FEASIBILITY_LABELS.get(spec.feasibility, ""),
            evidence_strength=evidence,
            indicative_cost=indicative_cost,
            lever_field=lever_field,
            requested_delta=requested,
            applied_delta=clamp.applied_delta,
            new_value=new_value,
            unit=spec.unit,
            explanation=explanation,
            composite_score=composite,
        ))

    recommendations.sort(key=lambda r: r.composite_score, reverse=True)
    if top_n is not None:
        recommendations = recommendations[:top_n]
    for i, rec in enumerate(recommendations, 1):
        rec.rank = i
    return recommendations


# ---------------------------------------------------------------------------
# Automatic candidate generation (what the what-if simulator sweeps)
# ---------------------------------------------------------------------------


def generate_candidate_deltas(
    model: ClassModelContext,
    *,
    levers: list[str] | None = None,
    include_combinations: bool = False,
    max_combination_size: int = 3,
) -> list[dict[str, Any]]:
    """Generate sensible candidate changes for the what-if simulator.

    For each recommendable lever, propose a move *toward* the class mean
    (or smart-boat cohort mean if available) capped by the lever's single-
    step limit, plus a standard single-step reduction.
    """
    levers = levers or RECOMMENDABLE_LEVERS
    singles: list[dict[str, Any]] = []

    for lever_field in levers:
        spec = LEVER_SPECS.get(lever_field)
        if spec is None:
            continue
        raw_beta = model.raw_beta(lever_field)
        if raw_beta == 0.0:
            continue

        current = model.current_values.get(lever_field)
        # Optimal direction: if beta > 0, reducing the lever reduces TCC.
        direction = -1.0 if raw_beta > 0 else 1.0

        # Standard single-step candidate.
        step = spec.max_step_down if direction < 0 else spec.max_step_up
        if step is None:
            step = 1.0
        step = min(step, 1.0 if spec.integer_valued else step)
        candidates_for_lever = [direction * step]

        # Move-toward-smart-mean candidate (if we know where the boat sits).
        target = model.smart_means.get(lever_field, model.class_means.get(lever_field))
        if current is not None and target is not None:
            toward = target - current
            # Only keep if it moves in the TCC-reducing direction.
            if toward * raw_beta < 0:
                limit = spec.max_step_down if toward < 0 else spec.max_step_up
                if limit is not None:
                    toward = max(-limit, min(limit, toward))
                if spec.integer_valued:
                    toward = float(math.floor(toward) if toward < 0 else math.ceil(toward))
                if toward != 0:
                    candidates_for_lever.append(toward)

        seen = set()
        for d in candidates_for_lever:
            if d != 0 and d not in seen:
                seen.add(d)
                singles.append({"lever": lever_field, "delta": d})

    if not include_combinations:
        return singles

    # Greedy combinations of the top single-lever candidates.
    combos: list[dict[str, Any]] = []
    for size in range(2, max_combination_size + 1):
        for group in itertools.combinations(singles[:8], size):
            levers_in_group = [g["lever"] for g in group]
            if len(set(levers_in_group)) != len(levers_in_group):
                continue
            combos.append({
                "lever": "+".join(levers_in_group),
                "delta": None,
                "components": [
                    {"lever": g["lever"], "delta": g["delta"]} for g in group
                ],
            })
    return singles + combos


# ---------------------------------------------------------------------------
# DB-backed bridge — power the what-if simulator from a real boat
# ---------------------------------------------------------------------------


def get_what_if_model_for_boat(engine, boat_id: int) -> ClassModelContext | None:
    """Build a :class:`ClassModelContext` for a boat from the database.

    Wraps Engine 1 (``get_boat_sensitivity_context``, SM-01-02) and Engine 3d
    (smart-boat cohort means) so the what-if simulator and recommendation
    ranking run against the promoted canonical data. Returns ``None`` when
    the boat does not exist.
    """
    # Local imports keep the core estimator DB-free (pure unit tests).
    from sqlalchemy import text

    from irc_data.analysis.regression import get_boat_sensitivity_context

    query = text("""
        SELECT b.id, b.boat_name,
               COALESCE(b.design_canonical, b.design) AS design,
               t.tcc, t.lh, t.beam, t.draft, t.headsails, t.spinnakers, t.crew, t.dlr
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT * FROM tcc_snapshots WHERE boat_id = b.id
            ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        WHERE b.id = :boat_id
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"boat_id": boat_id}).first()
    if not row:
        return None

    boat_data = dict(row._mapping)
    design = boat_data.get("design")
    if not design:
        return ClassModelContext(coefficients={}, boat_name=boat_data.get("boat_name"))

    sensitivity = get_boat_sensitivity_context(engine, boat_id, design) or {}

    # Smart-boat cohort means (Engine 3d) — for candidate generation.
    smart_means: dict[str, float] = {}
    class_means: dict[str, float] = {}
    try:
        from irc_data.analysis.performance import get_smart_boats

        smart = get_smart_boats(engine, design)
        smart_means = {
            k: float(v) for k, v in (smart.get("smart_boat_means") or {}).items()
            if v is not None
        }
        class_means = {
            k: float(v) for k, v in (smart.get("class_means") or {}).items()
            if v is not None
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Smart-boat means unavailable for %s: %s", design, exc)

    model = ClassModelContext.from_sensitivity_context(sensitivity, boat_data)
    model.boat_name = boat_data.get("boat_name")
    model.design = design
    model.smart_means = smart_means
    model.class_means = class_means
    tcc = boat_data.get("tcc")
    model.current_tcc = float(tcc) if tcc is not None else model.current_tcc
    return model


def simulate_what_if(
    engine,
    boat_id: int,
    lever_deltas: dict[str, float],
    *,
    include_trial_certificate: bool = True,
) -> WhatIfEstimateV1 | None:
    """DB-backed what-if estimate for the simulator endpoint."""
    model = get_what_if_model_for_boat(engine, boat_id)
    if model is None:
        return None
    return estimate_delta_tcc(
        model,
        lever_deltas,
        include_trial_certificate=include_trial_certificate,
    )


def recommend_for_boat(
    engine,
    boat_id: int,
    *,
    top_n: int = 5,
    cost_provider=None,
) -> list[RecommendationV1] | None:
    """DB-backed ranked recommendations for a boat."""
    model = get_what_if_model_for_boat(engine, boat_id)
    if model is None:
        return None
    candidates = generate_candidate_deltas(model)
    return rank_recommendations(model, candidates, cost_provider=cost_provider, top_n=top_n)
