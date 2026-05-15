"""Enrich canonical fields on the boats table for better cross-referencing.

Steps:
  1. Backfill country from sail number prefixes
  2. Backfill design_canonical from ORC class_name
  3. Identify and report SEC/SH duplicate certificates
  4. Backfill country from race results (organizing club)
  5. Refresh materialized views

SAFETY: Never overwrites existing non-NULL values.
"""

import re
import sys
from collections import Counter

from sqlalchemy import text

from irc_data.db.connection import get_engine
from irc_data.db.operations import refresh_materialized_views
from irc_data.config import COUNTRY_PREFIXES
from irc_data.matching.designs import normalize_design, DESIGN_ALIASES


def step1_backfill_country_from_sail_prefix(engine) -> int:
    """Backfill country from sail number prefixes.  NEVER overwrites existing."""
    print("\n" + "=" * 70)
    print("STEP 1: Backfill country from sail number prefixes")
    print("=" * 70)

    # Build the prefix map: codebase COUNTRY_PREFIXES plus extra local rules
    # Sort by longest prefix first so "AUS" matches before "A"
    prefix_map = dict(COUNTRY_PREFIXES)

    # Extra mappings from the task spec
    extra_prefixes = {
        "MYC": "AUS",   # Middle Harbour Yacht Club
        "RQ": "AUS",    # RQYS
        "Sm": "AUS",    # RSYS/MHYC convention
        "GB": "GBR",
        "US": "USA",
        "CH": "SUI",
        "DP": "RSA",
        "UAE": "UAE",
        "IND": "IND",
        "MLT": "MLT",
    }
    prefix_map.update(extra_prefixes)

    # Sort prefixes longest first for greedy matching
    sorted_prefixes = sorted(prefix_map.keys(), key=len, reverse=True)

    with engine.begin() as conn:
        # Get all boats with NULL country
        null_country_boats = conn.execute(text(
            "SELECT id, sail_number FROM boats WHERE country IS NULL AND sail_number IS NOT NULL"
        )).fetchall()

        print(f"  Boats with country=NULL: {len(null_country_boats)}")

        updates_by_country = Counter()
        total_updated = 0

        for boat in null_country_boats:
            sail = boat.sail_number.strip() if boat.sail_number else ""
            if not sail:
                continue

            matched_country = None

            # Try standard prefixes (longest first)
            for prefix in sorted_prefixes:
                if sail.startswith(prefix):
                    matched_country = prefix_map[prefix]
                    break

            # Special: "F" followed by digits -> AUS (Fremantle/RPAYC)
            if not matched_country and re.match(r'^F\d+', sail):
                matched_country = "AUS"

            # Special: "B" followed by digits only -> BEL (tentative)
            # Skip this one as per spec "BEL or unknown" - too ambiguous
            # if not matched_country and re.match(r'^B\d+$', sail):
            #     matched_country = "BEL"

            if matched_country:
                conn.execute(
                    text("UPDATE boats SET country = :country, updated_at = now() WHERE id = :id"),
                    {"country": matched_country, "id": boat.id},
                )
                updates_by_country[matched_country] += 1
                total_updated += 1

        print(f"  Updated {total_updated} boats total")
        print(f"  Breakdown by country:")
        for country, count in sorted(updates_by_country.items(), key=lambda x: -x[1]):
            print(f"    {country}: {count}")

    return total_updated


