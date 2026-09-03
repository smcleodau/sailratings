"""OPS-02-10 — run the ORC VPP detail drain to completion.

Unlimited backfill: every orc_certificates row on the latest snapshot that
is missing GPH gets its full RMS detail (GPH, CDL, allowances, dimensions,
polars) fetched from data.orc.org.  Resumable: re-running only picks up
rows still missing GPH.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "src"))

from irc_data.scrapers.orc import backfill_orc_details

if __name__ == "__main__":
    stats = asyncio.run(backfill_orc_details(limit=None))
    print(f"FINAL_STATS {stats}")
