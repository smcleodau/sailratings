"""Comparison analysis between boats and configurations."""

import json
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def compare_boats(engine: Engine, sail_numbers: list[str]) -> list[dict]:
    """Compare multiple boats side by side using their latest TCC snapshots."""
    placeholders = ", ".join(f":s{i}" for i in range(len(sail_numbers)))
    params = {f"s{i}": sn for i, sn in enumerate(sail_numbers)}

    query = f"""
        SELECT b.boat_name, b.sail_number, b.design, b.country,
               t.tcc, t.non_spi_tcc, t.dlr, t.crew,
               t.lh, t.beam, t.draft,
               t.headsails, t.flying_headsails, t.spinnakers
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT * FROM tcc_snapshots
            WHERE boat_id = b.id
            ORDER BY snapshot_date DESC
            LIMIT 1
        ) t ON true
        WHERE b.sail_number IN ({placeholders})
        ORDER BY t.tcc ASC
    """

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        return [dict(row._mapping) for row in result]


def fleet_stats(engine: Engine, design: str | None = None, country: str | None = None) -> dict:
    """Compute fleet statistics (mean, median, min, max TCC etc.)."""
    where_clauses = ["1=1"]
    params: dict = {}
    if design:
        where_clauses.append("b.design ILIKE :design")
        params["design"] = f"%{design}%"
    if country:
        where_clauses.append("b.country = :country")
        params["country"] = country

    where = " AND ".join(where_clauses)

    query = f"""
        SELECT
            COUNT(*) as count,
            MIN(t.tcc) as min_tcc,
            MAX(t.tcc) as max_tcc,
            AVG(t.tcc) as avg_tcc,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY t.tcc) as median_tcc,
            AVG(t.dlr) as avg_dlr,
            AVG(t.headsails) as avg_headsails,
            AVG(t.spinnakers) as avg_spinnakers
        FROM boats b
        JOIN LATERAL (
            SELECT * FROM tcc_snapshots
            WHERE boat_id = b.id
            ORDER BY snapshot_date DESC
            LIMIT 1
        ) t ON true
        WHERE {where}
    """

    with engine.connect() as conn:
        row = conn.execute(text(query), params).first()
        if row:
            return dict(row._mapping)
        return {}


def tcc_snapshot_diff(engine: Engine, date1: str, date2: str) -> list[dict]:
    """Compare TCC snapshots between two dates to detect changes.

    Returns boats whose TCC changed between the two snapshot dates.
    """
    query = """
        SELECT b.boat_name, b.sail_number, b.design, b.country,
               t1.tcc as tcc_old, t2.tcc as tcc_new,
               t2.tcc - t1.tcc as tcc_delta,
               t1.dlr as dlr_old, t2.dlr as dlr_new,
               t1.headsails as hs_old, t2.headsails as hs_new,
               t1.spinnakers as spi_old, t2.spinnakers as spi_new
        FROM boats b
        JOIN tcc_snapshots t1 ON t1.boat_id = b.id AND t1.snapshot_date = :date1
        JOIN tcc_snapshots t2 ON t2.boat_id = b.id AND t2.snapshot_date = :date2
        WHERE t1.tcc != t2.tcc
        ORDER BY ABS(t2.tcc - t1.tcc) DESC
    """
    with engine.connect() as conn:
        result = conn.execute(text(query), {"date1": date1, "date2": date2})
        return [dict(row._mapping) for row in result]
