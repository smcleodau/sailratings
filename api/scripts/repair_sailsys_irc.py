"""Repair SailSys race_results stored without IRC values (pre-b2e43e4 parser).

Usage (from api/, PYTHONPATH=src):
  python scripts/repair_sailsys_irc.py scan   [--clubs CYCA,...] [--races 1,2]
  python scripts/repair_sailsys_irc.py plan   [--races ...]          # writes plan.json
  python scripts/repair_sailsys_irc.py backup                        # backup table from plan
  python scripts/repair_sailsys_irc.py apply  [--races ...] [--dry-run]

scan fetches each stored SailSys race once (scraper rate limiter + policy
User-Agent) and caches payloads of races with missed IRC definitions; plan,
backup and apply work from that cache. See irc_data.scrapers.sailsys_irc_repair.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import text

from irc_data.config import RACE_RESULTS_DIR
from irc_data.db.connection import get_engine
from irc_data.scrapers import sailsys
from irc_data.scrapers.sailsys_irc_repair import (
    BACKUP_TABLE,
    PayloadCache,
    group_rows_by_race,
    plan_race,
)

CACHE_DIR = Path(os.environ.get("SAILSYS_IRC_REPAIR_DIR")
                 or RACE_RESULTS_DIR / "sailsys" / "irc_repair_20261103")
PLAN_PATH = CACHE_DIR / "plan.json"


def _ids(s: str | None) -> set[int] | None:
    return {int(x) for x in s.split(",")} if s else None


def stored_races(engine) -> list[dict]:
    """Distinct SailSys races in race_results, CYCA first."""
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT (raw_data->>'race_id')::int AS race_id,
                   max((raw_data->>'club_id')::int) AS club_id,
                   max((raw_data->>'series_id')::int) AS series_id,
                   max(coalesce(raw_data->>'club_name', organizing_club)) AS club
            FROM race_results
            WHERE source = 'sailsys' AND raw_data ? 'race_id'
            GROUP BY 1
            ORDER BY (max(raw_data->>'club_name') = 'CYCA') DESC, 1 DESC
        """)).mappings().all()
    return [dict(r) for r in rows]


def race_rows(engine, race_ids) -> dict[int, list[dict]]:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT id, tcc_at_race, rating_value, rating_type, place, status, raw_data
            FROM race_results
            WHERE source = 'sailsys' AND (raw_data->>'race_id')::int = ANY(:ids)
        """), {"ids": list(race_ids)}).mappings().all()
    return group_rows_by_race(dict(r) for r in rows)


async def _scan(races: list[dict], cache: PayloadCache) -> None:
    done = cache.scanned()
    todo = [r for r in races if r["race_id"] not in done]
    print(f"scan: {len(races)} races, {len(done)} already scanned, {len(todo)} to fetch", flush=True)
    found = 0
    async with sailsys.http_client() as client:
        for i, r in enumerate(todo, 1):
            try:
                payload = await sailsys.fetch_race_payload(client, r["race_id"])
            except Exception as e:  # network blip: leave unscanned so a rerun retries
                print(f"  race {r['race_id']}: {type(e).__name__}: {e}", flush=True)
                continue
            rec = cache.record(r["race_id"], payload)
            if rec.get("new_irc_ids"):
                found += 1
                print(f"  race {r['race_id']} ({r['club']}): IRC defs {rec['new_irc_ids']}", flush=True)
            if i % 100 == 0:
                print(f"  {i}/{len(todo)} fetched, {found} with missed IRC defs", flush=True)
    print(f"scan done: {found} new races with missed IRC defs", flush=True)


def build_plan(engine, cache: PayloadCache, only: set[int] | None) -> dict:
    meta = {r["race_id"]: r for r in stored_races(engine)}
    scanned = cache.scanned()
    targets = sorted(rid for rid, rec in scanned.items()
                     if rec.get("new_irc_ids") and (only is None or rid in only))
    rows = race_rows(engine, targets)
    plan = {"races": {}, "unscanned": len(set(meta) - set(scanned)),
            "http_errors": sorted(r for r, rec in scanned.items() if rec["status"] != "ok")}
    for rid in targets:
        m = meta.get(rid) or {}
        rp = plan_race(cache.load(rid), rows.get(rid, []), m.get("club_id") or 0,
                       m.get("series_id") or 0, rid)
        plan["races"][rid] = {"club": m.get("club"), "new_irc_ids": scanned[rid]["new_irc_ids"],
                              "updates": rp.updates, "unmatched": rp.unmatched,
                              "already_ok": rp.already_ok}
    return plan


def summarise(plan: dict) -> None:
    per_club, unmatched, ok = Counter(), Counter(), 0
    for r in plan["races"].values():
        per_club[r["club"]] += len(r["updates"])
        for u in r["unmatched"]:
            unmatched[(r["club"], u["reason"])] += 1
        ok += r["already_ok"]
    print("affected rows per club:", dict(per_club.most_common()), "total", sum(per_club.values()))
    print("races with updates:", sum(1 for r in plan["races"].values() if r["updates"]))
    print("unmatched entrants:", {f"{c}: {why}": n for (c, why), n in unmatched.items()})
    print("already correct:", ok, "| unscanned races:", plan["unscanned"],
          "| http errors:", len(plan["http_errors"]))


def _fmt(d: dict) -> str:
    return (f"tcc={d['tcc_at_race']} place={d['place']} status={d['status']} "
            f"irc_tcc={d['irc_tcc']} irc_place={d['irc_place']} irc_corr={d['irc_corrected']}")


def print_diff(plan: dict, limit: int | None = None) -> None:
    for rid, r in plan["races"].items():
        if not r["updates"]:
            continue
        print(f"race {rid} ({r['club']}, IRC defs {r['new_irc_ids']}): {len(r['updates'])} rows")
        for u in r["updates"][:limit]:
            print(f"  id {u['id']}\n    before {_fmt(u['before'])}\n    after  {_fmt(u['after'])}")
        for u in r["unmatched"]:
            print(f"  UNMATCHED {u['boat_name']}: {u['reason']}")


def backup(engine, plan: dict) -> int:
    ids = [u["id"] for r in plan["races"].values() for u in r["updates"]]
    with engine.begin() as c:
        exists = c.execute(text("SELECT to_regclass(:t)"), {"t": BACKUP_TABLE}).scalar()
        if exists:
            sys.exit(f"{BACKUP_TABLE} already exists; refusing to overwrite it")
        c.execute(text(f"CREATE TABLE {BACKUP_TABLE} AS SELECT * FROM race_results "
                       f"WHERE id = ANY(:ids)"), {"ids": ids})
        n = c.execute(text(f"SELECT count(*) FROM {BACKUP_TABLE}")).scalar()
    print(f"backup {BACKUP_TABLE}: {n} rows (plan has {len(ids)} ids, {len(set(ids))} distinct)")
    if n != len(set(ids)):
        sys.exit("backup row count does not match the plan")
    return n


_UPDATE = text(f"""
    UPDATE race_results SET
        tcc_at_race = :tcc, rating_value = :tcc, rating_type = :rating_type,
        place = :place, status = :status,
        raw_data = raw_data || jsonb_build_object(
            'irc_tcc', CAST(:irc_tcc AS text), 'irc_place', CAST(:irc_place AS integer),
            'irc_corrected', CAST(:irc_corrected AS text))
    WHERE id = :id
      AND id IN (SELECT id FROM {BACKUP_TABLE})
      AND tcc_at_race IS NOT DISTINCT FROM CAST(:b_tcc AS numeric)
      AND place IS NOT DISTINCT FROM CAST(:b_place AS integer)
      AND status IS NOT DISTINCT FROM CAST(:b_status AS text)
