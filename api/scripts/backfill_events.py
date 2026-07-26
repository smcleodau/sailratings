import sys
from sqlalchemy import text
from irc_data.db.connection import get_engine

def main():
    engine = get_engine()
    print("Backfilling events and event_entries from race_results...")
    
    with engine.begin() as conn:
        # 1. Backfill events
        res = conn.execute(text("""
            INSERT INTO events (name, start_date, end_date, organiser)
            SELECT 
                event_name,
                min(event_date) as start_date,
                max(event_date) as end_date,
                max(organizing_club) as organiser
            FROM race_results
            WHERE event_name IS NOT NULL
            GROUP BY event_name
            HAVING min(event_date) IS NOT NULL
        """))
        print(f"Inserted {res.rowcount} events.")
        
        # 2. Backfill event_entries
        res = conn.execute(text("""
            INSERT INTO event_entries (event_id, boat_id, boat_name, sail_number, design, tcc)
            SELECT 
                e.id as event_id,
                r.boat_id,
                MAX(COALESCE(b.boat_name, r.raw_data->>'boat_name')) as boat_name,
                MAX(COALESCE(b.sail_number, r.raw_data->>'sail_number')) as sail_number,
                MAX(b.design) as design,
                MAX(r.tcc_at_race) as tcc
            FROM race_results r
            JOIN events e ON e.name = r.event_name
            LEFT JOIN boats b ON b.id = r.boat_id
            WHERE r.event_name IS NOT NULL
            GROUP BY e.id, r.boat_id
        """))
        print(f"Inserted {res.rowcount} event_entries.")

if __name__ == "__main__":
    main()
