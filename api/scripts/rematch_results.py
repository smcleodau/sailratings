#!/usr/bin/env python3
"""Re-match unmatched race_results to boats in the database.

Handles two sources differently:
- SailSys: raw_data contains boat_name and sail_no directly
- RORC: raw_data only has owner and boat_type (boat_name/sail_number were not persisted)
  so we use owner-based matching from already-matched RORC results, plus TCC-based matching.

Matching strategies (in order of confidence):
1. SailSys sail_number exact match (normalized)
2. SailSys boat_name exact match (case-insensitive)
3. SailSys boat_name fuzzy match via pg_trgm similarity()
4. RORC unambiguous owner mapping (owner -> single boat_id from matched results)
5. RORC ambiguous owner + TCC disambiguation
6. RORC unique exact TCC match (single boat with that exact TCC)
"""

import sys
import time
from collections import defaultdict

from sqlalchemy import text

from irc_data.db.connection import get_engine
from irc_data.matching.identity import normalize_name, normalize_sail


def build_owner_boat_map(conn) -> tuple[dict[str, int], dict[str, set[int]]]:
    """Build owner -> boat_id mapping from already-matched RORC results.

    Returns (unambiguous_map, ambiguous_map) where:
    - unambiguous_map: owner_norm -> single boat_id
    - ambiguous_map: owner_norm -> set of boat_ids (for TCC disambiguation)
    """
    rows = conn.execute(text("""
        SELECT UPPER(TRIM(raw_data->>'owner')) as owner_norm, boat_id
        FROM race_results
        WHERE boat_id IS NOT NULL AND source = 'rorc'
        AND raw_data->>'owner' IS NOT NULL AND raw_data->>'owner' != ''
    """)).fetchall()

    owner_boats: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        if r[0]:
            owner_boats[r[0]].add(r[1])

    unambiguous = {k: next(iter(v)) for k, v in owner_boats.items() if len(v) == 1}
    ambiguous = {k: v for k, v in owner_boats.items() if len(v) > 1}

    return unambiguous, ambiguous


def build_boat_tcc_index(conn) -> dict[int, set]:
    """Build boat_id -> set of known TCC values from tcc_snapshots."""
    rows = conn.execute(text("""
        SELECT boat_id, tcc FROM tcc_snapshots
    """)).fetchall()
    index: dict[int, set] = defaultdict(set)
    for r in rows:
        index[r[0]].add(r[1])
    return index


def build_tcc_boat_index(conn) -> dict:
    """Build TCC value -> set of boat_ids from tcc_snapshots."""
    rows = conn.execute(text("""
        SELECT DISTINCT tcc, boat_id FROM tcc_snapshots
    """)).fetchall()
    index: dict = defaultdict(set)
    for r in rows:
        index[r[0]].add(r[1])
    return index


def build_sail_index(conn) -> dict[str, list[dict]]:
    """Build normalized sail_number -> list of boats index."""
    boats = conn.execute(text("""
        SELECT id, sail_number, boat_name, country FROM boats
    """)).fetchall()
    index: dict[str, list[dict]] = defaultdict(list)
    for b in boats:
        norm = normalize_sail(b[1])
        if norm:
            index[norm].append({
                "id": b[0], "sail_number": b[1],
                "boat_name": b[2], "country": b[3],
            })
    return index


def build_name_index(conn) -> dict[str, list[int]]:
    """Build normalized boat_name -> list of boat_ids index."""
    boats = conn.execute(text("""
        SELECT id, boat_name FROM boats
    """)).fetchall()
    index: dict[str, list[int]] = defaultdict(list)
    for b in boats:
        norm = normalize_name(b[1])
        if norm:
            index[norm].append(b[0])
    return index


