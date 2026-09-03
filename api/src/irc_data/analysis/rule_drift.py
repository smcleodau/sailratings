"""SM-01-04: Rule / formula drift analysis (RuleDriftV1).

Tell owners when their rating changed because the *rule* changed.

Method
------
IRC re-issues certificates each year. When a boat's measurements are
unchanged between two rating cycles but its TCC moves, that movement is
attributable to the rule/formula rather than to the boat. We call a set of
consecutive snapshot pairs with unchanged measurements a *stable-certificate
cohort* and compute, per class and fleet-wide:

- mean/median/std TCC drift across the cohort
- statistical significance (one-sample t-test + Wilcoxon signed-rank)
- per-lever attribution ("taxed more" / "eased" / "stable") by comparing
  standardised class-regression (Tier B ridge) coefficients between cycles
- per-boat decomposition of total TCC change into a *rule movement*
  component (the class/stable-cohort mean drift) and a *boat movement*
  component (the remainder, attributable to measurement/configuration
  changes).

Output contract: ``RuleDriftV1``.

We never claim to know the IRC formula — all findings are framed as
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
    _prepare_matrix,
    _run_ridge,
)

logger = logging.getLogger(__name__)

RULE_DRIFT_VERSION = "RuleDriftV1"

# Measurement levers tracked for stability / attribution.
MEASUREMENT_LEVERS = ["lh", "beam", "draft", "headsails", "spinnakers"]

# Minimum stable boats to report per-class statistics.
MIN_CLASS_COHORT = 3
# Minimum boats per side to run the per-cycle lever-attribution regression.
MIN_BOATS_LEVER_REGRESSION = 10
# |Δ standardised coefficient| above which a lever is said to have moved.
LEVER_EPSILON = 0.001
# |Δ TCC| below which a cycle pair reports "no measurable drift".
DRIFT_EPSILON = 0.0005


# ---------------------------------------------------------------------------
# Output contract dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CohortDriftStats:
    """Drift statistics for one stable-certificate cohort over one period."""

    n_stable: int
    n_total: int
    mean_drift: float
    median_drift: float
    std_drift: float
    p_value_ttest: float | None
    p_value_wilcoxon: float | None
    cohens_d: float | None
    pct_decreased: float

    def to_dict(self) -> dict:
        return {
            "n_stable": self.n_stable,
            "n_total": self.n_total,
            "mean_drift": round(self.mean_drift, 5),
            "median_drift": round(self.median_drift, 5),
            "std_drift": round(self.std_drift, 5),
            "p_value_ttest": (
                round(self.p_value_ttest, 6) if self.p_value_ttest is not None else None
            ),
            "p_value_wilcoxon": (
                round(self.p_value_wilcoxon, 6) if self.p_value_wilcoxon is not None else None
            ),
            "cohens_d": round(self.cohens_d, 3) if self.cohens_d is not None else None,
            "pct_decreased": round(self.pct_decreased, 1),
        }


@dataclass
class LeverAttribution:
    """Per-lever movement between two rating cycles.

    ``attribution`` is one of ``"taxed more"``, ``"eased"``, ``"stable"``.
    A positive standardised-coefficient change means the lever pushes TCC
    *up* more strongly than before → the lever is taxed more.
    """

    lever: str
    coefficient_change: float
    attribution: str

    def to_dict(self) -> dict:
        return {
            "lever": self.lever,
            "coefficient_change": round(self.coefficient_change, 5),
            "attribution": self.attribution,
        }


@dataclass
class CycleDrift:
    """Fleet-wide drift between two consecutive rating cycles."""

    year_from: str
    year_to: str
    stats: CohortDriftStats
    lever_attribution: list[LeverAttribution] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "period": f"{self.year_from} -> {self.year_to}",
            "year_from": self.year_from,
            "year_to": self.year_to,
            **self.stats.to_dict(),
            "lever_attribution": [la.to_dict() for la in self.lever_attribution],
        }


@dataclass
class BoatDriftDecompositionV1:
    """Per-boat 'rule movement vs boat movement' decomposition."""

    boat_id: int
    boat_name: str
    sail_number: str
    design: str
    year_from: str
    year_to: str
    tcc_from: float
    tcc_to: float
    total_change: float
    rule_movement: float
    boat_movement: float
    measurements_stable: bool
    measurement_deltas: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "boat_id": self.boat_id,
            "boat_name": self.boat_name,
            "sail_number": self.sail_number,
            "design": self.design,
            "period": f"{self.year_from} -> {self.year_to}",
            "tcc_from": round(self.tcc_from, 4),
            "tcc_to": round(self.tcc_to, 4),
            "total_change": round(self.total_change, 5),
            "rule_movement": round(self.rule_movement, 5),
            "boat_movement": round(self.boat_movement, 5),
            "measurements_stable": self.measurements_stable,
            "measurement_deltas": {
                k: round(v, 4) for k, v in self.measurement_deltas.items()
            },
        }


@dataclass
class RuleDriftResult:
    """RuleDriftV1 — fleet-wide or per-class rule drift analysis."""

    version: str
    scope: str  # "fleet" or the design name
    cycles: list[CycleDrift]
    by_class: dict[str, CohortDriftStats]
    boat_decompositions: list[BoatDriftDecompositionV1]
    interpretation: str
    disclaimer: str = (
        "Consistent with observed certificate data — we never claim to know "
        "the IRC formula."
    )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "scope": self.scope,
            "cycles": [c.to_dict() for c in self.cycles],
            "by_class": {k: v.to_dict() for k, v in self.by_class.items()},
            "boat_decompositions": [b.to_dict() for b in self.boat_decompositions[:50]],
            "interpretation": self.interpretation,
            "disclaimer": self.disclaimer,
        }


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def _fetch_snapshot_pairs(engine: Engine) -> list[dict]:
    """Fetch consecutive per-boat snapshot pairs with measurement deltas.

    Uses ``mv_tcc_drift`` when present, falling back to a live query so the
    analysis works against a bare SQLite fixture too.
    """
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM mv_tcc_drift ORDER BY boat_id, date_from")
            ).fetchall()
            if rows:
                return [dict(r._mapping) for r in rows]
    except Exception:
        pass

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


def _fetch_boat_names(engine: Engine) -> dict[int, tuple[str, str]]:
    query = text("SELECT id, boat_name, sail_number FROM boats")
    with engine.connect() as conn:
        return {row.id: (row.boat_name, row.sail_number) for row in conn.execute(query)}


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


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def _compute_cohort_stats(deltas: np.ndarray, n_total: int) -> CohortDriftStats:
    """Mean drift + significance for a stable-certificate cohort."""
    n = len(deltas)
    mean_drift = float(np.mean(deltas))
    median_drift = float(np.median(deltas))
    std_drift = float(np.std(deltas, ddof=1)) if n > 1 else 0.0

    p_ttest = None
    if n >= 3 and std_drift > 0:
        _, p_ttest = scipy_stats.ttest_1samp(deltas, 0)
        p_ttest = float(p_ttest)
        if not np.isfinite(p_ttest):
            p_ttest = None

    p_wilcoxon = None
    if n >= 6 and np.any(deltas != 0):
        try:
            _, p_wilcoxon = scipy_stats.wilcoxon(deltas)
            p_wilcoxon = float(p_wilcoxon)
            if not np.isfinite(p_wilcoxon):
                p_wilcoxon = None
        except ValueError:
            pass  # All values identical

    cohens_d = mean_drift / std_drift if std_drift > 0 else None
    pct_decreased = float(np.sum(deltas < 0) / n * 100) if n > 0 else 0.0

    return CohortDriftStats(
        n_stable=n,
        n_total=n_total,
        mean_drift=mean_drift,
        median_drift=median_drift,
        std_drift=std_drift,
        p_value_ttest=p_ttest,
        p_value_wilcoxon=p_wilcoxon,
        cohens_d=cohens_d,
        pct_decreased=pct_decreased,
    )


def attribute_levers(
    coef_from: dict[str, float], coef_to: dict[str, float]
) -> list[LeverAttribution]:
    """Pure lever attribution from two dicts of standardised coefficients.

    Levers whose standardised coefficient rises are "taxed more"; those whose
    coefficient falls are "eased"; the rest are "stable".
    """
    common = [lever for lever in coef_from if lever in coef_to]
    levers: list[LeverAttribution] = []
    for lever in common:
        change = float(coef_to[lever] - coef_from[lever])
        if change > LEVER_EPSILON:
            attribution = "taxed more"
        elif change < -LEVER_EPSILON:
            attribution = "eased"
        else:
            attribution = "stable"
        levers.append(
            LeverAttribution(
                lever=lever, coefficient_change=change, attribution=attribution
            )
        )
    levers.sort(key=lambda la: abs(la.coefficient_change), reverse=True)
    return levers


def _fit_cycle_coefficients(rows: list[dict]) -> dict[str, float]:
    """Fit a Tier-B ridge on one cycle's fleet; return standardised coefs."""
    X, y, feats, _ = _prepare_matrix(rows, TIER_B_FEATURES)
    if len(y) < MIN_BOATS_LEVER_REGRESSION:
        return {}
    model, scaler, _ = _run_ridge(X, y, feats, len(y))
    return {feat: float(model.coef_[i]) for i, feat in enumerate(feats)}


