"""Build Facts dataclasses from DB + analysis-engine output.

Each builder is a pure function: engine + boat_id → Facts.
The orchestrator runs builders in parallel; section modules accept
the resulting Facts as input.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.analysis.regression import get_boat_sensitivity_context
from irc_data.api.services.report.facts import (
    MeasurementContribution, RatingAnatomyFacts,
)

logger = logging.getLogger(__name__)


# ── Unit scaling — must match the regression engine's `unit` strings ───

_UNIT_SCALE = {
    "per 100kg": 100.0,
    "per 0.1m": 0.1,
    "per sail": 1.0,
    "per crew": 1.0,
    "per kg": 1.0,
    "per m": 1.0,
}


def _scale_for_unit(unit: str) -> float:
    return _UNIT_SCALE.get(unit, 1.0)


# ── Rating Anatomy ─────────────────────────────────────────────────────


def build_rating_anatomy(engine: Engine, boat_id: int) -> RatingAnatomyFacts:
    """Assemble the per-measurement TCC contribution facts for one boat."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT b.boat_name, COALESCE(b.design_canonical, b.design) AS design,
                   t.tcc
            FROM boats b
            LEFT JOIN LATERAL (
                SELECT tcc FROM tcc_snapshots WHERE boat_id = b.id
                ORDER BY snapshot_date DESC LIMIT 1
            ) t ON true
            WHERE b.id = :id
        """), {"id": boat_id}).first()

    if not row or row.design is None or row.tcc is None:
        # No data — return an empty Facts payload.
        return RatingAnatomyFacts(
            boat_name=(row.boat_name if row else f"boat #{boat_id}"),
            tcc_now=Decimal("0"),
            class_mean_tcc=None, class_median_tcc=None,
            decomposition=[], explained_variance_pct=None,
            model_tier="", n_boats_in_class=0,
        )

    sens = get_boat_sensitivity_context(engine, boat_id, row.design)
    if sens is None:
        return RatingAnatomyFacts(
            boat_name=row.boat_name, tcc_now=row.tcc,
            class_mean_tcc=None, class_median_tcc=None,
            decomposition=[], explained_variance_pct=None,
            model_tier="", n_boats_in_class=0,
        )

    baseline = sens.get("class_baseline") or {}
    decomposition: list[MeasurementContribution] = []
    for coef in sens.get("coefficients", []):
        feat = coef["field"]
        pos = (sens.get("boat_position") or {}).get(feat) or {}
        if "value" not in pos or "class_mean" not in pos:
            continue
        delta_raw = pos["value"] - pos["class_mean"]
        scale = _scale_for_unit(coef.get("unit", ""))
        contrib = (delta_raw / scale) * coef["beta_per_unit"]
        decomposition.append(MeasurementContribution(
            field=feat,
            this_boat=round(pos["value"], 3),
            class_mean=round(pos["class_mean"], 3),
            delta=round(delta_raw, 3),
            contrib_tcc=round(contrib, 5),
            unit=coef.get("unit", ""),
            beta=coef["beta_per_unit"],
        ))

    # Sort by absolute impact, biggest first.
    decomposition.sort(key=lambda c: -abs(c.contrib_tcc))

    return RatingAnatomyFacts(
        boat_name=row.boat_name,
        tcc_now=row.tcc,
        class_mean_tcc=baseline.get("mean_tcc"),
        class_median_tcc=baseline.get("median_tcc"),
        decomposition=decomposition,
        explained_variance_pct=round((sens.get("r_squared") or 0) * 100, 1),
        model_tier=sens.get("model_tier", ""),
        n_boats_in_class=sens.get("n_boats") or 0,
    )
