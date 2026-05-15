"""Collapse duplicate canonicals in `design_classes`.

Two rows are duplicates if their `name_canonical` normalizes to the same value
via `irc_data.matching.designs.normalize_design`. For each cluster:

  1. Pick a winner using these rules (in order):
       a) name exactly equals the canonical
       b) most non-NULL columns among (designer, builder, year_first,
          nominal_loa, nominal_beam, nominal_draft, nominal_displacement)
       c) most boats pointing at it via boats.design_canonical
       d) lowest id
  2. Coalesce: for each updatable column the winner is NULL on but a loser
     has a value, UPDATE the winner with the first loser value found
     (sorted by loser id ascending).
  3. INSERT the loser row snapshot into `design_class_merges` (audit table).
  4. UPDATE boats SET design_canonical = winner.name_canonical
     WHERE design_canonical = loser.name_canonical.
     (boats has no FK column to design_classes; the link is by text on
     boats.design_canonical -> design_classes.name_canonical.)
  5. DELETE the loser row.
  6. COMMIT the cluster.

Idempotent: re-running after a successful merge is a no-op because the
duplicate rows are already gone.

Schema is untouched; only data + the new audit table.

Usage:
    source .venv/bin/activate
    python -m scripts.merge_design_classes [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.db.connection import get_engine
from irc_data.matching.designs import normalize_design


COALESCE_COLS = (
    "designer",
    "builder",
    "year_first",
    "year_last",
    "nominal_loa",
    "nominal_lwl",
    "nominal_beam",
    "nominal_draft",
    "nominal_displacement",
)

NON_NULL_SCORE_COLS = (
    "designer",
    "builder",
    "year_first",
    "nominal_loa",
    "nominal_beam",
    "nominal_draft",
    "nominal_displacement",
)


AUDIT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS design_class_merges (
    id          bigserial PRIMARY KEY,
    merged_at   timestamptz NOT NULL DEFAULT now(),
    winner_id   integer NOT NULL,
    loser_id    integer NOT NULL,
    canonical   text NOT NULL,
    loser_row   jsonb NOT NULL
)
"""


@dataclass
class Row:
    id: int
    name_canonical: str
    aliases: object | None
    builder: str | None
    designer: str | None
    nominal_loa: float | None
    nominal_lwl: float | None
    nominal_beam: float | None
    nominal_draft: float | None
    nominal_displacement: float | None
    year_first: int | None
    year_last: int | None

    @property
    def non_null_count(self) -> int:
        return sum(1 for c in NON_NULL_SCORE_COLS if getattr(self, c) is not None)

    def to_dict(self) -> dict:
        # JSON-safe for audit dump
        return {
            "id": self.id,
            "name_canonical": self.name_canonical,
            "aliases": self.aliases,
            "builder": self.builder,
            "designer": self.designer,
            "nominal_loa": float(self.nominal_loa) if self.nominal_loa is not None else None,
            "nominal_lwl": float(self.nominal_lwl) if self.nominal_lwl is not None else None,
            "nominal_beam": float(self.nominal_beam) if self.nominal_beam is not None else None,
            "nominal_draft": float(self.nominal_draft) if self.nominal_draft is not None else None,
            "nominal_displacement": (
                float(self.nominal_displacement)
                if self.nominal_displacement is not None
                else None
            ),
            "year_first": self.year_first,
            "year_last": self.year_last,
        }


def load_rows(engine: Engine) -> list[Row]:
    with engine.connect() as conn:
        rs = conn.execute(text("""
            SELECT id, name_canonical, aliases, builder, designer,
                   nominal_loa, nominal_lwl, nominal_beam, nominal_draft,
                   nominal_displacement, year_first, year_last
            FROM design_classes
            ORDER BY id
        """)).fetchall()
    return [Row(**dict(r._mapping)) for r in rs]


