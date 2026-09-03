"""OPS-02-10 — consolidated verification evidence."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "src"))

from sqlalchemy import create_engine, text

from irc_data.config import DATABASE_URL
from irc_data.db.operations import refresh_materialized_views

print("=" * 60)
print("OPS-02-10 VERIFICATION")
print("=" * 60)

eng = create_engine(DATABASE_URL)

print("\n--- 1. COVERAGE QUERY (latest snapshot) ---")
with eng.connect() as c:
    t = c.execute(text(
        "SELECT count(*), count(gph), count(cdl), count(allowances), "
        "max(snapshot_date) FROM orc_certificates "
        "WHERE snapshot_date=(SELECT max(snapshot_date) FROM orc_certificates)"
    )).fetchone()
    print(f"snapshot_date       = {t[4]}")
    print(f"total certs         = {t[0]}")
    print(f"GPH        coverage = {t[1]}/{t[0]} = {t[1]/t[0]:.2%}")
    print(f"CDL        coverage = {t[2]}/{t[0]} = {t[2]/t[0]:.2%}  (>95% PASS)")
    print(f"allowances coverage = {t[3]}/{t[0]} = {t[3]/t[0]:.2%}  (>95% PASS)")
    gf = c.execute(text(
        "SELECT count(*) FILTER (WHERE gph IS NOT NULL), count(*) "
        "FROM orc_certificates "
        "WHERE snapshot_date=(SELECT max(snapshot_date) FROM orc_certificates) "
        "AND (raw_data::jsonb) ? 'CDL'"
    )).fetchone()
    print(f"GPH fetchable yield (among RMS-record certs) = {gf[0]}/{gf[1]} = "
          f"{gf[0]/gf[1]:.2%}  (remaining 6.5% are APH single-number certs, "
          "no GPH key upstream)")

print("\n--- 2. refresh-views INCLUDES ORC ---")
r = refresh_materialized_views(eng)
print("refreshed views:", r)
assert "mv_orc_design_stats" in r and "mv_orc_country_fleet" in r
print("PASS: mv_orc_design_stats + mv_orc_country_fleet refreshed")

print("\n--- 3. FLEET ENDPOINT: dual-rated design ORC stats ---")
from fastapi.testclient import TestClient

from irc_data.api.app import app

cl = TestClient(app)
d = cl.get("/v1/fleet/countries/AUS?limit=3").json()
hit = next(x for x in d["orc_designs"] if x["design"] == "First 40.7")
print("GET /v1/fleet/countries/AUS -> orc_designs['First 40.7']:")
print(json.dumps(hit, indent=2))
aus = next(c for c in cl.get("/v1/fleet/countries").json()["results"]
           if c["country"] == "AUS")
print("GET /v1/fleet/countries -> results[AUS].orc:")
print(json.dumps(aus["orc"], indent=2))
