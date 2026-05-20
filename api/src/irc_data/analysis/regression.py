"""Engine 1: Within-class measurement sensitivity analysis.

For any design class, quantify how each measurement affects TCC using Ridge
regression. Three tiers of model depending on data availability:

- Tier A: Full certificate model (15+ features from parsed PDFs)
- Tier B: Fleet model (snapshot fields available for all boats)
- Tier C: Country/fleet-wide model (all boats, design as categorical)

The IRC formula is SECRET — we never claim to know it. All coefficients are
framed as "consistent with" not "caused by".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
from scipy import stats as scipy_stats
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature definitions
# ---------------------------------------------------------------------------

TIER_A_FEATURES = [
    "displacement", "draft", "lh", "p", "e", "j",
    "hlu", "hlp", "muw", "mhw", "stl",
    "sym_slu", "sym_sf",
    "headsails", "spinnakers",
]

TIER_B_FEATURES = [
    "lh", "beam", "draft", "headsails", "spinnakers", "crew", "dlr",
]

TIER_C_FEATURES = TIER_B_FEATURES  # same features, but across all designs

MIN_BOATS_FOR_REGRESSION = 5
MIN_BOATS_TIER_A = 5  # with certs
MIN_BOATS_FULL_CV = 15

# RidgeCV alphas — log-spaced from 0.01 to 100
RIDGE_ALPHAS = np.logspace(-2, 2, 20)


# ---------------------------------------------------------------------------
# Data classes for results
# ---------------------------------------------------------------------------


@dataclass
class CoefficientResult:
    field: str
    beta_per_unit: float
    unit: str
    std_beta: float  # standardised coefficient
    rank: int = 0

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "beta_per_unit": round(self.beta_per_unit, 6),
            "unit": self.unit,
            "std_beta": round(self.std_beta, 4),
            "rank": self.rank,
        }


@dataclass
class RegressionResult:
    design: str
    model_tier: str  # "A", "B", "C"
    n_boats: int
    r_squared: float
    r_squared_cv: float | None = None
    alpha: float | None = None
    coefficients: list[CoefficientResult] = field(default_factory=list)
    collinearity_warnings: list[str] = field(default_factory=list)
    interpretation: str = ""

    def to_dict(self) -> dict:
        return {
            "design": self.design,
            "model_tier": self.model_tier,
            "n_boats": self.n_boats,
            "r_squared": round(self.r_squared, 4),
            "r_squared_cv": round(self.r_squared_cv, 4) if self.r_squared_cv is not None else None,
            "alpha": round(self.alpha, 4) if self.alpha is not None else None,
            "coefficients": [c.to_dict() for c in self.coefficients],
            "collinearity_warnings": self.collinearity_warnings,
            "interpretation": self.interpretation,
        }


@dataclass
class CorrelationResult:
    """For classes with < MIN_BOATS_FOR_REGRESSION boats."""
    design: str
    n_boats: int
    correlations: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "design": self.design,
            "n_boats": self.n_boats,
            "model_tier": "correlation_only",
            "correlations": {k: round(v, 4) for k, v in self.correlations.items()},
        }


# ---------------------------------------------------------------------------
# Unit descriptions
# ---------------------------------------------------------------------------

UNITS = {
    "displacement": "per 100kg",
    "draft": "per 0.1m",
    "lh": "per 0.1m",
    "beam": "per 0.1m",
    "p": "per 0.1m",
    "e": "per 0.1m",
    "j": "per 0.1m",
    "hlu": "per 0.1m",
    "hlp": "per 0.1m",
    "muw": "per 0.1m",
    "mhw": "per 0.1m",
    "stl": "per 0.1m",
    "sym_slu": "per 0.1m",
    "sym_sf": "per 0.1m",
    "headsails": "per sail",
    "spinnakers": "per sail",
    "crew": "per person",
    "dlr": "per unit",
}

# Scale factors for "per unit" interpretation
SCALE_FACTORS = {
    "displacement": 100.0,  # per 100kg
    "draft": 0.1,
    "lh": 0.1,
    "beam": 0.1,
    "p": 0.1,
    "e": 0.1,
    "j": 0.1,
    "hlu": 0.1,
    "hlp": 0.1,
    "muw": 0.1,
    "mhw": 0.1,
    "stl": 0.1,
    "sym_slu": 0.1,
    "sym_sf": 0.1,
    "headsails": 1.0,
    "spinnakers": 1.0,
    "crew": 1.0,
    "dlr": 1.0,
}


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def _fetch_tier_a_data(engine: Engine, design: str) -> list[dict]:
    """Fetch boats with full certificate data for a design class."""
    query = text("""
        SELECT
            b.id, b.boat_name, b.sail_number,
            t.tcc,
            c.displacement_kg AS displacement, c.draft, c.lh, c.p, c.e, c.j,
            c.hlu, c.hlp, c.muw, c.mhw, c.stl,
            c.sym_slu, c.sym_sf,
            t.headsails, t.spinnakers
        FROM boats b
        JOIN LATERAL (
            SELECT * FROM tcc_snapshots
            WHERE boat_id = b.id
            ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        JOIN LATERAL (
            SELECT * FROM irc_certificates
            WHERE boat_id = b.id
            ORDER BY issue_date DESC NULLS LAST LIMIT 1
        ) c ON true
        WHERE COALESCE(b.design_canonical, b.design) = :design
          AND t.tcc IS NOT NULL
          AND c.id IS NOT NULL
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"design": design}).fetchall()
        return [dict(r._mapping) for r in rows]


