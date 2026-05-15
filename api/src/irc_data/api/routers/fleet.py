"""Fleet endpoints -- country-level fleet statistics."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("/countries")
def list_countries(
    engine: Engine = Depends(get_db),
):
    """List all countries with boat counts and average TCC."""
    sql = text("""
        SELECT
            b.country,
            count(*)                          AS boat_count,
            round(avg(lat.tcc)::numeric, 4)   AS avg_tcc,
            count(DISTINCT b.design)          AS design_count
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT t.tcc
            FROM tcc_snapshots t
            WHERE t.boat_id = b.id
            ORDER BY t.snapshot_date DESC
            LIMIT 1
        ) lat ON true
        WHERE b.country IS NOT NULL
          AND b.country != ''
        GROUP BY b.country
        ORDER BY boat_count DESC
    """)

    with engine.connect() as conn:
        rows = conn.execute(sql)
        results = [dict(r._mapping) for r in rows]

    return {"total": len(results), "results": results}


@router.get("/countries/{code}")
def get_country_fleet(
    code: str,
    sort: str = Query("name", pattern="^(name|tcc|design)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    engine: Engine = Depends(get_db),
):
    """All boats registered to a given country code."""
    order_map = {
        "name": "b.boat_name",
        "tcc": "lat.tcc DESC NULLS LAST",
        "design": "b.design, b.boat_name",
    }
    order = order_map[sort]
    country_upper = code.upper()

    sql = text(f"""
        SELECT
            b.id,
            b.boat_name,
            b.sail_number,
            b.design,
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
        WHERE UPPER(b.country) = :country
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
    """)
    count_sql = text(
        "SELECT count(*) FROM boats WHERE UPPER(country) = :country"
    )

    with engine.connect() as conn:
        total = conn.execute(count_sql, {"country": country_upper}).scalar()
        rows = conn.execute(sql, {
            "country": country_upper, "limit": limit, "offset": offset,
        })
        results = [dict(r._mapping) for r in rows]

    if total == 0:
        raise HTTPException(
            status_code=404, detail=f"No boats found for country '{code}'"
        )

    return {
        "country": country_upper,
        "total": total,
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "results": results,
    }
