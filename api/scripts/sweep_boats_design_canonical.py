"""Sweep `boats.design_canonical` through `normalize_design()`.

For every boat with a non-NULL `design_canonical`, run the value through
`normalize_design()`. When the normalized form differs from the current
value, record the change in the audit table `boats_design_canonical_sweep`
and UPDATE the boat row.

Safeguards:
  * Audit table records every change (boat_id, old, new, swept_at).
  * Only writes audit + UPDATE rows when `normalize_design(old) != old`.
  * Runs inside a single transaction so the sweep is all-or-nothing.
  * Idempotent: re-running yields zero updates (since the second pass'
    inputs are already canonical).
  * Touches ONLY `boats.design_canonical` (and `updated_at`).

After the sweep, re-checks the orphan count (boats whose
`design_canonical` doesn't match any `design_classes.name_canonical`) and
prints the top-30 remaining unrecognized design strings so they can be
triaged into new aliases or new `design_classes` rows.

Run via:
    source .venv/bin/activate
    python -m scripts.sweep_boats_design_canonical [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from sqlalchemy import text

from irc_data.db.connection import get_engine
from irc_data.matching.designs import normalize_design


AUDIT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS boats_design_canonical_sweep (
    id          bigserial PRIMARY KEY,
    swept_at    timestamptz NOT NULL DEFAULT now(),
    boat_id     integer NOT NULL,
    old_value   text,
    new_value   text
);
"""


def _orphan_stats(conn) -> tuple[int, int]:
    """(orphan_boat_count, orphan_distinct_designs)."""
    total = conn.execute(text(
        """
        SELECT COUNT(*) FROM boats b
        WHERE b.design_canonical IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM design_classes dc
              WHERE dc.name_canonical = b.design_canonical
          )
        """
    )).scalar() or 0
    distinct = conn.execute(text(
        """
        SELECT COUNT(DISTINCT design_canonical) FROM boats b
        WHERE b.design_canonical IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM design_classes dc
              WHERE dc.name_canonical = b.design_canonical
          )
        """
    )).scalar() or 0
    return int(total), int(distinct)


def _top_orphans(conn, limit: int = 30) -> list[tuple[str, int]]:
    rows = conn.execute(text(
        """
        SELECT design_canonical, COUNT(*) AS cnt
        FROM boats b
        WHERE b.design_canonical IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM design_classes dc
              WHERE dc.name_canonical = b.design_canonical
          )
        GROUP BY design_canonical
        ORDER BY cnt DESC, design_canonical ASC
        LIMIT :lim
        """
    ), {"lim": limit}).fetchall()
    return [(r[0], int(r[1])) for r in rows]


