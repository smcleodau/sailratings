"""Engine 2: IRC Formula Drift Detection.

Detect how the IRC formula itself has changed year-over-year by analysing
TCC changes for boats whose measurements remained stable. When measurements
are unchanged, any TCC change is attributable to formula evolution.

We never claim to know the formula — all findings are framed as
"consistent with" observed patterns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
from scipy import stats as scipy_stats
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.analysis.regression import (
    TIER_B_FEATURES,
    _fetch_tier_b_data_for_date,
    _fit_tier,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DriftDimension:
    field: str
    coefficient_change: float
    direction: str  # "more penalised" or "less penalised"

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "coefficient_change": round(self.coefficient_change, 5),
            "direction": self.direction,
        }


@dataclass
class FleetDrift:
    n_stable: int
    n_total: int
    mean_drift: float
    median_drift: float
    std_drift: float
    p_value_ttest: float | None
    p_value_wilcoxon: float | None
    cohens_d: float | None
    pct_decreased: float
    interpretation: str

    def to_dict(self) -> dict:
        return {
            "n_stable": self.n_stable,
            "n_total": self.n_total,
            "mean_drift": round(self.mean_drift, 5),
            "median_drift": round(self.median_drift, 5),
            "std_drift": round(self.std_drift, 5),
            "p_value_ttest": round(self.p_value_ttest, 6) if self.p_value_ttest is not None else None,
            "p_value_wilcoxon": round(self.p_value_wilcoxon, 6) if self.p_value_wilcoxon is not None else None,
            "cohens_d": round(self.cohens_d, 3) if self.cohens_d is not None else None,
            "pct_decreased": round(self.pct_decreased, 1),
            "interpretation": self.interpretation,
        }


@dataclass
class BoatDriftDecomposition:
    boat_id: int
    boat_name: str
    sail_number: str
    tcc_from: float
    tcc_to: float
    total_change: float
    formula_drift_component: float
    measurement_change_component: float
    measurement_deltas: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "boat_id": self.boat_id,
            "boat_name": self.boat_name,
            "sail_number": self.sail_number,
            "tcc_from": round(self.tcc_from, 4),
            "tcc_to": round(self.tcc_to, 4),
            "total_change": round(self.total_change, 5),
            "formula_drift_component": round(self.formula_drift_component, 5),
            "measurement_change_component": round(self.measurement_change_component, 5),
            "measurement_deltas": {k: round(v, 4) for k, v in self.measurement_deltas.items()},
        }


@dataclass
class DriftResult:
    date_from: str
    date_to: str
    fleet_wide: FleetDrift
    by_dimension: list[DriftDimension] = field(default_factory=list)
    by_country: dict[str, dict] = field(default_factory=dict)
    boat_decompositions: list[BoatDriftDecomposition] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "period": f"{self.date_from} -> {self.date_to}",
            "fleet_wide": self.fleet_wide.to_dict(),
            "by_dimension": [d.to_dict() for d in self.by_dimension],
            "by_country": self.by_country,
            "boat_decompositions": [b.to_dict() for b in self.boat_decompositions[:50]],
        }


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def _fetch_drift_data(engine: Engine) -> list[dict]:
    """Fetch all TCC drift records from materialized view, falling back to live query."""
    try:
        query = text("""
            SELECT * FROM mv_tcc_drift
            ORDER BY boat_id, date_from
        """)
        with engine.connect() as conn:
            rows = conn.execute(query).fetchall()
            if rows:
                return [dict(r._mapping) for r in rows]
    except Exception:
        pass

    # Fallback: compute live
    query = text("""
        WITH ordered_snapshots AS (
            SELECT
                t.boat_id,
                t.snapshot_date,
                t.tcc,
                t.lh, t.beam, t.draft, t.headsails, t.spinnakers,
                b.design,
                COALESCE(b.design_canonical, b.design) AS design_name,
                b.country,
                LAG(t.snapshot_date) OVER w AS prev_date,
                LAG(t.tcc) OVER w AS prev_tcc,
                LAG(t.lh) OVER w AS prev_lh,
                LAG(t.beam) OVER w AS prev_beam,
                LAG(t.draft) OVER w AS prev_draft,
                LAG(t.headsails) OVER w AS prev_headsails,
                LAG(t.spinnakers) OVER w AS prev_spinnakers
            FROM tcc_snapshots t
            JOIN boats b ON b.id = t.boat_id
            WINDOW w AS (PARTITION BY t.boat_id ORDER BY t.snapshot_date)
        )
        SELECT
            boat_id,
            prev_date AS date_from,
            snapshot_date AS date_to,
            prev_tcc AS tcc_from,
            tcc AS tcc_to,
            (tcc - prev_tcc) AS tcc_delta,
            design_name,
            country,
            (COALESCE(lh = prev_lh, true)
             AND COALESCE(beam = prev_beam, true)
             AND COALESCE(draft = prev_draft, true)
             AND COALESCE(headsails = prev_headsails, true)
             AND COALESCE(spinnakers = prev_spinnakers, true)
            ) AS measurements_stable,
            (lh - prev_lh) AS delta_lh,
            (beam - prev_beam) AS delta_beam,
            (draft - prev_draft) AS delta_draft,
            (headsails - prev_headsails) AS delta_headsails,
            (spinnakers - prev_spinnakers) AS delta_spinnakers
        FROM ordered_snapshots
        WHERE prev_tcc IS NOT NULL
        ORDER BY boat_id, prev_date
    """)
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
        return [dict(r._mapping) for r in rows]


def _fetch_snapshot_dates(engine: Engine) -> list[str]:
    """Get distinct snapshot dates ordered by date."""
    query = text("""
        SELECT DISTINCT snapshot_date
        FROM tcc_snapshots
        ORDER BY snapshot_date
    """)
    with engine.connect() as conn:
        return [str(row[0]) for row in conn.execute(query)]


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------


def _compute_fleet_drift(deltas: np.ndarray) -> FleetDrift:
    """Compute fleet-wide drift statistics from an array of TCC changes."""
    n = len(deltas)
    mean_drift = float(np.mean(deltas))
    median_drift = float(np.median(deltas))
    std_drift = float(np.std(deltas, ddof=1)) if n > 1 else 0.0

    # One-sample t-test: is mean drift significantly different from 0?
    p_ttest = None
    if n >= 3:
        t_stat, p_ttest = scipy_stats.ttest_1samp(deltas, 0)
        p_ttest = float(p_ttest)

    # Wilcoxon signed-rank (non-parametric)
    p_wilcoxon = None
    if n >= 6:
        try:
            stat, p_wilcoxon = scipy_stats.wilcoxon(deltas)
            p_wilcoxon = float(p_wilcoxon)
        except ValueError:
            pass  # All values identical

    # Cohen's d
    cohens_d = None
    if std_drift > 0:
        cohens_d = mean_drift / std_drift

    pct_decreased = float(np.sum(deltas < 0) / n * 100) if n > 0 else 0.0

    # Interpretation
    if abs(mean_drift) < 0.001:
        interpretation = "No significant fleet-wide formula drift detected."
    elif mean_drift < 0:
        interpretation = (
            f"The IRC formula has reduced ratings by ~{abs(mean_drift):.3f} across stable boats "
            f"({pct_decreased:.0f}% decreased). "
        )
    else:
        interpretation = (
            f"The IRC formula has increased ratings by ~{mean_drift:.3f} across stable boats "
            f"({100-pct_decreased:.0f}% increased). "
        )

    if p_ttest is not None:
        if p_ttest < 0.001:
            interpretation += "This is highly statistically significant (p<0.001)."
        elif p_ttest < 0.05:
            interpretation += f"This is statistically significant (p={p_ttest:.4f})."
        else:
            interpretation += f"This is not statistically significant (p={p_ttest:.3f})."

    return FleetDrift(
        n_stable=n,
        n_total=n,
        mean_drift=mean_drift,
        median_drift=median_drift,
        std_drift=std_drift,
        p_value_ttest=p_ttest,
        p_value_wilcoxon=p_wilcoxon,
        cohens_d=cohens_d,
        pct_decreased=pct_decreased,
        interpretation=interpretation,
    )


def _compute_dimensional_decomposition(
    engine: Engine,
    date_from: str,
    date_to: str,
) -> list[DriftDimension]:
    """Compare Tier B regression coefficients between two time periods.

    The difference in coefficients reveals what the formula changed.
    """
    # Get all designs that appear in both snapshots
    query = text("""
        SELECT COALESCE(b.design_canonical, b.design) AS design_name
        FROM boats b
        JOIN tcc_snapshots t1 ON t1.boat_id = b.id AND t1.snapshot_date = :d1
        JOIN tcc_snapshots t2 ON t2.boat_id = b.id AND t2.snapshot_date = :d2
        WHERE COALESCE(b.design_canonical, b.design) IS NOT NULL
        GROUP BY COALESCE(b.design_canonical, b.design)
        HAVING COUNT(*) >= 10
    """)

    # Instead of per-design (which may have too few boats per date),
    # run a fleet-wide Tier B regression on each date's data
    from irc_data.analysis.regression import _fetch_tier_c_data, _prepare_matrix, _run_ridge, TIER_B_FEATURES

    # Fetch all boats at each date
    data_from = _fetch_all_at_date(engine, date_from)
    data_to = _fetch_all_at_date(engine, date_to)

    if len(data_from) < 20 or len(data_to) < 20:
        return []

    features = TIER_B_FEATURES

    X1, y1, feats1, _ = _prepare_matrix(data_from, features)
    X2, y2, feats2, _ = _prepare_matrix(data_to, features)

    if len(y1) < 10 or len(y2) < 10:
        return []

    # Use common features
    common = [f for f in feats1 if f in feats2]
    if not common:
        return []

    # Re-prepare with common features
    X1, y1, _, _ = _prepare_matrix(data_from, common)
    X2, y2, _, _ = _prepare_matrix(data_to, common)

    if len(y1) < 10 or len(y2) < 10:
        return []

    model1, scaler1, _ = _run_ridge(X1, y1, common, len(y1))
    model2, scaler2, _ = _run_ridge(X2, y2, common, len(y2))

    # Compare standardised coefficients
    dimensions = []
    for i, feat in enumerate(common):
        beta1 = model1.coef_[i]
        beta2 = model2.coef_[i]
        change = beta2 - beta1

        if abs(change) > 0.001:  # Only report meaningful changes
            direction = "more penalised" if change > 0 else "less penalised"
            dimensions.append(DriftDimension(
                field=feat,
                coefficient_change=float(change),
                direction=direction,
            ))

    # Sort by absolute change
    dimensions.sort(key=lambda d: abs(d.coefficient_change), reverse=True)
    return dimensions


def _fetch_all_at_date(engine: Engine, snapshot_date: str) -> list[dict]:
    """Fetch all boats' TCC and measurements at a specific snapshot date."""
    query = text("""
        SELECT
            b.id, b.boat_name, b.sail_number,
            COALESCE(b.design_canonical, b.design) AS design_name,
            t.tcc, t.lh, t.beam, t.draft,
            t.headsails, t.spinnakers, t.crew, t.dlr
        FROM boats b
        JOIN tcc_snapshots t ON t.boat_id = b.id AND t.snapshot_date = :snap_date
        WHERE t.tcc IS NOT NULL
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"snap_date": snapshot_date}).fetchall()
        return [dict(r._mapping) for r in rows]


def _compute_boat_decompositions(
    drift_data: list[dict],
    fleet_mean_drift: float,
) -> list[BoatDriftDecomposition]:
    """Decompose TCC changes into formula drift vs measurement change components."""
    decompositions = []

    # Get Tier B regression coefficients from the fleet-wide stable boat drift
    # For simplicity, use the fleet mean drift as the formula component for stable boats
    # and attribute the remainder to measurement changes

    for row in drift_data:
        if row.get("measurements_stable"):
            continue  # Already accounted for in fleet drift

        tcc_from = float(row["tcc_from"]) if row.get("tcc_from") is not None else None
        tcc_to = float(row["tcc_to"]) if row.get("tcc_to") is not None else None
        if tcc_from is None or tcc_to is None:
            continue

        total_change = tcc_to - tcc_from

        # Collect measurement deltas
        measurement_deltas = {}
        for field in ["lh", "beam", "draft", "headsails", "spinnakers"]:
            delta = row.get(f"delta_{field}")
            if delta is not None and float(delta) != 0:
                measurement_deltas[field] = float(delta)

        # Formula drift component = fleet mean drift for stable boats
        formula_component = fleet_mean_drift
        measurement_component = total_change - formula_component

        decompositions.append(BoatDriftDecomposition(
            boat_id=row["boat_id"],
            boat_name=row.get("boat_name", ""),
            sail_number=row.get("sail_number", ""),
            tcc_from=tcc_from,
            tcc_to=tcc_to,
            total_change=total_change,
            formula_drift_component=formula_component,
            measurement_change_component=measurement_component,
            measurement_deltas=measurement_deltas,
        ))

    return decompositions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_fleet_drift(engine: Engine, design: str | None = None) -> DriftResult | None:
    """Analyse IRC formula drift across available time periods.

    If design is specified, restricts to that class only.
    """
    drift_data = _fetch_drift_data(engine)
    if not drift_data:
        return None

    # Filter by design if specified
    if design:
        drift_data = [d for d in drift_data if d.get("design_name") == design]
        if not drift_data:
            return None

    # Determine date range
    dates_from = sorted(set(str(d["date_from"]) for d in drift_data if d.get("date_from")))
    dates_to = sorted(set(str(d["date_to"]) for d in drift_data if d.get("date_to")))

    if not dates_from or not dates_to:
        return None

    date_from = dates_from[0]
    date_to = dates_to[-1]

    # Step 1: Stable boat detection — boats whose measurements didn't change
    stable = [d for d in drift_data if d.get("measurements_stable")]
    all_boats = drift_data

    if not stable:
        # No stable boats found, use all data with caveat
        stable_deltas = np.array([float(d["tcc_delta"]) for d in drift_data if d.get("tcc_delta") is not None])
        if len(stable_deltas) == 0:
            return None
        fleet_drift = _compute_fleet_drift(stable_deltas)
        fleet_drift.interpretation = "[No measurement-stable boats found] " + fleet_drift.interpretation
    else:
        stable_deltas = np.array([float(d["tcc_delta"]) for d in stable if d.get("tcc_delta") is not None])
        if len(stable_deltas) == 0:
            return None
        fleet_drift = _compute_fleet_drift(stable_deltas)
        fleet_drift.n_total = len(drift_data)

    # Step 3: Dimensional decomposition
    dimensions = _compute_dimensional_decomposition(engine, date_from, date_to)

    # Step 4: Per-boat decomposition
    boat_decomps = _compute_boat_decompositions(drift_data, fleet_drift.mean_drift)

    # Enrich decomposition with boat names
    boat_names = {}
    query = text("SELECT id, boat_name, sail_number FROM boats")
    with engine.connect() as conn:
        for row in conn.execute(query):
            boat_names[row.id] = (row.boat_name, row.sail_number)

    for bd in boat_decomps:
        if bd.boat_id in boat_names:
            bd.boat_name, bd.sail_number = boat_names[bd.boat_id]

    # Step 5: Country-level drift
    by_country = {}
    country_groups: dict[str, list[float]] = {}
    for d in stable:
        country = d.get("country")
        delta = d.get("tcc_delta")
        if country and delta is not None:
            country_groups.setdefault(country, []).append(float(delta))

    for country, deltas in sorted(country_groups.items()):
        arr = np.array(deltas)
        by_country[country] = {
            "n_stable": len(arr),
            "mean_drift": round(float(np.mean(arr)), 5),
            "median_drift": round(float(np.median(arr)), 5),
            "pct_decreased": round(float(np.sum(arr < 0) / len(arr) * 100), 1),
        }

    return DriftResult(
        date_from=date_from,
        date_to=date_to,
        fleet_wide=fleet_drift,
        by_dimension=dimensions,
        by_country=by_country,
        boat_decompositions=boat_decomps,
    )


def get_design_drift(engine: Engine, design: str) -> dict | None:
    """Get drift analysis filtered to a specific design class."""
    result = analyze_fleet_drift(engine, design=design)
    if result:
        return result.to_dict()
    return None