def match_sailsys(conn, sail_index, name_index, stats):
    """Match SailSys results using boat_name and sail_no from raw_data."""
    rows = conn.execute(text("""
        SELECT id, raw_data, tcc_at_race, rating_value
        FROM race_results
        WHERE boat_id IS NULL AND source = 'sailsys'
    """)).fetchall()

    updates = []
    for row in rows:
        raw = row[1] or {}
        boat_name = (raw.get("boat_name") or "").strip()
        sail_no = (raw.get("sail_no") or "").strip()
        hull_id = (raw.get("hull_id") or "").strip()
        tcc = row[2] or row[3]
        matched_id = None
        method = None

        # Strategy 1: sail number match (skip single-char like "C")
        if sail_no and len(sail_no) > 2:
            norm_sail = normalize_sail(sail_no)
            candidates = sail_index.get(norm_sail, [])
            if len(candidates) == 1:
                matched_id = candidates[0]["id"]
                method = "sailsys_sail_exact"

        # Strategy 1b: hull_id as sail number
        if not matched_id and hull_id and len(hull_id) >= 3:
            norm_hull = normalize_sail(hull_id)
            candidates = sail_index.get(norm_hull, [])
            if len(candidates) == 1:
                matched_id = candidates[0]["id"]
                method = "sailsys_hull_id"

        # Strategy 2: exact name match
        if not matched_id and boat_name:
            norm_name = normalize_name(boat_name)
            candidates = name_index.get(norm_name, [])
            if len(candidates) == 1:
                matched_id = candidates[0]
                method = "sailsys_name_exact"
            elif len(candidates) > 1 and tcc:
                # Disambiguate by TCC
                result = conn.execute(
                    text("""
                        SELECT b.id FROM boats b
                        JOIN tcc_snapshots t ON t.boat_id = b.id
                        WHERE UPPER(TRIM(b.boat_name)) = :name
                          AND ABS(t.tcc - :tcc) < 0.005
                        ORDER BY t.snapshot_date DESC
                        LIMIT 1
                    """),
                    {"name": norm_name, "tcc": tcc},
                )
                r = result.first()
                if r:
                    matched_id = r[0]
                    method = "sailsys_name_tcc"

        # Strategy 3: fuzzy name match using similarity()
        # Use high threshold (0.8) to avoid false positives, and verify with TCC if available
        if not matched_id and boat_name and len(boat_name) >= 4:
            norm = normalize_name(boat_name)
            if tcc:
                # Fuzzy name + TCC verification (more confident)
                result = conn.execute(
                    text("""
                        SELECT b.id, b.boat_name, similarity(UPPER(b.boat_name), :name) as sim
                        FROM boats b
                        JOIN tcc_snapshots t ON t.boat_id = b.id
                        WHERE similarity(UPPER(b.boat_name), :name) > 0.7
                          AND ABS(t.tcc - :tcc) < 0.01
                        ORDER BY sim DESC
                        LIMIT 1
                    """),
                    {"name": norm, "tcc": tcc},
                )
                r = result.first()
                if r:
                    matched_id = r[0]
                    method = "sailsys_name_fuzzy"
            else:
                # Fuzzy name only -- require very high similarity
                result = conn.execute(
                    text("""
                        SELECT id, boat_name, similarity(UPPER(boat_name), :name) as sim
                        FROM boats
                        WHERE similarity(UPPER(boat_name), :name) > 0.85
                        ORDER BY sim DESC
                        LIMIT 1
                    """),
                    {"name": norm},
                )
                r = result.first()
                if r:
                    matched_id = r[0]
                    method = "sailsys_name_fuzzy"

        if matched_id:
            updates.append((matched_id, row[0]))
            stats[method] = stats.get(method, 0) + 1
            stats["matched"] += 1

    return updates


def match_rorc(conn, owner_unambiguous, owner_ambiguous, boat_tcc_index, tcc_boat_index, stats):
    """Match RORC results using owner mapping and TCC-based strategies."""
    rows = conn.execute(text("""
        SELECT id, raw_data, rating_value
        FROM race_results
        WHERE boat_id IS NULL AND source = 'rorc'
    """)).fetchall()

    updates = []
    for row in rows:
        raw = row[1] or {}
        owner = (raw.get("owner") or "").strip().upper()
        rating_value = row[2]
        matched_id = None
        method = None

        # Strategy 4: unambiguous owner mapping
        if owner and owner in owner_unambiguous:
            matched_id = owner_unambiguous[owner]
            method = "rorc_owner_exact"

        # Strategy 5: ambiguous owner + TCC disambiguation
        if not matched_id and owner and owner in owner_ambiguous and rating_value:
            candidate_boat_ids = owner_ambiguous[owner]
            tcc_matches = []
            for bid in candidate_boat_ids:
                known_tccs = boat_tcc_index.get(bid, set())
                for known_tcc in known_tccs:
                    if abs(known_tcc - rating_value) < 0.01:
                        tcc_matches.append(bid)
                        break
            if len(tcc_matches) == 1:
                matched_id = tcc_matches[0]
                method = "rorc_owner_tcc"

        # Strategy 6: unique exact TCC match (only if no owner match)
        if not matched_id and rating_value:
            tcc_candidates = tcc_boat_index.get(rating_value, set())
            if len(tcc_candidates) == 1:
                matched_id = next(iter(tcc_candidates))
                method = "rorc_tcc_unique"

        if matched_id:
            updates.append((matched_id, row[0]))
            stats[method] = stats.get(method, 0) + 1
            stats["matched"] += 1

    return updates