def step2_backfill_design_from_orc(engine) -> int:
    """Backfill design_canonical from ORC class_name. NEVER overwrites existing."""
    print("\n" + "=" * 70)
    print("STEP 2: Backfill design_canonical from ORC class_name")
    print("=" * 70)

    with engine.begin() as conn:
        # Count boats that have ORC match but no design_canonical
        count_before = conn.execute(text("""
            SELECT COUNT(*) FROM boats b
            WHERE b.design_canonical IS NULL
              AND EXISTS (
                SELECT 1 FROM orc_certificates oc
                WHERE oc.boat_id = b.id AND oc.class_name IS NOT NULL AND oc.class_name != ''
              )
        """)).scalar()
        print(f"  Boats with ORC match but no design_canonical: {count_before}")

        # Load design_classes lookup: name_canonical + aliases -> name_canonical
        dc_rows = conn.execute(text(
            "SELECT name_canonical, aliases FROM design_classes"
        )).fetchall()

        # Build lookup: lowered alias -> canonical
        alias_lookup = {}
        for row in dc_rows:
            canon = row.name_canonical
            alias_lookup[canon.strip().upper()] = canon
            if row.aliases:
                for alias in row.aliases:
                    alias_lookup[alias.strip().upper()] = canon

        # Also add the DESIGN_ALIASES dict from matching/designs.py
        for alias, canon in DESIGN_ALIASES.items():
            alias_lookup[alias.strip().upper()] = canon

        # Get the boats to backfill: latest ORC class_name per boat
        boats_to_fill = conn.execute(text("""
            SELECT b.id AS boat_id, oc.class_name
            FROM boats b
            JOIN (
                SELECT DISTINCT ON (boat_id) boat_id, class_name
                FROM orc_certificates
                WHERE boat_id IS NOT NULL AND class_name IS NOT NULL AND class_name != ''
                ORDER BY boat_id, snapshot_date DESC
            ) oc ON oc.boat_id = b.id
            WHERE b.design_canonical IS NULL
        """)).fetchall()

        matched_via_lookup = 0
        set_directly = 0

        for row in boats_to_fill:
            orc_class = row.class_name.strip()
            orc_upper = orc_class.upper()

            # Try alias lookup first
            canonical = alias_lookup.get(orc_upper)
            if not canonical:
                # Try normalize_design
                canonical = normalize_design(orc_class)

            if canonical:
                # Check if it came from the lookup (known design class)
                if orc_upper in alias_lookup:
                    matched_via_lookup += 1
                else:
                    set_directly += 1
            else:
                # Fallback: use ORC class_name directly
                canonical = orc_class
                set_directly += 1

            conn.execute(
                text("UPDATE boats SET design_canonical = :dc, updated_at = now() WHERE id = :id"),
                {"dc": canonical, "id": row.boat_id},
            )

        total = matched_via_lookup + set_directly
        print(f"  Matched via design_classes/alias lookup: {matched_via_lookup}")
        print(f"  Set directly from ORC class_name (no alias match): {set_directly}")
        print(f"  Total boats updated: {total}")

    return total


def step3_report_sec_sh_duplicates(engine) -> int:
    """Identify SEC/SH duplicate certificates and report fleet inflation."""
    print("\n" + "=" * 70)
    print("STEP 3: Identify SEC/SH duplicate certificates (report only)")
    print("=" * 70)

    with engine.begin() as conn:
        # Find boats ending in " - SEC" or "(SH)"
        sec_boats = conn.execute(text("""
            SELECT id, boat_name, sail_number, design, design_canonical, country
            FROM boats
            WHERE boat_name LIKE '%- SEC'
               OR boat_name LIKE '%(SH)'
            ORDER BY boat_name
        """)).fetchall()

        print(f"  Total SEC/SH boats found: {len(sec_boats)}")

        # Try to find matching primary boats
        matched_pairs = 0
        unmatched = 0
        design_inflation = Counter()

        for sec in sec_boats:
            # Strip suffix to get primary name
            name = sec.boat_name.strip()
            if name.endswith("- SEC"):
                primary_name = name[:-5].strip()
            elif name.endswith("(SH)"):
                primary_name = name[:-4].strip()
            else:
                continue

            design_label = sec.design_canonical or sec.design or "UNKNOWN"

            # Look for primary: same sail_number, name matches
            primary = conn.execute(text("""
                SELECT id, boat_name FROM boats
                WHERE sail_number = :sail
                  AND boat_name = :name
                LIMIT 1
            """), {"sail": sec.sail_number, "name": primary_name}).first()

            if primary:
                matched_pairs += 1
            else:
                unmatched += 1

            design_inflation[design_label] += 1

        print(f"  Matched to a primary boat: {matched_pairs}")
        print(f"  No primary found (orphan SEC/SH): {unmatched}")
        print(f"\n  Fleet inflation by design class (top 10):")
        for design, count in design_inflation.most_common(10):
            print(f"    {design}: +{count} duplicate(s)")

    return len(sec_boats)


