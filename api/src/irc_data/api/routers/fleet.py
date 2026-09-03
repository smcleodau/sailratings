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
    """List all countries with boat counts, average TCC, and ORC stats.

    OPS-02-10: the ORC block is sourced from ``mv_orc_country_fleet`` so
    dual-rated pages have ORC numbers to stand on without re-scanning
    ``orc_certificates`` per request.  Falls back to a live aggregate when
    the materialised view has not been created yet (pre-migration DBs).
    """
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
        orc_stats = _country_orc_stats(conn)

    for row in results:
        row["orc"] = orc_stats.get(row["country"].upper())

    return {"total": len(results), "results": results}


def _country_orc_stats(conn) -> dict[str, dict]:
    """Latest-snapshot ORC fleet stats keyed by upper-cased country code.

    Reads ``mv_orc_country_fleet`` when present (migration 0030); falls back
    to a live aggregate over ``orc_certificates`` otherwise so the endpoint
    keeps working on databases that have not run 0030 yet.
    """
    fallback_sql = text("""
        SELECT
            country_id AS country,
            COUNT(*) AS cert_count,
            COUNT(gph) AS n_with_gph,
            COUNT(cdl) AS n_with_cdl,
            COUNT(allowances) AS n_with_allowances,
            round(avg(gph)::numeric, 2) AS avg_gph,
            round(min(gph)::numeric, 2) AS min_gph,
            round(max(gph)::numeric, 2) AS max_gph,
            round(avg(cdl)::numeric, 3) AS avg_cdl,
            count(DISTINCT class_name) AS design_count
        FROM orc_certificates
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM orc_certificates)
          AND country_id IS NOT NULL AND country_id != ''
        GROUP BY country_id
    """)
    mv_sql = text("""
        SELECT
            country,
            cert_count,
            n_with_gph,
            n_with_cdl,
            n_with_allowances,
            round(avg_gph::numeric, 2) AS avg_gph,
            round(min_gph::numeric, 2) AS min_gph,
            round(max_gph::numeric, 2) AS max_gph,
            round(avg_cdl::numeric, 3) AS avg_cdl,
            design_count
        FROM mv_orc_country_fleet
    """)
    try:
        rows = conn.execute(mv_sql).fetchall()
    except Exception:
        conn.rollback()
        rows = conn.execute(fallback_sql).fetchall()

    out: dict[str, dict] = {}
    for r in rows:
        d = dict(r._mapping)
        for k in ("avg_gph", "min_gph", "max_gph", "avg_cdl"):
            if d.get(k) is not None:
                d[k] = float(d[k])
        out[d["country"].upper()] = {
            "cert_count": d["cert_count"],
            "with_gph": d["n_with_gph"],
            "with_cdl": d["n_with_cdl"],
            "with_allowances": d["n_with_allowances"],
            "avg_gph": d.get("avg_gph"),
            "min_gph": d.get("min_gph"),
            "max_gph": d.get("max_gph"),
            "avg_cdl": d.get("avg_cdl"),
            "design_count": d["design_count"],
        }
    return out


@router.get("/countries/{code}")
def get_country_fleet(
    code: str,
    sort: str = Query("name", pattern="^(name|tcc|design)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    engine: Engine = Depends(get_db),
):
    """All boats registered to a given country code.

    OPS-02-10: each row carries a ``dual_rated`` flag plus the ORC
    certificate's GPH/CDL when the boat is linked to an ORC certificate,
    and the response's ``orc_designs`` block lists the ORC stats for every
    dual-rated design in the fleet (mean/min/max GPH, CDL, triple numbers)
    so the design comparator has ORC numbers to stand on.
    """
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
            lat.snapshot_date AS rating_date,
            orc.ref_no        AS orc_ref_no,
            orc.gph           AS orc_gph,
            orc.cdl           AS orc_cdl,
            (orc.ref_no IS NOT NULL) AS dual_rated
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT t.tcc, t.non_spi_tcc, t.snapshot_date
            FROM tcc_snapshots t
            WHERE t.boat_id = b.id
            ORDER BY t.snapshot_date DESC
            LIMIT 1
        ) lat ON true
        LEFT JOIN LATERAL (
            SELECT o.ref_no, o.gph, o.cdl
            FROM orc_certificates o
            WHERE o.boat_id = b.id
            ORDER BY o.snapshot_date DESC
            LIMIT 1
        ) orc ON true
        WHERE UPPER(b.country) = :country
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
    """)
    count_sql = text(
        "SELECT count(*) FROM boats WHERE UPPER(country) = :country"
    )

    # ORC stats for dual-rated designs in this country's fleet: designs that
    # appear both in the local IRC fleet (boats.design / design_canonical)
    # and in the latest ORC snapshot (orc_certificates.class_name).
    designs_sql = text("""
        WITH fleet_designs AS (
            SELECT DISTINCT COALESCE(b.design_canonical, b.design) AS design
            FROM boats b
            WHERE UPPER(b.country) = :country
              AND COALESCE(b.design_canonical, b.design) IS NOT NULL
        ),
        latest AS (
            SELECT * FROM orc_certificates
            WHERE snapshot_date = (SELECT max(snapshot_date) FROM orc_certificates)
        )
        SELECT
            fd.design,
            count(*)                          AS orc_fleet_size,
            round(avg(l.gph)::numeric, 2)     AS avg_gph,
            round(min(l.gph)::numeric, 2)     AS min_gph,
            round(max(l.gph)::numeric, 2)     AS max_gph,
            round(avg(l.cdl)::numeric, 3)     AS avg_cdl,
            round(avg(l.triple_low)::numeric, 2)  AS avg_triple_low,
            round(avg(l.triple_med)::numeric, 2)  AS avg_triple_med,
            round(avg(l.triple_high)::numeric, 2) AS avg_triple_high
        FROM fleet_designs fd
        JOIN latest l ON upper(l.class_name) = upper(fd.design)
        GROUP BY fd.design
        ORDER BY orc_fleet_size DESC, fd.design
    """)

    with engine.connect() as conn:
        total = conn.execute(count_sql, {"country": country_upper}).scalar()
        rows = conn.execute(sql, {
            "country": country_upper, "limit": limit, "offset": offset,
        })
        results = [dict(r._mapping) for r in rows]
        orc_designs = [
            _orc_design_row(r)
            for r in conn.execute(designs_sql, {"country": country_upper})
        ]

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
        "orc_designs": orc_designs,
    }


def _orc_design_row(r) -> dict:
    d = dict(r._mapping)
    for k in (
        "avg_gph", "min_gph", "max_gph", "avg_cdl",
        "avg_triple_low", "avg_triple_med", "avg_triple_high",
    ):
        if d.get(k) is not None:
            d[k] = float(d[k])
    return d