def sweep(engine, dry_run: bool = False) -> dict:
    stats: dict = {
        "considered": 0,
        "updates_planned": 0,
        "updates_skipped_no_target": 0,
        "updates_applied": 0,
        "audit_rows_written": 0,
        "orphans_before": 0,
        "orphans_before_distinct": 0,
        "orphans_after": 0,
        "orphans_after_distinct": 0,
        "top_remaining": [],
        "skipped_targets": [],
    }

    with engine.begin() as conn:
        # 1. Ensure audit table exists.
        conn.execute(text(AUDIT_TABLE_DDL))

        # Capture orphan stats BEFORE the sweep.
        before_total, before_distinct = _orphan_stats(conn)
        stats["orphans_before"] = before_total
        stats["orphans_before_distinct"] = before_distinct

        # 2. Pull every boat with a design_canonical.
        rows = conn.execute(text(
            """
            SELECT id, design_canonical
            FROM boats
            WHERE design_canonical IS NOT NULL
            """
        )).fetchall()
        stats["considered"] = len(rows)

        # 3. Plan updates where normalization changes the value.
        planned: list[tuple[int, str, str]] = []
        for boat_id, current in rows:
            new_val = normalize_design(current)
            if new_val is None:
                continue
            if new_val == current:
                continue
            planned.append((int(boat_id), current, new_val))

        stats["updates_planned"] = len(planned)

        # 3a. Filter out updates whose target name_canonical is not in
        # design_classes — they would violate the FK constraint, and we
        # are not allowed to insert into design_classes. Such rows are
        # reported separately so the operator can add the needed
        # design_classes rows / aliases later.
        existing_names: set[str] = set()
        if planned:
            target_names = sorted({new for _, _, new in planned})
            res = conn.execute(
                text(
                    "SELECT name_canonical FROM design_classes "
                    "WHERE name_canonical = ANY(:names)"
                ),
                {"names": target_names},
            ).fetchall()
            existing_names = {r[0] for r in res}

        applicable: list[tuple[int, str, str]] = []
        skipped_targets: Counter = Counter()
        for boat_id, old, new in planned:
            if new in existing_names:
                applicable.append((boat_id, old, new))
            else:
                skipped_targets[new] += 1
        stats["updates_skipped_no_target"] = sum(skipped_targets.values())
        stats["skipped_targets"] = sorted(
            skipped_targets.items(), key=lambda kv: (-kv[1], kv[0])
        )

        if dry_run:
            print(
                f"[dry-run] would update {len(applicable)} boats "
                f"({stats['updates_skipped_no_target']} skipped — target not in design_classes)"
            )
            for boat_id, old, new in applicable[:20]:
                print(f"  boat {boat_id}: {old!r} -> {new!r}")
            if len(applicable) > 20:
                print(f"  ... and {len(applicable) - 20} more")
            if stats["skipped_targets"]:
                print()
                print("Skipped — target name_canonical not in design_classes:")
                for name, cnt in stats["skipped_targets"]:
                    print(f"  {cnt:>3}  {name}")
            return stats

        # 4. Apply updates.
        for boat_id, old, new in applicable:
            conn.execute(
                text(
                    """
                    INSERT INTO boats_design_canonical_sweep
                        (boat_id, old_value, new_value)
                    VALUES (:boat_id, :old, :new)
                    """
                ),
                {"boat_id": boat_id, "old": old, "new": new},
            )
            conn.execute(
                text(
                    """
                    UPDATE boats
                    SET design_canonical = :new,
                        updated_at = now()
                    WHERE id = :boat_id
                    """
                ),
                {"boat_id": boat_id, "new": new},
            )
            stats["updates_applied"] += 1
            stats["audit_rows_written"] += 1

        # 5. Re-check orphan stats AFTER the sweep, inside the same txn.
        after_total, after_distinct = _orphan_stats(conn)
        stats["orphans_after"] = after_total
        stats["orphans_after_distinct"] = after_distinct
        stats["top_remaining"] = _top_orphans(conn, limit=30)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan + report only; do not write updates or audit rows.",
    )
    args = parser.parse_args()

    engine = get_engine()
    stats = sweep(engine, dry_run=args.dry_run)

    print()
    print("=" * 70)
    print("SWEEP: boats.design_canonical via normalize_design()")
    print("=" * 70)
    print(f"  boats considered                 : {stats['considered']}")
    print(f"  updates planned (norm differs)   : {stats['updates_planned']}")
    print(f"  updates skipped (no FK target)   : {stats['updates_skipped_no_target']}")
    if not args.dry_run:
        print(f"  updates applied                  : {stats['updates_applied']}")
        print(f"  audit rows written               : {stats['audit_rows_written']}")
    print(
        f"  orphans BEFORE (boats / distinct): "
        f"{stats['orphans_before']} / {stats['orphans_before_distinct']}"
    )
    print(
        f"  orphans AFTER  (boats / distinct): "
        f"{stats['orphans_after']} / {stats['orphans_after_distinct']}"
    )
    print()
    print("Top-30 remaining unrecognized design_canonical values:")
    print(f"  {'count':>5}  design_canonical")
    print(f"  {'-----':>5}  {'-' * 60}")
    for name, count in stats["top_remaining"]:
        print(f"  {count:>5}  {name}")
    print()

    if stats["skipped_targets"]:
        print("Skipped — normalize_design produced a target NOT in design_classes:")
        print(f"  {'count':>5}  target name_canonical")
        print(f"  {'-----':>5}  {'-' * 60}")
        for name, cnt in stats["skipped_targets"]:
            print(f"  {cnt:>5}  {name}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
