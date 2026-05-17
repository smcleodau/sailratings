"""Health check endpoint with data freshness and table counts."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(
    engine: Engine = Depends(get_db),
):
    """System health: database connectivity, table row counts, and data freshness."""
    counts_sql = text("""
        SELECT
            (SELECT count(*) FROM boats)             AS boats,
            (SELECT count(*) FROM tcc_snapshots)     AS tcc_snapshots,
            (SELECT count(*) FROM irc_certificates)  AS irc_certificates,
            (SELECT count(*) FROM orc_certificates)  AS orc_certificates,
            (SELECT count(*) FROM race_results)      AS race_results,
            (SELECT count(*) FROM design_classes)     AS design_classes,
            (SELECT count(*) FROM boat_identities)   AS boat_identities
    """)

    freshness_sql = text("""
        SELECT
            (SELECT max(snapshot_date) FROM tcc_snapshots)    AS latest_irc_snapshot,
            (SELECT max(snapshot_date) FROM orc_certificates) AS latest_orc_snapshot,
            (SELECT max(event_date) FROM race_results)        AS latest_race_result,
            (SELECT max(scraped_at) FROM irc_certificates)    AS latest_irc_certificate
    """)

    ingestion_sql = text("""
        SELECT
            source,
            status,
            started_at,
            completed_at,
            records_found,
            records_new,
            records_updated,
            error_message
        FROM ingestion_log
        ORDER BY started_at DESC
        LIMIT 5
    """)

    try:
        with engine.connect() as conn:
            counts_row = conn.execute(counts_sql).first()
            freshness_row = conn.execute(freshness_sql).first()
            ingestion_rows = conn.execute(ingestion_sql)

            counts = dict(counts_row._mapping) if counts_row else {}
            freshness = dict(freshness_row._mapping) if freshness_row else {}
            recent_ingestions = [dict(r._mapping) for r in ingestion_rows]

        return {
            "status": "healthy",
            "counts": counts,
            "freshness": freshness,
            "recent_ingestions": recent_ingestions,
        }
    except Exception as exc:
        return {
            "status": "unhealthy",
            "error": str(exc),
        }
