import datetime
from sqlalchemy import text
from sqlalchemy.engine import Engine
import logging

logger = logging.getLogger(__name__)

def generate_boat_events(engine: Engine, days_back: int = 7) -> dict:
    """
    Scans recent race_results and tcc_snapshots to generate events
    for the boat_events timeline table.
    """
    cutoff = datetime.date.today() - datetime.timedelta(days=days_back)
    stats = {"races_added": 0, "certs_added": 0}

    with engine.begin() as conn:
        # 1. Race Results
        # Find results created recently that aren't already in boat_events
        result = conn.execute(text("""
            INSERT INTO boat_events (boat_id, event_type, event_date, payload)
            SELECT
                r.boat_id,
                'race_completed' as event_type,
                COALESCE(r.event_date::timestamp with time zone, r.created_at) as event_date,
                jsonb_build_object(
                    'reference_id', r.id,
                    'description', 'Finished ' || COALESCE(r.place::text || CASE
                        WHEN r.place % 100 IN (11, 12, 13) THEN 'th'
                        WHEN r.place % 10 = 1 THEN 'st'
                        WHEN r.place % 10 = 2 THEN 'nd'
                        WHEN r.place % 10 = 3 THEN 'rd'
                        ELSE 'th' END, r.status) || ' in ' || COALESCE(r.race_name, r.event_name, 'a race'),
                    'event_name', r.event_name,
                    'race_name', r.race_name,
                    'place', r.place,
                    'status', r.status,
                    'tcc_at_race', r.tcc_at_race
                ) as payload
            FROM race_results r
            WHERE r.boat_id IS NOT NULL
              AND r.created_at >= :cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM boat_events e
                  WHERE e.boat_id = r.boat_id
                    AND e.event_type = 'race_completed'
                    AND (e.payload->>'reference_id')::text = r.id::text
              )
        """), {"cutoff": cutoff})
        
        stats["races_added"] = result.rowcount

        # 2. Certificate Issuances
        # Find snapshots created recently that aren't already in boat_events
        result = conn.execute(text("""
            INSERT INTO boat_events (boat_id, event_type, event_date, payload)
            SELECT
                b.id as boat_id,
                'certificate_issued' as event_type,
                t.snapshot_date::timestamp with time zone as event_date,
                jsonb_build_object(
                    'reference_id', t.id,
                    'description', 'New IRC Certificate issued (TCC: ' || t.tcc || ')',
                    'tcc', t.tcc,
                    'design', b.design,
                    'dlr', t.dlr,
                    'lh', t.lh,
                    'beam', t.beam,
                    'snapshot_date', t.snapshot_date
                ) as payload
            FROM tcc_snapshots t
            JOIN boats b ON b.id = t.boat_id
            WHERE t.snapshot_date >= :cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM boat_events e
                  WHERE e.boat_id = b.id
                    AND e.event_type = 'certificate_issued'
                    AND (e.payload->>'reference_id')::text = t.id::text
              )
        """), {"cutoff": cutoff})
        
        stats["certs_added"] = result.rowcount

    logger.info(f"Generated boat events: {stats['races_added']} races, {stats['certs_added']} certs.")
    return stats
