"""Analytics engine API endpoints.

Exposes all five analysis engines via REST:
- Engine 1: Within-class measurement sensitivity
- Engine 2: IRC formula drift detection
- Engine 3: Racing performance (RAI, head-to-head, smart boats)
- Engine 4: Optimisation recommender
- Engine 5: Cross-design comparison
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(prefix="/analytics", tags=["Analytics"])


# ---------------------------------------------------------------------------
# Engine 1: Sensitivity
# ---------------------------------------------------------------------------


@router.get("/designs/{design_name}/sensitivity")
def get_design_sensitivity(
    design_name: str,
    engine: Engine = Depends(get_db),
):
    """Within-class measurement sensitivity analysis (Engine 1).

    Returns regression coefficients showing how each measurement lever
    affects TCC for this design class.
    """
    from irc_data.analysis.regression import analyze_design_sensitivity

    result = analyze_design_sensitivity(engine, design_name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Not enough data for design '{design_name}'")

    return result.to_dict()


# ---------------------------------------------------------------------------
# Engine 2: Drift
# ---------------------------------------------------------------------------


@router.get("/designs/{design_name}/drift")
def get_design_drift(
    design_name: str,
    engine: Engine = Depends(get_db),
):
    """IRC formula drift analysis for a specific design (Engine 2).

    Shows how the IRC formula has changed over time for this class.
    """
    from irc_data.analysis.temporal import get_design_drift

    result = get_design_drift(engine, design_name)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No drift data available for design '{design_name}'",
        )

    return result


@router.get("/fleet/drift")
def get_fleet_drift(
    engine: Engine = Depends(get_db),
):
    """Fleet-wide IRC formula drift analysis (Engine 2).

    Analyses TCC changes for measurement-stable boats to isolate formula evolution.
    """
    from irc_data.analysis.temporal import analyze_fleet_drift

    result = analyze_fleet_drift(engine)
    if result is None:
        raise HTTPException(status_code=404, detail="No drift data available")

    return result.to_dict()


# ---------------------------------------------------------------------------
# Engine 3: Performance
# ---------------------------------------------------------------------------


@router.get("/boats/{boat_id}/rai")
def get_boat_rai(
    boat_id: int,
    engine: Engine = Depends(get_db),
):
    """Rating Advantage Index for a specific boat (Engine 3a).

    Positive RAI = boat consistently finishes better than TCC predicts.
    """
    from irc_data.analysis.performance import compute_rai

    result = compute_rai(engine, boat_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No race results for boat {boat_id} or boat not found",
        )

    return result.to_dict()


@router.get("/boats/{boat_id}/rivals")
def get_boat_rivals(
    boat_id: int,
    min_meetings: int = Query(2, ge=1, le=100),
    engine: Engine = Depends(get_db),
):
    """Head-to-head race records against rival boats (Engine 3b)."""
    from irc_data.analysis.performance import compute_head_to_head

    # Verify boat exists
    from sqlalchemy import text
    with engine.connect() as conn:
        boat = conn.execute(
            text("SELECT id, boat_name, sail_number FROM boats WHERE id = :id"),
            {"id": boat_id},
        ).first()

    if not boat:
        raise HTTPException(status_code=404, detail=f"Boat {boat_id} not found")

    rivals = compute_head_to_head(engine, boat_id, min_meetings=min_meetings)

    return {
        "boat_id": boat_id,
        "boat_name": boat.boat_name,
        "rivals": [r.to_dict() for r in rivals],
    }


@router.get("/designs/{design_name}/smart-boats")
def get_smart_boats(
    design_name: str,
    min_races: int = Query(3, ge=1, le=100),
    engine: Engine = Depends(get_db),
):
    """Top-performing boats in a design class (Engine 3d).

    Identifies the top 10% by performance and analyses their measurement profiles.
    """
    from irc_data.analysis.performance import get_smart_boats as _get_smart_boats

    result = _get_smart_boats(engine, design_name, min_races=min_races)
    if result.get("n_total", 0) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No boats found for design '{design_name}'",
        )

    return result


# ---------------------------------------------------------------------------
# Engine 4: Optimisation
# ---------------------------------------------------------------------------


@router.get("/boats/{boat_id}/optimize")
def get_boat_optimisation(
    boat_id: int,
    engine: Engine = Depends(get_db),
):
    """Full optimisation report for a specific boat (Engine 4).

    Synthesises sensitivity, drift, performance, and ORC data into
    ranked actionable recommendations.
    """
    from irc_data.analysis.optimizer import generate_optimisation_report

    report = generate_optimisation_report(engine, boat_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Boat {boat_id} not found")

    return report.to_dict()


# ---------------------------------------------------------------------------
# Engine 5: Comparison
# ---------------------------------------------------------------------------


@router.get("/compare")
def compare_designs_endpoint(
    designs: str = Query(..., description="Comma-separated design names"),
    engine: Engine = Depends(get_db),
):
    """Compare two or more design classes (Engine 5).

    Pass design names as comma-separated: ?designs=Sunfast 3300,J/109
    """
    from irc_data.analysis.design_compare import compare_designs

    design_list = [d.strip() for d in designs.split(",") if d.strip()]
    if len(design_list) < 1:
        raise HTTPException(status_code=400, detail="Provide at least one design name")

    result = compare_designs(engine, design_list)
    return result
