"""Pydantic request/response models for the what-if simulator (SM-01-05).

These mirror the ``WhatIfEstimateV1`` and ``RecommendationV1`` output
contracts produced by ``irc_data.analysis.what_if``. Every response carries
the mandatory disclaimer: "estimate from class regression — not an official
rating".
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# What-if simulator
# ---------------------------------------------------------------------------


class WhatIfRequest(BaseModel):
    """Proposed lever deltas (raw units; negative = reduce).

    Example: ``{"lever_deltas": {"headsails": -1, "spinnakers": -1}}``
    """

    lever_deltas: dict[str, float] = Field(
        ...,
        description="Mapping lever field → requested change in raw units.",
    )
    include_trial_certificate: bool = True


class LeverEstimateSchema(BaseModel):
    field: str
    label: str
    unit: str
    requested_delta: float
    applied_delta: float
    clamped: bool
    clamp_reason: str | None = None
    delta_tcc: float
    sec_per_hour: float
    new_value: float | None = None
    disclaimer: str
    estimate_flag: str


class TrialCertificateSchema(BaseModel):
    boat_name: str | None = None
    design: str | None = None
    base_tcc: float | None = None
    estimated_tcc: float | None = None
    delta_tcc: float
    proposed_changes: list[dict] = Field(default_factory=list)
    notes: str = ""
    disclaimer: str
    estimate_flag: str


class WhatIfEstimateResponse(BaseModel):
    """The WhatIfEstimateV1 contract."""

    base_tcc: float | None = None
    estimated_tcc: float | None = None
    delta_tcc: float
    sec_per_hour: float
    uncertainty: dict[str, float]
    levers: list[LeverEstimateSchema] = Field(default_factory=list)
    model_tier: str | None = None
    r_squared: float | None = None
    design: str | None = None
    combination_factor: float = 1.0
    disclaimer: str
    estimate_flag: str
    trial_certificate: TrialCertificateSchema | None = None


# ---------------------------------------------------------------------------
# Recommendation ranking
# ---------------------------------------------------------------------------


class RecommendationSchema(BaseModel):
    """The RecommendationV1 contract."""

    change: str
    category: str
    delta_tcc: float
    sec_per_hour: float
    feasibility: int
    feasibility_label: str = ""
    evidence_strength: str
    lever_field: str | None = None
    requested_delta: float | None = None
    applied_delta: float | None = None
    new_value: float | None = None
    unit: str | None = None
    explanation: str = ""
    composite_score: float = 0.0
    rank: int = 0
    indicative_cost: float | None = None
    disclaimer: str
    estimate_flag: str


class RecommendationListResponse(BaseModel):
    boat_id: int
    boat_name: str | None = None
    design: str | None = None
    disclaimer: str
    estimate_flag: str
    recommendations: list[RecommendationSchema] = Field(default_factory=list)