def _lever_attribution(
    engine: Engine, date_from: str, date_to: str
) -> list[LeverAttribution]:
    """Compare per-cycle fleet-wide Tier-B standardised coefficients."""
    data_from = _fetch_all_at_date(engine, date_from)
    data_to = _fetch_all_at_date(engine, date_to)

    if (
        len(data_from) < MIN_BOATS_LEVER_REGRESSION
        or len(data_to) < MIN_BOATS_LEVER_REGRESSION
    ):
        return []

    coef_from = _fit_cycle_coefficients(data_from)
    coef_to = _fit_cycle_coefficients(data_to)
    if not coef_from or not coef_to:
        return []

    return attribute_levers(coef_from, coef_to)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_rule_drift(
    engine: Engine,
    design: str | None = None,
    year_from: str | None = None,
    year_to: str | None = None,
    include_levers: bool = True,
) -> RuleDriftResult | None:
    """RuleDriftV1 — stable-certificate cohort drift across rating cycles.

    If ``design`` is given the analysis is restricted to that class.
    ``year_from``/``year_to`` (ISO date strings, typically cycle years like
    ``"2025"``/``"2026"``) bound the analysis window; the default is every
    consecutive rating cycle in the data.
    """
    pairs = _fetch_snapshot_pairs(engine)
    if not pairs:
        return None

    if design:
        pairs = [p for p in pairs if p.get("design_name") == design]
        if not pairs:
            return None

    # Restrict to requested window. A pair's cycle year is the year of the
    # later snapshot (date_to) — the new rating cycle it belongs to.
    if year_from:
        pairs = [p for p in pairs if str(p.get("date_to") or "")[:4] >= year_from[:4]]
    if year_to:
        pairs = [p for p in pairs if str(p.get("date_to") or "")[:4] <= year_to[:4]]

    if not pairs:
        return None

    # Group consecutive pairs into rating cycles keyed by (from, to) dates.
    cycles_map: dict[tuple[str, str], list[dict]] = {}
    for p in pairs:
        key = (str(p.get("date_from")), str(p.get("date_to")))
        cycles_map.setdefault(key, []).append(p)

    boat_names = _fetch_boat_names(engine)

    cycles: list[CycleDrift] = []
    # class -> list of (delta) aggregated across cycles for the by_class rollup
    class_deltas: dict[str, list[float]] = {}
    class_total: dict[str, int] = {}
    boat_decompositions: list[BoatDriftDecompositionV1] = []

    for (date_from, date_to), rows in sorted(cycles_map.items()):
        stable = [r for r in rows if r.get("measurements_stable")]
        deltas = np.array(
            [float(r["tcc_delta"]) for r in stable if r.get("tcc_delta") is not None]
        )
        if len(deltas) == 0:
            continue

        stats = _compute_cohort_stats(deltas, n_total=len(rows))
        levers = (
            _lever_attribution(engine, date_from, date_to) if include_levers else []
        )
        cycles.append(
            CycleDrift(
                year_from=date_from,
                year_to=date_to,
                stats=stats,
                lever_attribution=levers,
            )
        )

        # By-class rollup uses stable-certificate cohorts.
        for r in stable:
            cls = r.get("design_name")
            delta = r.get("tcc_delta")
            if cls and delta is not None:
                class_deltas.setdefault(cls, []).append(float(delta))
        for r in rows:
            cls = r.get("design_name")
            if cls:
                class_total[cls] = class_total.get(cls, 0) + 1

        # Per-boat decomposition: rule movement = stable-cohort mean drift
        # for this cycle; boat movement = remainder.
        cohort_mean = stats.mean_drift
        for r in rows:
            tcc_from = r.get("tcc_from")
            tcc_to = r.get("tcc_to")
            if tcc_from is None or tcc_to is None:
                continue
            tcc_from_f = float(tcc_from)
            tcc_to_f = float(tcc_to)
            total_change = tcc_to_f - tcc_from_f
            rule_movement = cohort_mean
            boat_movement = total_change - rule_movement

            measurement_deltas = {}
            for lever in MEASUREMENT_LEVERS:
                delta = r.get(f"delta_{lever}")
                if delta is not None and float(delta) != 0:
                    measurement_deltas[lever] = float(delta)

            name, sail = boat_names.get(r["boat_id"], ("", ""))
            boat_decompositions.append(
                BoatDriftDecompositionV1(
                    boat_id=r["boat_id"],
                    boat_name=name,
                    sail_number=sail,
                    design=r.get("design_name") or "",
                    year_from=date_from,
                    year_to=date_to,
                    tcc_from=tcc_from_f,
                    tcc_to=tcc_to_f,
                    total_change=total_change,
                    rule_movement=rule_movement,
                    boat_movement=boat_movement,
                    measurements_stable=bool(r.get("measurements_stable")),
                    measurement_deltas=measurement_deltas,
                )
            )

    if not cycles:
        return None

    by_class: dict[str, CohortDriftStats] = {}
    for cls, deltas in sorted(class_deltas.items()):
        if len(deltas) >= MIN_CLASS_COHORT:
            by_class[cls] = _compute_cohort_stats(
                np.array(deltas), n_total=class_total.get(cls, len(deltas))
            )

    interpretation = _interpret(cycles, scope=design or "fleet")

    return RuleDriftResult(
        version=RULE_DRIFT_VERSION,
        scope=design or "fleet",
        cycles=cycles,
        by_class=by_class,
        boat_decompositions=boat_decompositions,
        interpretation=interpretation,
    )


