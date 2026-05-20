"""Dry-run preview of the 37 Tier S boat-duplicate merges.

Tier S = same sail_number + same design + same year_built. The
canonical case is the IRC certificate scraper creating a "BOAT - SEC"
sibling row when it sees a re-issued certificate. All 37 current Tier S
clusters are Sunfast 3300s; every loser is a "- SEC" twin.

This script:
  1. Builds the 37 clusters from the boats table directly.
  2. For each cluster, runs the FULL merge inside a transaction and
     ROLLS BACK regardless of outcome. The DB is unchanged after this
     script runs.
  3. Reports per-cluster: winner, losers, FK rows that would be
     re-pointed, unique-constraint collisions that would be resolved.
  4. Summary totals at the end.

To actually apply: rerun with `--apply` (each cluster commits in its own
transaction). The merge logic is identical between dry-run and apply
modes — what you preview is what you'd get.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.db.connection import get_engine


# ── Cluster discovery ────────────────────────────────────────────────────

# Tier S: same sail+design+year_built. The cleanest signal — paired
# Sunfast 3300s where both rows are fully populated. 37 clusters merged
# 2026-05-20.
TIER_S_QUERY = """
SELECT sail_number AS k1, design AS k2, year_built AS k3,
       ARRAY_AGG(id ORDER BY id) AS ids
  FROM boats
 WHERE sail_number > ''
   AND design IS NOT NULL
   AND year_built IS NOT NULL
 GROUP BY sail_number, design, year_built
HAVING COUNT(*) > 1
 ORDER BY sail_number
"""

# Tier S-prime: the residual SEC twins where the scraper left fields
# unpopulated. Match an existing "BOAT - SEC" row to its non-SEC twin
# by (sail_number, name-without-suffix). Skip ambiguous cases where the
# SEC has more than one candidate primary. Stuart-approved cleanup of
# the 199 remaining SEC residuals after the import-path fix.
TIER_S_PRIME_QUERY = """
WITH sec AS (
  SELECT id, sail_number,
         regexp_replace(boat_name, '\\s*-\\s*SEC\\s*$', '', 'i') AS canon_name,
         boat_name AS sec_name
    FROM boats
   WHERE boat_name ILIKE '% - SEC' AND sail_number > ''
),
candidates AS (
  SELECT s.id AS sec_id, b.id AS primary_id, s.sail_number,
         s.canon_name, b.design AS primary_design
    FROM sec s
    JOIN boats b
      ON b.sail_number = s.sail_number
     AND b.id <> s.id
     AND b.boat_name NOT ILIKE '% - SEC'
     AND UPPER(TRIM(b.boat_name)) = UPPER(TRIM(s.canon_name))
),
counted AS (
  SELECT sec_id, COUNT(*) AS n FROM candidates GROUP BY sec_id
)
SELECT c.sail_number AS k1,
       c.canon_name  AS k2,
       NULL::int     AS k3,
       ARRAY[c.primary_id, c.sec_id] AS ids
  FROM candidates c
  JOIN counted t ON t.sec_id = c.sec_id
 WHERE t.n = 1               -- skip ambiguous (multi-primary) SEC twins
 ORDER BY c.sail_number
