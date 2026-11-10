"""Full-history backfill for rorc / cowesweek / sydney-hobart / manage2sail.

Usage (from api/, PYTHONPATH=src)::

  python scripts/backfill_results_history.py rorc --since 2007-01-01 [--dry-run]
  python scripts/backfill_results_history.py cowesweek --since 2001-01-01
  python scripts/backfill_results_history.py cowesweek --since 2016-01-01 \
      --events '2016 Cowes Week - IRC Class 2'
  python scripts/backfill_results_history.py sydney-hobart --since 1945-01-01 --dry-run
  python scripts/backfill_results_history.py manage2sail --since 2010-01-01
  python scripts/backfill_results_history.py manage2sail --since 2018-01-01 \
      --m2s-events 'https://www.manage2sail.com/en-US/event/<id>|2018-05-10'

Persists exactly like the scheduled runners: event sources go through
``store_normalized_results`` (same arguments as
``legacy_adapters._run_event_source``), manage2sail through
``manage2sail.store_results``. Both use ``upsert_race_result`` and its
natural-key matching (``irc_data.db.result_identity``). Nothing is deleted.

``--dry-run`` classifies every scraped row with the same lookup
``upsert_race_result`` uses (``_find_existing_result``) and reports
strong / weak / insert counts per year without writing anything.

RORC only: two scope columns were stored inconsistently.
* ``event_date`` is NULL on rows saved before the start-date parsing fix
  ("Start: Sun 11 Aug 13 12:30:00" did not parse).
* ``class_name`` holds the boat type on the 2026-03 archive import but is
  NULL on rows the runner saved on 2026-11-03 (it did not store class_name).
Both are part of the result scope, so re-saving those rows would insert a
second copy. Before a page is saved, its stored rows (same source,
event_name and canonical page URL) get the page's parsed date where
event_date is NULL and ``raw_data->>'boat_type'`` where class_name is NULL
(fill-NULL UPDATEs only, ids logged to ``--heal-log``), so the upsert then
updates them in place. The dry run classifies as if that had happened.

Sydney Hobart only: rows saved from the old BWPS HTML pages carry
``source_url = https://bwps.cycaracing.com/standings?seriesId=15&raceId=N``.
The page URL is part of the identity (``urls_compatible``) and that URL now
redirects to the *current* year's page, so the feed-based scraper uses
``/race/{year}/results/rolex-sydney-hobart-yacht-race``. Before an edition is
saved, its stored rows with the legacy URL get the new URL (UPDATE only, ids
logged to ``--heal-log``); the dry run classifies as if that had happened.

manage2sail: clubs are walked year by year (seed + learnt + ``--clubs``) and
organiser clubs of every IRC event found are added to the walk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import text

from irc_data.db.connection import get_engine
from irc_data.db.models import RaceResultModel
from irc_data.db.operations import _find_existing_result, find_boat_by_sail_number
from irc_data.db.result_identity import (
    SKIPPER_SQL,
    ResultIdentity,
    canonical_source_url,
    match_strength,
    raw_boat_name,
    raw_sail_number,
    raw_skipper,
)
from irc_data.matching.identity import normalize_sail
from irc_data.scrapers.result_import import _find_boat_by_name, store_normalized_results

TABLE_COLS = set(RaceResultModel.__table__.c.keys())

STORE_SOURCE = {"rorc": "rorc", "cowesweek": "cowesweek", "sydney-hobart": "sydneyhobart",
                "manage2sail": "manage2sail"}
# Same include_class_name the legacy adapters pass.
INCLUDE_CLASS = {"rorc": True, "cowesweek": True, "sydney-hobart": False}


def log(**kw):
    print(json.dumps(kw, default=str), flush=True)


def year_counts(engine, source: str) -> dict:
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT extract(year from event_date)::int y, count(*) FROM race_results "
            "WHERE source = :s GROUP BY 1"), {"s": source}).fetchall()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------- dry run

def _boat_id(engine, sail, name, rating, cache):
    key = (sail, name)
    if key not in cache:
        bid = find_boat_by_sail_number(engine, normalize_sail(sail)) if sail else None
        if not bid and name:
            bid = _find_boat_by_name(engine, name, rating)
        cache[key] = bid
    return cache[key]


def event_source_kwargs(r, source, include_class_name):
    """Scope/identity part of store_normalized_results' upsert call."""
    raw = dict(r.raw_data) if r.raw_data else {}
    if r.boat_name and "boat_name" not in raw:
        raw["boat_name"] = r.boat_name
    if r.sail_number and "sail_number" not in raw:
        raw["sail_number"] = r.sail_number
    kw = dict(event_name=r.event_name, event_date=r.event_date, race_name=r.race_name,
              source=source, source_url=r.source_url, place=r.place, status=r.status,
              raw_data=raw)
    if include_class_name:
        kw["class_name"] = r.class_name
    return kw


