"""Design class endpoints -- fleet composition and design statistics."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(prefix="/designs", tags=["designs"])


@router.get("")
def list_designs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("fleet_size", pattern="^(fleet_size|name|tcc_avg)$"),
    engine: Engine = Depends(get_db),
):
    """List design classes with fleet counts and average ratings.

    Uses the mv_design_class_stats materialized view when available,
    falling back to a live aggregation query.
    """
    order_map = {
        "fleet_size": "fleet_size DESC",
        "name": "design",
        "tcc_avg": "avg_tcc DESC NULLS LAST",
    }
    order = order_map[sort]

    # Try materialized view first, fall back to live query.
    sql = text(f"""
        SELECT
            b.design,
            count(*)                                      AS fleet_size,
            round(avg(lat.tcc)::numeric, 4)               AS avg_tcc,
            round(min(lat.tcc)::numeric, 4)               AS min_tcc,
            round(max(lat.tcc)::numeric, 4)               AS max_tcc,
            count(DISTINCT b.country)                     AS countries,
            min(b.year_built)                             AS year_first,
            max(b.year_built)                             AS year_last
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT t.tcc
            FROM tcc_snapshots t
            WHERE t.boat_id = b.id
            ORDER BY t.snapshot_date DESC
            LIMIT 1
        ) lat ON true
        WHERE b.design IS NOT NULL
          AND b.design != ''
        GROUP BY b.design
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
    """)
    count_sql = text("""
        SELECT count(DISTINCT design)
        FROM boats
        WHERE design IS NOT NULL AND design != ''
    """)

    with engine.connect() as conn:
        total = conn.execute(count_sql).scalar()
        rows = conn.execute(sql, {"limit": limit, "offset": offset})
        results = [dict(r._mapping) for r in rows]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "results": results,
    }


@router.get("/{design_name}")
def get_design(
    design_name: str,
    engine: Engine = Depends(get_db),
):
    """Detail for a single design class: stats, canonical info, and TCC distribution."""
    stats_sql = text("""
        SELECT
            b.design,
            count(*)                                      AS fleet_size,
            round(avg(lat.tcc)::numeric, 4)               AS avg_tcc,
            round(stddev(lat.tcc)::numeric, 4)            AS stddev_tcc,
            round(min(lat.tcc)::numeric, 4)               AS min_tcc,
            round(max(lat.tcc)::numeric, 4)               AS max_tcc,
            count(DISTINCT b.country)                     AS countries,
            min(b.year_built)                             AS year_first,
            max(b.year_built)                             AS year_last,
            round(avg(lat.lh)::numeric, 2)                AS avg_lh,
            round(avg(lat.beam)::numeric, 2)              AS avg_beam,
            round(avg(lat.draft)::numeric, 2)             AS avg_draft
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT t.tcc, t.lh, t.beam, t.draft
            FROM tcc_snapshots t
            WHERE t.boat_id = b.id
            ORDER BY t.snapshot_date DESC
            LIMIT 1
        ) lat ON true
        WHERE LOWER(b.design) = LOWER(:design_name)
        GROUP BY b.design
    """)

    # Also fetch canonical design_classes info if it exists.
    canonical_sql = text("""
        SELECT
            dc.name_canonical,
            dc.aliases,
            dc.builder,
            dc.designer,
            dc.nominal_loa,
            dc.nominal_lwl,
            dc.nominal_beam,
            dc.nominal_draft,
            dc.nominal_displacement,
            dc.year_first,
            dc.year_last
        FROM design_classes dc
        WHERE LOWER(dc.name_canonical) = LOWER(:design_name)
           OR CAST(dc.aliases AS text) ILIKE '%%' || :design_name || '%%'
        LIMIT 1
    """)

    with engine.connect() as conn:
        stats_row = conn.execute(stats_sql, {"design_name": design_name}).first()
        canonical_row = conn.execute(canonical_sql, {"design_name": design_name}).first()

    if not stats_row:
        raise HTTPException(status_code=404, detail="Design not found")

    result = dict(stats_row._mapping)
    if canonical_row:
        result["canonical"] = dict(canonical_row._mapping)

    return result


@router.get("/{design_name}/fleet")
def get_design_fleet(
    design_name: str,
    sort: str = Query("tcc", pattern="^(tcc|name|country)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    engine: Engine = Depends(get_db),
):
    """All boats in a design class with their current IRC ratings."""
    order_map = {
        "tcc": "lat.tcc DESC NULLS LAST",
        "name": "b.boat_name",
        "country": "b.country, b.boat_name",
    }
    order = order_map[sort]

    sql = text(f"""
        SELECT
            b.id,
            b.boat_name,
            b.sail_number,
            b.country,
            b.year_built,
            lat.tcc,
            lat.non_spi_tcc,
            lat.snapshot_date AS rating_date
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT t.tcc, t.non_spi_tcc, t.snapshot_date
            FROM tcc_snapshots t
            WHERE t.boat_id = b.id
            ORDER BY t.snapshot_date DESC
            LIMIT 1
        ) lat ON true
        WHERE LOWER(b.design) = LOWER(:design_name)
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
    """)
    count_sql = text("""
        SELECT count(*) FROM boats
        WHERE LOWER(design) = LOWER(:design_name)
    """)

    with engine.connect() as conn:
        total = conn.execute(count_sql, {"design_name": design_name}).scalar()
        rows = conn.execute(sql, {
            "design_name": design_name, "limit": limit, "offset": offset,
        })
        results = [dict(r._mapping) for r in rows]

    if total == 0:
        raise HTTPException(status_code=404, detail="Design not found")

    return {
        "design": design_name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "results": results,
    }