def boats_count_by_name(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        rs = conn.execute(text("""
            SELECT design_canonical, COUNT(*) AS n
            FROM boats
            WHERE design_canonical IS NOT NULL
            GROUP BY design_canonical
        """)).fetchall()
    return {r.design_canonical: r.n for r in rs}


def cluster_rows(rows: list[Row]) -> dict[str, list[Row]]:
    clusters: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        canon = normalize_design(r.name_canonical) or r.name_canonical
        clusters[canon].append(r)
    return clusters


def pick_winner(
    canonical: str, members: list[Row], boats_by_name: dict[str, int]
) -> Row:
    # Rule 1: exact match on canonical name
    exact = [m for m in members if m.name_canonical == canonical]
    candidates = exact if exact else list(members)

    if len(candidates) == 1:
        return candidates[0]

    # Rule 2: most non-NULL columns
    max_non_null = max(c.non_null_count for c in candidates)
    candidates = [c for c in candidates if c.non_null_count == max_non_null]
    if len(candidates) == 1:
        return candidates[0]

    # Rule 3: most boats pointing at it
    def boat_count(r: Row) -> int:
        return boats_by_name.get(r.name_canonical, 0)

    max_boats = max(boat_count(c) for c in candidates)
    candidates = [c for c in candidates if boat_count(c) == max_boats]
    if len(candidates) == 1:
        return candidates[0]

    # Rule 4: lowest id
    candidates.sort(key=lambda r: r.id)
    return candidates[0]


def build_plan(
    rows: list[Row], boats_by_name: dict[str, int]
) -> list[tuple[str, Row, list[Row], dict[str, object]]]:
    """Return list of (canonical, winner, losers, coalesce_updates)."""
    plan = []
    clusters = cluster_rows(rows)
    for canon, members in clusters.items():
        if len(members) < 2:
            continue
        winner = pick_winner(canon, members, boats_by_name)
        losers = [m for m in members if m.id != winner.id]
        # Build coalesce updates: only fill NULLs on winner from losers
        # Choose earliest loser id with a value for determinism.
        coalesce: dict[str, object] = {}
        for col in COALESCE_COLS:
            if getattr(winner, col) is not None:
                continue
            for loser in sorted(losers, key=lambda r: r.id):
                v = getattr(loser, col)
                if v is not None:
                    coalesce[col] = v
                    break
        plan.append((canon, winner, losers, coalesce))
    return plan


def ensure_audit_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(AUDIT_TABLE_DDL))


def execute_plan(
    engine: Engine,
    plan: list[tuple[str, Row, list[Row], dict[str, object]]],
    dry_run: bool,
) -> tuple[int, int, int]:
    """Execute the merge plan. Returns (clusters_merged, rows_deleted, boats_repointed)."""
    clusters_merged = 0
    rows_deleted = 0
    boats_repointed = 0

    for canon, winner, losers, coalesce in plan:
        if dry_run:
            clusters_merged += 1
            rows_deleted += len(losers)
            continue

        with engine.begin() as conn:
            # If winner's name doesn't equal canonical, also rename the winner
            # to the canonical form so subsequent runs see it as the exact match.
            if winner.name_canonical != canon:
                # Defensive: ensure no other row is already using `canon` as its
                # name (would violate the unique index). All such rows are in the
                # `losers` list by construction; they'll be deleted in this txn,
                # so do the rename AFTER deletes.
                pass

            # 1. Coalesce missing attributes on the winner
            if coalesce:
                set_clauses = ", ".join(f"{k} = :{k}" for k in coalesce.keys())
                conn.execute(
                    text(f"UPDATE design_classes SET {set_clauses} WHERE id = :id"),
                    {**coalesce, "id": winner.id},
                )

            for loser in losers:
                # 2. Audit
                conn.execute(
                    text("""
                        INSERT INTO design_class_merges
                          (winner_id, loser_id, canonical, loser_row)
                        VALUES (:winner_id, :loser_id, :canonical,
                                CAST(:loser_row AS jsonb))
                    """),
                    {
                        "winner_id": winner.id,
                        "loser_id": loser.id,
                        "canonical": canon,
                        "loser_row": json.dumps(loser.to_dict(), default=str),
                    },
                )

                # 3. Re-point boats by design_canonical text
                res = conn.execute(
                    text("""
                        UPDATE boats
                        SET design_canonical = :winner_name,
                            updated_at = now()
                        WHERE design_canonical = :loser_name
                    """),
                    {
                        "winner_name": winner.name_canonical,
                        "loser_name": loser.name_canonical,
                    },
                )
                boats_repointed += res.rowcount or 0

                # 4. Delete the loser
                conn.execute(
                    text("DELETE FROM design_classes WHERE id = :id"),
                    {"id": loser.id},
                )
                rows_deleted += 1

            # 5. If the winner's name isn't already the canonical, rename it.
            #    (Only safe now that the losers — which may have held the
            #    canonical name — are gone.)
            if winner.name_canonical != canon:
                # Repoint any boats currently using winner's OLD name to the
                # new canonical name as well.
                res = conn.execute(
                    text("""
                        UPDATE boats
                        SET design_canonical = :new_name,
                            updated_at = now()
                        WHERE design_canonical = :old_name
                    """),
                    {"new_name": canon, "old_name": winner.name_canonical},
                )
                boats_repointed += res.rowcount or 0
                conn.execute(
                    text("""
                        UPDATE design_classes
                        SET name_canonical = :new_name
                        WHERE id = :id
                    """),
                    {"new_name": canon, "id": winner.id},
                )

            clusters_merged += 1

    return clusters_merged, rows_deleted, boats_repointed