"""


# ── FK topology for re-pointing ──────────────────────────────────────────

# These have no boat_id in any UNIQUE constraint — straight UPDATE works.
SIMPLE_FK_TABLES = [
    "boat_identities",
    "boat_corrections",
    "insight_cache",
    "orders",
    "orc_certificates",
]

# These have UNIQUE constraints involving boat_id — need collision logic.
# - irc_certificates(boat_id, cert_number) implicit via boats UNIQUE(sail, cert)
# - tcc_snapshots UNIQUE(boat_id, snapshot_date)
# - race_results UNIQUE(boat_id, event_name, race_name, event_date)


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class MergePlan:
    cluster_key: str
    sail_number: str
    k2: str                              # design (Tier S) or canon_name (S-prime)
    k3: int | None                       # year_built (Tier S) or None
    winner_id: int = 0
    winner_name: str = ""
    loser_ids: list[int] = field(default_factory=list)
    loser_names: list[str] = field(default_factory=list)
    rows_repointed: dict[str, int] = field(default_factory=dict)
    cert_collisions: int = 0
    tcc_collisions: int = 0
    race_result_collisions: int = 0
    error: str | None = None


# ── Winner selection ─────────────────────────────────────────────────────


def pick_winner(conn, boat_ids: list[int]) -> int:
    """Pick the canonical winner id.

    Priority:
      1. Most race_results (the boat with the actual racing history wins)
      2. Most recent IRC cert (latest issue_date)
      3. Earliest created_at (the original row, not the duplicate)
    """
    rows = conn.execute(
        text("""
            SELECT b.id,
                   b.created_at,
                   b.boat_name,
                   (SELECT COUNT(*) FROM race_results r WHERE r.boat_id = b.id) AS rr_count,
                   (SELECT MAX(c.issue_date) FROM irc_certificates c WHERE c.boat_id = b.id) AS latest_cert
              FROM boats b
             WHERE b.id = ANY(:ids)
        """),
        {"ids": boat_ids},
    ).mappings().all()

    def sort_key(r):
        latest_cert = r["latest_cert"]
        return (
            -r["rr_count"],                                          # most race results first
            0 if latest_cert is not None else 1,                     # has-cert
            -(latest_cert.toordinal()) if latest_cert else 0,        # newer cert first
            r["created_at"],                                         # original row first
        )

    return sorted(rows, key=sort_key)[0]["id"]


# ── Per-table re-point + collision handling ──────────────────────────────


def repoint_simple(conn, table: str, winner_id: int, loser_id: int) -> int:
    res = conn.execute(
        text(f"UPDATE {table} SET boat_id = :w WHERE boat_id = :l"),
        {"w": winner_id, "l": loser_id},
    )
    return res.rowcount or 0


def resolve_irc_cert_collisions(conn, winner_id: int, loser_id: int) -> tuple[int, int]:
    """Re-point loser's irc_certificates rows. The (sail_number, cert_number)
    UNIQUE lives on `boats` not `irc_certificates`, so per-boat cert rows
    rarely collide — but defend on (boat_id, cert_number) just in case.
    """
    winner_certs = {
        r["cert_number"]: dict(r)
        for r in conn.execute(
            text("SELECT id, cert_number, issue_date FROM irc_certificates "
                 "WHERE boat_id = :w AND cert_number IS NOT NULL"),
            {"w": winner_id},
        ).mappings()
    }

    loser_certs = conn.execute(
        text("SELECT id, cert_number, issue_date FROM irc_certificates "
             "WHERE boat_id = :l"),
        {"l": loser_id},
    ).mappings().all()

    repointed = 0
    collisions = 0
    for lc in loser_certs:
        wc = winner_certs.get(lc["cert_number"]) if lc["cert_number"] else None
        if wc is None:
            conn.execute(
                text("UPDATE irc_certificates SET boat_id = :w WHERE id = :id"),
                {"w": winner_id, "id": lc["id"]},
            )
            repointed += 1
        else:
            # Keep the newer one. NULLs treated as oldest.
            wd = wc["issue_date"]
            ld = lc["issue_date"]
            winner_is_newer = (wd or 0) >= (ld or 0) if (wd or ld) else True
            if winner_is_newer:
                conn.execute(text("DELETE FROM irc_certificates WHERE id = :id"),
                             {"id": lc["id"]})
            else:
                conn.execute(text("DELETE FROM irc_certificates WHERE id = :id"),
                             {"id": wc["id"]})
                conn.execute(text("UPDATE irc_certificates SET boat_id = :w WHERE id = :id"),
                             {"w": winner_id, "id": lc["id"]})
                repointed += 1
            collisions += 1
    return repointed, collisions


def resolve_tcc_collisions(conn, winner_id: int, loser_id: int) -> tuple[int, int]:
    """UNIQUE(boat_id, snapshot_date) — keep most-populated row on collision."""
    winner_snaps = {
        r["snapshot_date"]: dict(r)
        for r in conn.execute(
            text("SELECT id, snapshot_date, cert_year FROM tcc_snapshots "
                 "WHERE boat_id = :w"),
            {"w": winner_id},
        ).mappings()
    }
    loser_snaps = conn.execute(
        text("SELECT id, snapshot_date, cert_year FROM tcc_snapshots "
             "WHERE boat_id = :l"),
        {"l": loser_id},
    ).mappings().all()

    repointed = 0
    collisions = 0
    for ls in loser_snaps:
        ws = winner_snaps.get(ls["snapshot_date"])
        if ws is None:
            conn.execute(
                text("UPDATE tcc_snapshots SET boat_id = :w WHERE id = :id"),
                {"w": winner_id, "id": ls["id"]},
            )
            repointed += 1
        else:
            wy = ws["cert_year"] or 0
            ly = ls["cert_year"] or 0
            if wy >= ly:
                conn.execute(text("DELETE FROM tcc_snapshots WHERE id = :id"),
                             {"id": ls["id"]})
            else:
                conn.execute(text("DELETE FROM tcc_snapshots WHERE id = :id"),
                             {"id": ws["id"]})
                conn.execute(text("UPDATE tcc_snapshots SET boat_id = :w WHERE id = :id"),
                             {"w": winner_id, "id": ls["id"]})
                repointed += 1
            collisions += 1
    return repointed, collisions


def resolve_race_result_collisions(conn, winner_id: int, loser_id: int) -> tuple[int, int]:
    """UNIQUE(boat_id, event_name, race_name, event_date) — drop loser
    on collision (winner kept; the loser is a true dupe row)."""
    colliding = conn.execute(
        text("""
            SELECT r.id FROM race_results r
             WHERE r.boat_id = :l
               AND EXISTS (
                   SELECT 1 FROM race_results r2
                    WHERE r2.boat_id = :w
                      AND COALESCE(r2.event_name,'') = COALESCE(r.event_name,'')
                      AND COALESCE(r2.race_name,'')  = COALESCE(r.race_name,'')
                      AND r2.event_date IS NOT DISTINCT FROM r.event_date
               )
        """),
        {"w": winner_id, "l": loser_id},
    ).fetchall()

    collisions = len(colliding)
    for r in colliding:
        conn.execute(text("DELETE FROM race_results WHERE id = :id"), {"id": r[0]})

    res = conn.execute(
        text("UPDATE race_results SET boat_id = :w WHERE boat_id = :l"),
        {"w": winner_id, "l": loser_id},
    )
    return res.rowcount or 0, collisions


# ── Cluster merge driver ─────────────────────────────────────────────────


def plan_cluster(engine: Engine, sail: str, k2: str, k3: int | None,
                 ids: list[int], *, apply: bool) -> MergePlan:
    """Run a merge for one cluster. In dry-run mode (apply=False), the
    transaction rolls back at the end and the database is unchanged.

    `k2`/`k3` carry the secondary cluster keys: design + year_built for
    Tier S, canon_name + None for Tier S-prime. Used only for logging.
    """
    plan = MergePlan(
        cluster_key=f"{sail}|{k2}|{k3 if k3 is not None else '-'}",
        sail_number=sail, k2=k2, k3=k3,
    )

    class _DryRunOnly(Exception):
        pass

    try:
        with engine.begin() as conn:
            # Fetch boat rows for context
            boat_rows = conn.execute(
                text("SELECT id, boat_name FROM boats WHERE id = ANY(:ids) ORDER BY id"),
                {"ids": ids},
            ).fetchall()
            name_by_id = {r.id: r.boat_name for r in boat_rows}

            winner_id = pick_winner(conn, ids)
            plan.winner_id = winner_id
            plan.winner_name = name_by_id.get(winner_id, "")
            plan.loser_ids = [b for b in ids if b != winner_id]
            plan.loser_names = [name_by_id.get(b, "") for b in plan.loser_ids]

            for loser_id in plan.loser_ids:
                irc_rp, irc_col = resolve_irc_cert_collisions(conn, winner_id, loser_id)
                tcc_rp, tcc_col = resolve_tcc_collisions(conn, winner_id, loser_id)
                rr_rp, rr_col = resolve_race_result_collisions(conn, winner_id, loser_id)

                plan.rows_repointed.setdefault("irc_certificates", 0)
                plan.rows_repointed["irc_certificates"] += irc_rp
                plan.rows_repointed.setdefault("tcc_snapshots", 0)
                plan.rows_repointed["tcc_snapshots"] += tcc_rp
                plan.rows_repointed.setdefault("race_results", 0)
                plan.rows_repointed["race_results"] += rr_rp
                plan.cert_collisions += irc_col
                plan.tcc_collisions += tcc_col
                plan.race_result_collisions += rr_col

                for tbl in SIMPLE_FK_TABLES:
                    n = repoint_simple(conn, tbl, winner_id, loser_id)
                    plan.rows_repointed.setdefault(tbl, 0)
                    plan.rows_repointed[tbl] += n

                # Confirm no residual references before delete
                for tbl in SIMPLE_FK_TABLES + ["irc_certificates", "tcc_snapshots", "race_results"]:
                    rem = conn.execute(
                        text(f"SELECT COUNT(*) FROM {tbl} WHERE boat_id = :l"),
                        {"l": loser_id},
                    ).scalar()
                    if rem:
                        raise RuntimeError(
                            f"residual {rem} refs in {tbl} for loser={loser_id}"
                        )

                if apply:
                    # Persist audit row before delete
                    conn.execute(
                        text("""
                            INSERT INTO boat_merges (winner_id, loser_id, cluster_key, loser_snapshot)
                            VALUES (:w, :l, :ck, CAST(:snap AS jsonb))
                        """),
                        {
                            "w": winner_id, "l": loser_id, "ck": plan.cluster_key,
                            "snap": json.dumps({"boat_name": name_by_id.get(loser_id)}),
                        },
                    )

                conn.execute(text("DELETE FROM boats WHERE id = :id"), {"id": loser_id})

            if not apply:
                raise _DryRunOnly()
    except _DryRunOnly:
        pass  # rollback already triggered; plan object survives
    except Exception as e:
        plan.error = f"{type(e).__name__}: {e}"
    return plan


def ensure_boat_merges_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS boat_merges (
              id             bigserial PRIMARY KEY,
              merged_at      timestamptz NOT NULL DEFAULT now(),
              winner_id      integer NOT NULL,
              loser_id       integer NOT NULL,
              cluster_key    text NOT NULL,
              loser_snapshot jsonb NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_boat_merges_loser ON boat_merges(loser_id);
            CREATE INDEX IF NOT EXISTS idx_boat_merges_winner ON boat_merges(winner_id);
        """))