def get_class_rule_drift(engine: Engine, design: str) -> dict | None:
    """RuleDriftV1 for a single design class."""
    result = analyze_rule_drift(engine, design=design)
    if result:
        return result.to_dict()
    return None


def get_boat_rule_drift(engine: Engine, boat_id: int) -> dict | None:
    """Per-boat 'rule movement vs boat movement' decomposition history."""
    result = analyze_rule_drift(engine)
    if result is None:
        return None
    rows = [
        b.to_dict() for b in result.boat_decompositions if b.boat_id == boat_id
    ]
    if not rows:
        return None
    return {
        "version": RULE_DRIFT_VERSION,
        "boat_id": boat_id,
        "decompositions": rows,
        "disclaimer": result.disclaimer,
    }


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------


def _interpret(cycles: list[CycleDrift], scope: str) -> str:
    if not cycles:
        return "No rating-cycle drift detected."

    parts = []
    for c in cycles:
        mean = c.stats.mean_drift
        p = c.stats.p_value_ttest
        label = "fleet-wide" if scope == "fleet" else f"the {scope} class"
        if abs(mean) < DRIFT_EPSILON:
            text_part = (
                f"{c.year_from}→{c.year_to}: no measurable rule drift for {label} "
                f"(mean {mean:+.4f} TCC across {c.stats.n_stable} stable certificates"
            )
            if p is not None:
                text_part += f", p={p:.4f}"
            text_part += ")."
        else:
            direction = "down" if mean < 0 else "up"
            text_part = (
                f"{c.year_from}→{c.year_to}: the rule moved {label} {direction} by "
                f"~{abs(mean):.3f} TCC on average ({c.stats.pct_decreased:.0f}% of "
                f"stable certificates decreased"
            )
            if p is not None:
                sig = (
                    "highly significant"
                    if p < 0.001
                    else "statistically significant"
                    if p < 0.05
                    else "not statistically significant"
                )
                text_part += f", {sig} p={p:.4f}"
            text_part += ")."

        movers = [la for la in c.lever_attribution if la.attribution != "stable"]
        if movers:
            lever_txt = ", ".join(
                f"{la.lever} {la.attribution}" for la in movers[:3]
            )
            text_part += f" Lever attribution: {lever_txt}."
        parts.append(text_part)

    return " ".join(parts)
