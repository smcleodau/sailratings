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
    ClassContextFacts, ExecutiveSummaryFacts, Identity, IdentityFacts,
    MeasurementContribution, PerformanceFacts, RaceResultLite,
    RatingAnatomyFacts, RatingEvolutionFacts, RatingSnapshot, RivalSummary,
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
            decomposition=[],
            design=(row.design if row else ""),
            explained_variance_pct=None,
            model_tier="", n_boats_in_class=0,
        )

    sens = get_boat_sensitivity_context(engine, boat_id, row.design)
    if sens is None:
        return RatingAnatomyFacts(
            boat_name=row.boat_name, tcc_now=row.tcc,
            class_mean_tcc=None, class_median_tcc=None,
            decomposition=[],
            design=row.design,
            explained_variance_pct=None,
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
        design=row.design,
        explained_variance_pct=round((sens.get("r_squared") or 0) * 100, 1),
        model_tier=sens.get("model_tier", ""),
        n_boats_in_class=sens.get("n_boats") or 0,
    )


# ── Identity & History ────────────────────────────────────────────────


def build_identity(engine: Engine, boat_id: int) -> IdentityFacts:
    """Identity facts: build metadata + historical name/sail observations."""
    with engine.connect() as conn:
        boat = conn.execute(text("""
            SELECT b.boat_name, b.sail_number,
                   COALESCE(b.design_canonical, b.design) AS design,
                   b.designer, b.builder, b.year_built,
                   b.loa, b.lwl, b.beam_max, b.displacement_kg
            FROM boats b WHERE b.id = :id
        """), {"id": boat_id}).first()
        if not boat:
            return IdentityFacts(
                boat_name=f"boat #{boat_id}", sail_number="", design="",
                designer=None, builder=None, year_built=None,
                loa=None, lwl=None, beam_max=None, displacement_kg=None,
            )
        identities = conn.execute(text("""
            SELECT boat_name, sail_number, owner, flag, source, observed_date
            FROM boat_identities WHERE boat_id = :id
            ORDER BY observed_date NULLS LAST
        """), {"id": boat_id}).fetchall()

    def _f(v):
        return float(v) if v is not None else None
    return IdentityFacts(
        boat_name=boat.boat_name,
        sail_number=boat.sail_number or "",
        design=boat.design or "",
        designer=boat.designer,
        builder=boat.builder,
        year_built=boat.year_built,
        loa=_f(boat.loa), lwl=_f(boat.lwl), beam_max=_f(boat.beam_max),
        displacement_kg=_f(boat.displacement_kg),
        identities=[
            Identity(
                boat_name=r.boat_name or "", sail_number=r.sail_number,
                owner=r.owner, flag=r.flag, source=r.source,
                observed_date=r.observed_date,
            )
            for r in identities
        ],
    )


# ── Rating Evolution ───────────────────────────────────────────────────


def build_rating_evolution(engine: Engine, boat_id: int) -> RatingEvolutionFacts:
    """Trace the boat's TCC over time + cert re-issue dates."""
    with engine.connect() as conn:
        boat = conn.execute(text(
            "SELECT boat_name FROM boats WHERE id = :id"
        ), {"id": boat_id}).first()
        snaps = conn.execute(text("""
            SELECT snapshot_date AS date, tcc, cert_year, 'irc_tcc' AS source
            FROM tcc_snapshots WHERE boat_id = :id
            ORDER BY snapshot_date
        """), {"id": boat_id}).fetchall()
        certs = conn.execute(text("""
            SELECT issue_date FROM irc_certificates
            WHERE boat_id = :id AND issue_date IS NOT NULL
            ORDER BY issue_date
        """), {"id": boat_id}).fetchall()

    snapshots = [
        RatingSnapshot(date=s.date, tcc=s.tcc, cert_year=s.cert_year, source=s.source)
        for s in snaps
    ]
    largest_jump = 0.0
    largest_jump_date = None
    for i in range(1, len(snapshots)):
        diff = float(snapshots[i].tcc) - float(snapshots[i - 1].tcc)
        if abs(diff) > abs(largest_jump):
            largest_jump = diff
            largest_jump_date = snapshots[i].date
    first = snapshots[0].tcc if snapshots else None
    latest = snapshots[-1].tcc if snapshots else None
    return RatingEvolutionFacts(
        boat_name=(boat.boat_name if boat else f"boat #{boat_id}"),
        snapshots=snapshots,
        cert_reissue_dates=[c.issue_date for c in certs],
        first_snapshot_tcc=first,
        latest_snapshot_tcc=latest,
        total_movement=(float(latest) - float(first)) if (first and latest) else 0.0,
        largest_jump_tcc=largest_jump,
        largest_jump_date=largest_jump_date,
    )


