"""Historical trend analysis."""

from sqlalchemy import text
from sqlalchemy.engine import Engine


def tcc_history(engine: Engine, boat_id: int) -> list[dict]:
    """Get TCC history for a boat from both snapshots and race results."""
    query = """
        SELECT snapshot_date as date, tcc, 'snapshot' as source
        FROM tcc_snapshots WHERE boat_id = :boat_id
        UNION ALL
        SELECT event_date as date, tcc_at_race as tcc, 'race' as source
        FROM race_results WHERE boat_id = :boat_id AND tcc_at_race IS NOT NULL
        ORDER BY date
    """
    with engine.connect() as conn:
        result = conn.execute(text(query), {"boat_id": boat_id})
        return [dict(row._mapping) for row in result]


def detect_tcc_changes(engine: Engine, boat_id: int) -> list[dict]:
    """Detect TCC changes over time (potential config or rule changes)."""
    history = tcc_history(engine, boat_id)
    changes = []
    for i in range(1, len(history)):
        prev = history[i - 1]
        curr = history[i]
        if prev["tcc"] != curr["tcc"]:
            changes.append({
                "from_date": prev["date"],
                "to_date": curr["date"],
                "from_tcc": prev["tcc"],
                "to_tcc": curr["tcc"],
                "delta": curr["tcc"] - prev["tcc"],
                "from_source": prev["source"],
                "to_source": curr["source"],
            })
    return changes


def fleet_wide_changes(engine: Engine, design: str | None = None) -> list[dict]:
    """Detect fleet-wide TCC shifts (indicating rule changes vs config changes).

    If most boats of a design change TCC at the same time, it's likely a rule change.
    If only one boat changes, it's likely a configuration change.
    """
    where = "AND b.design ILIKE :design" if design else ""
    params = {"design": f"%{design}%"} if design else {}

    query = f"""
        WITH boat_changes AS (
            SELECT b.id, b.boat_name, b.sail_number,
                   t1.snapshot_date as date1, t1.tcc as tcc1,
                   t2.snapshot_date as date2, t2.tcc as tcc2,
                   t2.tcc - t1.tcc as delta
            FROM boats b
            JOIN tcc_snapshots t1 ON t1.boat_id = b.id
            JOIN tcc_snapshots t2 ON t2.boat_id = b.id AND t2.snapshot_date > t1.snapshot_date
            WHERE t1.tcc != t2.tcc {where}
            AND NOT EXISTS (
                SELECT 1 FROM tcc_snapshots t3
                WHERE t3.boat_id = b.id
                AND t3.snapshot_date > t1.snapshot_date
                AND t3.snapshot_date < t2.snapshot_date
            )
        )
        SELECT date2 as change_date,
               COUNT(*) as boats_changed,
               AVG(delta) as avg_delta,
               ARRAY_AGG(boat_name) as boats
        FROM boat_changes
        GROUP BY date2
        HAVING COUNT(*) > 1
        ORDER BY date2
    """

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        return [dict(row._mapping) for row in result]