def _fetch_tier_b_data(engine: Engine, design: str) -> list[dict]:
    """Fetch boats with TCC snapshot data for a design class."""
    query = text("""
        SELECT
            b.id, b.boat_name, b.sail_number,
            t.tcc, t.lh, t.beam, t.draft,
            t.headsails, t.spinnakers, t.crew, t.dlr
        FROM boats b
        JOIN LATERAL (
            SELECT * FROM tcc_snapshots
            WHERE boat_id = b.id
            ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        WHERE COALESCE(b.design_canonical, b.design) = :design
          AND t.tcc IS NOT NULL
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"design": design}).fetchall()
        return [dict(r._mapping) for r in rows]


def _fetch_tier_c_data(engine: Engine) -> list[dict]:
    """Fetch all boats with TCC snapshot data for fleet-wide model."""
    query = text("""
        SELECT
            b.id, b.boat_name, b.sail_number,
            COALESCE(b.design_canonical, b.design) AS design_name,
            t.tcc, t.lh, t.beam, t.draft,
            t.headsails, t.spinnakers, t.crew, t.dlr
        FROM boats b
        JOIN LATERAL (
            SELECT * FROM tcc_snapshots
            WHERE boat_id = b.id
            ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        WHERE t.tcc IS NOT NULL
    """)
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
        return [dict(r._mapping) for r in rows]


def _fetch_tier_b_data_for_date(engine: Engine, design: str, snapshot_date: str) -> list[dict]:
    """Fetch boats with TCC snapshot data at a specific date (for temporal analysis)."""
    query = text("""
        SELECT
            b.id, b.boat_name, b.sail_number,
            t.tcc, t.lh, t.beam, t.draft,
            t.headsails, t.spinnakers, t.crew, t.dlr
        FROM boats b
        JOIN tcc_snapshots t ON t.boat_id = b.id AND t.snapshot_date = :snap_date
        WHERE COALESCE(b.design_canonical, b.design) = :design
          AND t.tcc IS NOT NULL
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"design": design, "snap_date": snapshot_date}).fetchall()
        return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# Core regression logic
# ---------------------------------------------------------------------------


def _prepare_matrix(rows: list[dict], features: list[str]) -> tuple[np.ndarray, np.ndarray, list[str], list[int]]:
    """Build X, y matrices from row data, handling missing values.

    Returns (X, y, used_features, valid_row_indices).
    Drops features where >50% of values are missing.
    Drops rows where any remaining feature is missing.
    """
    n = len(rows)
    if n == 0:
        return np.array([]), np.array([]), [], []

    # First pass: check feature coverage
    used_features = []
    for feat in features:
        non_null = sum(1 for r in rows if r.get(feat) is not None)
        if non_null / n >= 0.5:
            used_features.append(feat)

    if not used_features:
        return np.array([]), np.array([]), [], []

    # Second pass: build matrix, dropping rows with any missing value
    valid_indices = []
    X_list = []
    y_list = []

    for i, row in enumerate(rows):
        vals = []
        skip = False
        for feat in used_features:
            v = row.get(feat)
            if v is None:
                skip = True
                break
            vals.append(float(v) if isinstance(v, Decimal) else float(v))
        if skip:
            continue

        tcc = row.get("tcc")
        if tcc is None:
            continue

        X_list.append(vals)
        y_list.append(float(tcc) if isinstance(tcc, Decimal) else float(tcc))
        valid_indices.append(i)

    if not X_list:
        return np.array([]), np.array([]), [], []

    return np.array(X_list), np.array(y_list), used_features, valid_indices


