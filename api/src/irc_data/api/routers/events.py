"""Event and race result endpoints."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
def list_events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    after: date | None = Query(None, description="Only events on or after this date"),
    source: str | None = Query(None, description="Filter by result source"),
    engine: Engine = Depends(get_db),
):
    """List distinct events with boat counts, date ranges, and match metrics."""
    conditions = []
    params: dict = {"limit": limit, "offset": offset}

    if after:
        conditions.append("r.event_date >= :after")
        params["after"] = after
    if source:
        conditions.append("r.source = :source")
        params["source"] = source

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sql = text(f"""
        SELECT
            r.event_name,
            min(r.event_date)                  AS first_date,
            max(r.event_date)                  AS last_date,
            count(DISTINCT r.boat_id)
                FILTER (WHERE r.boat_id IS NOT NULL) AS matched_boat_count,
            count(*)                           AS result_count,
            count(*) FILTER (WHERE r.boat_id IS NULL) AS unmatched_count,
            max(r.fleet_size)                  AS max_fleet_size,
            array_agg(DISTINCT r.source)
                FILTER (WHERE r.source IS NOT NULL) AS sources,
            array_agg(DISTINCT r.class_name)
                FILTER (WHERE r.class_name IS NOT NULL) AS classes
        FROM race_results r
        {where}
        GROUP BY r.event_name
        ORDER BY max(r.event_date) DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)
    count_sql = text(f"""
        SELECT count(DISTINCT event_name) FROM race_results r {where}
    """)

    with engine.connect() as conn:
        total = conn.execute(count_sql, params).scalar()
        rows = conn.execute(sql, params)
        results = [dict(r._mapping) for r in rows]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": results,
    }


