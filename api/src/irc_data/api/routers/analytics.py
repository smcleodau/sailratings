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
# SM-01-04: Rule / formula drift analysis (RuleDriftV1)
# ---------------------------------------------------------------------------


@router.get("/rule-drift")
def get_rule_drift(
    design: str | None = Query(None, description="Restrict to one design class"),
    year_from: str | None = Query(None, description="First rating cycle year, e.g. 2022"),
    year_to: str | None = Query(None, description="Last rating cycle year, e.g. 2026"),
    engine: Engine = Depends(get_db),
):
    """RuleDriftV1 — fleet-wide or per-class rule/formula drift.

    Stable-certificate cohorts (measurements unchanged) across rating cycles
    yield mean TCC drift per class and fleet-wide, with t-test/Wilcoxon
    p-values, per-lever attribution ('taxed more' / 'eased' / 'stable') and
    per-boat 'rule movement vs boat movement' decomposition.
    """
    from irc_data.analysis.rule_drift import analyze_rule_drift

    result = analyze_rule_drift(
        engine, design=design, year_from=year_from, year_to=year_to
    )
    if result is None:
        raise HTTPException(status_code=404, detail="No rule drift data available")

    return result.to_dict()


@router.get("/designs/{design_name}/rule-drift-v1")
def get_design_rule_drift_v1(
    design_name: str,
    engine: Engine = Depends(get_db),
):
    """RuleDriftV1 restricted to a single design class."""
    from irc_data.analysis.rule_drift import get_class_rule_drift

    result = get_class_rule_drift(engine, design_name)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No rule drift data available for design '{design_name}'",
        )

    return result


@router.get("/boats/{boat_id}/rule-drift")
def get_boat_rule_drift(
    boat_id: int,
    engine: Engine = Depends(get_db),
):
    """Per-boat 'rule movement vs boat movement' decomposition history."""
    from irc_data.analysis.rule_drift import get_boat_rule_drift

    result = get_boat_rule_drift(engine, boat_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No rule drift decomposition available for boat {boat_id}",
        )

    return result


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


# ---------------------------------------------------------------------------
# SM-01-06: Rivals head-to-head / Design comparator / Fleet intelligence
# ---------------------------------------------------------------------------


@router.get("/head-to-head")
def get_head_to_head(
    boat_id: int = Query(..., ge=1),
    rival_id: int = Query(..., ge=1),
    engine: Engine = Depends(get_db),
):
    """HeadToHeadV1 — corrected and uncorrected records between two boats.

    Uncorrected uses official finishing places; corrected uses elapsed × TCC
    where the source payload carries times, otherwise a place-per-TCC proxy.
    """
    from irc_data.analysis.comparative import compute_head_to_head_v1

    result = compute_head_to_head_v1(engine, boat_id, rival_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Boat {boat_id} or rival {rival_id} not found",
        )
    return result.to_dict()


@router.get("/boats/{boat_id}/rivals-v1")
def get_boat_rivals_v1(
    boat_id: int,
    min_meetings: int = Query(2, ge=1, le=100),
    engine: Engine = Depends(get_db),
):
    """HeadToHeadV1 records for every rival sharing ``min_meetings`` races."""
    from irc_data.analysis.comparative import compute_rivals_v1

    from sqlalchemy import text
    with engine.connect() as conn:
        boat = conn.execute(
            text("SELECT id, boat_name FROM boats WHERE id = :id"),
            {"id": boat_id},
        ).first()
    if not boat:
        raise HTTPException(status_code=404, detail=f"Boat {boat_id} not found")

    records = compute_rivals_v1(engine, boat_id, min_meetings=min_meetings)
    return {
        "version": "HeadToHeadV1",
        "boat_id": boat_id,
        "boat_name": boat.boat_name,
        "min_meetings": min_meetings,
        "rivals": [r.to_dict() for r in records],
    }


@router.get("/design-comparator")
def get_design_comparator(
    designs: str = Query(..., description="Comma-separated design names"),
    engine: Engine = Depends(get_db),
):
    """DesignComparatorV1 — band, mean/median RAI, results depth, headroom."""
    from irc_data.analysis.comparative import design_comparator_batch

    design_list = [d.strip() for d in designs.split(",") if d.strip()]
    if not design_list:
        raise HTTPException(status_code=400, detail="Provide at least one design name")

    return design_comparator_batch(engine, design_list)


@router.get("/design-comparator/{design_name}")
def get_design_comparator_single(
    design_name: str,
    engine: Engine = Depends(get_db),
):
    """DesignComparatorV1 for a single design class."""
    from irc_data.analysis.comparative import design_comparator

    result = design_comparator(engine, design_name)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No boats found for design '{design_name}'",
        )
    return result.to_dict()


@router.get("/fleet-summary")
def get_fleet_summary(
    design: str | None = Query(None),
    country: str | None = Query(None),
    engine: Engine = Depends(get_db),
):
    """FleetSummaryV1 — fleet-at-a-glance aggregates (optionally scoped)."""
    from irc_data.analysis.comparative import fleet_summary_v1

    return fleet_summary_v1(engine, design=design, country=country)


# ---------------------------------------------------------------------------
# Global System Statistics
# ---------------------------------------------------------------------------


@router.get("/stats")
def get_global_stats(
    engine: Engine = Depends(get_db),
):
    """Retrieve live global database statistics for the platform landing page."""
    from sqlalchemy import text

    with engine.connect() as conn:
        total_boats = conn.execute(text("SELECT COUNT(*) FROM boats")).scalar() or 0
        active_certs = conn.execute(
            text("SELECT COUNT(*) FROM irc_certificates")
        ).scalar() or 0
        total_results = conn.execute(text("SELECT COUNT(*) FROM race_results")).scalar() or 0
        tracked_designs = conn.execute(text("SELECT COUNT(*) FROM design_classes")).scalar() or 0
        countries_covered = conn.execute(
            text("SELECT COUNT(DISTINCT country) FROM boats WHERE country IS NOT NULL")
        ).scalar() or 0

    return {
        "total_boats": total_boats,
        "active_certs": active_certs,
        "total_results": total_results,
        "tracked_designs": tracked_designs,
        "countries_covered": countries_covered,
    }