def _check_collinearity(X: np.ndarray, features: list[str], threshold: float = 0.9) -> list[str]:
    """Check for high pairwise correlations that might affect interpretation."""
    warnings = []
    if X.shape[0] < 3 or X.shape[1] < 2:
        return warnings

    with np.errstate(divide="ignore", invalid="ignore"):
        corr = np.corrcoef(X.T)

    for i in range(len(features)):
        for j in range(i + 1, len(features)):
            r = corr[i, j]
            if np.isfinite(r) and abs(r) > threshold:
                warnings.append(
                    f"{features[i]} ↔ {features[j]}: r={r:.2f} — interpret individual coefficients with caution"
                )
    return warnings


def _run_ridge(
    X: np.ndarray,
    y: np.ndarray,
    features: list[str],
    n_boats: int,
) -> tuple[RidgeCV, StandardScaler, float | None]:
    """Fit RidgeCV with standardised features.

    Returns (model, scaler, cv_r2).
    Uses leave-one-out CV for small samples, 5-fold for larger.
    """
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    cv = min(n_boats, 5) if n_boats >= MIN_BOATS_FULL_CV else n_boats  # LOO for small

    model = RidgeCV(alphas=RIDGE_ALPHAS, cv=cv, scoring="r2")
    model.fit(X_scaled, y)

    # Cross-validated R² — use the best score from RidgeCV
    cv_r2 = float(model.best_score_) if hasattr(model, "best_score_") else None

    return model, scaler, cv_r2


def _build_coefficients(
    model: RidgeCV,
    scaler: StandardScaler,
    features: list[str],
) -> list[CoefficientResult]:
    """Extract and rank coefficients from fitted model."""
    coefs = []
    std_betas = model.coef_  # standardised since we fit on scaled X

    for i, feat in enumerate(features):
        # Convert back to original scale
        scale = scaler.scale_[i]
        raw_beta = model.coef_[i] / scale if scale > 0 else 0.0

        # Scale for human-readable units
        unit_scale = SCALE_FACTORS.get(feat, 1.0)
        beta_per_unit = raw_beta * unit_scale

        coefs.append(CoefficientResult(
            field=feat,
            beta_per_unit=beta_per_unit,
            unit=UNITS.get(feat, "per unit"),
            std_beta=float(std_betas[i]),
        ))

    # Rank by absolute standardised beta
    coefs.sort(key=lambda c: abs(c.std_beta), reverse=True)
    for rank, c in enumerate(coefs, 1):
        c.rank = rank

    return coefs