""")


def apply(engine, plan: dict, only: set[int] | None) -> None:
    with engine.connect() as c:
        if not c.execute(text("SELECT to_regclass(:t)"), {"t": BACKUP_TABLE}).scalar():
            sys.exit(f"no backup table {BACKUP_TABLE}; run backup first")
    updated = skipped = 0
    for rid, r in plan["races"].items():
        if only is not None and int(rid) not in only:
            continue
        with engine.begin() as c:
            for u in r["updates"]:
                a, b = u["after"], u["before"]
                n = c.execute(_UPDATE, {
                    "id": u["id"], "tcc": a["tcc_at_race"], "rating_type": a["rating_type"],
                    "place": a["place"], "status": a["status"], "irc_tcc": a["irc_tcc"],
                    "irc_place": a["irc_place"], "irc_corrected": a["irc_corrected"],
                    "b_tcc": b["tcc_at_race"], "b_place": b["place"], "b_status": b["status"],
                }).rowcount
                updated += n
                if not n:
                    skipped += 1
                    print(f"  skipped id {u['id']} (not in backup or changed since plan)")
    print(f"apply: {updated} rows updated, {skipped} skipped")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "plan", "backup", "apply"])
    ap.add_argument("--races", help="comma-separated SailSys race ids")
    ap.add_argument("--clubs", help="scan only these club names")
    ap.add_argument("--dry-run", action="store_true", help="apply: print the diff only")
    ap.add_argument("--diff-limit", type=int, default=None)
    args = ap.parse_args(argv)
    engine = get_engine()
    cache = PayloadCache(CACHE_DIR)
    only = _ids(args.races)

    if args.cmd == "scan":
        races = stored_races(engine)
        if args.clubs:
            races = [r for r in races if r["club"] in set(args.clubs.split(","))]
        if only:
            races = [r for r in races if r["race_id"] in only]
        asyncio.run(_scan(races, cache))
        return

    if args.cmd == "plan" or (args.cmd == "apply" and args.dry_run):
        plan = build_plan(engine, cache, only)
        if args.cmd == "plan" and only is None:
            PLAN_PATH.write_text(json.dumps(plan, default=str))
            print(f"plan written to {PLAN_PATH}")
        summarise(plan)
        if args.dry_run or only:
            print_diff(plan, args.diff_limit)
        return

    plan = json.loads(PLAN_PATH.read_text())
    if args.cmd == "backup":
        backup(engine, plan)
    else:
        apply(engine, plan, only)


if __name__ == "__main__":
    main()
