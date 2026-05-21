"""Validate discovery.extractor.extract_results against known race_results rows.

Usage:
    op run --environment vzhxzxt7mgb4tolyepo5wqzcz4 -- \
        .venv/bin/python scripts/validate_extract_results.py \
        --source cowesweek --limit 3

Picks N source_urls from race_results for the given source, scrapes each
via Firecrawl, runs the Claude extractor, and prints a per-event diff:

  rows_db   — what we have in race_results
  rows_new  — what Claude found
  match     — sail-number intersection / rows_db
  extra     — rows in Claude's output not in DB
  missing   — rows in DB not in Claude's output

This tells us whether we can retire the bespoke scraper. ≥95% match on
sail numbers is "ship it"; below that, we need to understand why before
cutting over.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from typing import Any

from sqlalchemy import text

from irc_data.db.connection import get_engine
from irc_data.discovery.extractor import extract_results
from irc_data.discovery.firecrawl_client import scrape_url


def _normalize_sail(s: str | None) -> str:
    if not s:
        return ""
    return "".join(c for c in s.upper() if c.isalnum())


def _normalize_name(s: str | None) -> str:
    """Compare boat names tolerant of crew-format suffixes, sponsor changes,
    and spacing. Strips (DH)/(TH) tags, collapses whitespace, removes
    non-alphanumeric. The DB has artifacts from the old scraper that
    matter less than whether the same boat is in both lists.
    """
    if not s:
        return ""
    import re
    s = s.upper()
    s = re.sub(r"\s*\((DH|TH|DOUBLE.?HANDED|TWO.?HANDED)\)\s*", "", s)
    s = re.sub(r"[^A-Z0-9]+", "", s)
    return s


def _name_match(a: str, b: str) -> bool:
    """Two normalized names match if either contains the other (handles
    DB truncation / sponsor-name changes like CALIBRE ↔ CALIBRE12 or
    URM ↔ URMGROUP)."""
    if not a or not b:
        return False
    if a == b:
        return True
    return (a in b or b in a) and min(len(a), len(b)) >= 3


def fetch_db_rows(engine, source_url: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT place, class_name, rating_value, status,
                   raw_data->>'boat_name' AS boat_name,
                   raw_data->>'sail_number' AS sail_number
            FROM race_results
            WHERE source_url = :url
            ORDER BY place NULLS LAST
        """), {"url": source_url}).fetchall()
    return [dict(r._mapping) for r in rows]


def pick_targets(engine, source: str, limit: int) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT source_url, COUNT(*) AS rows
            FROM race_results
            WHERE source = :source AND source_url IS NOT NULL
            GROUP BY source_url
            HAVING COUNT(*) >= 8
            ORDER BY MAX(event_date) DESC, rows DESC
            LIMIT :limit
        """), {"source": source, "limit": limit}).fetchall()
    return [r.source_url for r in rows]


def diff(db_rows: list[dict], new_rows: list[dict]) -> dict[str, Any]:
    """Match primarily by boat name (the only field that's reliably present
    on every page format), fall back to sail number when both sides have one.
    """
    db_norm = [(_normalize_name(r.get("boat_name", "")), r) for r in db_rows if r.get("boat_name")]
    new_norm = [(_normalize_name(r.get("boat_name", "")), r) for r in new_rows if r.get("boat_name")]
    db_names = {n for n, _ in db_norm if n}
    new_names = {n for n, _ in new_norm if n}

    # Tolerant matching: try exact intersection first, then containment
    matched: set[str] = set()
    used_new: set[str] = set()
    for n in db_names:
        if n in new_names:
            matched.add(n)
            used_new.add(n)
            continue
        for m in new_names:
            if m in used_new:
                continue
            if _name_match(n, m):
                matched.add(n)
                used_new.add(m)
                break
    missing = db_names - matched - used_new
    extra = new_names - used_new

    # Sail-number coverage (informational — extra fidelity if the page has it)
    db_with_sail = sum(1 for r in db_rows if r.get("sail_number"))
    new_with_sail = sum(1 for r in new_rows if r.get("sail_number"))

    db_rows_named = [r for r in db_rows if r.get("boat_name")]
    rate = (len(matched) / len(db_rows_named)) if db_rows_named else 0.0

    return {
        "rows_db": len(db_rows),
        "rows_new": len(new_rows),
        "db_named": len(db_rows_named),
        "db_with_sail": db_with_sail,
        "new_with_sail": new_with_sail,
        "matched_by_name": len(matched),
        "missing_names": sorted(missing)[:10],
        "extra_names": sorted(extra)[:10],
        "match_rate": rate,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="race_results.source to validate (e.g. cowesweek, sydneyhobart, rhkyc, isora, sailracehq)")
    ap.add_argument("--limit", type=int, default=3,
                    help="Number of source_urls to test (default 3)")
    ap.add_argument("--url", action="append",
                    help="Override: specific URL(s) to test instead of picking from DB")
    args = ap.parse_args()

    engine = get_engine()
    targets = args.url or pick_targets(engine, args.source, args.limit)
    if not targets:
        print(f"No targets found for source={args.source!r}", file=sys.stderr)
        return 1

    print(f"\nValidating {len(targets)} {args.source} URLs against extract_results()\n")

    all_stats: list[dict] = []
    for i, url in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {url[:90]}")
        db_rows = fetch_db_rows(engine, url)
        print(f"   DB rows: {len(db_rows)}")

        try:
            scraped = scrape_url(url, caller="validate")
        except Exception as e:
            print(f"   FIRECRAWL FAILED: {e}")
            continue
        if not scraped.markdown.strip():
            print("   FIRECRAWL: empty markdown — skipping")
            continue
        print(f"   scraped: {len(scraped.markdown)} chars")

        extraction = extract_results(url, scraped.markdown)
        if extraction.get("_error"):
            print(f"   EXTRACT FAILED: {extraction['_error']}")
            continue

        rows = extraction.get("results", [])
        stats = diff(db_rows, rows)
        stats["url"] = url
        stats["event_name"] = extraction.get("event_name")
        stats["class_name"] = extraction.get("class_name")
        stats["confidence"] = extraction.get("confidence")
        all_stats.append(stats)

        print(f"   event_name: {extraction.get('event_name')!r}")
        print(f"   class:      {extraction.get('class_name')!r}")
        print(f"   confidence: {extraction.get('confidence')}")
        print(f"   rows:       {stats['rows_new']} extracted vs {stats['rows_db']} in DB ({stats['db_named']} named)")
        print(f"   matched:    by-name={stats['matched_by_name']}  · sail coverage: db={stats['db_with_sail']} new={stats['new_with_sail']}")
        print(f"   match_rate: {stats['match_rate']*100:.1f}%")
        if stats["missing_names"]:
            print(f"   missing:    {stats['missing_names']}")
        if stats["extra_names"]:
            print(f"   extra:      {stats['extra_names']}")
        print()

    if all_stats:
        avg_match = sum(s["match_rate"] for s in all_stats) / len(all_stats)
        print(f"OVERALL: {len(all_stats)} events tested, avg match rate {avg_match*100:.1f}%")
        if avg_match >= 0.95:
            print("VERDICT: ≥95% — ready to cut over.")
        elif avg_match >= 0.85:
            print("VERDICT: 85–95% — investigate edge cases before cutover.")
        else:
            print("VERDICT: <85% — extractor needs work.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