def _generate_interpretation(coefs: list[CoefficientResult], design: str, tier: str) -> str:
    """Generate a human-readable interpretation of the top coefficients."""
    if not coefs:
        return "Insufficient data for interpretation."

    top = coefs[0]
    direction = "increases" if top.std_beta > 0 else "decreases"
    parts = [
        f"{top.field.replace('_', ' ').title()} is the strongest TCC lever in the {design} class"
        f" — more {top.field.replace('_', ' ')} {direction} TCC."
    ]

    if len(coefs) >= 2:
        second = coefs[1]
        parts.append(
            f" {second.field.replace('_', ' ').title()} ranks second"
            f" (std beta={second.std_beta:+.3f})."
        )

    if tier == "B":
        parts.append(" (Based on snapshot fields only — full certificate data would refine this.)")

    return "".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_design_sensitivity(
    engine: Engine,
    design: str,
) -> RegressionResult | CorrelationResult | None:
    """Run within-class measurement sensitivity for a single design.

    Automatically selects the best available tier:
    - Tier A if 5+ boats have full certificate data
    - Tier B otherwise (snapshot fields only)
    - Returns CorrelationResult if < 5 boats total
    - Returns None if < 2 boats
    """
    # Try Tier A first
    tier_a_data = _fetch_tier_a_data(engine, design)
    tier_b_data = _fetch_tier_b_data(engine, design)

    # Determine total boats available
    n_total = len(tier_b_data)
    if n_total < 2:
        return None

    # For < 5 boats, return correlations only
    if n_total < MIN_BOATS_FOR_REGRESSION:
        return _correlation_only(tier_b_data, design)

    results = []

    # Try Tier A
    if len(tier_a_data) >= MIN_BOATS_TIER_A:
        result = _fit_tier(tier_a_data, TIER_A_FEATURES, design, "A")
        if result:
            results.append(result)

    # Always run Tier B
    result = _fit_tier(tier_b_data, TIER_B_FEATURES, design, "B")
    if result:
        results.append(result)

    # Return best result (Tier A if available and decent, else Tier B)
    if not results:
        return _correlation_only(tier_b_data, design)

    # Prefer Tier A if R² is reasonable
    for r in results:
        if r.model_tier == "A" and r.r_squared > 0.3:
            return r

    return results[-1]  # Tier B


def analyze_all_designs(engine: Engine, min_boats: int = 5) -> list[dict]:
    """Run regression for all eligible design classes.

    Returns list of result dicts, sorted by R² descending.
    """
    # Get all designs with enough boats
    query = text("""
        SELECT COALESCE(b.design_canonical, b.design) AS design_name, COUNT(*) AS cnt
        FROM boats b
        JOIN tcc_snapshots t ON t.boat_id = b.id
        WHERE COALESCE(b.design_canonical, b.design) IS NOT NULL
        GROUP BY COALESCE(b.design_canonical, b.design)
        HAVING COUNT(*) >= :min_boats
        ORDER BY COUNT(*) DESC
    """)

    with engine.connect() as conn:
        designs = conn.execute(query, {"min_boats": min_boats}).fetchall()

    results = []
    for row in designs:
        design_name = row.design_name
        try:
            result = analyze_design_sensitivity(engine, design_name)
            if result:
                results.append(result.to_dict())
        except Exception as e:
            logger.warning(f"Failed to analyze {design_name}: {e}")
            results.append({
                "design": design_name,
                "model_tier": "error",
                "error": str(e),
            })

    # Sort by R² descending (regression results first)
    results.sort(
        key=lambda r: r.get("r_squared", -1),
        reverse=True,
    )
    return results