# ── Class Context ──────────────────────────────────────────────────────


def build_class_context(engine: Engine, boat_id: int) -> ClassContextFacts:
    """Place this boat in the context of her design class.

    Returns class TCC distribution stats, this boat's percentile, the
    top 5 boats in the class by wins, and the raw TCC list (for the
    histogram chart).
    """
    with engine.connect() as conn:
        boat = conn.execute(text("""
            SELECT COALESCE(design_canonical, design) AS design FROM boats WHERE id = :id
        """), {"id": boat_id}).first()
        if not boat or not boat.design:
            return ClassContextFacts(
                design="", class_n=0, class_tcc_min=0.0, class_tcc_max=0.0,
                class_tcc_median=0.0, class_tcc_mean=0.0,
                this_boat_tcc=0.0, this_boat_percentile=None,
            )
    sens = get_boat_sensitivity_context(engine, boat_id, boat.design)
    baseline = (sens or {}).get("class_baseline") or {}
    with engine.connect() as conn:
        top5 = conn.execute(text("""
            WITH latest AS (
                SELECT DISTINCT ON (b.id) b.id, b.boat_name, b.sail_number,
                       b.country, t.tcc,
                       (SELECT COUNT(*) FROM race_results r
                        WHERE r.boat_id = b.id AND r.place = 1) AS wins
                FROM boats b JOIN tcc_snapshots t ON t.boat_id = b.id
                WHERE COALESCE(b.design_canonical, b.design) = :design
                  AND t.tcc IS NOT NULL
                ORDER BY b.id, t.snapshot_date DESC
            )
            SELECT boat_name, sail_number, country, tcc, wins FROM latest
            ORDER BY wins DESC, tcc DESC LIMIT 5
        """), {"design": boat.design}).fetchall()
        all_tccs = [float(r.tcc) for r in conn.execute(text("""
            SELECT DISTINCT ON (b.id) t.tcc FROM boats b JOIN tcc_snapshots t ON t.boat_id = b.id
            WHERE COALESCE(b.design_canonical, b.design) = :design
              AND t.tcc IS NOT NULL
            ORDER BY b.id, t.snapshot_date DESC
        """), {"design": boat.design}).fetchall()]
    return ClassContextFacts(
        design=boat.design,
        class_n=baseline.get("n_boats") or len(all_tccs),
        class_tcc_min=baseline.get("min_tcc") or 0.0,
        class_tcc_max=baseline.get("max_tcc") or 0.0,
        class_tcc_median=baseline.get("median_tcc") or 0.0,
        class_tcc_mean=baseline.get("mean_tcc") or 0.0,
        this_boat_tcc=baseline.get("this_boat_tcc") or 0.0,
        this_boat_percentile=baseline.get("this_boat_percentile"),
        top_5_boats=[
            {"name": r.boat_name, "sail": r.sail_number,
             "tcc": float(r.tcc), "country": r.country, "wins": r.wins}
            for r in top5
        ],
        class_tcc_list=all_tccs,
    )


# ── Executive Summary ──────────────────────────────────────────────────