def main():
    engine = get_engine()
    stats = {
        "total_unmatched": 0,
        "matched": 0,
        "updated": 0,
        "sailsys_sail_exact": 0,
        "sailsys_hull_id": 0,
        "sailsys_name_exact": 0,
        "sailsys_name_tcc": 0,
        "sailsys_name_fuzzy": 0,
        "rorc_owner_exact": 0,
        "rorc_owner_tcc": 0,
        "rorc_tcc_unique": 0,
    }

    dry_run = "--dry-run" in sys.argv

    print("=" * 60)
    print("Race Results Re-matching")
    print("=" * 60)
    if dry_run:
        print("DRY RUN MODE -- no updates will be applied\n")

    with engine.begin() as conn:
        # Count total unmatched
        total = conn.execute(text(
            "SELECT COUNT(*) FROM race_results WHERE boat_id IS NULL"
        )).scalar()
        stats["total_unmatched"] = total
        print(f"Total unmatched results: {total}")

        by_source = conn.execute(text("""
            SELECT source, COUNT(*) FROM race_results
            WHERE boat_id IS NULL GROUP BY source ORDER BY COUNT(*) DESC
        """)).fetchall()
        for src, cnt in by_source:
            print(f"  {src}: {cnt}")

        # Build indexes
        print("\nBuilding lookup indexes...")
        t0 = time.time()
        sail_index = build_sail_index(conn)
        name_index = build_name_index(conn)
        owner_unambiguous, owner_ambiguous = build_owner_boat_map(conn)
        boat_tcc_index = build_boat_tcc_index(conn)
        tcc_boat_index = build_tcc_boat_index(conn)
        print(f"  Sail numbers indexed: {len(sail_index)}")
        print(f"  Boat names indexed: {len(name_index)}")
        print(f"  Unambiguous owners: {len(owner_unambiguous)}")
        print(f"  Ambiguous owners: {len(owner_ambiguous)}")
        print(f"  Boats with TCC data: {len(boat_tcc_index)}")
        print(f"  Unique TCC values: {len(tcc_boat_index)}")
        print(f"  Indexes built in {time.time() - t0:.1f}s")

        # Match SailSys results
        print("\nMatching SailSys results...")
        t0 = time.time()
        sailsys_updates = match_sailsys(conn, sail_index, name_index, stats)
        print(f"  Found {len(sailsys_updates)} matches in {time.time() - t0:.1f}s")

        # Match RORC results
        print("\nMatching RORC results...")
        t0 = time.time()
        rorc_updates = match_rorc(
            conn, owner_unambiguous, owner_ambiguous,
            boat_tcc_index, tcc_boat_index, stats,
        )
        print(f"  Found {len(rorc_updates)} matches in {time.time() - t0:.1f}s")

        # Apply updates
        all_updates = sailsys_updates + rorc_updates
        if all_updates and not dry_run:
            print(f"\nApplying {len(all_updates)} updates...")
            t0 = time.time()
            batch_size = 500
            for i in range(0, len(all_updates), batch_size):
                batch = all_updates[i:i + batch_size]
                for boat_id, result_id in batch:
                    conn.execute(
                        text("UPDATE race_results SET boat_id = :boat_id WHERE id = :id"),
                        {"boat_id": boat_id, "id": result_id},
                    )
                if (i + batch_size) % 2000 == 0 or i + batch_size >= len(all_updates):
                    print(f"  Updated {min(i + batch_size, len(all_updates))}/{len(all_updates)} rows...")
            stats["updated"] = len(all_updates)
            print(f"  Done in {time.time() - t0:.1f}s")
        elif all_updates:
            print(f"\nDRY RUN: Would update {len(all_updates)} rows")
            stats["updated"] = 0
        else:
            print("\nNo matches found.")

        # Verify final counts
        remaining = conn.execute(text(
            "SELECT COUNT(*) FROM race_results WHERE boat_id IS NULL"
        )).scalar()

    # Print summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    print(f"Total unmatched before:   {stats['total_unmatched']}")
    print(f"Total matches found:      {stats['matched']}")
    print(f"Updates applied:          {stats['updated']}")
    print(f"Remaining unmatched:      {remaining}")
    print(f"\nBy strategy:")
    print(f"  SailSys sail exact:     {stats['sailsys_sail_exact']}")
    print(f"  SailSys hull ID:        {stats['sailsys_hull_id']}")
    print(f"  SailSys name exact:     {stats['sailsys_name_exact']}")
    print(f"  SailSys name + TCC:     {stats['sailsys_name_tcc']}")
    print(f"  SailSys name fuzzy:     {stats['sailsys_name_fuzzy']}")
    print(f"  RORC owner exact:       {stats['rorc_owner_exact']}")
    print(f"  RORC owner + TCC:       {stats['rorc_owner_tcc']}")
    print(f"  RORC TCC unique:        {stats['rorc_tcc_unique']}")
    print(f"\nMatch rate: {stats['matched'] / stats['total_unmatched'] * 100:.1f}%")


if __name__ == "__main__":
    main()