def m2s_kwargs(r):
    """Scope/identity part of manage2sail.store_results' upsert call."""
    race_date = (r.raw_data or {}).get("race_date")
    return dict(event_name=r.event_name, event_date=r.event_date, race_name=r.race_name,
                race_number=r.race_number,
                race_date_specific=date.fromisoformat(race_date) if race_date else None,
                source="manage2sail", source_url=r.source_url, place=r.place,
                class_name=r.class_name, division=(r.raw_data or {}).get("scoring_name"),
                status=r.status, raw_data=r.raw_data)


def classify(engine, kw, boat_id):
    """('strong'|'weak'|'insert', matched row or None) for one would-be upsert."""
    values = {k: v for k, v in dict(boat_id=boat_id, **kw).items() if k in TABLE_COLS}
    with engine.connect() as conn:
        hit = _find_existing_result(conn, values, boat_id)
        if hit is None:
            return "insert", None
        row = conn.execute(text(
            "SELECT r.id, r.boat_id, r.place, r.status, r.source_url, "
            "COALESCE(r.raw_data->>'boat_name', ee.boat_name) bn, "
            "COALESCE(r.raw_data->>'sail_number', r.raw_data->>'sail_no', ee.sail_number) sn, "
            + SKIPPER_SQL + " sk "
            "FROM race_results r LEFT JOIN event_entries ee ON ee.id = r.event_entry_id "
            "WHERE r.id = :id"), {"id": hit[0]}).mappings().one()
    raw = kw.get("raw_data") or {}
    new = ResultIdentity.build(boat_name=raw_boat_name(raw), sail_number=raw_sail_number(raw),
                               place=kw.get("place"), status=kw.get("status"),
                               boat_id=boat_id, source_url=kw.get("source_url"),
                               skipper=raw_skipper(raw))
    old = ResultIdentity.build(id=row["id"], boat_name=row["bn"], sail_number=row["sn"],
                               place=row["place"], status=row["status"],
                               boat_id=row["boat_id"], source_url=row["source_url"],
                               skipper=row["sk"])
    return match_strength(new, old) or "?", dict(row)


# ---------------------------------------------------------------- RORC date heal