@router.get("/{event_name}/results")
def get_event_results(
    event_name: str,
    year: int | None = Query(None, description="Filter results to a specific year"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    engine: Engine = Depends(get_db),
):
    """Full results for a given event. Uses LEFT JOIN so unmatched results are visible."""
    conditions = ["r.event_name = :event_name"]
    params: dict = {
        "event_name": event_name,
        "limit": limit,
        "offset": offset,
    }

    if year:
        conditions.append("EXTRACT(YEAR FROM r.event_date) = :year")
        params["year"] = year

    where = "WHERE " + " AND ".join(conditions)

    sql = text(f"""
        SELECT
            r.id,
            COALESCE(b.boat_name, r.raw_data->>'boat_name') AS boat_name,
            COALESCE(b.sail_number, r.raw_data->>'sail_number') AS sail_number,
            b.design,
            b.country,
            r.boat_id,
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
            r.source,
            r.organizing_club
        FROM race_results r
        LEFT JOIN boats b ON b.id = r.boat_id
        {where}
        ORDER BY r.event_date DESC NULLS LAST, r.place NULLS LAST
        LIMIT :limit OFFSET :offset
    """)
    count_sql = text(f"SELECT count(*) FROM race_results r {where}")

    with engine.connect() as conn:
        total = conn.execute(count_sql, params).scalar()
        rows = conn.execute(sql, params)
        results = [dict(r._mapping) for r in rows]

    if total == 0:
        raise HTTPException(status_code=404, detail="Event not found")

    return {
        "event_name": event_name,
        "year": year,
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": results,
    }


def _build_rating_analysis(conn, event_name: str) -> dict | None:
    """Build IRC rating analysis for an event's matched boats."""

    # Fleet TCC spread — how diverse is the rating band?
    spread_row = conn.execute(text("""
        SELECT
            count(DISTINCT r.boat_id) as boat_count,
            min(r.tcc_at_race) as min_tcc,
            max(r.tcc_at_race) as max_tcc,
            round(avg(r.tcc_at_race)::numeric, 4) as mean_tcc,
            round(stddev(r.tcc_at_race)::numeric, 4) as stddev_tcc
        FROM (
            SELECT DISTINCT ON (boat_id) boat_id, tcc_at_race
            FROM race_results
            WHERE event_name = :name AND boat_id IS NOT NULL AND tcc_at_race IS NOT NULL
            ORDER BY boat_id, event_date DESC
        ) r
    """), {"name": event_name}).first()

    if not spread_row or not spread_row[0] or spread_row[0] < 2:
        return None

    fleet_spread = {
        "boat_count": spread_row[0],
        "min_tcc": float(spread_row[1]) if spread_row[1] else None,
        "max_tcc": float(spread_row[2]) if spread_row[2] else None,
        "mean_tcc": float(spread_row[3]) if spread_row[3] else None,
        "stddev_tcc": float(spread_row[4]) if spread_row[4] else None,
        "range": float(spread_row[2] - spread_row[1]) if spread_row[1] and spread_row[2] else None,
    }

    # Rating vs performance — compare TCC rank to average finish position.
    # Higher TCC = theoretically faster boat. If a lower-rated boat outperforms,
    # they're sailing above their rating.
    rvp_rows = conn.execute(text("""
        WITH boat_race_stats AS (
            SELECT
                r.boat_id,
                COALESCE(b.boat_name, r.raw_data->>'boat_name') as boat_name,
                b.design,
                round(avg(r.tcc_at_race)::numeric, 4) as tcc_at_race,
                round(avg(r.place)::numeric, 1) as avg_place,
                count(*) as races
            FROM race_results r
            LEFT JOIN boats b ON b.id = r.boat_id
            WHERE r.event_name = :name
              AND r.boat_id IS NOT NULL
              AND r.tcc_at_race IS NOT NULL
              AND r.place IS NOT NULL
            GROUP BY r.boat_id, b.boat_name, b.design, r.raw_data->>'boat_name'
        )
        SELECT
            boat_id, boat_name, design, tcc_at_race, avg_place, races,
            RANK() OVER (ORDER BY tcc_at_race DESC) as tcc_rank,
            count(*) OVER () as fleet_size
        FROM boat_race_stats
        ORDER BY avg_place
    """), {"name": event_name}).fetchall()

    rating_vs_perf = []
    for r in rvp_rows:
        fleet_size = r[7]
        tcc_rank = r[6]
        avg_place = float(r[4])
        # performance_delta: positive = outperforming their rating
        # Normalized to fleet size: expected_place based on TCC rank vs actual avg_place
        expected_place = float(tcc_rank)
        perf_delta = round((expected_place - avg_place) / fleet_size, 2) if fleet_size > 1 else 0

        rating_vs_perf.append({
            "boat_id": r[0],
            "boat_name": r[1],
            "design": r[2],
            "tcc_at_race": float(r[3]),
            "avg_place": avg_place,
            "races": r[5],
            "tcc_rank": int(tcc_rank),
            "performance_delta": perf_delta,
        })

    # Rating band competitiveness — group boats by 0.05 TCC bands
    band_rows = conn.execute(text("""
        WITH boat_tcc AS (
            SELECT DISTINCT ON (boat_id)
                boat_id, tcc_at_race,
                (SELECT round(avg(place)::numeric, 1)
                 FROM race_results r2
                 WHERE r2.boat_id = r.boat_id AND r2.event_name = :name AND r2.place IS NOT NULL
                ) as avg_place
            FROM race_results r
            WHERE event_name = :name AND boat_id IS NOT NULL AND tcc_at_race IS NOT NULL
            ORDER BY boat_id, event_date DESC
        )
        SELECT
            floor(tcc_at_race * 20) / 20 as band_low,
            floor(tcc_at_race * 20) / 20 + 0.05 as band_high,
            count(*) as boat_count,
            round(avg(avg_place)::numeric, 1) as avg_place_in_band
        FROM boat_tcc
        GROUP BY floor(tcc_at_race * 20)
        ORDER BY floor(tcc_at_race * 20) DESC
    """), {"name": event_name}).fetchall()

    rating_bands = [
        {
            "band": f"{float(r[0]):.2f}–{float(r[1]):.2f}",
            "boat_count": r[2],
            "avg_place": float(r[3]) if r[3] else None,
        }
        for r in band_rows
    ]

    # For matched boats, compare their raced-under TCC to their latest official TCC
    tcc_drift_rows = conn.execute(text("""
        SELECT
            r.boat_id,
            COALESCE(b.boat_name, r.raw_data->>'boat_name') as boat_name,
            round(avg(r.tcc_at_race)::numeric, 4) as raced_tcc,
            (SELECT round(t.tcc::numeric, 4) FROM tcc_snapshots t
             WHERE t.boat_id = r.boat_id ORDER BY t.snapshot_date DESC LIMIT 1) as latest_official_tcc
        FROM race_results r
        LEFT JOIN boats b ON b.id = r.boat_id
        WHERE r.event_name = :name
          AND r.boat_id IS NOT NULL
          AND r.tcc_at_race IS NOT NULL
        GROUP BY r.boat_id, b.boat_name, r.raw_data->>'boat_name'
        HAVING (SELECT t.tcc FROM tcc_snapshots t
                WHERE t.boat_id = r.boat_id ORDER BY t.snapshot_date DESC LIMIT 1) IS NOT NULL
        ORDER BY ABS(avg(r.tcc_at_race) - (
            SELECT t.tcc FROM tcc_snapshots t
            WHERE t.boat_id = r.boat_id ORDER BY t.snapshot_date DESC LIMIT 1
        )) DESC
        LIMIT 10
    """), {"name": event_name}).fetchall()

    tcc_changes = []
    for r in tcc_drift_rows:
        raced = float(r[2])
        latest = float(r[3])
        diff = round(latest - raced, 4)
        if abs(diff) >= 0.0001:
            tcc_changes.append({
                "boat_id": r[0],
                "boat_name": r[1],
                "raced_tcc": raced,
                "latest_official_tcc": latest,
                "tcc_change": diff,
            })

    return {
        "fleet_tcc_spread": fleet_spread,
        "rating_vs_performance": rating_vs_perf,
        "rating_bands": rating_bands,
        "tcc_changes_since_event": tcc_changes,
    }


@router.get("/{event_name}/summary")
def get_event_summary(
    event_name: str,
    engine: Engine = Depends(get_db),
):
    """Rich regatta summary: standings, race breakdown, class distribution, key stories."""

    with engine.connect() as conn:
        # Check event exists
        exists = conn.execute(
            text("SELECT count(*) FROM race_results WHERE event_name = :name"),
            {"name": event_name},
        ).scalar()
        if not exists:
            raise HTTPException(status_code=404, detail="Event not found")

        # --- Event metadata ---
        meta = conn.execute(text("""
            SELECT
                min(event_date) as first_date,
                max(event_date) as last_date,
                count(DISTINCT race_name) as race_count,
                max(fleet_size) as max_fleet_size,
                array_agg(DISTINCT organizing_club) FILTER (WHERE organizing_club IS NOT NULL) as clubs,
                array_agg(DISTINCT source) FILTER (WHERE source IS NOT NULL) as sources,
                count(*) as total_results,
                count(DISTINCT COALESCE(
                    r.boat_id::text,
                    r.raw_data->>'boat_name'
                )) as unique_boats
            FROM race_results r
            WHERE event_name = :name
        """), {"name": event_name}).first()

        # --- Overall standings (aggregated across races) ---
        standings = conn.execute(text("""
            SELECT
                COALESCE(b.boat_name, r.raw_data->>'boat_name') AS boat_name,
                COALESCE(b.sail_number, r.raw_data->>'sail_number') AS sail_number,
                b.design,
                r.boat_id,
                count(*) as races,
                round(avg(r.place)::numeric, 1) as avg_place,
                count(*) FILTER (WHERE r.place = 1) as wins,
                count(*) FILTER (WHERE r.place <= 3) as podiums,
                min(r.place) as best_place,
                max(r.place) as worst_place,
                array_agg(r.place ORDER BY r.event_date, r.race_name) as places
            FROM race_results r
            LEFT JOIN boats b ON b.id = r.boat_id
            WHERE r.event_name = :name AND r.place IS NOT NULL
            GROUP BY r.boat_id, b.boat_name, b.sail_number, b.design, r.raw_data->>'boat_name', r.raw_data->>'sail_number'
            ORDER BY avg(r.place), count(*) FILTER (WHERE r.place = 1) DESC
        """), {"name": event_name}).fetchall()

        standings_list = [
            {
                "boat_name": r[0],
                "sail_number": r[1],
                "design": r[2],
                "boat_id": r[3],
                "races": r[4],
                "avg_place": float(r[5]) if r[5] else None,
                "wins": r[6],
                "podiums": r[7],
                "best_place": r[8],
                "worst_place": r[9],
                "places": r[10],
            }
            for r in standings
        ]

        # --- Race breakdown ---
        races = conn.execute(text("""
            SELECT
                r.race_name,
                r.event_date,
                count(*) as entries,
                count(*) FILTER (WHERE r.place IS NOT NULL) as finishers
            FROM race_results r
            WHERE r.event_name = :name
            GROUP BY r.race_name, r.event_date
            ORDER BY r.event_date NULLS LAST, r.race_name
        """), {"name": event_name}).fetchall()

        race_breakdown = [
            {
                "race_name": r[0],
                "date": str(r[1]) if r[1] else None,
                "entries": r[2],
                "finishers": r[3],
            }
            for r in races
        ]

        # --- Class/division breakdown ---
        classes = conn.execute(text("""
            SELECT
                r.class_name,
                count(DISTINCT COALESCE(r.boat_id::text, r.raw_data->>'boat_name')) as boat_count,
                count(*) as result_count
            FROM race_results r
            WHERE r.event_name = :name AND r.class_name IS NOT NULL
            GROUP BY r.class_name
            ORDER BY count(*) DESC
        """), {"name": event_name}).fetchall()

        class_breakdown = [
            {"class_name": r[0], "boat_count": r[1], "result_count": r[2]}
            for r in classes
        ]

        # --- Design distribution ---
        designs = conn.execute(text("""
            SELECT
                COALESCE(b.design,
                    NULLIF(TRIM(CONCAT_WS(' ', r.raw_data->>'boat_make', r.raw_data->>'boat_model')), '')
                ) as design,
                count(DISTINCT COALESCE(r.boat_id::text, r.raw_data->>'boat_name')) as boat_count
            FROM race_results r
            LEFT JOIN boats b ON b.id = r.boat_id
            WHERE r.event_name = :name
            GROUP BY COALESCE(b.design,
                NULLIF(TRIM(CONCAT_WS(' ', r.raw_data->>'boat_make', r.raw_data->>'boat_model')), '')
            )
            HAVING COALESCE(b.design,
                NULLIF(TRIM(CONCAT_WS(' ', r.raw_data->>'boat_make', r.raw_data->>'boat_model')), '')
            ) IS NOT NULL
            ORDER BY count(*) DESC
            LIMIT 20
        """), {"name": event_name}).fetchall()

        design_distribution = [
            {"design": r[0], "boat_count": r[1]}
            for r in designs if r[0] and r[0].strip()
        ]

        # --- Rating analysis (IRC-aware) ---
        rating_analysis = _build_rating_analysis(conn, event_name)

        # --- Key stories ---
        stories = []

        # Dominant boat (most wins)
        if standings_list and standings_list[0]["wins"] > 0:
            top = standings_list[0]
            stories.append({
                "type": "dominant",
                "boat_name": top["boat_name"],
                "detail": f"{top['wins']} wins from {top['races']} races, avg place {top['avg_place']}",
            })

        # Biggest mover (best single-race improvement)
        if len(standings_list) >= 2:
            for s in standings_list:
                places = s["places"]
                if places and len(places) >= 2:
                    improvements = []
                    for i in range(1, len(places)):
                        if places[i-1] is not None and places[i] is not None:
                            improvements.append(places[i-1] - places[i])
                    if improvements:
                        best_improvement = max(improvements)
                        if best_improvement >= 3:
                            stories.append({
                                "type": "biggest_mover",
                                "boat_name": s["boat_name"],
                                "detail": f"Improved {best_improvement} places in a single race",
                            })
                            break

        # Rating-derived stories
        if rating_analysis and rating_analysis.get("fleet_tcc_spread"):
            spread = rating_analysis["fleet_tcc_spread"]
            if spread.get("range"):
                stories.append({
                    "type": "fleet_spread",
                    "detail": f"TCC range {spread['min_tcc']:.4f}–{spread['max_tcc']:.4f} ({spread['range']:.4f} spread across {spread['boat_count']} rated boats)",
                })

        if rating_analysis and rating_analysis.get("rating_vs_performance"):
            rvp = rating_analysis["rating_vs_performance"]
            # Find boats punching above their rating
            overperformers = [b for b in rvp if b.get("performance_delta") and b["performance_delta"] > 0.2]
            if overperformers:
                top_op = max(overperformers, key=lambda b: b["performance_delta"])
                stories.append({
                    "type": "rating_overperformer",
                    "boat_name": top_op["boat_name"],
                    "detail": f"Rated {top_op['tcc_at_race']:.4f} TCC (#{top_op['tcc_rank']} in fleet) but averaged place {top_op['avg_place']:.1f} — sailing well above rating",
                })

    return {
        "event_name": event_name,
        "metadata": {
            "first_date": str(meta[0]) if meta[0] else None,
            "last_date": str(meta[1]) if meta[1] else None,
            "race_count": meta[2],
            "max_fleet_size": meta[3],
            "organizing_clubs": meta[4],
            "sources": meta[5],
            "total_results": meta[6],
            "unique_boats": meta[7],
        },
        "standings": standings_list,
        "race_breakdown": race_breakdown,
        "class_breakdown": class_breakdown,
        "design_distribution": design_distribution,
        "rating_analysis": rating_analysis,
        "stories": stories,
    }
