"""Pipeline monitoring endpoints — data quality, scraper health, and match funnel."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/status")
def pipeline_status(engine: Engine = Depends(get_db)):
    """Comprehensive pipeline health: per-source stats, quality scores, stale alerts."""

    with engine.connect() as conn:
        # --- Per-source stats ---
        source_stats = conn.execute(text("""
            SELECT
                source,
                count(*) as total,
                count(boat_id) as matched,
                round(count(boat_id)::numeric / NULLIF(count(*), 0) * 100, 1) as match_rate,
                max(event_date) as latest_event_date,
                max(created_at) as last_ingested
            FROM race_results
            GROUP BY source
            ORDER BY count(*) DESC
        """)).fetchall()

        sources = [
            {
                "source": r[0],
                "total": r[1],
                "matched": r[2],
                "match_rate": float(r[3]) if r[3] else 0,
                "latest_event_date": str(r[4]) if r[4] else None,
                "last_ingested": r[5].isoformat() if r[5] else None,
            }
            for r in source_stats
        ]

        # --- Per-club breakdown (SailSys) ---
        club_stats = conn.execute(text("""
            SELECT
                organizing_club,
                count(*) as total,
                count(boat_id) as matched,
                max(event_date) as latest_date,
                max(created_at) as last_ingested
            FROM race_results
            WHERE source = 'sailsys'
            GROUP BY organizing_club
            ORDER BY organizing_club
        """)).fetchall()

        clubs = [
            {
                "club": r[0],
                "total": r[1],
                "matched": r[2],
                "latest_date": str(r[3]) if r[3] else None,
                "last_ingested": r[4].isoformat() if r[4] else None,
            }
            for r in club_stats
        ]

        # --- Quality scores ---
        quality = conn.execute(text("""
            SELECT
                count(*) as total,
                round(count(boat_id)::numeric / NULLIF(count(*), 0) * 100, 1) as match_rate,
                round(count(fleet_size)::numeric / NULLIF(count(*), 0) * 100, 1) as fleet_size_rate,
                round(count(class_name)::numeric / NULLIF(count(*), 0) * 100, 1) as class_name_rate,
                round(count(event_date)::numeric / NULLIF(count(*), 0) * 100, 1) as has_date_rate,
                round(count(place)::numeric / NULLIF(count(*), 0) * 100, 1) as has_place_rate
            FROM race_results
        """)).first()

        quality_dict = {
            "total_results": quality[0],
            "match_rate": float(quality[1]) if quality[1] else 0,
            "fleet_size_rate": float(quality[2]) if quality[2] else 0,
            "class_name_rate": float(quality[3]) if quality[3] else 0,
            "has_date_rate": float(quality[4]) if quality[4] else 0,
            "has_place_rate": float(quality[5]) if quality[5] else 0,
        }

        # --- Unmatched top-20 (most common unmatched boat names) ---
        unmatched_top = conn.execute(text("""
            SELECT
                raw_data->>'boat_name' as boat_name,
                raw_data->>'sail_number' as sail_number,
                source,
                count(*) as result_count
            FROM race_results
            WHERE boat_id IS NULL
              AND raw_data->>'boat_name' IS NOT NULL
            GROUP BY raw_data->>'boat_name', raw_data->>'sail_number', source
            ORDER BY count(*) DESC
            LIMIT 20
        """)).fetchall()

        unmatched = [
            {
                "boat_name": r[0],
                "sail_number": r[1],
                "source": r[2],
                "result_count": r[3],
            }
            for r in unmatched_top
        ]

        # --- Ingestion history (last 30 runs) ---
        ingestion_rows = conn.execute(text("""
            SELECT source, started_at, completed_at, status,
                   records_found, records_new, error_message,
                   metadata
            FROM ingestion_log
            ORDER BY started_at DESC
            LIMIT 30
        """)).fetchall()

        ingestion_history = [
            {
                "source": r[0],
                "started_at": r[1].isoformat() if r[1] else None,
                "completed_at": r[2].isoformat() if r[2] else None,
                "status": r[3],
                "records_found": r[4],
                "records_new": r[5],
                "error": r[6],
                "metadata": r[7],
            }
            for r in ingestion_rows
        ]

        # --- Match funnel ---
        total_results = quality[0]
        matched_total = conn.execute(text(
            "SELECT count(*) FROM race_results WHERE boat_id IS NOT NULL"
        )).scalar()
        unmatched_total = total_results - matched_total

        funnel = {
            "total": total_results,
            "matched": matched_total,
            "unmatched": unmatched_total,
            "match_rate": round(matched_total / total_results * 100, 1) if total_results else 0,
        }

        # --- Stale alerts (any source not scraped in >2 hours) ---
        now = datetime.now(timezone.utc)
        alerts = []
        for src in sources:
            if src["last_ingested"]:
                last = datetime.fromisoformat(src["last_ingested"])
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                hours_ago = (now - last).total_seconds() / 3600
                if hours_ago > 48:  # alert if >48h since last ingest
                    alerts.append({
                        "source": src["source"],
                        "last_ingested": src["last_ingested"],
                        "hours_ago": round(hours_ago, 1),
                        "message": f"{src['source']} not scraped in {hours_ago:.0f} hours",
                    })

        # --- Counts summary ---
        boat_count = conn.execute(text("SELECT count(*) FROM boats")).scalar()
        orc_count = conn.execute(text("SELECT count(*) FROM orc_certificates")).scalar()
        cert_count = conn.execute(text("SELECT count(*) FROM irc_certificates")).scalar()

    return {
        "generated_at": now.isoformat(),
        "counts": {
            "boats": boat_count,
            "race_results": total_results,
            "orc_certificates": orc_count,
            "irc_certificates": cert_count,
        },
        "sources": sources,
        "clubs": clubs,
        "quality": quality_dict,
        "match_funnel": funnel,
        "unmatched_top_20": unmatched,
        "ingestion_history": ingestion_history,
        "alerts": alerts,
    }