def build_executive_summary(engine: Engine, boat_id: int) -> ExecutiveSummaryFacts:
    """Pull the headline numbers + pre-compute three findings.

    Findings are computed from raw DB facts (not LLM-derived) so the
    executive summary cannot drift from reality. Claude only paraphrases.
    """
    with engine.connect() as conn:
        boat = conn.execute(text("""
            SELECT b.boat_name, b.sail_number, b.country,
                   COALESCE(b.design_canonical, b.design) AS design, t.tcc
            FROM boats b
            LEFT JOIN LATERAL (
                SELECT tcc FROM tcc_snapshots WHERE boat_id = b.id
                ORDER BY snapshot_date DESC LIMIT 1
            ) t ON true
            WHERE b.id = :id
        """), {"id": boat_id}).first()
        if not boat:
            return ExecutiveSummaryFacts(
                boat_name=f"boat #{boat_id}", sail_number="", design="",
                country=None, tcc_now=Decimal("0"),
                class_median_tcc=None, this_boat_percentile=None,
                finishes=0, wins=0, podiums=0,
                headline_finding_1="", headline_finding_2="",
                headline_finding_3="", top_recommendation=None,
            )

        race_row = conn.execute(text("""
            SELECT COUNT(*) FILTER (WHERE status='finished' AND place IS NOT NULL) AS finishes,
                   COUNT(*) FILTER (WHERE place = 1) AS wins,
                   COUNT(*) FILTER (WHERE place BETWEEN 1 AND 3) AS podiums
            FROM race_results WHERE boat_id = :id
        """), {"id": boat_id}).first()

    # Class median + percentile from the anatomy facts (already computed).
    anatomy = build_rating_anatomy(engine, boat_id)
    class_median = anatomy.class_median_tcc

    # Pre-cook findings from raw signals — no LLM in the loop here.
    findings: list[str] = []
    if class_median and boat.tcc and abs(float(boat.tcc) - class_median) > 0.005:
        gap = float(boat.tcc) - class_median
        direction = "above" if gap > 0 else "below"
        findings.append(
            f"Rates {gap:+.4f} TCC {direction} the {boat.design} median "
            f"({float(boat.tcc):.4f} vs {class_median:.4f})."
        )
    if race_row.finishes >= 10:
        win_pct = (race_row.wins / race_row.finishes) * 100
        findings.append(
            f"{race_row.wins} wins and {race_row.podiums} podiums "
            f"across {race_row.finishes} finishes ({win_pct:.0f}% win rate)."
        )
    if anatomy.decomposition:
        top = anatomy.decomposition[0]
        findings.append(
            f"Largest rating driver: {top.field} ({top.contrib_tcc:+.4f} TCC "
            f"vs class mean — this boat is {abs(top.delta):.2f}{'kg' if top.field=='displacement' else 'm' if 'per' in top.unit and 'm' in top.unit else ''} "
            f"{'above' if top.delta > 0 else 'below'} the class mean)."
        )
    while len(findings) < 3:
        findings.append("")

    return ExecutiveSummaryFacts(
        boat_name=boat.boat_name,
        sail_number=boat.sail_number or "",
        design=boat.design or "",
        country=boat.country,
        tcc_now=boat.tcc or Decimal("0"),
        class_median_tcc=class_median,
        this_boat_percentile=None,  # filled by class_context if available
        finishes=race_row.finishes or 0,
        wins=race_row.wins or 0,
        podiums=race_row.podiums or 0,
        headline_finding_1=findings[0],
        headline_finding_2=findings[1],
        headline_finding_3=findings[2],
        top_recommendation=None,  # filled by optimisation builder; default None
    )


# ── Racing Performance ─────────────────────────────────────────────────


