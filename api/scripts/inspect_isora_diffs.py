import sys
from sqlalchemy import text
from irc_data.db.connection import get_engine

def main():
    engine = get_engine()
    with engine.connect() as conn:
        for diff_id in [334, 337]:
            row = conn.execute(text("""
                SELECT id, source_url, event_name, event_date, legacy_rows,
                       firecrawl_rows, matched, match_rate, notes,
                       missing_names, extra_names
                FROM firecrawl_diffs
                WHERE id = :id
            """), {"id": diff_id}).fetchone()
            if not row:
                print(f"No diff found for ID {diff_id}")
                continue
            
            print(f"=== DIFF ID {diff_id} ===")
            print(f"URL:          {row.source_url}")
            print(f"Event Name:   {row.event_name}")
            print(f"Event Date:   {row.event_date}")
            print(f"Legacy Rows:  {row.legacy_rows}")
            print(f"Firecrawl:    {row.firecrawl_rows}")
            print(f"Matched:      {row.matched} (Rate: {float(row.match_rate)*100:.1f}%)")
            print(f"Notes:        {row.notes}")
            print(f"Missing:      {row.missing_names}")
            print(f"Extra:        {row.extra_names}")
            print()

if __name__ == '__main__':
    main()
