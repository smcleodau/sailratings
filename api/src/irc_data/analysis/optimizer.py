"""Engine 4: Optimisation Recommender.

For a specific boat, synthesise all analysis engines into ranked, actionable
recommendations. Each recommendation includes estimated TCC impact, feasibility,
and empirical evidence strength.

Categories (matching business plan report structure):
- Admin/config: declared headsails/spinnakers, crew number
- Sail/inventory: headsail dimensions, spinnaker size
- Hardware/structural: keel modification, weight reduction, draft change
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.analysis.performance import compute_rai, get_smart_boats
from irc_data.analysis.regression import (
    CoefficientResult,
    analyze_design_sensitivity,
    get_boat_sensitivity_context,
)
from irc_data.analysis.temporal import analyze_fleet_drift

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Feasibility ratings (lower = easier to change)
FEASIBILITY = {
    "headsails": 1,       # Admin — just declare fewer/more
    "spinnakers": 1,      # Admin
    "crew": 1,            # Admin
    "dlr": 2,             # May require rule interpretation
    "hlu": 3,             # New headsail purchase
    "hlp": 3,
    "sym_slu": 3,         # New spinnaker
    "sym_sf": 3,
    "stl": 3,             # New spinnaker pole / bowsprit
    "e": 4,               # Boom modification
    "p": 4,               # Mast modification (major)
    "j": 4,               # Forestay / bowsprit
    "muw": 5,             # Mast weight — new mast
    "mhw": 5,
    "displacement": 6,    # Weight reduction — expensive
    "draft": 7,           # Keel modification — major
    "lh": 8,              # Hull length — effectively impossible
    "beam": 8,            # Beam — effectively impossible
}

FEASIBILITY_LABELS = {
    1: "Admin/config (no cost)",
    2: "Minor config change",
    3: "Sail purchase ($)",
    4: "Rig modification ($$)",
    5: "Major rig work ($$$)",
    6: "Structural — weight ($$$$)",
    7: "Keel/draft modification ($$$$)",
    8: "Not practically changeable",
}

CATEGORY_MAP = {
    1: "admin",
    2: "admin",
    3: "sail",
    4: "sail",
    5: "sail",
    6: "hardware",
    7: "hardware",
    8: "hardware",
}


@dataclass
class Recommendation:
    field: str
    category: str  # admin, sail, hardware
    current_value: float | None
    class_mean: float | None
    smart_boat_avg: float | None
    optimal_direction: str  # "increase" or "decrease"
    estimated_tcc_delta: float
    feasibility: int  # 1-8
    feasibility_label: str
    evidence_strength: str  # "strong", "moderate", "limited"
    explanation: str
    seconds_saved_per_hour: float = 0.0
    return_on_rating: float = 0.0
    return_on_rating_text: str = ""
    rank: int = 0

    def to_dict(self) -> dict:
        result = {
            "field": self.field,
            "category": self.category,
            "current_value": round(self.current_value, 3) if self.current_value is not None else None,
            "class_mean": round(self.class_mean, 3) if self.class_mean is not None else None,
            "smart_boat_avg": round(self.smart_boat_avg, 3) if self.smart_boat_avg is not None else None,
            "optimal_direction": self.optimal_direction,
            "estimated_tcc_delta": round(self.estimated_tcc_delta, 5),
            "feasibility": self.feasibility,
            "feasibility_label": self.feasibility_label,
            "evidence_strength": self.evidence_strength,
            "explanation": self.explanation,
            "seconds_saved_per_hour": self.seconds_saved_per_hour,
            "return_on_rating": self.return_on_rating,
            "return_on_rating_text": self.return_on_rating_text,
            "rank": self.rank,
        }
        return result


@dataclass
class OptimisationReport:
    boat_id: int
    boat_name: str
    sail_number: str
    design: str | None
    current_tcc: float | None
    model_tier: str | None
    r_squared: float | None
    recommendations: list[Recommendation] = field(default_factory=list)
    rai: float | None = None
    drift_context: str | None = None
    orc_context: dict | None = None

    def to_dict(self) -> dict:
        return {
            "boat_id": self.boat_id,
            "boat_name": self.boat_name,
            "sail_number": self.sail_number,
            "design": self.design,
            "current_tcc": round(self.current_tcc, 4) if self.current_tcc is not None else None,
            "model_tier": self.model_tier,
            "r_squared": round(self.r_squared, 4) if self.r_squared is not None else None,
            "rai": round(self.rai, 2) if self.rai is not None else None,
            "drift_context": self.drift_context,
            "orc_context": self.orc_context,
            "recommendations": [r.to_dict() for r in self.recommendations],
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_optimisation_report(engine: Engine, boat_id: int) -> OptimisationReport | None:
    """Generate a full optimisation report for a specific boat.

    Synthesises:
    - Engine 1: measurement sensitivity coefficients
    - Engine 2: formula drift trends
    - Engine 3: racing performance (RAI, smart boat profiles)
    - ORC cross-reference (if available)
    """
    # Fetch boat identity
    query = text("""
        SELECT b.id, b.boat_name, b.sail_number,
               COALESCE(b.design_canonical, b.design) AS design,
               t.tcc, t.lh, t.beam, t.draft, t.headsails, t.spinnakers, t.crew, t.dlr
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT * FROM tcc_snapshots WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        WHERE b.id = :boat_id
    """)

    with engine.connect() as conn:
        boat = conn.execute(query, {"boat_id": boat_id}).first()

    if not boat:
        return None

    boat_data = dict(boat._mapping)
    design = boat_data.get("design")

    report = OptimisationReport(
        boat_id=boat_id,
        boat_name=boat_data["boat_name"],
        sail_number=boat_data["sail_number"],
        design=design,
        current_tcc=float(boat_data["tcc"]) if boat_data.get("tcc") else None,
        model_tier=None,
        r_squared=None,
    )

    if not design:
        report.recommendations = []
        return report

    # --- Engine 1: Sensitivity ---
    sensitivity = get_boat_sensitivity_context(engine, boat_id, design)

    model_tier = None
    r_squared = None
    coefficients = []
    boat_position = {}

    if sensitivity:
        model_tier = sensitivity.get("model_tier")
        r_squared = sensitivity.get("r_squared")
        coefficients = sensitivity.get("coefficients", [])
        boat_position = sensitivity.get("boat_position", {})
        report.model_tier = model_tier
        report.r_squared = r_squared

    # --- Engine 3: RAI ---
    try:
        rai_result = compute_rai(engine, boat_id)
        if rai_result:
            report.rai = rai_result.rai
    except Exception as e:
        logger.warning(f"RAI computation failed for boat {boat_id}: {e}")

    # --- Engine 3d: Smart boats ---
    smart_data = {}
    try:
        smart_data = get_smart_boats(engine, design)
    except Exception as e:
        logger.warning(f"Smart boat analysis failed for {design}: {e}")

    smart_means = smart_data.get("smart_boat_means", {})
    class_means = smart_data.get("class_means", {})

    # --- Engine 2: Drift context ---
    try:
        drift = analyze_fleet_drift(engine, design=design)
        if drift:
            fw = drift.fleet_wide
            report.drift_context = (
                f"IRC formula shifted {fw.mean_drift:+.4f} for {design} "
                f"({fw.n_stable} stable boats, "
                f"{'p<0.001' if fw.p_value_ttest and fw.p_value_ttest < 0.001 else f'p={fw.p_value_ttest:.3f}' if fw.p_value_ttest else 'p=N/A'})."
            )
    except Exception as e:
        logger.warning(f"Drift analysis failed for {design}: {e}")

    # --- ORC cross-reference ---
    orc_ctx = _fetch_orc_context(engine, boat_id)
    if orc_ctx:
        report.orc_context = orc_ctx

    # --- Build recommendations ---
    recommendations = _build_recommendations(
        coefficients, boat_data, boat_position, class_means, smart_means, model_tier,
    )

    # Rank by composite score: TCC impact × feasibility × evidence
    _rank_recommendations(recommendations)
    report.recommendations = recommendations

    return report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_recommendations(
    coefficients: list[dict],
    boat_data: dict,
    boat_position: dict,
    class_means: dict,
    smart_means: dict,
    model_tier: str | None,
) -> list[Recommendation]:
    """Build recommendations from sensitivity coefficients and fleet positioning."""
    recommendations = []

    for coef in coefficients:
        field = coef["field"]
        std_beta = coef.get("std_beta", 0)
        beta_per_unit = coef.get("beta_per_unit", 0)

        current_val = None
        pos = boat_position.get(field, {})
        if pos:
            current_val = pos.get("value")

        mean_val = class_means.get(field)
        smart_val = smart_means.get(field)

        if current_val is None:
            continue

        # Determine optimal direction
        # If beta is positive, reducing the value reduces TCC (good)
        # If beta is negative, increasing the value reduces TCC (good)
        if std_beta > 0:
            optimal_dir = "decrease"
        else:
            optimal_dir = "increase"

        # Estimate TCC delta: how much TCC would change if moving toward smart boat avg
        target = smart_val if smart_val is not None else mean_val
        if target is None:
            continue

        delta_measurement = target - current_val
        # beta_per_unit is already scaled to human units — we need raw beta
        # Approximate: estimated_tcc_delta = delta_measurement × raw_beta
        # raw_beta ≈ beta_per_unit / SCALE_FACTOR
        from irc_data.analysis.regression import SCALE_FACTORS
        scale = SCALE_FACTORS.get(field, 1.0)
        raw_beta = beta_per_unit / scale if scale != 0 else 0
        estimated_tcc = delta_measurement * raw_beta

        # Skip if already near optimal or no meaningful improvement
        if abs(estimated_tcc) < 0.0005:
            continue

        # Feasibility
        feas = FEASIBILITY.get(field, 5)
        feas_label = FEASIBILITY_LABELS.get(feas, "Unknown")
        category = CATEGORY_MAP.get(feas, "hardware")

        # Evidence strength
        if model_tier == "A":
            evidence = "strong" if abs(std_beta) > 0.3 else "moderate"
        elif model_tier == "B":
            evidence = "moderate" if abs(std_beta) > 0.3 else "limited"
        else:
            evidence = "limited"

        # Seconds saved per hour: 1 TCC point (0.001) = ~3.6s per hour
        seconds_saved_hr = round(abs(estimated_tcc / 0.001) * 3.6, 1)

        # Proxy cost per feasibility level ($)
        cost_proxy = 0.0
        if feas in (1, 2):
            cost_proxy = 0.0
        elif feas == 3:
            cost_proxy = 2500.0
        elif feas == 4:
            cost_proxy = 4000.0
        elif feas == 5:
            cost_proxy = 10000.0
        elif feas == 6:
            cost_proxy = 15000.0
        elif feas == 7:
            cost_proxy = 25000.0

        if cost_proxy > 0:
            ror = round(seconds_saved_hr / (cost_proxy / 1000.0), 2)
            ror_text = f"{ror}s saved / $1,000"
        else:
            ror = 999.0
            ror_text = "Immediate / High Value ($0 admin cost)"

        # Explanation
        direction_word = "reducing" if optimal_dir == "decrease" else "increasing"
        explanation = (
            f"Consider {direction_word} {field}: "
            f"your {current_val:.2f} vs class avg {mean_val:.2f}"
        )
        if smart_val is not None:
            explanation += f" (top performers avg {smart_val:.2f})"
        explanation += f". Est. TCC impact: {estimated_tcc:+.4f} ({seconds_saved_hr}s/hr saved)."

        recommendations.append(Recommendation(
            field=field,
            category=category,
            current_value=current_val,
            class_mean=mean_val,
            smart_boat_avg=smart_val,
            optimal_direction=optimal_dir,
            estimated_tcc_delta=estimated_tcc,
            feasibility=feas,
            feasibility_label=feas_label,
            evidence_strength=evidence,
            explanation=explanation,
            seconds_saved_per_hour=seconds_saved_hr,
            return_on_rating=ror,
            return_on_rating_text=ror_text,
        ))

    return recommendations


def _rank_recommendations(recommendations: list[Recommendation]) -> None:
    """Rank recommendations by composite score: impact × feasibility × evidence."""
    evidence_weights = {"strong": 1.0, "moderate": 0.7, "limited": 0.4}

    for rec in recommendations:
        # Composite score: larger TCC reduction is better (negative estimated_tcc = TCC reduction)
        tcc_score = abs(rec.estimated_tcc_delta)
        feas_score = (9 - rec.feasibility) / 8.0  # 1.0 for easiest, 0.125 for hardest
        ev_score = evidence_weights.get(rec.evidence_strength, 0.5)
        rec._composite_score = tcc_score * feas_score * ev_score

    recommendations.sort(key=lambda r: r._composite_score, reverse=True)

    for i, rec in enumerate(recommendations, 1):
        rec.rank = i
        del rec._composite_score  # Clean up


def _fetch_orc_context(engine: Engine, boat_id: int) -> dict | None:
    """Fetch ORC data for cross-reference if available."""
    query = text("""
        SELECT
            gph, osn, cdl, triple_low, triple_med, triple_high,
            loa, displacement, draft,
            sail_area_upwind, sail_area_downwind,
            class_name, ref_no
        FROM orc_certificates
        WHERE boat_id = :boat_id
        ORDER BY snapshot_date DESC
        LIMIT 1
    """)

    with engine.connect() as conn:
        row = conn.execute(query, {"boat_id": boat_id}).first()

    if not row:
        return None

    data = dict(row._mapping)
    # Convert Decimals
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in data.items() if v is not None}
