"""AI insights service — assembles boat context and streams LLM responses.

The core flow:
1. Search for boat(s) matching the user query
2. Assemble a comprehensive context document from all DB tables
3. Stream the context to the model (irc_data.llm) with the system prompt
4. Return SSE events to the frontend
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.services.report.prompt_library import build_system_prompt

# System prompts now live in repo-root prompts/ (base + guardrails + voice + persona +
# knowledge pack); these names are thin aliases for the two call sites below.
SYSTEM_PROMPT_PREMIUM = build_system_prompt("analyst")
SYSTEM_PROMPT_TEASER = build_system_prompt("analyst", variant="teaser")

# Keep the old prompt as fallback for the free/general tier
SYSTEM_PROMPT = SYSTEM_PROMPT_TEASER


def _fmt(val) -> str:
    """Format a value for the context document."""
    if val is None:
        return "—"
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def build_boat_context(engine: Engine, boat_id: int, detail_level: str = "free") -> str | None:
    """Assemble the full context document for a boat.

    Args:
        engine: SQLAlchemy engine
        boat_id: Boat primary key
        detail_level: "free" (current) or "premium" (adds analytics sections)

    Returns a markdown string with all available data, or None if boat not found.
    """
    with engine.connect() as conn:
        # --- Identity ---
        boat = conn.execute(text("""
            SELECT b.*, t.tcc, t.non_spi_tcc, t.crew, t.snapshot_date as tcc_date,
                   t.lh, t.beam, t.draft, t.headsails, t.spinnakers, t.stix, t.avs, t.category
            FROM boats b
            LEFT JOIN LATERAL (
                SELECT * FROM tcc_snapshots WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
            ) t ON true
            WHERE b.id = :id
        """), {"id": boat_id}).first()

        if not boat:
            return None

        lines = []
        lines.append(f"# Boat: {boat.boat_name} ({boat.sail_number})")
        lines.append("")
        lines.append("## Identity")
        lines.append(f"- Design: {_fmt(boat.design_canonical or boat.design)}")
        if boat.builder:
            lines.append(f"- Builder: {boat.builder}")
        if boat.designer:
            lines.append(f"- Designer: {boat.designer}")
        if boat.year_built:
            lines.append(f"- Year built: {boat.year_built}")
        lines.append(f"- Country: {_fmt(boat.country)}")
        if boat.hull_id:
            lines.append(f"- Hull ID: {boat.hull_id}")

        # Previous identities
        identities = conn.execute(text("""
            SELECT boat_name, sail_number, owner, flag, source, observed_date
            FROM boat_identities WHERE boat_id = :id ORDER BY observed_date DESC NULLS LAST
        """), {"id": boat_id}).fetchall()

        if len(identities) > 1:
            lines.append(f"- Also known as: {', '.join(set(i.boat_name for i in identities if i.boat_name != boat.boat_name))}")

        # --- Current Ratings ---
        lines.append("")
        lines.append("## Current Ratings")
        if boat.tcc:
            lines.append(f"- IRC TCC: {boat.tcc} (snapshot {_fmt(boat.tcc_date)})")
            if boat.non_spi_tcc:
                lines.append(f"  - Non-spinnaker: {boat.non_spi_tcc}")
            if boat.crew:
                lines.append(f"  - Crew: {boat.crew}")

        # ORC rating
        orc = conn.execute(text("""
            SELECT gph, osn, cdl, triple_low, triple_med, triple_high, ref_no, snapshot_date,
                   loa, displacement, draft, class_name
            FROM orc_certificates WHERE boat_id = :id ORDER BY snapshot_date DESC LIMIT 1
        """), {"id": boat_id}).first()

        if orc:
            lines.append(f"- ORC GPH: {_fmt(orc.gph)} sec/mile (ref {orc.ref_no})")
            if orc.triple_low:
                lines.append(f"  - Triple Number: {orc.triple_low}/{orc.triple_med}/{orc.triple_high}")
            if orc.osn:
                lines.append(f"  - OSN: {orc.osn}")
            if orc.class_name:
                lines.append(f"  - ORC Class: {orc.class_name}")

        # --- Rating History ---
        snapshots = conn.execute(text("""
            SELECT snapshot_date, tcc, non_spi_tcc, crew, cert_year
            FROM tcc_snapshots WHERE boat_id = :id ORDER BY snapshot_date DESC
        """), {"id": boat_id}).fetchall()

        if snapshots:
            lines.append("")
            lines.append("## IRC Rating History")
            lines.append("| Date | TCC | Non-Spi | Crew | Cert Year |")
            lines.append("|------|-----|---------|------|-----------|")
            prev_tcc = None
            for s in snapshots[:20]:
                change = ""
                if prev_tcc and s.tcc != prev_tcc:
                    delta = s.tcc - prev_tcc
                    change = f" ({delta:+.4f})"
                lines.append(f"| {s.snapshot_date} | {s.tcc}{change} | {_fmt(s.non_spi_tcc)} | {_fmt(s.crew)} | {_fmt(s.cert_year)} |")
                prev_tcc = s.tcc

        # --- Measurements from certificates ---
        certs = conn.execute(text("""
            SELECT * FROM irc_certificates WHERE boat_id = :id ORDER BY issue_date DESC NULLS LAST
        """), {"id": boat_id}).fetchall()

        if certs:
            latest = certs[0]
            lines.append("")
            lines.append("## Measurements (latest certificate)")
            if latest.lh:
                lines.append(f"- LH: {latest.lh}m | Beam: {_fmt(latest.beam)}m | Draft: {_fmt(latest.draft)}m")
            if latest.displacement_kg:
                lines.append(f"- Displacement: {latest.displacement_kg}kg")
            if latest.p:
                lines.append(f"- Rig: P={latest.p}m E={_fmt(latest.e)}m J={_fmt(latest.j)}m")
            if latest.rig_type:
                lines.append(f"- Rig type: {latest.rig_type} | Mast: {_fmt(latest.mast_material)} | Spreaders: {_fmt(latest.spreaders)}")
            if latest.stix:
                lines.append(f"- STIX: {latest.stix} | AVS: {_fmt(latest.avs)}° | Category: {_fmt(latest.design_category)}")

            # Measurement diffs between consecutive certs
            if len(certs) >= 2:
                lines.append("")
                lines.append("## Measurement Changes Between Certificates")
                lines.append("| Field | Previous | Current | Delta |")
                lines.append("|-------|----------|---------|-------|")
                curr, prev = certs[0], certs[1]
                for field in ["lh", "beam", "draft", "displacement", "p", "e", "j", "stl"]:
                    c_val = getattr(curr, field, None)
                    p_val = getattr(prev, field, None)
                    if c_val is not None and p_val is not None and c_val != p_val:
                        delta = c_val - p_val
                        lines.append(f"| {field} | {p_val} | {c_val} | {delta:+} |")

        # --- Race Results ---
        results = conn.execute(text("""
            SELECT event_name, event_date, place, fleet_size, rating_value,
                   status, source, elapsed_time, corrected_time
            FROM race_results WHERE boat_id = :id ORDER BY event_date DESC NULLS LAST
        """), {"id": boat_id}).fetchall()

        if results:
            lines.append("")
            lines.append(f"## Race Results ({len(results)} on record)")
            lines.append("| Date | Event | Place | Fleet | TCC | Status |")
            lines.append("|------|-------|-------|-------|-----|--------|")
            for r in results[:20]:
                lines.append(f"| {_fmt(r.event_date)} | {r.event_name} | {_fmt(r.place)} | {_fmt(r.fleet_size)} | {_fmt(r.rating_value)} | {r.status} |")

            # Performance summary
            finished = [r for r in results if r.status == "finished" and r.place]
            if finished:
                wins = sum(1 for r in finished if r.place == 1)
                top3 = sum(1 for r in finished if r.place <= 3)
                avg_place = sum(r.place for r in finished) / len(finished)
                fleet_results = [r for r in finished if r.fleet_size]
                avg_pct = sum(r.place / r.fleet_size for r in fleet_results) / len(fleet_results) if fleet_results else None

                lines.append("")
                lines.append("### Performance Summary")
                lines.append(f"- Win rate: {wins}/{len(finished)} ({wins/len(finished)*100:.0f}%)")
                lines.append(f"- Top 3 rate: {top3}/{len(finished)} ({top3/len(finished)*100:.0f}%)")
                lines.append(f"- Avg finish position: {avg_place:.1f}")
                if avg_pct:
                    lines.append(f"- Avg fleet percentile: {avg_pct*100:.0f}th (lower = better)")

        # --- Design Class Context ---
        design = boat.design_canonical or boat.design
        if design:
            class_stats = conn.execute(text("""
                SELECT COUNT(*) as fleet_size,
                       MIN(t.tcc) as min_tcc, MAX(t.tcc) as max_tcc,
                       AVG(t.tcc) as avg_tcc, STDDEV(t.tcc) as stddev_tcc,
                       COUNT(DISTINCT b.country) as countries
                FROM boats b
                JOIN LATERAL (
                    SELECT tcc FROM tcc_snapshots WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
                ) t ON true
                WHERE COALESCE(b.design_canonical, b.design) = :design
            """), {"design": design}).first()

            if class_stats and class_stats.fleet_size > 1:
                lines.append("")
                lines.append(f"## Design Class: {design}")
                lines.append(f"- Fleet size: {class_stats.fleet_size} boats in {class_stats.countries} countries")
                lines.append(f"- TCC spread: {_fmt(class_stats.min_tcc)} – {_fmt(class_stats.max_tcc)} (avg {float(class_stats.avg_tcc):.4f})")

                if boat.tcc:
                    # Boats in the same country/region FIRST (these are the ones they race against)
                    # Determine country — from boat record, or infer from sail number prefix
                    boat_country = boat.country
                    local_fleet = []
                    if boat_country:
                        local_fleet = conn.execute(text("""
                            SELECT b.boat_name, b.sail_number, t.tcc, b.country
                            FROM boats b
                            JOIN LATERAL (
                                SELECT tcc FROM tcc_snapshots WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
                            ) t ON true
                            WHERE COALESCE(b.design_canonical, b.design) = :design
                              AND b.id != :id
                              AND b.country = :country
                              AND b.boat_name NOT LIKE '%- SEC%'
                              AND b.boat_name NOT LIKE '%(SH)%'
                            ORDER BY ABS(t.tcc - :tcc)
                            LIMIT 10
                        """), {"design": design, "id": boat_id, "tcc": boat.tcc, "country": boat_country}).fetchall()

                    if local_fleet:
                        lines.append("")
                        lines.append(f"### {design} Fleet in {boat_country} (boats you race against)")
                        lines.append("| Boat | Sail # | TCC | vs This Boat |")
                        lines.append("|------|--------|-----|-------------|")
                        for f in local_fleet:
                            delta = f.tcc - boat.tcc
                            lines.append(f"| {f.boat_name} | {f.sail_number} | {f.tcc} | {delta:+.4f} |")

                    # Also show closest-rated boats globally (for broader context)
                    global_fleet = conn.execute(text("""
                        SELECT b.boat_name, b.sail_number, t.tcc, b.country
                        FROM boats b
                        JOIN LATERAL (
                            SELECT tcc FROM tcc_snapshots WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
                        ) t ON true
                        WHERE COALESCE(b.design_canonical, b.design) = :design
                          AND b.id != :id
                          AND b.boat_name NOT LIKE '%- SEC%'
                          AND b.boat_name NOT LIKE '%(SH)%'
                        ORDER BY ABS(t.tcc - :tcc)
                        LIMIT 8
                    """), {"design": design, "id": boat_id, "tcc": boat.tcc}).fetchall()

                    if global_fleet:
                        lines.append("")
                        lines.append("### Closest-Rated Worldwide")
                        lines.append("| Boat | Sail # | TCC | vs This Boat | Country |")
                        lines.append("|------|--------|-----|-------------|---------|")
                        for f in global_fleet:
                            delta = f.tcc - boat.tcc
                            lines.append(f"| {f.boat_name} | {f.sail_number} | {f.tcc} | {delta:+.4f} | {_fmt(f.country)} |")

        # --- Premium Analytics Sections ---
        if detail_level == "premium":
            _append_premium_sections(engine, boat_id, boat, design, lines)

        return "\n".join(lines)


def _append_premium_sections(engine: Engine, boat_id: int, boat, design: str | None, lines: list[str]) -> None:
    """Append premium analytics sections to the context document."""
    import logging

    logger = logging.getLogger(__name__)

    # --- Measurement Sensitivity (Engine 1) ---
    if design:
        try:
            from irc_data.analysis.regression import get_boat_sensitivity_context

            sensitivity = get_boat_sensitivity_context(engine, boat_id, design)
            if sensitivity and sensitivity.get("coefficients"):
                tier = sensitivity.get("model_tier", "?")
                n = sensitivity.get("n_boats", 0)
                r2 = sensitivity.get("r_squared", 0)
                lines.append("")
                lines.append(f"## Measurement Sensitivity ({design}, n={n}, model R²={r2:.2f}, Tier {tier})")
                lines.append("| Lever | Your Value | Class Mean | Smart Boat Avg | TCC Impact/Unit | Rank |")
                lines.append("|-------|-----------|------------|----------------|-----------------|------|")

                boat_pos = sensitivity.get("boat_position", {})

                # Try to get smart boat data
                smart_means = {}
                try:
                    from irc_data.analysis.performance import get_smart_boats
                    smart_data = get_smart_boats(engine, design)
                    smart_means = smart_data.get("smart_boat_means", {})
                except Exception:
                    pass

                top_opportunity = None
                top_delta = 0

                for coef in sensitivity["coefficients"][:10]:
                    field = coef["field"]
                    pos = boat_pos.get(field, {})
                    your_val = pos.get("value", "—")
                    class_mean = pos.get("class_mean", "—")
                    smart_avg = smart_means.get(field, "—")
                    impact = f"{coef['beta_per_unit']:+.4f} {coef['unit']}"

                    lines.append(
                        f"| {field} | {your_val} | {class_mean} | {smart_avg} | {impact} | {coef['rank']} |"
                    )

                    # Track top opportunity
                    if isinstance(your_val, (int, float)) and isinstance(class_mean, (int, float)):
                        from irc_data.analysis.regression import SCALE_FACTORS
                        scale = SCALE_FACTORS.get(field, 1.0)
                        raw_beta = coef["beta_per_unit"] / scale if scale != 0 else 0
                        target = smart_avg if isinstance(smart_avg, (int, float)) else class_mean
                        delta = (target - your_val) * raw_beta
                        if abs(delta) > abs(top_delta):
                            top_delta = delta
                            top_opportunity = (field, your_val, target, delta)

                if top_opportunity:
                    f, curr, opt, delta = top_opportunity
                    lines.append(
                        f"\nTop opportunity: {f} — moving from {curr:.2f} to {opt:.2f} "
                        f"would save est. {delta:+.4f} TCC"
                    )

                if sensitivity.get("interpretation"):
                    lines.append(f"\n{sensitivity['interpretation']}")
        except Exception as e:
            logger.debug(f"Sensitivity section failed: {e}")

    # --- IRC Formula Trend (Engine 2) ---
    if design:
        try:
            from irc_data.analysis.temporal import analyze_fleet_drift

            drift = analyze_fleet_drift(engine, design=design)
            if drift:
                fw = drift.fleet_wide
                lines.append("")
                lines.append("## IRC Formula Trend")
                lines.append(
                    f"The IRC formula shifted {fw.mean_drift:+.4f} for {design} "
                    f"between {drift.date_from}–{drift.date_to} "
                    f"({fw.n_stable} stable boats, "
                    f"{'p<0.001' if fw.p_value_ttest and fw.p_value_ttest < 0.001 else f'p={fw.p_value_ttest:.4f}' if fw.p_value_ttest else 'p=N/A'})."
                )

                if drift.by_dimension:
                    strongest = drift.by_dimension[0]
                    lines.append(
                        f"Strongest change: {strongest.field} is now {strongest.direction} "
                        f"(coefficient change: {strongest.coefficient_change:+.4f})."
                    )

                # Does this boat's config benefit?
                if boat.tcc and drift.by_dimension:
                    lines.append(
                        "Your configuration may be affected by this trend — "
                        "see optimisation recommendations below."
                    )
        except Exception as e:
            logger.debug(f"Drift section failed: {e}")

    # --- Racing Performance (Engine 3) ---
    try:
        from irc_data.analysis.performance import compute_head_to_head, compute_rai

        rai_result = compute_rai(engine, boat_id)
        if rai_result:
            lines.append("")
            lines.append(f"## Racing Performance (RAI: {rai_result.rai:+.1f})")
            better_worse = "better" if rai_result.rai > 0 else "worse"
            lines.append(
                f"You finish {abs(rai_result.rai):.1f} percentile points {better_worse} "
                f"than your TCC predicts across {rai_result.n_races} races."
            )
            lines.append(rai_result.interpretation)
            lines.append(
                f"- Wins: {rai_result.wins} | Podiums: {rai_result.podiums} | "
                f"CI: [{rai_result.ci_lower:+.1f}, {rai_result.ci_upper:+.1f}]"
            )

            # Top rivals
            rivals = compute_head_to_head(engine, boat_id)
            if rivals:
                rival_strs = []
                for r in rivals[:5]:
                    total = r.wins + r.losses
                    rival_strs.append(f"{r.rival_name} ({r.wins}/{r.losses})")
                lines.append(f"- Top rivals: {', '.join(rival_strs)}")
    except Exception as e:
        logger.debug(f"Performance section failed: {e}")

    # --- Optimisation Opportunities (Engine 4) ---
    try:
        from irc_data.analysis.optimizer import generate_optimisation_report

        report = generate_optimisation_report(engine, boat_id)
        if report and report.recommendations:
            lines.append("")
            lines.append("## Optimisation Opportunities (ranked by impact x feasibility)")
            for i, rec in enumerate(report.recommendations[:5], 1):
                lines.append(
                    f"{i}. **{rec.field}** ({rec.category}): est. {rec.estimated_tcc_delta:+.4f} TCC "
                    f"— {rec.explanation} [{rec.evidence_strength} evidence, {rec.feasibility_label}]"
                )
    except Exception as e:
        logger.debug(f"Optimisation section failed: {e}")


def _compute_thinking_steps(engine: Engine, boat_id: int) -> list[dict]:
    """Compute 6 'report being generated' steps from real DB queries.

    These are streamed as SSE 'step' events before the AI text begins so the
    customer sees concrete signals of work (numbers tied to their boat) rather
    than a generic spinner. Every number must come from a real query — no
    placeholders, no fakes.
    """
    import asyncio  # noqa: F401 — placeholder for future async timing
    steps: list[dict] = []
    try:
        with engine.connect() as conn:
            # Step 1: total fleet indexed + this boat located
            total_boats = conn.execute(text("SELECT COUNT(*) FROM boats")).scalar() or 0
            boat = conn.execute(
                text("""
                    SELECT id, boat_name, sail_number, design, design_canonical, country, year_built
                    FROM boats WHERE id = :id
                """),
                {"id": boat_id},
            ).first()
            if not boat:
                return []

            boat_name = boat.boat_name or "this boat"
            sail = boat.sail_number or "—"
            design = boat.design or boat.design_canonical or "—"
            steps.append({
                "label": f"Read the certificate registry — indexed {total_boats:,} boats",
                "detail": f"Located {boat_name} ({sail} · {design})",
            })

            # Step 2: IRC certificates / TCC snapshots for this boat
            tcc_count = conn.execute(
                text("SELECT COUNT(*) FROM tcc_snapshots WHERE boat_id = :id"),
                {"id": boat_id},
            ).scalar() or 0
            cert_count = conn.execute(
                text("SELECT COUNT(*) FROM irc_certificates WHERE boat_id = :id"),
                {"id": boat_id},
            ).scalar() or 0
            latest_tcc = conn.execute(
                text("""
                    SELECT tcc, snapshot_date FROM tcc_snapshots
                    WHERE boat_id = :id AND tcc IS NOT NULL
                    ORDER BY snapshot_date DESC LIMIT 1
                """),
                {"id": boat_id},
            ).first()
            if tcc_count > 0 and latest_tcc is not None:
                tcc_val = float(latest_tcc.tcc) if latest_tcc.tcc else None
                yr = latest_tcc.snapshot_date.year if latest_tcc.snapshot_date else "—"
                tcc_str = f"{tcc_val:.4f}" if tcc_val else "—"
                steps.append({
                    "label": f"Pulled certificate history — {tcc_count} on file",
                    "detail": f"Current TCC {tcc_str} ({yr})",
                })
            elif cert_count > 0:
                steps.append({
                    "label": f"Pulled certificate history — {cert_count} on file",
                    "detail": "Parsed from the IRC PDF",
                })

            # Step 3: ORC certificate (if present)
            orc = conn.execute(
                text("""
                    SELECT class_name, gph FROM orc_certificates
                    WHERE boat_id = :id ORDER BY snapshot_date DESC LIMIT 1
                """),
                {"id": boat_id},
            ).first()
            if orc:
                gph = float(orc.gph) if orc.gph else None
                steps.append({
                    "label": "Read ORC certificate",
                    "detail": f"GPH {gph:.1f}" if gph else "Found",
                })

            # Step 4: sister boats in the same design class
            design_key = boat.design_canonical or boat.design
            if design_key:
                sisters = conn.execute(
                    text("""
                        SELECT COUNT(*) FROM boats
                        WHERE (design_canonical = :d OR design = :d)
                          AND id <> :id
                    """),
                    {"d": design_key, "id": boat_id},
                ).scalar() or 0
                if sisters > 0:
                    steps.append({
                        "label": f"Compared against {sisters} sister boats",
                        "detail": f"{design_key}",
                    })

            # Step 5: race results across events
            rr = conn.execute(
                text("""
                    SELECT COUNT(*) AS finishes, COUNT(DISTINCT event_name) AS events
                    FROM race_results WHERE boat_id = :id
                """),
                {"id": boat_id},
            ).first()
            if rr and (rr.finishes or 0) > 0:
                steps.append({
                    "label": f"Scored fleet performance — {rr.finishes:,} race finishes",
                    "detail": f"across {rr.events} events",
                })

            # Step 6: design class fleet TCC band (gives a frame)
            if design_key:
                band = conn.execute(
                    text("""
                        SELECT MIN(t.tcc) AS lo, MAX(t.tcc) AS hi, COUNT(DISTINCT b.id) AS n
                        FROM boats b
                        JOIN tcc_snapshots t ON t.boat_id = b.id
                        WHERE (b.design_canonical = :d OR b.design = :d)
                          AND t.snapshot_date = (
                            SELECT MAX(snapshot_date) FROM tcc_snapshots WHERE boat_id = b.id
                          )
                    """),
                    {"d": design_key},
                ).first()
                if band and band.n and band.lo and band.hi:
                    steps.append({
                        "label": f"Mapped class TCC band ({band.n} boats)",
                        "detail": f"{float(band.lo):.4f} – {float(band.hi):.4f}",
                    })
    except Exception as e:
        logger.debug(f"_compute_thinking_steps failed for boat {boat_id}: {e}")
    return steps


def _compute_thinking_prose(engine: Engine, boat_id: int) -> list[str]:
    """Return a list of prose paragraphs — what an analyst would mutter while
    opening the boat's file. Each item is one short thought (1–3 sentences),
    grounded entirely in real DB data.

    No numbers in headers, no checklists, no step labels. Used by the modal's
    prose-thinking stream — the frontend reveals them word by word so the user
    reads an analyst thinking aloud, not a job queue.
    """
    blocks: list[str] = []
    try:
        with engine.connect() as conn:
            boat = conn.execute(
                text("""
                    SELECT b.*, t.tcc, t.snapshot_date AS tcc_date,
                           t.headsails, t.spinnakers, t.crew
                    FROM boats b
                    LEFT JOIN LATERAL (
                        SELECT * FROM tcc_snapshots WHERE boat_id = b.id
                        ORDER BY snapshot_date DESC LIMIT 1
                    ) t ON true
                    WHERE b.id = :id
                """),
                {"id": boat_id},
            ).first()
            if not boat:
                return []

            name = boat.boat_name or "this boat"
            sail = boat.sail_number or "—"
            design = boat.design_canonical or boat.design
            country = boat.country

            # Opening — locating her in the registry
            opener = f"{name} — {sail}. Yes, here she is."
            if design and country:
                opener += f" A {design}, flagged {country}."
            elif design:
                opener += f" A {design}."
            elif country:
                opener += f" Flagged {country}."
            blocks.append(opener)

            # Hull provenance
            prov: list[str] = []
            if boat.designer:
                prov.append(f"{boat.designer} drew her")
            if boat.builder:
                prov.append(f"yard's listed as {boat.builder}")
            if boat.year_built:
                age = max(0, 2026 - int(boat.year_built))
                prov.append(f"laid down in {boat.year_built}, about {age} seasons in the water")
            if prov:
                blocks.append(". ".join(s.capitalize() if i == 0 else s for i, s in enumerate(prov)) + ".")

            # Identities — prior names / sail numbers (proxy for ownership change)
            identities = conn.execute(
                text("""
                    SELECT DISTINCT boat_name, sail_number, flag, source, observed_date
                    FROM boat_identities WHERE boat_id = :id
                """),
                {"id": boat_id},
            ).fetchall()
            prior_names = sorted({i.boat_name for i in identities if i.boat_name and i.boat_name.strip() and i.boat_name != name})
            prior_sails = sorted({i.sail_number for i in identities if i.sail_number and i.sail_number.strip() and i.sail_number != sail})
            if prior_names:
                listed = ", ".join(prior_names[:3])
                blocks.append(
                    f"She's been on the register before as {listed} — likely a rename, possibly a change of hands."
                )
            elif prior_sails:
                listed = ", ".join(prior_sails[:3])
                blocks.append(f"Her sail number has shifted at one point — also recorded as {listed}.")
            elif identities:
                # First-seen date — gives a sense of tenure
                dates = [i.observed_date for i in identities if i.observed_date]
                if dates:
                    earliest = min(dates)
                    blocks.append(
                        f"First on the system {earliest.strftime('%B %Y')}. Steady ownership — no name or sail-number changes recorded."
                    )

            # IRC certificate history
            tccs = conn.execute(
                text("""
                    SELECT snapshot_date, tcc FROM tcc_snapshots
                    WHERE boat_id = :id AND tcc IS NOT NULL
                    ORDER BY snapshot_date ASC
                """),
                {"id": boat_id},
            ).fetchall()
            if len(tccs) >= 2:
                first, last = tccs[0], tccs[-1]
                delta = float(last.tcc) - float(first.tcc)
                pts = int(round(abs(delta) * 1000))
                direction = "off her rating" if delta < 0 else "added to her rating"
                blocks.append(
                    f"Latest TCC sits at {float(last.tcc):.4f}. That's {pts} point"
                    f"{'s' if pts != 1 else ''} {direction} since the {first.snapshot_date.year} certificate — "
                    f"worth poking at where the movement came from."
                )
            elif len(tccs) == 1:
                blocks.append(
                    f"Single certificate on file — TCC {float(tccs[0].tcc):.4f}, dated {tccs[0].snapshot_date.year}."
                )

            # Declared inventory
            if boat.headsails or boat.spinnakers:
                decls: list[str] = []
                if boat.headsails:
                    h = int(boat.headsails)
                    decls.append(f"{h} headsail{'s' if h != 1 else ''}")
                if boat.spinnakers:
                    s = int(boat.spinnakers)
                    decls.append(f"{s} spinnaker{'s' if s != 1 else ''}")
                if boat.crew:
                    decls.append(f"crew of {int(boat.crew)}")
                blocks.append(f"Declared inventory reads: {', '.join(decls)}.")

            # Race results — most recent + career picture
            races = conn.execute(
                text("""
                    SELECT event_name, event_date, place, fleet_size,
                           class_name, class_place, class_fleet_size, status, source
                    FROM race_results WHERE boat_id = :id
                    ORDER BY event_date DESC NULLS LAST
                """),
                {"id": boat_id},
            ).fetchall()
            if races:
                # Most recent
                latest_with_place = next(
                    (r for r in races if r.place and r.event_date), None
                )
                if latest_with_place:
                    r = latest_with_place
                    fleet = r.class_fleet_size or r.fleet_size
                    place = r.class_place or r.place
                    when = r.event_date.strftime("%B %Y") if r.event_date else "recently"
                    if fleet:
                        blocks.append(
                            f"Last logged result: {place} of {fleet} at {r.event_name}, {when}."
                        )
                    else:
                        blocks.append(f"Last logged result: place {place} at {r.event_name}, {when}.")

                finished = [
                    r for r in races
                    if r.status == "finished" and r.place
                ]
                if len(finished) >= 5:
                    wins = sum(1 for r in finished if r.place == 1)
                    podiums = sum(1 for r in finished if r.place <= 3)
                    distinct_events = len({r.event_name for r in finished})
                    summary = (
                        f"{len(finished)} finishes across {distinct_events} event"
                        f"{'s' if distinct_events != 1 else ''} on record"
                    )
                    accolades: list[str] = []
                    if wins:
                        accolades.append(f"{wins} outright win{'s' if wins != 1 else ''}")
                    if podiums:
                        accolades.append(f"{podiums} podium{'s' if podiums != 1 else ''}")
                    if accolades:
                        summary += " — " + ", ".join(accolades)
                    elif distinct_events >= 3:
                        summary += " — no podium so far on this hull"
                    blocks.append(summary + ".")
                elif races:
                    blocks.append(
                        f"Race history is thin — {len(races)} entries on the system. "
                        "Worth thinking about whether more results are out there to pull in."
                    )
            else:
                blocks.append(
                    "No race results on file for this hull — either she's a club racer who doesn't publish, or the regatta scoring hasn't made it into the system yet."
                )

            # Design class context
            if design:
                band = conn.execute(
                    text("""
                        SELECT COUNT(DISTINCT b.id) AS n,
                               AVG(t.tcc) AS avg_tcc,
                               MIN(t.tcc) AS lo, MAX(t.tcc) AS hi
                        FROM boats b
                        JOIN LATERAL (
                            SELECT tcc FROM tcc_snapshots
                            WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
                        ) t ON true
                        WHERE COALESCE(b.design_canonical, b.design) = :d
                          AND t.tcc IS NOT NULL
                    """),
                    {"d": design},
                ).first()
                if band and band.n and band.n > 1 and band.avg_tcc and boat.tcc:
                    avg = float(band.avg_tcc)
                    lo, hi = float(band.lo), float(band.hi)
                    own = float(boat.tcc)
                    spread = hi - lo
                    if spread < 0.0005:
                        # All sister boats sit at the same rating — degenerate
                        # "band". Frame it differently: they all line up.
                        blocks.append(
                            f"Class context: {int(band.n)} {design}s on the system — every one of them rated at {avg:.4f}. "
                            "Either the class is tightly one-design under IRC, or the others haven't moved off the stock certificate."
                        )
                    else:
                        if own < avg - 0.005:
                            position = "she's a touch below the class average — she's rated favourably"
                        elif own > avg + 0.005:
                            position = "she sits above the class average — more declared than the median boat in her fleet"
                        else:
                            position = "she's bang in the middle of the class band"
                        blocks.append(
                            f"Class context: {int(band.n)} {design}s on the system, band running "
                            f"{lo:.4f} to {hi:.4f}, average {avg:.4f}. {position.capitalize()}."
                        )

            # ORC, if present
            orc = conn.execute(
                text("""
                    SELECT gph, class_name, snapshot_date FROM orc_certificates
                    WHERE boat_id = :id ORDER BY snapshot_date DESC LIMIT 1
                """),
                {"id": boat_id},
            ).first()
            if orc and orc.gph:
                blocks.append(
                    f"She's ORC-rated as well — GPH {float(orc.gph):.2f}"
                    f"{', class ' + orc.class_name if orc.class_name else ''}. "
                    "That gives a second lens on her actual speed potential."
                )

            # Segue to the report
            blocks.append("Alright — that's enough flipping through the file. Let me write up what the data is actually saying.")
    except Exception:
        # Never fail the stream on prose-assembly errors; just shorten.
        pass

    return blocks


def _chunk_prose_to_words(paragraph: str) -> list[str]:
    """Split a paragraph into stream-able tokens that preserve whitespace.

    The frontend simply appends these — we keep punctuation attached so words
    like "TCC." or "1985," don't get awkwardly broken.
    """
    out: list[str] = []
    buf = ""
    for ch in paragraph:
        buf += ch
        if ch == " ":
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


async def stream_insight(engine: Engine, boat_id: int, question: str | None = None, detail_level: str = "free", thinking_style: str = "steps"):
    """Stream an AI insight about a boat using Claude.

    Yields SSE-formatted events:
    - {"type": "step", "data": {...}}        (when thinking_style == "steps")
    - {"type": "thought_chunk", "data": "…"} (when thinking_style == "prose")
    - {"type": "phase", "data": "report"}    (prose → report transition)
    - {"type": "text", "data": "chunk..."}   (LLM-generated report text)
    - {"type": "done", "data": {"boat_id": ...}}

    Requires ANTHROPIC_API_KEY (see irc_data.llm for model configuration).
    """
    import asyncio

    from irc_data import llm

    try:
        llm.client()
    except llm.LLMNotConfigured as e:
        yield {"type": "error", "data": str(e)}
        return

    # Pre-compute everything that involves sync DB work, so once events start
    # flowing, only the model stream is outstanding.
    context = build_boat_context(engine, boat_id, detail_level=detail_level)
    if not context:
        yield {"type": "error", "data": f"Boat {boat_id} not found"}
        return

    if thinking_style == "prose":
        prose_blocks = _compute_thinking_prose(engine, boat_id)
    else:
        steps_to_emit = _compute_thinking_steps(engine, boat_id)

    # Build user message
    if question:
        user_message = f"{context}\n\n---\n\nUser question: {question}"
    else:
        user_message = context

    if thinking_style == "prose":
        # Stream prose word-by-word. Between paragraphs we hold a slightly
        # longer beat so the reader catches the break.
        WORD_DELAY = 0.055
        PARAGRAPH_BEAT = 0.55
        for i, paragraph in enumerate(prose_blocks):
            if i > 0:
                # Paragraph break in the stream
                yield {"type": "thought_chunk", "data": "\n\n"}
                await asyncio.sleep(PARAGRAPH_BEAT)
            tokens = _chunk_prose_to_words(paragraph)
            for tok in tokens:
                yield {"type": "thought_chunk", "data": tok}
                await asyncio.sleep(WORD_DELAY)
        # Transition marker — frontend uses this to switch into report mode
        yield {"type": "phase", "data": "report"}
        await asyncio.sleep(0.4)
    else:
        # Legacy: stepped checklist with a final "drafting" placeholder
        dwells = [0.55, 0.75, 0.95, 1.05, 1.15, 1.25]
        for i, step in enumerate(steps_to_emit):
            yield {"type": "step", "data": step}
            dwell = dwells[i] if i < len(dwells) else 1.0
            await asyncio.sleep(dwell)
        yield {"type": "step", "data": {
            "label": "Drafting the executive summary",
            "detail": "Reading your file",
        }}
        await asyncio.sleep(0)  # force flush before the sync model call

    # Select system prompt based on detail level. Length is set by the prompt;
    # max_tokens is only a ceiling (it also covers adaptive thinking).
    system_prompt = SYSTEM_PROMPT_PREMIUM if detail_level == "premium" else SYSTEM_PROMPT_TEASER

    # Stream the model's text and emit a properly-shaped $ai_generation event
    # ourselves so PostHog LLM Analytics picks it up.
    from irc_data.api.services.analytics_service import track
    import time as _time
    import uuid as _uuid
    _started = _time.time()
    _output_text_parts: list[str] = []
    _model = llm.model("insights")
    _trace_id = str(_uuid.uuid4())
    _input_messages = [{"role": "user", "content": user_message}]
    _span = "insights.teaser_stream" if detail_level == "free" else "insights.premium_stream"

    try:
        with llm.stream(system=system_prompt, user=user_message, feature="insights") as response_stream:
            for text in response_stream.text_stream:
                if text:
                    _output_text_parts.append(text)
                    yield {"type": "text", "data": text}
            final = llm.check(response_stream.get_final_message())

        _output_text = "".join(_output_text_parts)
        track("$ai_generation", str(boat_id), {
            "$ai_provider": llm.PROVIDER,
            "$ai_model": final.model,
            "$ai_input": _input_messages,
            "$ai_output_choices": [
                {"role": "assistant", "content": _output_text}
            ],
            "$ai_input_tokens": final.usage.input_tokens,
            "$ai_output_tokens": final.usage.output_tokens,
            "$ai_latency": _time.time() - _started,
            "$ai_http_status": 200,
            "$ai_is_error": False,
            "$ai_trace_id": _trace_id,
            "$ai_span_name": _span,
            "$ai_base_url": llm.API_BASE_URL,
            "$ai_request_url": f"{llm.API_BASE_URL}/v1/messages",
            "endpoint": "insights/ask",
            "detail_level": detail_level,
            "boat_id": boat_id,
        })
        yield {"type": "done", "data": {"boat_id": boat_id}}

    except Exception as e:
        track("$ai_generation", str(boat_id), {
            "$ai_provider": llm.PROVIDER,
            "$ai_model": _model,
            "$ai_input": _input_messages,
            "$ai_output_choices": [
                {"role": "assistant", "content": "".join(_output_text_parts)}
            ] if _output_text_parts else [],
            "$ai_latency": _time.time() - _started,
            "$ai_http_status": 500,
            "$ai_is_error": True,
            "$ai_error": str(e)[:500],
            "$ai_trace_id": _trace_id,
            "$ai_span_name": _span,
            "endpoint": "insights/ask",
            "detail_level": detail_level,
            "boat_id": boat_id,
            "error": str(e)[:200],
        })
        yield {"type": "error", "data": str(e)}
