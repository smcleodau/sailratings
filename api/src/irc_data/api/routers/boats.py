"""Boat detail, rating history, certificates, results, and polar endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(prefix="/boats", tags=["boats"])


@router.get("/{boat_id}")
def get_boat(
    boat_id: int,
    engine: Engine = Depends(get_db),
):
    """Full boat detail with latest IRC and ORC ratings."""
    sql = text("""
        SELECT
            b.*,
            irc_lat.tcc          AS irc_tcc,
            irc_lat.non_spi_tcc  AS irc_non_spi_tcc,
            irc_lat.snapshot_date AS irc_snapshot_date,
            irc_lat.endorsed     AS irc_endorsed,
            irc_lat.crew         AS irc_crew,
            irc_lat.lh           AS irc_lh,
            irc_lat.beam         AS irc_beam,
            irc_lat.draft        AS irc_draft,
            orc_lat.gph          AS orc_gph,
            orc_lat.cdl          AS orc_cdl,
            orc_lat.triple_low   AS orc_triple_low,
            orc_lat.triple_med   AS orc_triple_med,
            orc_lat.triple_high  AS orc_triple_high,
            orc_lat.snapshot_date AS orc_snapshot_date,
            orc_lat.loa          AS orc_loa,
            orc_lat.displacement AS orc_displacement
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT t.tcc, t.non_spi_tcc, t.snapshot_date, t.endorsed,
                   t.crew, t.lh, t.beam, t.draft
            FROM tcc_snapshots t
            WHERE t.boat_id = b.id
            ORDER BY t.snapshot_date DESC
            LIMIT 1
        ) irc_lat ON true
        LEFT JOIN LATERAL (
            SELECT o.gph, o.cdl, o.triple_low, o.triple_med, o.triple_high,
                   o.snapshot_date, o.loa, o.displacement
            FROM orc_certificates o
            WHERE o.boat_id = b.id
               OR o.boat_id IN (
                   SELECT sec.id FROM boats sec
                   WHERE sec.sail_number = b.sail_number
                     AND sec.boat_name = b.boat_name || ' - SEC'
               )
            ORDER BY o.gph NULLS LAST, o.snapshot_date DESC
            LIMIT 1
        ) orc_lat ON true
        WHERE b.id = :boat_id
    """)

    with engine.connect() as conn:
        row = conn.execute(sql, {"boat_id": boat_id}).first()

    if not row:
        raise HTTPException(status_code=404, detail="Boat not found")

    result = dict(row._mapping)

    # Include secondary certificate (SEC) data if this boat has one.
    # SEC certs are separate boats with " - SEC" suffix and same sail number.
    with engine.connect() as conn:
        sec_row = conn.execute(text("""
            SELECT
                sec_b.id AS sec_boat_id,
                sec_t.tcc AS sec_tcc,
                sec_t.non_spi_tcc AS sec_non_spi_tcc,
                sec_t.snapshot_date AS sec_snapshot_date,
                sec_t.headsails AS sec_headsails,
                sec_t.spinnakers AS sec_spinnakers,
                sec_t.crew AS sec_crew
            FROM boats sec_b
            LEFT JOIN LATERAL (
                SELECT t.tcc, t.non_spi_tcc, t.snapshot_date, t.headsails, t.spinnakers, t.crew
                FROM tcc_snapshots t WHERE t.boat_id = sec_b.id
                ORDER BY t.snapshot_date DESC LIMIT 1
            ) sec_t ON true
            WHERE sec_b.sail_number = :sail
              AND sec_b.boat_name = :name || ' - SEC'
            LIMIT 1
        """), {
            "sail": result.get("sail_number"),
            "name": result.get("boat_name"),
        }).first()

        if sec_row:
            result["secondary_certificate"] = dict(sec_row._mapping)

    return result


@router.get("/{boat_id}/ratings")
def get_boat_ratings(
    boat_id: int,
    system: str = Query("all", pattern="^(irc|orc|all)$"),
    engine: Engine = Depends(get_db),
):
    """Rating history for a boat, filterable by rating system."""
    _ensure_boat_exists(boat_id, engine)

    result: dict = {"boat_id": boat_id, "system": system}

    if system in ("irc", "all"):
        irc_sql = text("""
            SELECT
                t.snapshot_date,
                t.cert_year,
                t.tcc,
                t.non_spi_tcc,
                t.endorsed,
                t.secondary,
                t.crew,
                t.lh, t.beam, t.draft
            FROM tcc_snapshots t
            WHERE t.boat_id = :boat_id
            ORDER BY t.snapshot_date DESC
        """)
        with engine.connect() as conn:
            rows = conn.execute(irc_sql, {"boat_id": boat_id})
            result["irc"] = [dict(r._mapping) for r in rows]

            # Include SEC certificate rating history if one exists
            sec_sql = text("""
                SELECT
                    t.snapshot_date,
                    t.cert_year,
                    t.tcc,
                    t.non_spi_tcc,
                    t.endorsed,
                    t.secondary,
                    t.crew,
                    t.lh, t.beam, t.draft
                FROM tcc_snapshots t
                JOIN boats sec_b ON sec_b.id = t.boat_id
                JOIN boats pri_b ON pri_b.id = :boat_id
                WHERE sec_b.sail_number = pri_b.sail_number
                  AND sec_b.boat_name = pri_b.boat_name || ' - SEC'
                ORDER BY t.snapshot_date DESC
            """)
            sec_rows = conn.execute(sec_sql, {"boat_id": boat_id})
            sec_list = [dict(r._mapping) for r in sec_rows]
            if sec_list:
                result["irc_secondary"] = sec_list

    if system in ("orc", "all"):
        orc_sql = text("""
            SELECT
                o.snapshot_date,
                o.ref_no,
                o.gph,
                o.osn,
                o.cdl,
                o.triple_low,
                o.triple_med,
                o.triple_high,
                o.loa,
                o.displacement,
                o.draft,
                o.sail_area_upwind,
                o.sail_area_downwind,
                o.stability_index
            FROM orc_certificates o
            WHERE o.boat_id = :boat_id
            ORDER BY o.snapshot_date DESC
        """)
        with engine.connect() as conn:
            rows = conn.execute(orc_sql, {"boat_id": boat_id})
            result["orc"] = [dict(r._mapping) for r in rows]

    return result


@router.get("/{boat_id}/certificates")
def get_boat_certificates(
    boat_id: int,
    system: str = Query("all", pattern="^(irc|orc|all)$"),
    engine: Engine = Depends(get_db),
):
    """Certificate documents for a boat."""
    _ensure_boat_exists(boat_id, engine)

    result: dict = {"boat_id": boat_id, "system": system}

    if system in ("irc", "all"):
        irc_sql = text("""
            SELECT
                c.id,
                c.cert_number,
                c.issue_date,
                c.source,
                c.source_url,
                c.lh, c.beam, c.draft, c.displacement,
                c.rig_type,
                c.stix, c.avs,
                c.design_category,
                c.scraped_at
            FROM certificates c
            WHERE c.boat_id = :boat_id
            ORDER BY c.issue_date DESC NULLS LAST
        """)
        with engine.connect() as conn:
            rows = conn.execute(irc_sql, {"boat_id": boat_id})
            result["irc"] = [dict(r._mapping) for r in rows]

    if system in ("orc", "all"):
        orc_sql = text("""
            SELECT
                o.id,
                o.ref_no,
                o.country_id,
                o.snapshot_date,
                o.yacht_name,
                o.sail_no,
                o.class_name,
                o.gph,
                o.osn,
                o.cdl,
                o.triple_low, o.triple_med, o.triple_high,
                o.loa, o.displacement, o.draft,
                o.sail_area_upwind, o.sail_area_downwind,
                o.stability_index,
                o.created_at
            FROM orc_certificates o
            WHERE o.boat_id = :boat_id
            ORDER BY o.snapshot_date DESC
        """)
        with engine.connect() as conn:
            rows = conn.execute(orc_sql, {"boat_id": boat_id})
            result["orc"] = [dict(r._mapping) for r in rows]

    return result


@router.get("/{boat_id}/results")
def get_boat_results(
    boat_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    engine: Engine = Depends(get_db),
):
    """Race results for a boat with performance statistics."""
    _ensure_boat_exists(boat_id, engine)

    results_sql = text("""
        SELECT
            r.id,
            r.event_name,
            r.event_date,
            r.race_name,
            r.race_number,
            r.place,
            r.fleet_size,
            r.class_name,
            r.class_place,
            r.class_fleet_size,
            r.status,
            r.rating_type,
            r.rating_value,
            r.tcc_at_race,
            r.division,
            r.elapsed_time,
            r.corrected_time,
            r.source
        FROM race_results r
        WHERE r.boat_id = :boat_id
        ORDER BY r.event_date DESC NULLS LAST, r.race_number
        LIMIT :limit OFFSET :offset
    """)
    count_sql = text(
        "SELECT count(*) FROM race_results WHERE boat_id = :boat_id"
    )
    stats_sql = text("""
        SELECT
            count(*)                                         AS total_races,
            count(*) FILTER (WHERE place = 1)                AS wins,
            count(*) FILTER (WHERE place <= 3)               AS podiums,
            round(avg(place)::numeric, 1)                    AS avg_place,
            round(avg(
                CASE WHEN fleet_size > 0
                     THEN place::numeric / fleet_size
                END
            ), 3)                                            AS avg_fleet_pct,
            min(event_date)                                  AS first_race,
            max(event_date)                                  AS last_race,
            count(DISTINCT event_name)                       AS distinct_events
        FROM race_results
        WHERE boat_id = :boat_id
          AND status IS DISTINCT FROM 'dnf'
          AND status IS DISTINCT FROM 'dns'
          AND status IS DISTINCT FROM 'dsq'
    """)

    with engine.connect() as conn:
        total = conn.execute(count_sql, {"boat_id": boat_id}).scalar()
        rows = conn.execute(results_sql, {
            "boat_id": boat_id, "limit": limit, "offset": offset,
        })
        results = [dict(r._mapping) for r in rows]
        stats_row = conn.execute(stats_sql, {"boat_id": boat_id}).first()
        stats = dict(stats_row._mapping) if stats_row else {}

    return {
        "boat_id": boat_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "stats": stats,
        "results": results,
    }


@router.get("/{boat_id}/polars")
def get_boat_polars(
    boat_id: int,
    engine: Engine = Depends(get_db),
):
    """ORC polar data extracted from the raw_data JSONB on orc_certificates.

    Returns wind-speed/angle polar arrays when available.
    """
    _ensure_boat_exists(boat_id, engine)

    sql = text("""
        SELECT
            o.id                   AS cert_id,
            o.ref_no,
            o.snapshot_date,
            o.yacht_name,
            o.raw_data->'polar'    AS polar,
            o.raw_data->'vpp'      AS vpp,
            o.raw_data->'speeds'   AS speeds,
            o.raw_data->'angles'   AS angles,
            o.raw_data->'Allowances' AS allowances
        FROM orc_certificates o
        WHERE o.boat_id = :boat_id
          AND o.raw_data IS NOT NULL
        ORDER BY o.snapshot_date DESC
        LIMIT 1
    """)

    with engine.connect() as conn:
        row = conn.execute(sql, {"boat_id": boat_id}).first()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="No ORC polar data available for this boat",
        )

    data = dict(row._mapping)
    # If none of the JSONB keys contained actual polar info, report that.
    if not data.get("polar") and not data.get("vpp") and not data.get("speeds"):
        raise HTTPException(
            status_code=404,
            detail="ORC certificate exists but contains no polar data",
        )

    return {
        "boat_id": boat_id,
        "cert_id": data["cert_id"],
        "ref_no": data["ref_no"],
        "snapshot_date": data["snapshot_date"],
        "yacht_name": data["yacht_name"],
        "polar": data.get("polar"),
        "vpp": data.get("vpp"),
        "speeds": data.get("speeds"),
        "angles": data.get("angles"),
        "allowances": data.get("allowances"),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_boat_exists(boat_id: int, engine: Engine) -> None:
    """Raise 404 if the boat does not exist."""
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM boats WHERE id = :boat_id"),
            {"boat_id": boat_id},
        ).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Boat not found")