def get_boat_sensitivity_context(
    engine: Engine,
    boat_id: int,
    design: str,
) -> dict | None:
    """Get sensitivity analysis contextualised for a specific boat.

    Returns dict with coefficients plus this boat's position on each lever.
    """
    result = analyze_design_sensitivity(engine, design)
    if result is None:
        return None

    # Fetch this boat's measurements
    query = text("""
        SELECT
            t.tcc, t.lh, t.beam, t.draft, t.headsails, t.spinnakers, t.crew, t.dlr,
            c.displacement_kg AS displacement, c.p, c.e, c.j, c.hlu, c.hlp, c.muw, c.mhw, c.stl,
            c.sym_slu, c.sym_sf
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT * FROM tcc_snapshots WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        LEFT JOIN LATERAL (
            SELECT * FROM irc_certificates WHERE boat_id = b.id ORDER BY issue_date DESC NULLS LAST LIMIT 1
        ) c ON true
        WHERE b.id = :boat_id
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"boat_id": boat_id}).first()

    if not row:
        return result.to_dict()

    boat_data = dict(row._mapping)

    # Fetch class means from MV
    class_stats = _fetch_class_means(engine, design)

    result_dict = result.to_dict()
    result_dict["boat_position"] = {}

    for coef in result_dict.get("coefficients", []):
        feat = coef["field"]
        boat_val = boat_data.get(feat)
        if boat_val is not None:
            boat_val = float(boat_val) if isinstance(boat_val, Decimal) else float(boat_val)
            class_mean = class_stats.get(f"mean_{feat}")
            class_std = class_stats.get(f"std_{feat}")

            pos = {"value": round(boat_val, 3)}
            if class_mean is not None:
                pos["class_mean"] = round(float(class_mean), 3)
            if class_std is not None and float(class_std) > 0:
                pos["z_score"] = round((boat_val - float(class_mean)) / float(class_std), 2)

            result_dict["boat_position"][feat] = pos

    # Class baseline TCC distribution + this boat's percentile rank.
    boat_tcc = boat_data.get("tcc")
    boat_tcc_f = float(boat_tcc) if boat_tcc is not None else None
    cb: dict[str, float | None] = {
        "mean_tcc":    class_stats.get("mean_tcc"),
        "median_tcc":  class_stats.get("median_tcc"),
        "p25_tcc":     class_stats.get("p25_tcc"),
        "p75_tcc":     class_stats.get("p75_tcc"),
        "min_tcc":     class_stats.get("min_tcc"),
        "max_tcc":     class_stats.get("max_tcc"),
        "n_boats":     class_stats.get("n_boats"),
        "this_boat_tcc": boat_tcc_f,
    }
    # Percentile rank: count peers with tcc < this boat / total.
    if boat_tcc_f is not None and (class_stats.get("n_boats") or 0) > 1:
        with engine.connect() as conn:
            rank_row = conn.execute(text("""
                WITH latest AS (
                    SELECT DISTINCT ON (b.id) t.tcc
                    FROM boats b JOIN tcc_snapshots t ON t.boat_id = b.id
                    WHERE COALESCE(b.design_canonical, b.design) = :design
                      AND t.tcc IS NOT NULL
                    ORDER BY b.id, t.snapshot_date DESC
                )
                SELECT COUNT(*)::float / NULLIF((SELECT COUNT(*) FROM latest), 0)::float AS pct
                FROM latest WHERE tcc < :boat_tcc
            """), {"design": design, "boat_tcc": boat_tcc_f}).first()
        cb["this_boat_percentile"] = round((rank_row.pct or 0.0) * 100, 1)
    else:
        cb["this_boat_percentile"] = None

    result_dict["class_baseline"] = cb
    return result_dict


def run_tier_c_model(engine: Engine) -> RegressionResult | None:
    """Run fleet-wide (Tier C) model across all boats.

    Captures broad IRC formula behaviour without design-class controls.
    """
    data = _fetch_tier_c_data(engine)
    if len(data) < MIN_BOATS_FOR_REGRESSION:
        return None

    return _fit_tier(data, TIER_C_FEATURES, "All Designs (Fleet-Wide)", "C")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fit_tier(
    data: list[dict],
    features: list[str],
    design: str,
    tier: str,
) -> RegressionResult | None:
    """Fit a Ridge regression for a given tier and data."""
    X, y, used_features, valid_indices = _prepare_matrix(data, features)

    if len(y) < MIN_BOATS_FOR_REGRESSION or len(used_features) == 0:
        return None

    # Check collinearity
    warnings = _check_collinearity(X, used_features)

    # Fit model
    model, scaler, cv_r2 = _run_ridge(X, y, used_features, len(y))

    # R² on training data
    y_pred = model.predict(scaler.transform(X))
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Build coefficients
    coefs = _build_coefficients(model, scaler, used_features)
    interpretation = _generate_interpretation(coefs, design, tier)

    # Flag limited data
    if len(y) < MIN_BOATS_FULL_CV:
        interpretation += " [Limited data — interpret with caution.]"

    return RegressionResult(
        design=design,
        model_tier=tier,
        n_boats=len(y),
        r_squared=r_squared,
        r_squared_cv=cv_r2,
        alpha=float(model.alpha_),
        coefficients=coefs,
        collinearity_warnings=warnings,
        interpretation=interpretation,
    )


def _correlation_only(data: list[dict], design: str) -> CorrelationResult:
    """Compute pairwise correlations for small classes."""
    all_features = TIER_B_FEATURES + [
        f for f in TIER_A_FEATURES if f not in TIER_B_FEATURES
    ]
    tcc_vals = [float(r["tcc"]) for r in data if r.get("tcc") is not None]
    if len(tcc_vals) < 2:
        return CorrelationResult(design=design, n_boats=len(data), correlations={})

    tcc_arr = np.array(tcc_vals)
    correlations = {}

    for feat in all_features:
        pairs = [(float(r[feat]), float(r["tcc"]))
                 for r in data
                 if r.get(feat) is not None and r.get("tcc") is not None]
        if len(pairs) >= 2:
            x_arr = np.array([p[0] for p in pairs])
            y_arr = np.array([p[1] for p in pairs])
            if np.std(x_arr) > 0 and np.std(y_arr) > 0:
                corr = float(np.corrcoef(x_arr, y_arr)[0, 1])
                correlations[feat] = corr

    return CorrelationResult(
        design=design,
        n_boats=len(data),
        correlations=correlations,
    )


def _fetch_class_means(engine: Engine, design: str) -> dict:
    """Return per-feature means + a TCC distribution for the design class.

    Uses the latest tcc_snapshot per boat. The TCC summary lets the
    report anchor decomposition with the median rating in the class.
    """
    query = text("""
        WITH latest AS (
            SELECT DISTINCT ON (b.id)
                   b.id, t.tcc, t.lh, t.beam, t.draft, t.headsails,
                   t.spinnakers, t.crew, t.dlr,
                   c.displacement_kg AS displacement, c.p, c.e, c.j,
                   c.hlu, c.hlp, c.muw, c.mhw, c.stl,
                   c.sym_slu, c.sym_sf
            FROM boats b
            LEFT JOIN tcc_snapshots t ON t.boat_id = b.id
            LEFT JOIN irc_certificates c ON c.boat_id = b.id
            WHERE COALESCE(b.design_canonical, b.design) = :design
              AND t.tcc IS NOT NULL
            ORDER BY b.id, t.snapshot_date DESC, c.issue_date DESC
        )
        SELECT
            AVG(tcc)::float AS mean_tcc,
            (percentile_cont(0.5)  WITHIN GROUP (ORDER BY tcc))::float AS median_tcc,
            (percentile_cont(0.25) WITHIN GROUP (ORDER BY tcc))::float AS p25_tcc,
            (percentile_cont(0.75) WITHIN GROUP (ORDER BY tcc))::float AS p75_tcc,
            MIN(tcc)::float AS min_tcc,
            MAX(tcc)::float AS max_tcc,
            COUNT(*)::int   AS n_boats,
            AVG(lh)::float AS mean_lh, STDDEV(lh)::float AS std_lh,
            AVG(beam)::float AS mean_beam, STDDEV(beam)::float AS std_beam,
            AVG(draft)::float AS mean_draft, STDDEV(draft)::float AS std_draft,
            AVG(headsails)::float AS mean_headsails, STDDEV(headsails)::float AS std_headsails,
            AVG(spinnakers)::float AS mean_spinnakers, STDDEV(spinnakers)::float AS std_spinnakers,
            AVG(crew)::float AS mean_crew, STDDEV(crew)::float AS std_crew,
            AVG(dlr)::float AS mean_dlr, STDDEV(dlr)::float AS std_dlr,
            AVG(displacement)::float AS mean_displacement, STDDEV(displacement)::float AS std_displacement,
            AVG(p)::float AS mean_p, STDDEV(p)::float AS std_p,
            AVG(e)::float AS mean_e, STDDEV(e)::float AS std_e,
            AVG(j)::float AS mean_j, STDDEV(j)::float AS std_j,
            AVG(hlu)::float AS mean_hlu, STDDEV(hlu)::float AS std_hlu,
            AVG(hlp)::float AS mean_hlp, STDDEV(hlp)::float AS std_hlp,
            AVG(muw)::float AS mean_muw, STDDEV(muw)::float AS std_muw,
            AVG(mhw)::float AS mean_mhw, STDDEV(mhw)::float AS std_mhw,
            AVG(stl)::float AS mean_stl, STDDEV(stl)::float AS std_stl,
            AVG(sym_slu)::float AS mean_sym_slu, STDDEV(sym_slu)::float AS std_sym_slu,
            AVG(sym_sf)::float AS mean_sym_sf, STDDEV(sym_sf)::float AS std_sym_sf
        FROM latest
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"design": design}).first()
    return dict(row._mapping) if row else {}
