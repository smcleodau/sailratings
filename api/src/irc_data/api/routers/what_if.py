"""What-if simulator and recommendation ranking endpoints (SM-01-05).

Powers the what-if simulator: given proposed lever deltas for a boat,
returns the estimated Δ TCC, seconds/hour and combined estimate with
uncertainty. All outputs carry the mandatory disclaimer:

    "estimate from class regression — not an official rating"

These endpoints wrap Engine 1 (class regression, SM-01-02) and Engine 3d
(smart-boat cohort) via the DB-backed bridge in
``irc_data.analysis.what_if``.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db
from irc_data.api.schemas.what_if import (
    RecommendationListResponse,
    WhatIfEstimateResponse,
    WhatIfRequest,
)
from irc_data.analysis.what_if import (
    ESTIMATE_DISCLAIMER,
    ESTIMATE_FLAG,
    recommend_for_boat,
    simulate_what_if,
)

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.post(
    "/boats/{boat_id}/what-if",
    response_model=WhatIfEstimateResponse,
)
def post_what_if(
    boat_id: int,
    payload: WhatIfRequest,
    engine: Engine = Depends(get_db),
):
    """Estimate Δ TCC for a what-if scenario on a specific boat.

    Given lever deltas (e.g. ``{"headsails": -1, "crew": -1}``), returns the
    estimated Δ TCC, seconds/hour, a combined estimate with an uncertainty
    interval, and a trial-certificate suggestion payload. Class-legal bounds
    are enforced; every output is flagged as an estimate from class
    regression — not an official rating.
    """
    if not payload.lever_deltas:
        raise HTTPException(status_code=400, detail="Provide at least one lever delta")

    estimate = simulate_what_if(
        engine,
        boat_id,
        payload.lever_deltas,
        include_trial_certificate=payload.include_trial_certificate,
    )
    if estimate is None:
        raise HTTPException(status_code=404, detail=f"Boat {boat_id} not found")

    return estimate.to_dict()


@router.get(
    "/boats/{boat_id}/recommendations",
    response_model=RecommendationListResponse,
)
def get_recommendations(
    boat_id: int,
    top_n: int = Query(5, ge=1, le=25),
    engine: Engine = Depends(get_db),
):
    """Ranked optimisation recommendations for a boat.

    Recommendations are ranked by impact × feasibility × evidence and
    include indicative cost hooks for the sail-programme overlay. Every
    recommendation is flagged as an estimate from class regression — not an
    official rating — and advises confirming with a trial certificate.
    """
    recs = recommend_for_boat(engine, boat_id, top_n=top_n)
    if recs is None:
        raise HTTPException(status_code=404, detail=f"Boat {boat_id} not found")

    # Resolve boat identity for the envelope.
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT boat_name, COALESCE(design_canonical, design) AS design "
                "FROM boats WHERE id = :id"
            ),
            {"id": boat_id},
        ).first()

    return {
        "boat_id": boat_id,
        "boat_name": row.boat_name if row else None,
        "design": row.design if row else None,
        "disclaimer": ESTIMATE_DISCLAIMER,
        "estimate_flag": ESTIMATE_FLAG,
        "recommendations": [r.to_dict() for r in recs],
    }
