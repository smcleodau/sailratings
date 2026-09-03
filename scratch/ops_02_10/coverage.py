"""OPS-02-10 — coverage snapshot for evidence."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "src"))

from sqlalchemy import create_engine, text

from irc_data.config import DATABASE_URL

eng = create_engine(DATABASE_URL)
with eng.connect() as c:
    total, gph, cdl, allow = c.execute(text("""
        SELECT count(*), count(gph), count(cdl), count(allowances)
        FROM orc_certificates
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM orc_certificates)
    """)).one()
    print(f"total={total} gph={gph} ({gph/total:.2%}) cdl={cdl} ({cdl/total:.2%}) "
          f"allowances={allow} ({allow/total:.2%}) missing_gph={total-gph}")
