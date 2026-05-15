"""Search endpoints using pg_trgm fuzzy matching."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
def fuzzy_search(
    q: str = Query(..., min_length=1, description="Free-text search query"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    engine: Engine = Depends(get_db),
):
    """Fuzzy search across boats by name or sail number using pg_trgm similarity."""
    # Exclude SEC (secondary certificate) entries — they share sail number
    # with the primary boat and would show as duplicates.
    sql = text("""
        SELECT
            b.id,
            b.boat_name,
            b.sail_number,
            b.design,
            b.country,
            b.year_built,
            GREATEST(
                similarity(b.boat_name, :q),
                similarity(b.sail_number, :q)
            ) AS score
        FROM boats b
        WHERE (similarity(b.boat_name, :q) > 0.3
           OR similarity(b.sail_number, :q) > 0.5)
          AND b.boat_name NOT LIKE '% - SEC'
        ORDER BY score DESC, b.boat_name
        LIMIT :limit OFFSET :offset
    """)
    count_sql = text("""
        SELECT count(*) FROM boats b
        WHERE (similarity(b.boat_name, :q) > 0.3
           OR similarity(b.sail_number, :q) > 0.5)
          AND b.boat_name NOT LIKE '%% - SEC'
    """)

    # Suggestions ("did you mean?") for short/near-miss queries when the
    # primary returns nothing. Uses Levenshtein distance against sail_number
    # plus a relaxed trigram threshold on boat_name. Capped at 5.
    suggestions_sql = text("""
        SELECT
            b.id,
            b.boat_name,
            b.sail_number,
            b.design,
            b.country,
            b.year_built,
            CASE
                WHEN b.sail_number IS NOT NULL
                 AND length(b.sail_number) BETWEEN length(:q) - 2 AND length(:q) + 2
                THEN levenshtein(upper(b.sail_number), upper(:q))
                ELSE 999
            END AS sail_dist,
            similarity(b.boat_name, :q) AS name_sim
        FROM boats b
        WHERE b.boat_name NOT LIKE '%% - SEC'
          AND (
            (length(:q) BETWEEN 3 AND 10
             AND b.sail_number IS NOT NULL
             AND length(b.sail_number) BETWEEN length(:q) - 2 AND length(:q) + 2
             AND levenshtein(upper(b.sail_number), upper(:q)) <= 2)
            OR similarity(b.boat_name, :q) > 0.18
          )
        ORDER BY sail_dist ASC, name_sim DESC, b.boat_name
        LIMIT 5
    """)

    with engine.connect() as conn:
        total = conn.execute(count_sql, {"q": q}).scalar()
        rows = conn.execute(sql, {"q": q, "limit": limit, "offset": offset})
        results = [dict(row._mapping) for row in rows]
        suggestions: list[dict] = []
        if total == 0 and len(q.strip()) >= 2:
            sug_rows = conn.execute(suggestions_sql, {"q": q})
            suggestions = [
                {k: v for k, v in dict(r._mapping).items() if k not in ("sail_dist", "name_sim")}
                for r in sug_rows
            ]

    return {
        "query": q,
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": results,
        "suggestions": suggestions,
    }


@router.get("/boats")
def structured_search(
    name: str | None = Query(None, description="Boat name (partial match)"),
    sail_number: str | None = Query(None, description="Sail number (partial match)"),
    design: str | None = Query(None, description="Design class (partial match)"),
    country: str | None = Query(None, description="Country code (exact match)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    engine: Engine = Depends(get_db),
):
    """Structured search with explicit field filters.

    All text filters use case-insensitive ILIKE except country which is exact.
    """
    conditions = []
    params: dict = {"limit": limit, "offset": offset}

    if name:
        conditions.append("b.boat_name ILIKE :name")
        params["name"] = f"%{name}%"
    if sail_number:
        conditions.append("b.sail_number ILIKE :sail_number")
        params["sail_number"] = f"%{sail_number}%"
    if design:
        conditions.append("b.design ILIKE :design")
        params["design"] = f"%{design}%"
    if country:
        conditions.append("UPPER(b.country) = UPPER(:country)")
        params["country"] = country

    # Always exclude SEC (secondary certificate) duplicates
    conditions.append("b.boat_name NOT LIKE '%% - SEC'")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sql = text(f"""
        SELECT
            b.id,
            b.boat_name,
            b.sail_number,
            b.design,
            b.country,
            b.year_built,
            lat.tcc AS current_tcc
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT t.tcc
            FROM tcc_snapshots t
            WHERE t.boat_id = b.id
            ORDER BY t.snapshot_date DESC
            LIMIT 1
        ) lat ON true
        {where}
        ORDER BY b.boat_name
        LIMIT :limit OFFSET :offset
    """)
    count_sql = text(f"SELECT count(*) FROM boats b {where}")

    with engine.connect() as conn:
        total = conn.execute(count_sql, params).scalar()
        rows = conn.execute(sql, params)
        results = [dict(row._mapping) for row in rows]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": results,
    }
