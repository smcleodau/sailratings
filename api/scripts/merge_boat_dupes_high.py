"""Merge high-confidence duplicate boat clusters from /tmp/boat_dupes_high.csv.

For each cluster (boats sharing both cert_number and boat_name), pick a
canonical winner and re-point all FK references from losers to the winner,
then delete the loser rows.

Winner-picking rule (in order):
  1. Row whose id matches irc_certificates.boat_id AND has the most-recent
     irc_certificates.issue_date.
  2. Tie-break: highest race_results count.
  3. Tie-break: earliest boats.created_at.

Safeguards:
  * One transaction per cluster — failure on N+1 leaves N committed.
  * Every loser is snapshotted into boat_merges (jsonb) BEFORE delete.
  * Unique-constraint collisions on irc_certificates.cert_number and
    tcc_snapshots(boat_id, snapshot_date) are detected and resolved
    in-transaction by keeping the most-recent row and deleting the sibling
    (sibling row is captured in boat_merges.loser_snapshot.extra_certs[]
    or .extra_tcc_snapshots[]).
  * Idempotent: re-running is a no-op when no loser ids remain.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from irc_data.db.connection import get_engine

CSV_PATH = Path("/tmp/boat_dupes_high.csv")

# FK tables where re-pointing boat_id is safe (no boat_id in any UNIQUE key
# beyond a pure id PK and no constraint that involves boat_id).
SIMPLE_FK_TABLES = [
    "boat_identities",
    "insight_cache",
    "orders",
    "boat_corrections",
    "orc_certificates",
]


@dataclass
class MergeReport:
    cluster_key: str
    winner_id: int
    loser_ids: list[int] = field(default_factory=list)
    rows_repointed: dict[str, int] = field(default_factory=dict)
    cert_collisions_resolved: int = 0
    tcc_collisions_resolved: int = 0
    note: str = ""


def ensure_boat_merges_table(conn: Connection) -> None:
    conn.execute(
        text(
            """
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
            """
        )
    )


def load_clusters(engine) -> dict[str, list[int]]:
    clusters: dict[str, list[int]] = {}
    with engine.begin() as conn:
        res = conn.execute(text("""
            SELECT c.cert_number || '|' || b.boat_name AS cluster_id, array_agg(b.id) as boat_ids
            FROM irc_certificates c
            JOIN boats b ON b.id = c.boat_id
            WHERE c.cert_number IS NOT NULL AND c.cert_number != ''
            AND b.boat_name IS NOT NULL AND b.boat_name != ''
            GROUP BY c.cert_number, b.boat_name
            HAVING COUNT(*) > 1
        """))
        for row in res:
            clusters[row.cluster_id] = list(row.boat_ids)
    return clusters


def fetch_boat_row(conn: Connection, boat_id: int) -> dict[str, Any] | None:
    res = conn.execute(
        text("SELECT row_to_json(b)::jsonb AS j FROM boats b WHERE id = :id"),
        {"id": boat_id},
    ).fetchone()
    if res is None:
        return None
    return res.j


def pick_winner(conn: Connection, boat_ids: list[int]) -> int:
    """Pick the canonical winner id for a cluster of boats.

    Order:
      1. Has a certificate AND latest issue_date wins.
      2. Highest race_results count.
      3. Earliest created_at.
    """
    rows = conn.execute(
        text(
            """
            SELECT b.id,
                   b.created_at,
                   (SELECT MAX(c.issue_date)
                      FROM irc_certificates c WHERE c.boat_id = b.id) AS latest_cert,
                   (SELECT COUNT(*) FROM race_results r WHERE r.boat_id = b.id) AS rr_count
              FROM boats b
             WHERE b.id = ANY(:ids)
            """
        ),
        {"ids": boat_ids},
    ).mappings().all()

    def sort_key(r):
        # Most recent cert first (None sorts last); highest rr_count; earliest created_at.
        latest_cert = r["latest_cert"]
        return (
            0 if latest_cert is not None else 1,         # has-cert flag (lower better)
            -(latest_cert.toordinal()) if latest_cert else 0,  # newer cert -> more negative
            -r["rr_count"],                              # higher race count -> more negative
            r["created_at"],                             # earliest created_at first
        )

    rows_sorted = sorted(rows, key=sort_key)
    return rows_sorted[0]["id"]


def repoint_simple(
    conn: Connection, table: str, winner_id: int, loser_id: int
) -> int:
    res = conn.execute(
        text(f"UPDATE {table} SET boat_id = :w WHERE boat_id = :l"),
        {"w": winner_id, "l": loser_id},
    )
    return res.rowcount or 0


def resolve_cert_collisions(
    conn: Connection, winner_id: int, loser_id: int, extras_sink: list[dict]
) -> tuple[int, int]:
    """Handle irc_certificates.cert_number UNIQUE collisions.

    If winner has cert X and loser has cert X (same cert_number), keep the
    one with the most-recent issue_date and DELETE the other (captured in
    extras_sink for the loser_snapshot).

    Returns (rows_repointed_to_winner, collisions_resolved).
    """
    # Build (cert_number -> winner cert row) once.
    winner_certs = {
        r["cert_number"]: dict(r)
        for r in conn.execute(
            text(
                "SELECT id, cert_number, issue_date, source FROM irc_certificates "
                "WHERE boat_id = :w AND cert_number IS NOT NULL"
            ),
            {"w": winner_id},
        ).mappings()
    }

    loser_certs = conn.execute(
        text(
            "SELECT row_to_json(c)::jsonb AS j, id, cert_number, issue_date "
            "FROM irc_certificates c WHERE c.boat_id = :l"
        ),
        {"l": loser_id},
    ).mappings().all()

    collisions = 0
    repointed = 0
    for lc in loser_certs:
        cn = lc["cert_number"]
        wc = winner_certs.get(cn) if cn is not None else None
        if wc is None:
            # No collision — just re-point.
            conn.execute(
                text("UPDATE irc_certificates SET boat_id = :w WHERE id = :id"),
                {"w": winner_id, "id": lc["id"]},
            )
            repointed += 1
            continue

        # Collision on cert_number — keep newer issue_date.
        winner_date = wc["issue_date"]
        loser_date = lc["issue_date"]
        # Treat NULL as oldest possible.
        winner_is_newer = (
            (winner_date or 0) >= (loser_date or 0)
            if (winner_date is not None or loser_date is not None)
            else True
        )

        if winner_is_newer:
            # Drop the loser's cert row; capture in extras.
            extras_sink.append({"kind": "certificate_collision_dropped", "row": lc["j"]})
            conn.execute(
                text("DELETE FROM irc_certificates WHERE id = :id"), {"id": lc["id"]}
            )
        else:
            # Loser's cert is newer — capture winner's, delete winner's, re-point loser's.
            winner_full = conn.execute(
                text("SELECT row_to_json(c)::jsonb AS j FROM irc_certificates c WHERE c.id = :id"),
                {"id": wc["id"]},
            ).scalar()
            extras_sink.append(
                {"kind": "certificate_collision_replaced_winner", "row": winner_full}
            )
            conn.execute(
                text("DELETE FROM irc_certificates WHERE id = :id"), {"id": wc["id"]}
            )
            conn.execute(
                text("UPDATE irc_certificates SET boat_id = :w WHERE id = :id"),
                {"w": winner_id, "id": lc["id"]},
            )
            repointed += 1
        collisions += 1

    return repointed, collisions


def resolve_tcc_collisions(
    conn: Connection, winner_id: int, loser_id: int, extras_sink: list[dict]
) -> tuple[int, int]:
    """Handle tcc_snapshots(boat_id, snapshot_date) UNIQUE collisions.

    Keep the row with the newest cert_year (then most fields populated);
    delete the duplicate after capturing it into extras_sink.
    """
    winner_snaps = {
        r["snapshot_date"]: dict(r)
        for r in conn.execute(
            text(
                "SELECT id, snapshot_date, cert_year FROM tcc_snapshots WHERE boat_id = :w"
            ),
            {"w": winner_id},
        ).mappings()
    }

    loser_snaps = conn.execute(
        text(
            "SELECT row_to_json(t)::jsonb AS j, id, snapshot_date, cert_year "
            "FROM tcc_snapshots t WHERE t.boat_id = :l"
        ),
        {"l": loser_id},
    ).mappings().all()

    collisions = 0
    repointed = 0
    for ls in loser_snaps:
        sd = ls["snapshot_date"]
        ws = winner_snaps.get(sd)
        if ws is None:
            conn.execute(
                text("UPDATE tcc_snapshots SET boat_id = :w WHERE id = :id"),
                {"w": winner_id, "id": ls["id"]},
            )
            repointed += 1
            continue

        winner_year = ws["cert_year"] or 0
        loser_year = ls["cert_year"] or 0
        winner_is_newer = winner_year >= loser_year

        if winner_is_newer:
            extras_sink.append({"kind": "tcc_snapshot_collision_dropped", "row": ls["j"]})
            conn.execute(
                text("DELETE FROM tcc_snapshots WHERE id = :id"), {"id": ls["id"]}
            )
        else:
            winner_full = conn.execute(
                text(
                    "SELECT row_to_json(t)::jsonb AS j FROM tcc_snapshots t WHERE t.id = :id"
                ),
                {"id": ws["id"]},
            ).scalar()
            extras_sink.append(
                {"kind": "tcc_snapshot_collision_replaced_winner", "row": winner_full}
            )
            conn.execute(
                text("DELETE FROM tcc_snapshots WHERE id = :id"), {"id": ws["id"]}
            )
            conn.execute(
                text("UPDATE tcc_snapshots SET boat_id = :w WHERE id = :id"),
                {"w": winner_id, "id": ls["id"]},
            )
            repointed += 1
        collisions += 1

    return repointed, collisions


def resolve_race_results_collisions(
    conn: Connection, winner_id: int, loser_id: int, extras_sink: list[dict]
) -> tuple[int, int]:
    """Handle race_results(boat_id, event_name, race_name, event_date) UNIQUE.

    These are rarely populated for true dupes, but we defend anyway.
    """
    # Find loser rows that would collide on the unique key.
    colliding = conn.execute(
        text(
            """
            SELECT row_to_json(r)::jsonb AS j, r.id
              FROM race_results r
             WHERE r.boat_id = :l
               AND EXISTS (
                   SELECT 1 FROM race_results r2
                    WHERE r2.boat_id = :w
                      AND COALESCE(r2.event_name,'') = COALESCE(r.event_name,'')
                      AND COALESCE(r2.race_name,'')  = COALESCE(r.race_name,'')
                      AND r2.event_date IS NOT DISTINCT FROM r.event_date
               )
            """
        ),
        {"w": winner_id, "l": loser_id},
    ).mappings().all()

    collisions = 0
    for row in colliding:
        extras_sink.append({"kind": "race_result_collision_dropped", "row": row["j"]})
        conn.execute(
            text("DELETE FROM race_results WHERE id = :id"), {"id": row["id"]}
        )
        collisions += 1

    # Now safe to re-point survivors.
    res = conn.execute(
        text("UPDATE race_results SET boat_id = :w WHERE boat_id = :l"),
        {"w": winner_id, "l": loser_id},
    )
    return res.rowcount or 0, collisions


def merge_cluster(
    engine, cluster_key: str, boat_ids: list[int]
) -> MergeReport:
    rep = MergeReport(cluster_key=cluster_key, winner_id=0)

    with engine.begin() as conn:
        # Idempotency: drop ids that no longer exist (already merged).
        existing = [
            r[0]
            for r in conn.execute(
                text("SELECT id FROM boats WHERE id = ANY(:ids) ORDER BY id"),
                {"ids": boat_ids},
            ).fetchall()
        ]
        if len(existing) < 2:
            rep.winner_id = existing[0] if existing else 0
            rep.note = "already-merged"
            return rep

        winner_id = pick_winner(conn, existing)
        loser_ids = [b for b in existing if b != winner_id]
        rep.winner_id = winner_id
        rep.loser_ids = loser_ids

        for loser_id in loser_ids:
            extras: list[dict] = []
            loser_full = fetch_boat_row(conn, loser_id)
            if loser_full is None:
                rep.note = f"loser {loser_id} vanished mid-merge"
                raise RuntimeError(rep.note)

            # Unique-constraint-aware re-points.
            cert_rp, cert_col = resolve_cert_collisions(
                conn, winner_id, loser_id, extras
            )
            tcc_rp, tcc_col = resolve_tcc_collisions(
                conn, winner_id, loser_id, extras
            )
            rr_rp, rr_col = resolve_race_results_collisions(
                conn, winner_id, loser_id, extras
            )

            rep.rows_repointed.setdefault("irc_certificates", 0)
            rep.rows_repointed["irc_certificates"] += cert_rp
            rep.rows_repointed.setdefault("tcc_snapshots", 0)
            rep.rows_repointed["tcc_snapshots"] += tcc_rp
            rep.rows_repointed.setdefault("race_results", 0)
            rep.rows_repointed["race_results"] += rr_rp
            rep.cert_collisions_resolved += cert_col
            rep.tcc_collisions_resolved += tcc_col
            # race_results collisions captured in extras_sink too.

            # Simple FK re-points.
            for tbl in SIMPLE_FK_TABLES:
                n = repoint_simple(conn, tbl, winner_id, loser_id)
                rep.rows_repointed.setdefault(tbl, 0)
                rep.rows_repointed[tbl] += n

            # Persist audit row BEFORE delete.
            snapshot = {"boat": loser_full, "extras": extras}
            conn.execute(
                text(
                    """
                    INSERT INTO boat_merges (winner_id, loser_id, cluster_key, loser_snapshot)
                    VALUES (:w, :l, :ck, CAST(:snap AS jsonb))
                    """
                ),
                {
                    "w": winner_id,
                    "l": loser_id,
                    "ck": cluster_key,
                    "snap": json.dumps(snapshot, default=str),
                },
            )

            # Confirm no FK references remain.
            for tbl in [
                "irc_certificates",
                "tcc_snapshots",
                "race_results",
                "orc_certificates",
                "boat_identities",
                "insight_cache",
                "orders",
                "boat_corrections",
            ]:
                remaining = conn.execute(
                    text(f"SELECT COUNT(*) FROM {tbl} WHERE boat_id = :l"),
                    {"l": loser_id},
                ).scalar()
                if remaining:
                    raise RuntimeError(
                        f"residual references in {tbl} for loser {loser_id}: {remaining}"
                    )

            conn.execute(text("DELETE FROM boats WHERE id = :id"), {"id": loser_id})

    return rep


def main() -> int:
    engine = get_engine()
    with engine.begin() as conn:
        ensure_boat_merges_table(conn)

    clusters = load_clusters(engine)
    print(f"Loaded {len(clusters)} clusters from DB")

    reports: list[MergeReport] = []
    errors: list[tuple[str, str]] = []

    for cluster_key, boat_ids in clusters.items():
        try:
            rep = merge_cluster(engine, cluster_key, boat_ids)
            reports.append(rep)
            if rep.note == "already-merged":
                print(f"[skip] {cluster_key}: already merged (winner={rep.winner_id})")
            else:
                print(
                    f"[ok]   {cluster_key}: winner={rep.winner_id} "
                    f"losers={rep.loser_ids} "
                    f"repointed={dict(rep.rows_repointed)} "
                    f"cert_col={rep.cert_collisions_resolved} "
                    f"tcc_col={rep.tcc_collisions_resolved}"
                )
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            errors.append((cluster_key, msg))
            print(f"[fail] {cluster_key}: {msg}", file=sys.stderr)

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(
        f"{'cluster_key':<32} {'winner':>8} {'losers':<14} {'cert':>5} {'tcc':>5} "
        f"{'rr':>5} {'orc':>5} {'id':>5} {'cache':>6} {'ord':>4} {'corr':>4} "
        f"{'cCol':>5} {'tCol':>5}"
    )
    print("-" * 110)
    total_losers = 0
    total_cert_collisions = 0
    total_tcc_collisions = 0
    for r in reports:
        rp = r.rows_repointed
        losers_str = ",".join(str(x) for x in r.loser_ids) or "-"
        total_losers += len(r.loser_ids)
        total_cert_collisions += r.cert_collisions_resolved
        total_tcc_collisions += r.tcc_collisions_resolved
        print(
            f"{r.cluster_key:<32} {r.winner_id:>8} {losers_str:<14} "
            f"{rp.get('certificates', 0):>5} {rp.get('tcc_snapshots', 0):>5} "
            f"{rp.get('race_results', 0):>5} {rp.get('orc_certificates', 0):>5} "
            f"{rp.get('boat_identities', 0):>5} {rp.get('insight_cache', 0):>6} "
            f"{rp.get('orders', 0):>4} {rp.get('boat_corrections', 0):>4} "
            f"{r.cert_collisions_resolved:>5} {r.tcc_collisions_resolved:>5}"
            + (f"  ({r.note})" if r.note else "")
        )

    print("-" * 110)
    print(
        f"TOTALS: clusters_attempted={len(clusters)} "
        f"clusters_succeeded={len(reports)} "
        f"boats_removed={total_losers} "
        f"cert_collisions={total_cert_collisions} "
        f"tcc_collisions={total_tcc_collisions} "
        f"errors={len(errors)}"
    )

    if errors:
        print("\nERRORS:")
        for ck, msg in errors:
            print(f"  {ck}: {msg}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