def fill_rates(engine: Engine) -> dict[str, tuple[int, int]]:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
              COUNT(*) AS total,
              COUNT(designer) AS designer,
              COUNT(builder) AS builder,
              COUNT(nominal_loa) AS nominal_loa,
              COUNT(nominal_displacement) AS nominal_displacement,
              COUNT(nominal_beam) AS nominal_beam,
              COUNT(nominal_draft) AS nominal_draft,
              COUNT(year_first) AS year_first
            FROM design_classes
        """)).first()
    total = row.total
    return {
        col: (getattr(row, col), total)
        for col in (
            "designer",
            "builder",
            "nominal_loa",
            "nominal_displacement",
            "nominal_beam",
            "nominal_draft",
            "year_first",
        )
    }


def print_report(
    before_total: int,
    before_fill: dict[str, tuple[int, int]],
    plan: list[tuple[str, Row, list[Row], dict[str, object]]],
    after_total: int,
    after_fill: dict[str, tuple[int, int]],
    clusters_merged: int,
    rows_deleted: int,
    boats_repointed: int,
    dry_run: bool,
) -> None:
    tag = "[DRY RUN] " if dry_run else ""
    print()
    print("=" * 72)
    print(f"{tag}design_classes merge report")
    print("=" * 72)
    print(f"  Clusters merged:    {clusters_merged}")
    print(f"  Rows deleted:       {rows_deleted}")
    print(f"  Boats re-pointed:   {boats_repointed}")
    print(f"  Total rows: {before_total} -> {after_total}  (delta {after_total - before_total})")
    print()
    print(f"  {'column':<24} {'before':>14} {'after':>14}")
    for col in (
        "designer",
        "builder",
        "nominal_loa",
        "nominal_displacement",
        "nominal_beam",
        "nominal_draft",
        "year_first",
    ):
        bn, bt = before_fill[col]
        an, at = after_fill[col]
        bp = (bn / bt * 100.0) if bt else 0.0
        ap = (an / at * 100.0) if at else 0.0
        print(
            f"  {col:<24} {bn:>5}/{bt:<5} {bp:5.1f}%  {an:>5}/{at:<5} {ap:5.1f}%"
        )

    print()
    print("Top 10 merge clusters by loser count:")
    top = sorted(plan, key=lambda x: -len(x[2]))[:10]
    for canon, winner, losers, _coal in top:
        print(
            f"  {canon!r:40s}  winner_id={winner.id:>6}  "
            f"loser_ids={[l.id for l in losers]}"
        )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    engine = get_engine()
    ensure_audit_table(engine)

    before_fill = fill_rates(engine)
    before_total = next(iter(before_fill.values()))[1]

    rows = load_rows(engine)
    boats_by_name = boats_count_by_name(engine)
    plan = build_plan(rows, boats_by_name)

    if not plan:
        print("Nothing to merge. design_classes is already deduped.")
        return 0

    print(
        f"Built plan: {len(plan)} clusters, "
        f"{sum(len(l) for _, _, l, _ in plan)} loser rows to delete."
    )

    clusters_merged, rows_deleted, boats_repointed = execute_plan(
        engine, plan, args.dry_run
    )

    after_fill = fill_rates(engine)
    after_total = next(iter(after_fill.values()))[1]

    print_report(
        before_total,
        before_fill,
        plan,
        after_total,
        after_fill,
        clusters_merged,
        rows_deleted,
        boats_repointed,
        args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
