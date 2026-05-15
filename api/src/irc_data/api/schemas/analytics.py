"""Pydantic response models for the analytics engine."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Engine 1: Regression / Sensitivity
# ---------------------------------------------------------------------------


class CoefficientSchema(BaseModel):
    field: str
    beta_per_unit: float
    unit: str
    std_beta: float
    rank: int


class SensitivityResponse(BaseModel):
    design: str
    model_tier: str
    n_boats: int
    r_squared: float | None = None
    r_squared_cv: float | None = None
    alpha: float | None = None
    coefficients: list[CoefficientSchema] = Field(default_factory=list)
    collinearity_warnings: list[str] = Field(default_factory=list)
    interpretation: str = ""
    # Correlation-only fallback
    correlations: dict[str, float] | None = None


# ---------------------------------------------------------------------------
# Engine 2: Drift
# ---------------------------------------------------------------------------


class FleetDriftSchema(BaseModel):
    n_stable: int
    n_total: int
    mean_drift: float
    median_drift: float
    std_drift: float
    p_value_ttest: float | None = None
    p_value_wilcoxon: float | None = None
    cohens_d: float | None = None
    pct_decreased: float
    interpretation: str


class DriftDimensionSchema(BaseModel):
    field: str
    coefficient_change: float
    direction: str


class DriftResponse(BaseModel):
    period: str
    fleet_wide: FleetDriftSchema
    by_dimension: list[DriftDimensionSchema] = Field(default_factory=list)
    by_country: dict[str, dict] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Engine 3: Performance
# ---------------------------------------------------------------------------


class RAIResponse(BaseModel):
    boat_id: int
    boat_name: str
    sail_number: str
    design: str | None = None
    rai: float
    n_races: int
    ci_lower: float
    ci_upper: float
    avg_finish_pct: float
    avg_expected_pct: float
    wins: int
    podiums: int
    interpretation: str


class RivalSchema(BaseModel):
    rival_boat_id: int
    rival_name: str
    rival_sail_number: str
    wins: int
    losses: int
    total: int
    win_rate: float
    events_together: int


class RivalsResponse(BaseModel):
    boat_id: int
    boat_name: str
    rivals: list[RivalSchema] = Field(default_factory=list)


class SmartBoatSchema(BaseModel):
    boat_id: int
    boat_name: str
    sail_number: str
    rai: float
    n_races: int
    measurements: dict[str, float]


class SmartBoatsResponse(BaseModel):
    design: str
    n_total: int
    n_with_races: int = 0
    n_smart: int = 0
    class_means: dict[str, float] = Field(default_factory=dict)
    smart_boat_means: dict[str, float] = Field(default_factory=dict)
    smart_boats: list[SmartBoatSchema] = Field(default_factory=list)
    fleet_rai_distribution: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Engine 4: Optimisation
# ---------------------------------------------------------------------------


class RecommendationSchema(BaseModel):
    field: str
    category: str
    current_value: float | None = None
    class_mean: float | None = None
    smart_boat_avg: float | None = None
    optimal_direction: str
    estimated_tcc_delta: float
    feasibility: int
    feasibility_label: str
    evidence_strength: str
    explanation: str
    rank: int


class OptimisationResponse(BaseModel):
    boat_id: int
    boat_name: str
    sail_number: str
    design: str | None = None
    current_tcc: float | None = None
    model_tier: str | None = None
    r_squared: float | None = None
    rai: float | None = None
    drift_context: str | None = None
    orc_context: dict | None = None
    recommendations: list[RecommendationSchema] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Engine 5: Design Comparison
# ---------------------------------------------------------------------------


class DesignProfileSchema(BaseModel):
    design: str
    n_boats: int
    tcc: dict = Field(default_factory=dict)
    rating_efficiency: dict = Field(default_factory=dict)
    performance: dict = Field(default_factory=dict)
    activity: dict = Field(default_factory=dict)
    modification_potential: str = ""
    countries: dict[str, int] = Field(default_factory=dict)
    n_countries: int = 0
    orc: dict | None = None


class ComparisonResponse(BaseModel):
    designs: list[str]
    profiles: list[DesignProfileSchema] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