def step4_backfill_country_from_race_results(engine) -> int:
    """Backfill country from race result organizing_club. NEVER overwrites existing."""
    print("\n" + "=" * 70)
    print("STEP 4: Backfill country from race results (organizing club)")
    print("=" * 70)

    with engine.begin() as conn:
        # AUS clubs
        aus_updated = conn.execute(text("""
            UPDATE boats b
            SET country = 'AUS', updated_at = now()
            WHERE b.country IS NULL
              AND EXISTS (
                SELECT 1 FROM race_results rr
                WHERE rr.boat_id = b.id
                  AND rr.organizing_club IN ('CYCA', 'RPAYC', 'RSYS', 'MHYC')
              )
        """)).rowcount
        print(f"  AUS (from CYCA/RPAYC/RSYS/MHYC results): {aus_updated}")

        # RORC is trickier - international entries. Only set if still NULL
        # and we have multiple RORC entries (more likely GBR local fleet)
        # Actually, per spec: source='rorc' -> likely GBR but not certain.
        # We'll be conservative: only set GBR if the boat has RORC results
        # AND no other race results from non-GBR clubs.
        rorc_updated = conn.execute(text("""
            UPDATE boats b
            SET country = 'GBR', updated_at = now()
            WHERE b.country IS NULL
              AND EXISTS (
                SELECT 1 FROM race_results rr
                WHERE rr.boat_id = b.id
                  AND rr.source = 'rorc'
              )
              AND NOT EXISTS (
                SELECT 1 FROM race_results rr2
                WHERE rr2.boat_id = b.id
                  AND rr2.organizing_club IN ('CYCA', 'RPAYC', 'RSYS', 'MHYC')
              )
        """)).rowcount
        print(f"  GBR (from RORC results, no AUS club overlap): {rorc_updated}")

        total = aus_updated + rorc_updated
        print(f"  Total updated: {total}")

    return total


def step5_refresh_views(engine):
    """Refresh materialized views."""
    print("\n" + "=" * 70)
    print("STEP 5: Refresh materialized views")
    print("=" * 70)
    refreshed = refresh_materialized_views(engine)
    print(f"  Refreshed: {', '.join(refreshed) if refreshed else 'none'}")
    return refreshed


def main():
    print("=" * 70)
    print("CANONICALIZE BOATS - Enriching canonical fields")
    print("=" * 70)

    engine = get_engine()

    # Pre-flight stats
    with engine.connect() as conn:
        total_boats = conn.execute(text("SELECT COUNT(*) FROM boats")).scalar()
        null_country = conn.execute(text("SELECT COUNT(*) FROM boats WHERE country IS NULL")).scalar()
        null_design_canonical = conn.execute(text("SELECT COUNT(*) FROM boats WHERE design_canonical IS NULL")).scalar()
        print(f"\nPre-flight: {total_boats} boats, {null_country} with NULL country, "
              f"{null_design_canonical} with NULL design_canonical")

    # Run steps
    s1 = step1_backfill_country_from_sail_prefix(engine)
    s2 = step2_backfill_design_from_orc(engine)
    s3 = step3_report_sec_sh_duplicates(engine)
    s4 = step4_backfill_country_from_race_results(engine)
    s5 = step5_refresh_views(engine)

    # Post-flight stats
    with engine.connect() as conn:
        null_country_after = conn.execute(text("SELECT COUNT(*) FROM boats WHERE country IS NULL")).scalar()
        null_dc_after = conn.execute(text("SELECT COUNT(*) FROM boats WHERE design_canonical IS NULL")).scalar()

    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"  Step 1 (country from sail prefix): {s1} boats updated")
    print(f"  Step 2 (design_canonical from ORC): {s2} boats updated")
    print(f"  Step 3 (SEC/SH duplicates): {s3} duplicate certificates identified")
    print(f"  Step 4 (country from race results): {s4} boats updated")
    print(f"  Step 5 (materialized views): {len(s5)} views refreshed")
    print(f"\n  Country NULL: {null_country} -> {null_country_after} "
          f"(filled {null_country - null_country_after})")
    print(f"  design_canonical NULL: {null_design_canonical} -> {null_dc_after} "
          f"(filled {null_design_canonical - null_dc_after})")
    print("\nDone.")


if __name__ == "__main__":
    main()
