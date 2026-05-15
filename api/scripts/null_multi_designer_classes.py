"""NULL out designer / builder on `design_classes` for genuine multi-X classes.

For open / box-rule / development classes (TP 52, Maxi 72, IRC 52, GP 42,
Class 40, IMOCA, RC44, Volvo 65/70, Mini 6.50, Mini Maxi, Maxi, ORMA 60, ...)
there is no single canonical designer or builder -- multiple yards build to a
class rule and multiple design offices produce hulls within it. The earlier
backfill picked whichever variant won the modal vote, which yields a misleading
authoritative-looking attribution.

This script:
  1. Reads data/ambiguous_designs.json (the backfill's ambiguity audit).
  2. Re-collapses each design's designer/builder lists through
     normalize_designer() / normalize_builder() to ignore mere spelling drift.
  3. Cross-references the post-normalization distinct counts (>= 3) with a
     curated list of known open / box-rule / development classes.
  4. For each surviving candidate, audit-logs the existing
     `design_classes.designer` / `design_classes.builder` value into
     `design_class_attr_nulls` and then NULLs the column.

Idempotent: re-running is a no-op once a column is NULL (we skip rows that
are already NULL for that column). Single transaction.

Run via:
    source .venv/bin/activate
    python -m scripts.null_multi_designer_classes [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text

from irc_data.db.connection import get_engine
from irc_data.matching.designs import normalize_builder, normalize_designer


# ---------------------------------------------------------------------------
# Curated open / box-rule / development class list
# ---------------------------------------------------------------------------
# These are classes where the rule defines the boat, not a single design office
# or yard. Multiple designers and multiple builders compete within the box.
# Matched against `design_classes.name_canonical` case-insensitively.
CURATED_OPEN_CLASSES: set[str] = {
    "TP 52",
    "Maxi 72",
    "IRC 52",
    "GP 42",
    "GP 33",
    "Mini 6.50",
    "Class 40",
    "IMOCA",
    "IMOCA 60",
    "RC44",
    "Volvo Open 70",
    "Volvo 65",
    "Volvo Ocean 65",
    "Mini Maxi",
    "Super Maxi",
    "Maxi",
    "ORMA",
    "ORMA 60",
    "Fast 40+",
}

MIN_DISTINCT_POST_NORM = 3

AMBIGUOUS_PATH = Path("data/ambiguous_designs.json")


def _norm_lookup(name: str) -> str:
    return name.strip().casefold()


def _collapse(pairs: list[list], normalizer) -> dict[str, int]:
    """Run a list of [value, count] pairs through a normalizer and sum counts."""
    out: dict[str, int] = defaultdict(int)
    for value, count in pairs:
        canon = normalizer(value)
        if canon is None:
            continue
        out[canon] += int(count)
    return dict(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only; no DB writes")
    args = ap.parse_args()

    if not AMBIGUOUS_PATH.exists():
        print(f"ERROR: {AMBIGUOUS_PATH} not found", file=sys.stderr)
        return 2

    ambiguous = json.loads(AMBIGUOUS_PATH.read_text())
    curated_lookup = {_norm_lookup(c): c for c in CURATED_OPEN_CLASSES}

    # ---- Step 1: build candidate set from JSON + curated cross-ref ------
    candidates = []  # list of dicts with name, des_counts, bld_counts
    skipped: list[tuple[str, str]] = []

    for d in ambiguous:
        name = d.get("name") or ""
        des_counts = _collapse(d.get("designers", []), normalize_designer)
        bld_counts = _collapse(d.get("builders", []), normalize_builder)

        in_curated = _norm_lookup(name) in curated_lookup

        designer_qualifies = len(des_counts) >= MIN_DISTINCT_POST_NORM and in_curated
        builder_qualifies = len(bld_counts) >= MIN_DISTINCT_POST_NORM and in_curated

        if not (designer_qualifies or builder_qualifies):
            if not in_curated:
                skipped.append((name, "not on curated open-class list"))
            elif len(des_counts) < MIN_DISTINCT_POST_NORM and len(bld_counts) < MIN_DISTINCT_POST_NORM:
                skipped.append((name, f"post-norm distinct < {MIN_DISTINCT_POST_NORM}"))
            continue

        candidates.append({
            "name": name,
            "des_counts": des_counts,
            "bld_counts": bld_counts,
            "null_designer": designer_qualifies,
            "null_builder": builder_qualifies,
        })

    # ---- Step 2: apply changes in one transaction -----------------------
    engine = get_engine()
    results = []  # list of (canonical, n_des, n_bld, nulled)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS design_class_attr_nulls (
                id          bigserial PRIMARY KEY,
                nulled_at   timestamptz NOT NULL DEFAULT now(),
                design_id   integer NOT NULL,
                canonical   text NOT NULL,
                column_name text NOT NULL,
                old_value   text NOT NULL
            )
        """))

        for c in candidates:
            # Find row in design_classes. Match on lower(name_canonical) so
            # casing drift in the table doesn't hide a hit.
            row = conn.execute(
                text("""
                    SELECT id, name_canonical, designer, builder
                    FROM design_classes
                    WHERE LOWER(name_canonical) = LOWER(:n)
                    LIMIT 1
                """),
                {"n": c["name"]},
            ).fetchone()

            if row is None:
                results.append({
                    "canonical": c["name"],
                    "n_des": len(c["des_counts"]),
                    "n_bld": len(c["bld_counts"]),
                    "nulled": "(no design_classes row)",
                })
                continue

            nulled = []

            if c["null_designer"] and row.designer is not None:
                conn.execute(
                    text("""
                        INSERT INTO design_class_attr_nulls
                            (design_id, canonical, column_name, old_value)
                        VALUES (:id, :canon, 'designer', :old)
                    """),
                    {"id": row.id, "canon": row.name_canonical, "old": row.designer},
                )
                if not args.dry_run:
                    conn.execute(
                        text("UPDATE design_classes SET designer = NULL WHERE id = :id"),
                        {"id": row.id},
                    )
                nulled.append(f"designer (was {row.designer!r})")

            if c["null_builder"] and row.builder is not None:
                conn.execute(
                    text("""
                        INSERT INTO design_class_attr_nulls
                            (design_id, canonical, column_name, old_value)
                        VALUES (:id, :canon, 'builder', :old)
                    """),
                    {"id": row.id, "canon": row.name_canonical, "old": row.builder},
                )
                if not args.dry_run:
                    conn.execute(
                        text("UPDATE design_classes SET builder = NULL WHERE id = :id"),
                        {"id": row.id},
                    )
                nulled.append(f"builder (was {row.builder!r})")

            results.append({
                "canonical": row.name_canonical,
                "n_des": len(c["des_counts"]),
                "n_bld": len(c["bld_counts"]),
                "nulled": ", ".join(nulled) if nulled else "(already NULL / nothing to do)",
            })

        if args.dry_run:
            # Roll back: dry-run still inserted audit rows, undo them.
            # Print first while the connection's results are in hand.
            _print_report(results, skipped, dry_run=True)
            raise _DryRunRollback()

    _print_report(results, skipped, dry_run=False)
    return 0


class _DryRunRollback(Exception):
    pass


def _print_report(results, skipped, dry_run):
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"=== null_multi_designer_classes [{mode}] ===")
    print()
    print(f"{'canonical':<20}  {'n_des':>5}  {'n_bld':>5}  nulled")
    print("-" * 80)
    for r in results:
        print(f"{r['canonical']:<20}  {r['n_des']:>5}  {r['n_bld']:>5}  {r['nulled']}")
    if skipped:
        print()
        print("Skipped (ambiguous-JSON entries that did not qualify):")
        for name, reason in skipped:
            print(f"  - {name}: {reason}")


if __name__ == "__main__":
    try:
        rc = main()
    except _DryRunRollback:
        print("(dry-run: transaction rolled back; no DB writes persisted)")
        rc = 0
    sys.exit(rc or 0)