def heal_rorc_page(engine, rows, heal_log) -> int:
    """Fill NULL event_date (page date) / class_name (boat type) on this page's stored rows."""
    dates = {r.event_date for r in rows if r.event_date}
    page_date = dates.pop() if len(dates) == 1 else None
    names = sorted({r.event_name for r in rows})
    canon = canonical_source_url(rows[0].source_url)
    with engine.begin() as conn:
        cands = conn.execute(text(
            "SELECT id, source_url, event_date IS NULL AS no_date, "
            "class_name IS NULL AND coalesce(raw_data->>'boat_type', '') <> '' AS no_class "
            "FROM race_results WHERE source = 'rorc' AND event_name = ANY(:names) "
            "AND (event_date IS NULL OR class_name IS NULL)"
        ), {"names": names}).fetchall()
        cands = [c for c in cands if canonical_source_url(c.source_url) == canon]
        date_ids = [c.id for c in cands if c.no_date and page_date]
        class_ids = [c.id for c in cands if c.no_class]
        if date_ids:
            conn.execute(text("UPDATE race_results SET event_date = :d "
                              "WHERE id = ANY(:ids) AND event_date IS NULL"),
                         {"d": page_date, "ids": date_ids})
        if class_ids:
            conn.execute(text("UPDATE race_results SET class_name = raw_data->>'boat_type' "
                              "WHERE id = ANY(:ids) AND class_name IS NULL"),
                         {"ids": class_ids})
    if date_ids or class_ids:
        heal_log.write(json.dumps({"url": canon, "date": str(page_date), "date_ids": date_ids,
                                   "class_ids": class_ids}) + "\n")
        heal_log.flush()
    return len(set(date_ids) | set(class_ids))


# ---------------------------------------------------------------- Sydney Hobart URL heal

def hobart_legacy_url(ev) -> str | None:
    meta = ev.metadata or {}
    if not meta.get("race_id"):
        return None
    return (f"https://bwps.cycaracing.com/standings?seriesId={meta.get('series_id', 15)}"
            f"&raceId={meta['race_id']}")


def heal_hobart_urls(engine, ev, rows, heal_log) -> int:
    legacy = hobart_legacy_url(ev)
    new_urls = {r.source_url for r in rows}
    if not legacy or len(new_urls) != 1:
        return 0
    new_url = new_urls.pop()
    with engine.begin() as conn:
        ids = [r[0] for r in conn.execute(text(
            "UPDATE race_results SET source_url = :new WHERE source = 'sydneyhobart' "
            "AND event_name = :ev AND source_url = :old RETURNING id"),
            {"new": new_url, "ev": rows[0].event_name, "old": legacy}).fetchall()]
    if ids:
        heal_log.write(json.dumps({"old": legacy, "new": new_url, "ids": ids}) + "\n")
        heal_log.flush()
    return len(ids)



# ---------------------------------------------------------------- planning