def build_performance(engine: Engine, boat_id: int) -> PerformanceFacts:
    """Pulls overall race stats, last 20 results, by-event-type breakdown,
    RAI percentile from analysis.performance, and head-to-head against rivals."""
    from irc_data.analysis.performance import compute_head_to_head, compute_rai

    with engine.connect() as conn:
        boat = conn.execute(
            text("SELECT boat_name FROM boats WHERE id = :id"),
            {"id": boat_id},
        ).first()
        if not boat:
            return PerformanceFacts(
                boat_name=f"boat #{boat_id}",
                finishes=0, wins=0, podiums=0, distinct_events=0,
                rai_percentile=None, rai_interpretation=None,
            )

        # Overall stats
        stats = conn.execute(text("""
            SELECT COUNT(*) FILTER (WHERE status='finished' AND place IS NOT NULL) AS finishes,
                   COUNT(*) FILTER (WHERE place = 1) AS wins,
                   COUNT(*) FILTER (WHERE place BETWEEN 1 AND 3) AS podiums,
                   COUNT(DISTINCT event_name) AS distinct_events
            FROM race_results WHERE boat_id = :id
        """), {"id": boat_id}).first()

        # Last 20 results with a finishing place
        recent = conn.execute(text("""
            SELECT event_date, event_name, race_name, place, fleet_size,
                   class_name, status
            FROM race_results
            WHERE boat_id = :id AND place IS NOT NULL
            ORDER BY event_date DESC NULLS LAST, id DESC
            LIMIT 20
        """), {"id": boat_id}).fetchall()

        # By-event-type bucket — classify event_name patterns.
        # Twilight = casual; Offshore = passage/coastal; Series = regular pointscore.
        type_rows = conn.execute(text("""
            SELECT
              CASE
                WHEN LOWER(COALESCE(event_name,'')) ~ 'twilight' THEN 'twilight'
                WHEN LOWER(COALESCE(event_name,'')) ~ 'offshore|coastal|passage|sydney.*hobart|gladstone' THEN 'offshore'
                ELSE 'series'
              END AS bucket,
              COUNT(*) FILTER (WHERE status='finished' AND place IS NOT NULL) AS n,
              COUNT(*) FILTER (WHERE place = 1) AS wins,
              COUNT(*) FILTER (WHERE place BETWEEN 1 AND 3) AS podiums
            FROM race_results
            WHERE boat_id = :id
            GROUP BY bucket
        """), {"id": boat_id}).fetchall()

    by_event_type = {
        r.bucket: {"n": r.n or 0, "wins": r.wins or 0, "podiums": r.podiums or 0}
        for r in type_rows
    }

    # RAI from the analysis engine
    rai_pct: float | None = None
    rai_interp: str | None = None
    try:
        rai = compute_rai(engine, boat_id)
        if rai is not None:
            rai_pct = float(rai.rai)
            rai_interp = rai.interpretation
    except Exception as e:
        logger.warning("compute_rai failed for boat %s: %s", boat_id, e)

    # Head-to-head against rivals (min 2 meetings)
    h2h: list[RivalSummary] = []
    try:
        rivals = compute_head_to_head(engine, boat_id, min_meetings=2)
        for rec in rivals[:10]:
            with engine.connect() as conn:
                rb = conn.execute(text("""
                    SELECT b.country, t.tcc
                    FROM boats b
                    LEFT JOIN LATERAL (
                        SELECT tcc FROM tcc_snapshots WHERE boat_id = b.id
                        ORDER BY snapshot_date DESC LIMIT 1
                    ) t ON true
                    WHERE b.id = :id
                """), {"id": rec.rival_boat_id}).first()
            h2h.append(RivalSummary(
                boat_id=rec.rival_boat_id,
                name=rec.rival_name,
                sail_number=rec.rival_sail_number,
                country=(rb.country if rb else None),
                tcc=(rb.tcc if rb and rb.tcc else Decimal("0")),
                recent_finishes_count=rec.events_together,
                head_to_head_wins=rec.wins,
                head_to_head_losses=rec.losses,
            ))
    except Exception as e:
        logger.warning("compute_head_to_head failed for boat %s: %s", boat_id, e)

    return PerformanceFacts(
        boat_name=boat.boat_name,
        finishes=stats.finishes or 0,
        wins=stats.wins or 0,
        podiums=stats.podiums or 0,
        distinct_events=stats.distinct_events or 0,
        rai_percentile=rai_pct,
        rai_interpretation=rai_interp,
        recent_results=[
            RaceResultLite(
                event_date=r.event_date,
                event_name=r.event_name,
                race_name=r.race_name,
                place=r.place,
                fleet_size=r.fleet_size,
                class_name=r.class_name,
                status=r.status or "finished",
            ) for r in recent
        ],
        by_event_type=by_event_type,
        head_to_head=h2h,
    )