# ── CLI ──────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["s", "s-prime"], default="s",
                    help="Which Tier to run. 's' = same sail+design+year (37 merged 2026-05-20). "
                         "'s-prime' = residual SEC twins matched by (sail, canon_name).")
    ap.add_argument("--apply", action="store_true",
                    help="Actually commit the merges. Default is dry-run (rollback).")
    args = ap.parse_args()

    engine = get_engine()
    if args.apply:
        ensure_boat_merges_table(engine)

    query = TIER_S_QUERY if args.tier == "s" else TIER_S_PRIME_QUERY
    label = f"TIER {args.tier.upper()}"
    with engine.connect() as conn:
        clusters = conn.execute(text(query)).fetchall()

    mode = "APPLY (will commit)" if args.apply else "DRY-RUN (will rollback)"
    print(f"\n{'═' * 100}")
    print(f"{label} BOAT MERGE — {mode}")
    print(f"{'═' * 100}")
    print(f"{len(clusters)} clusters found.\n")

    plans: list[MergePlan] = []
    for c in clusters:
        plan = plan_cluster(
            engine, c.k1, str(c.k2), c.k3, list(c.ids),
            apply=args.apply,
        )
        plans.append(plan)
        loser_label = ", ".join(f"{n} (#{i})" for i, n in zip(plan.loser_ids, plan.loser_names))
        winner_label = f"{plan.winner_name} (#{plan.winner_id})"
        prefix = "FAIL" if plan.error else ("APPL" if args.apply else "PREV")
        suffix = f" y{plan.k3}" if plan.k3 is not None else ""
        print(f"[{prefix}] {plan.sail_number:<10}{suffix}  "
              f"winner: {winner_label}  ←  drops: {loser_label}")
        if plan.rows_repointed:
            details = "  ".join(
                f"{k}={v}" for k, v in plan.rows_repointed.items() if v
            )
            if details:
                print(f"        repoint: {details}")
        if any([plan.cert_collisions, plan.tcc_collisions, plan.race_result_collisions]):
            print(f"        collisions: irc_cert={plan.cert_collisions} "
                  f"tcc={plan.tcc_collisions} race_results={plan.race_result_collisions}")
        if plan.error:
            print(f"        ERROR: {plan.error}")

    print(f"\n{'═' * 100}")
    print("SUMMARY")
    print(f"{'═' * 100}")
    n_ok = sum(1 for p in plans if not p.error)
    n_fail = sum(1 for p in plans if p.error)
    total_losers = sum(len(p.loser_ids) for p in plans if not p.error)

    rollup: dict[str, int] = defaultdict(int)
    for p in plans:
        if p.error:
            continue
        for k, v in p.rows_repointed.items():
            rollup[k] += v

    print(f"  clusters_planned    = {len(plans)}")
    print(f"  clusters_ok         = {n_ok}")
    print(f"  clusters_failed     = {n_fail}")
    print(f"  boats_to_remove     = {total_losers}")
    print()
    print("  FK rows that would be re-pointed across all clusters:")
    for k, v in sorted(rollup.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<22} {v:>6}")
    total_cert_col = sum(p.cert_collisions for p in plans)
    total_tcc_col = sum(p.tcc_collisions for p in plans)
    total_rr_col = sum(p.race_result_collisions for p in plans)
    print()
    print(f"  irc_cert collisions      = {total_cert_col}")
    print(f"  tcc_snapshot collisions  = {total_tcc_col}")
    print(f"  race_result collisions   = {total_rr_col}")

    if not args.apply:
        print(f"\n  DRY-RUN — all transactions rolled back. To apply: rerun with --apply.")
    else:
        print(f"\n  APPLIED — see boat_merges table for the audit trail.")

    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