def page_rows(engine, source, r, *, any_date: bool):
    """Stored rows of the same published page (scope minus class/boat)."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT r.id, r.boat_id, r.place, r.status, r.source_url, "
            "COALESCE(r.raw_data->>'boat_name', ee.boat_name) bn, "
            "COALESCE(r.raw_data->>'sail_number', r.raw_data->>'sail_no', ee.sail_number) sn "
            "FROM race_results r LEFT JOIN event_entries ee ON ee.id = r.event_entry_id "
            "WHERE r.source = :s AND r.event_name = :e "
            "AND r.race_name IS NOT DISTINCT FROM :rn "
            "AND (r.event_date IS NOT DISTINCT FROM :d" + (" OR r.event_date IS NULL)" if any_date else ")")
        ), {"s": source, "e": r.event_name, "rn": r.race_name, "d": r.event_date}).mappings().all()
    canon = canonical_source_url(r.source_url)
    return [dict(x) for x in rows
            if canonical_source_url(x["source_url"]) in (canon, None) or canon is None]


def plan_rows(engine, slug, source, include_class, ev, rows, boat_cache, *, simulate_heal):
    """Classify each scraped row: strong / weak / heal+... / insert / held.

    ``held``: the natural key finds no stored row, yet the same published
    page already holds an unmatched row at the same place (and status). The
    archive imports stored boat names/sail numbers that are not what the
    page publishes (e.g. place 9 stored as "HAKUNA MATATA FRA35914", page
    says "SNSP Hakuna Matata FRA35425"), so neither identifier agrees and
    the upsert would insert a second copy of that result. Such rows are not
    written; they are logged for review instead.
    """
    plan = []
    for r in rows:
        kw = event_source_kwargs(r, source, include_class)
        if simulate_heal and slug == "sydney-hobart" and hobart_legacy_url(ev):
            # Stored rows get the new URL in the heal; equivalent comparison.
            kw["source_url"] = hobart_legacy_url(ev)
        bid = _boat_id(engine, r.sail_number, r.boat_name, r.rating_value, boat_cache)
        kind, old = classify(engine, kw, bid)
        if kind == "insert" and simulate_heal and slug == "rorc":
            variants = []
            if kw["event_date"] is not None:
                variants.append({"event_date": None})
            if kw.get("class_name"):
                variants.append({"class_name": None})
                if kw["event_date"] is not None:
                    variants.append({"event_date": None, "class_name": None})
            for v in variants:
                k2, old2 = classify(engine, {**kw, **v}, bid)
                if k2 != "insert":
                    kind, old = f"heal+{k2}", old2
                    break
        plan.append({"row": r, "kind": kind, "old": old})

    inserts = [p for p in plan if p["kind"] == "insert"]
    if not inserts:
        return plan
    matched = {p["old"]["id"] for p in plan if p["old"]}
    stored = page_rows(engine, source, rows[0], any_date=simulate_heal and slug == "rorc")
    free = defaultdict(list)  # (place, status-if-unplaced) -> unmatched stored rows
    for x in stored:
        if x["id"] in matched:
            continue
        key = (x["place"], None if x["place"] is not None else (x["status"] or "").upper())
        free[key].append(x)
    for p in inserts:
        r = p["row"]
        key = (r.place, None if r.place is not None else (r.status or "").upper())
        if free.get(key):
            p["kind"], p["old"] = "held", free[key].pop(0)
    return plan

# ---------------------------------------------------------------- event sources

def make_source(slug):
    if slug == "rorc":
        from irc_data.scrapers.rorc import RORCSource
        return RORCSource()
    if slug == "cowesweek":
        from irc_data.scrapers.cowesweek import CowesWeekSource
        return CowesWeekSource()
    if slug == "sydney-hobart":
        from irc_data.scrapers.sydneyhobart import SydneyHobartSource
        return SydneyHobartSource()
    raise ValueError(slug)


def run_event_source(args, engine):
    slug = args.source
    source = STORE_SOURCE[slug]
    include_class = INCLUDE_CLASS[slug]
    src = make_source(slug)
    events = asyncio.run(src.discover_events(since=date.fromisoformat(args.since))) or []
    if args.years:
        years = {int(y) for y in args.years.split(",")}
        events = [e for e in events if (e.metadata or {}).get("year") in years
                  or (e.event_date and e.event_date.year in years)]
    if args.events:
        wanted = {n.strip().lower() for n in args.events.split(";") if n.strip()}
        events = [e for e in events if e.event_name.strip().lower() in wanted]
    log(stage="discovered", source=slug, events=len(events))
    before = year_counts(engine, source)
    dry, dry_year = Counter(), defaultdict(Counter)
    found_year, written_year = Counter(), Counter()
    healed = 0
    errors: list[str] = []
    boat_cache: dict = {}
    heal_log = (open(args.heal_log or f"/home/irc-data/data-raw/backfills/{slug}_heal.jsonl", "a")
                if slug in ("rorc", "sydney-hobart") and not args.dry_run else None)
    held_log = (open(args.held_log or f"/home/irc-data/data-raw/backfills/{slug}_held.jsonl", "a")
                if not args.dry_run else None)
    held_year = Counter()
    for i, ev in enumerate(events, 1):
        try:
            rows = asyncio.run(src.scrape_event(ev)) or []
        except Exception as e:  # one broken event must not stop the backfill
            errors.append(f"{ev.event_name}: {type(e).__name__}: {e}"[:300])
            log(stage="event_error", event=ev.event_name, error=errors[-1])
            continue
        if not rows:
            log(stage="event", n=i, of=len(events), event=ev.event_name, rows=0)
            continue
        y = (ev.metadata or {}).get("year") or (rows[0].event_date.year if rows[0].event_date else None)
        found_year[y] += len(rows)
        if not args.dry_run and heal_log is not None and slug == "rorc":
            healed += heal_rorc_page(engine, rows, heal_log)
        elif not args.dry_run and heal_log is not None and slug == "sydney-hobart":
            healed += heal_hobart_urls(engine, ev, rows, heal_log)
        plan = plan_rows(engine, slug, source, include_class, ev, rows, boat_cache,
                         simulate_heal=args.dry_run)
        ev_kinds = Counter(p["kind"] for p in plan)
        dry.update(ev_kinds)
        dry_year[y].update(ev_kinds)
        held = [p for p in plan if p["kind"] == "held"]
        held_year[y] += len(held)
        samples = None
        if args.verbose:
            samples = [{"kind": p["kind"], "boat": p["row"].boat_name, "sail": p["row"].sail_number,
                        "place": p["row"].place,
                        "old": {k: p["old"][k] for k in ("id", "bn", "sn", "place")} if p["old"] else None}
                       for p in plan if p["kind"] != "strong"][:60] or None
        if args.dry_run:
            log(stage="event", n=i, of=len(events), event=ev.event_name, rows=len(rows),
                url=ev.event_url, dry=dict(ev_kinds), samples=samples)
            continue
        for p in held:
            held_log.write(json.dumps({
                "event": ev.event_name, "url": p["row"].source_url, "boat": p["row"].boat_name,
                "sail": p["row"].sail_number, "place": p["row"].place, "status": p["row"].status,
                "class_name": p["row"].class_name, "event_date": p["row"].event_date,
                "rating": p["row"].rating_value, "stored_row": p["old"]}, default=str) + "\n")
        held_log.flush()
        to_store = [p["row"] for p in plan if p["kind"] != "held"]
        stats = store_normalized_results(engine, to_store, source=source,
                                         include_class_name=include_class, log=False)
        written_year[y] += stats["written"]
        if stats["errors"]:
            errors.extend(stats["error_details"][:5])
        log(stage="event", n=i, of=len(events), event=ev.event_name, rows=len(rows),
            url=ev.event_url, kinds=dict(ev_kinds), written=stats["written"],
            store_errors=stats["errors"], healed_total=healed, samples=samples)
    log(stage="done", source=slug, dry_run=args.dry_run, found_by_year=dict(found_year),
        dry=dict(dry), dry_by_year={k: dict(v) for k, v in dry_year.items()},
        written_by_year=dict(written_year), held_by_year=dict(held_year), healed_rows=healed,
        counts_before=before, counts_after=year_counts(engine, source), errors=errors[:30])


# ---------------------------------------------------------------- manage2sail

def run_manage2sail(args, engine):
    from irc_data.scrapers import manage2sail as m

    since = date.fromisoformat(args.since)
    extra = [c.strip().lower() for c in (args.clubs or "").split(",") if c.strip()]
    scraper = m.Manage2SailScraper(club_ids=[*m.learned_club_ids(engine), *extra],
                                   max_events=10**6)
    queue = list(scraper.club_ids)
    walked: set[str] = set()
    seen_events: set[str] = set()
    before = year_counts(engine, "manage2sail")
    found_year, written_year = Counter(), Counter()
    dry_year: dict = defaultdict(Counter)
    irc_events = 0
    boat_cache: dict = {}

    async def club_events(club_id):
        refs = []
        for year in range(since.year, scraper.today.year + 1):
            url = f"{m.M2S_BASE}/en-US/Club/Detail/{club_id}"
            if year != scraper.today.year:
                url += f"?year={year}"
            try:
                resp = await scraper._get(url)
            except Exception as e:
                log(stage="club_page_error", club=club_id, year=year, error=str(e)[:200])
                continue
            refs.extend(m.parse_club_events(resp.text, club_id))
        return refs

    async def process(ref):
        nonlocal irc_events
        rows = await scraper.scrape_event(ref)
        if not rows:
            return
        irc_events += 1
        y = ref.event_date.year if ref.event_date else None
        found_year[y] += len(rows)
        orgs = {c for r in rows for c in (r.raw_data or {}).get("organizer_club_ids") or []}
        new_clubs = sorted(c for c in orgs if c not in walked and c not in queue)
        queue.extend(new_clubs)
        entry = {"event": ref.event_name, "date": ref.event_date, "rows": len(rows),
                 "url": ref.event_url, "new_clubs": new_clubs}
        if args.dry_run:
            kinds = Counter()
            for r in rows:
                bid = _boat_id(engine, r.sail_number, r.boat_name, r.rating_value, boat_cache)
                kinds[classify(engine, m2s_kwargs(r), bid)[0]] += 1
            dry_year[y].update(kinds)
            entry["dry"] = dict(kinds)
        else:
            st = m.store_results(engine, rows)
            written_year[y] += st["written"]
            entry.update(written=st["written"], store_errors=st["errors"])
        log(stage="irc_event", **entry)

    async def main():
        try:
            if args.m2s_events:
                # Only these events (URL|start-date), no club walk.
                for spec in (s.strip() for s in args.m2s_events.split(";")):
                    if not spec:
                        continue
                    url, start = spec.rsplit("|", 1)
                    ref = m.EventRef(source=m.SOURCE, event_name=url, event_url=url,
                                     event_date=date.fromisoformat(start),
                                     metadata={"end_date": start, "event_key": m._event_key(url),
                                               "club_ids": []})
                    seen_events.add(ref.metadata["event_key"])
                    await process(ref)
                return
            while queue:
                club = queue.pop(0)
                if club in walked:
                    continue
                walked.add(club)
                refs = await club_events(club)
                todo = []
                for ref in refs:
                    key = ref.metadata["event_key"]
                    if key in seen_events or date.fromisoformat(ref.metadata["end_date"]) < since:
                        continue
                    if ref.event_date and ref.event_date > scraper.today:
                        continue
                    if m._SKIP_EVENT_RE.search(ref.event_name):
                        continue
                    seen_events.add(key)
                    todo.append(ref)
                log(stage="club", club=club, events_listed=len(refs), events_new=len(todo),
                    queue=len(queue))
                for ref in todo:
                    await process(ref)
        finally:
            await scraper.aclose()

    asyncio.run(main())
    log(stage="done", source="manage2sail", dry_run=args.dry_run, clubs_walked=sorted(walked),
        events_seen=len(seen_events), irc_events=irc_events,
        found_by_year=dict(found_year), written_by_year=dict(written_year),
        dry_by_year={k: dict(v) for k, v in dry_year.items()}, http=scraper.stats,
        counts_before=before, counts_after=year_counts(engine, "manage2sail"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", choices=sorted(STORE_SOURCE))
    ap.add_argument("--since", required=True, help="ISO date; earliest event date to fetch")
    ap.add_argument("--years", help="comma list to restrict event sources to these years")
    ap.add_argument("--events", help="semicolon list of exact event names (event sources), "
                    "e.g. '2016 Cowes Week - IRC Class 2;2017 Cowes Week - IRC Class 2'")
    ap.add_argument("--m2s-events", help="manage2sail only: semicolon list of URL|YYYY-MM-DD "
                    "(event page and start date) to re-save without walking clubs")
    ap.add_argument("--clubs", help="extra manage2sail club GUIDs (comma list)")
    ap.add_argument("--held-log", help="JSONL of rows held back as probable duplicates "
                    "(default /home/irc-data/data-raw/backfills/<source>_held.jsonl)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--heal-log", help="JSONL of healed row ids "
                    "(default /home/irc-data/data-raw/backfills/<source>_heal.jsonl)")
    args = ap.parse_args()
    engine = get_engine()
    t0 = time.time()
    if args.source == "manage2sail":
        run_manage2sail(args, engine)
    else:
        run_event_source(args, engine)
    log(stage="elapsed", seconds=round(time.time() - t0))


if __name__ == "__main__":
    sys.exit(main())
