"""Strict Facts contracts for each report section.

Each section's Claude prompt receives ONE Facts object as input and is
forbidden from citing numbers outside its fields. Adding a new fact
requires updating both the dataclass here AND the prompt template in
prompts.py so they stay in lockstep.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


# ── Atomic value types ──────────────────────────────────────────────────


@dataclass
class MeasurementContribution:
    """One row of the rating decomposition table."""
    field: str          # 'displacement', 'p', 'e', etc.
    this_boat: float
    class_mean: float
    delta: float        # this_boat - class_mean
    contrib_tcc: float  # signed TCC impact vs class mean
    unit: str           # 'per 100kg', 'per 0.1m', 'per sail'
    beta: float         # raw regression coefficient


@dataclass
class RatingSnapshot:
    """One historical TCC point."""
    date: date
    tcc: Decimal
    cert_year: int | None
    source: str         # 'irc_tcc', 'irc_cert', etc.


@dataclass
class RaceResultLite:
    """A single race row in compact form for the timeline section."""
    event_date: date | None
    event_name: str
    race_name: str | None
    place: int | None
    fleet_size: int | None
    class_name: str | None
    status: str


@dataclass
class RivalSummary:
    """One rival in the Rival Watch section."""
    boat_id: int
    name: str
    sail_number: str | None
    country: str | None
    tcc: Decimal
    recent_finishes_count: int
    head_to_head_wins: int   # races where THIS boat beat RIVAL on corrected
    head_to_head_losses: int


@dataclass
class Identity:
    """A historical name/sail observation."""
    boat_name: str
    sail_number: str | None
    owner: str | None
    flag: str | None
    source: str
    observed_date: date | None


@dataclass
class Recommendation:
    """One optimisation recommendation."""
    measurement: str
    current_value: float
    suggested_value: float
    est_tcc_gain: float      # absolute, signed (negative = lower rating)
    rationale: str           # short justification grounded in coefficients
    confidence: str          # 'high' | 'medium' | 'low'


# ── Per-section Facts ───────────────────────────────────────────────────


@dataclass
class ExecutiveSummaryFacts:
    boat_name: str
    sail_number: str
    design: str
    country: str | None
    tcc_now: Decimal
    class_median_tcc: float | None
    this_boat_percentile: float | None
    finishes: int
    wins: int
    podiums: int
    headline_finding_1: str   # pre-cooked one-liners (built from raw stats)
    headline_finding_2: str
    headline_finding_3: str
    top_recommendation: str | None


@dataclass
class IdentityFacts:
    boat_name: str
    sail_number: str
    design: str
    designer: str | None
    builder: str | None
    year_built: int | None
    loa: float | None
    lwl: float | None
    beam_max: float | None
    displacement_kg: float | None
    identities: list[Identity] = field(default_factory=list)


@dataclass
class RatingAnatomyFacts:
    boat_name: str
    tcc_now: Decimal
    class_mean_tcc: float | None
    class_median_tcc: float | None
    decomposition: list[MeasurementContribution] = field(default_factory=list)
    design: str = ""
    explained_variance_pct: float | None = None   # R² × 100
    model_tier: str = ""
    n_boats_in_class: int = 0


@dataclass
class RatingEvolutionFacts:
    boat_name: str
    snapshots: list[RatingSnapshot] = field(default_factory=list)
    cert_reissue_dates: list[date] = field(default_factory=list)
    first_snapshot_tcc: Decimal | None = None
    latest_snapshot_tcc: Decimal | None = None
    total_movement: float = 0.0          # latest − first
    largest_jump_tcc: float = 0.0
    largest_jump_date: date | None = None


@dataclass
class ClassContextFacts:
    design: str
    class_n: int
    class_tcc_min: float
    class_tcc_max: float
    class_tcc_median: float
    class_tcc_mean: float
    this_boat_tcc: float
    this_boat_percentile: float | None
    top_5_boats: list[dict] = field(default_factory=list)  # {name, sail, tcc, country}
    class_tcc_list: list[float] = field(default_factory=list)


@dataclass
class PerformanceFacts:
    boat_name: str
    finishes: int
    wins: int
    podiums: int
    distinct_events: int
    rai_percentile: float | None
    rai_interpretation: str | None
    recent_results: list[RaceResultLite] = field(default_factory=list)
    by_event_type: dict[str, dict] = field(default_factory=dict)  # 'series'/'offshore'/'twilight' → {n, wins, podiums}
    head_to_head: list[RivalSummary] = field(default_factory=list)


@dataclass
class SensitivityFacts:
    design: str
    model_tier: str
    n_boats_in_class: int
    r_squared: float
    coefficients: list[MeasurementContribution] = field(default_factory=list)  # already enriched w/ boat's value


@dataclass
class OptimisationFacts:
    boat_name: str
    recommendations: list[Recommendation] = field(default_factory=list)
    top_3_summary: str = ""


@dataclass
class FormulaDriftFacts:
    design: str
    window_years: int
    drift_observed: bool
    affected_measurements: list[str] = field(default_factory=list)
    this_boat_likely_impact: str | None = None


@dataclass
class RivalsFacts:
    boat_name: str
    rivals: list[RivalSummary] = field(default_factory=list)


@dataclass
class AppendixFacts:
    methodology_blurb: str
    data_sources: list[str] = field(default_factory=list)
    glossary: list[tuple[str, str]] = field(default_factory=list)
