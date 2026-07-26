import sys
from sqlalchemy import text
from irc_data.db.connection import get_engine

def main():
    engine = get_engine()
    
    with engine.begin() as conn:
        # TopYacht backfill
        res1 = conn.execute(text("""
            UPDATE race_results
            SET status = 'DNF'
            WHERE source = 'topyacht' AND transport = 'legacy'
              AND status = 'finished' AND place IS NULL
              AND coalesce(raw_data->>'finish_time', '') = '';
        """))
        print(f"TopYacht backfilled: {res1.rowcount} rows.")
        
        # SailRaceHQ backfill
        res2 = conn.execute(text("""
            UPDATE race_results
            SET status = 'DNF'
            WHERE source = 'sailracehq' AND transport = 'legacy'
              AND status = 'finished' AND place IS NULL
              AND coalesce(raw_data->>'finish_time', '') = ''
              AND coalesce(raw_data->>'boat_name', '') <> '';
        """))
        print(f"SailRaceHQ backfilled: {res2.rowcount} rows.")

if __name__ == "__main__":
    main()
