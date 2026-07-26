"""Merge SAFE SUBSET of medium-confidence duplicate boat clusters.

Medium-confidence clusters share boat_name + country only (no cert match), so
we apply a strict safety gate before auto-merging:

  1. At most one row in the cluster bears STRONG signal (certificates /
     race_results / orders / insight_cache). tcc_snapshots, orc_certificates
     and boat_identities are EXCLUDED — the TCC scraper writes a snapshot
     for every boat it sees on each pass, so the presence of 2+ TCC-bearing
     rows is the duplicate pattern itself, not a discriminator. Likewise
     boat_identities are auto-generated per scraper sighting.
  2. Design conflict — for any two rows in the cluster, both having a
     non-NULL design_canonical: they must normalize to the same value via
     irc_data.matching.designs.normalize_design(). Mismatch → flag.
  3. Year conflict — for any two rows in the cluster, both having a
     non-NULL year_built: they must differ by ≤ 2 years.
  4. Cluster size <= 6 (after accounting for any boats already removed by
     the high-conf pass).

Clusters that fail any rule are written to /tmp/boat_dupes_medium_review.csv
with a reason and are NOT touched in the DB.

Re-uses the audit table (boat_merges), winner-picking rule (cert / results /
orders+insight_cache / created_at), and FK-aware re-point helpers from
merge_boat_dupes_high.py.

Idempotent: re-runs are no-ops when no loser ids remain.
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
from irc_data.matching.designs import normalize_design

CSV_IN = Path("/tmp/boat_dupes_medium.csv")
CSV_REVIEW_OUT = Path("/tmp/boat_dupes_medium_review.csv")

MAX_CLUSTER_SIZE = 6
YEAR_BUILT_TOLERANCE = 2

# Tables whose presence on a row counts as STRONG signal for safety-gate
# rule 1. tcc_snapshots / orc_certificates / boat_identities are EXCLUDED
# because they're auto-generated per scraper pass and so cannot
# discriminate duplicates — multiple rows bearing only those tables IS the
# duplicate pattern, not evidence of distinct boats.
SIGNAL_TABLES = (
    "irc_certificates",
    "race_results",
    "orders",
    "insight_cache",
)
# Tables we still need to count for the winner tie-breaker but which
# don't gate the merge.
COUNT_ONLY_TABLES = (
    "tcc_snapshots",
    "orc_certificates",
    "boat_identities",
    "boat_corrections",
)

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


# ---------------------------------------------------------------------------
# Loaders / db helpers
# ---------------------------------------------------------------------------

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
            SELECT boat_name || '|' || country AS cluster_id, array_agg(id) as boat_ids
            FROM boats
            WHERE boat_name IS NOT NULL AND boat_name != ''
            AND country IS NOT NULL AND country != ''
            GROUP BY boat_name, country
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


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------

def _norm_design_canon(value: str | None) -> str | None:
    """Normalise a design_canonical value for cluster-equality comparison.

    Uses the project-wide normalize_design() so e.g. "Sunfast 3300" and
    "Sun Fast 3300" both collapse to the same canonical name. Returns
    None for blank input.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    canon = normalize_design(s)
    if canon is None:
        return None
    # Final fold to avoid pure-case mismatches that survive normalize_design.
    return " ".join(canon.strip().split()).lower() or None


@dataclass
class ClusterFacts:
    boat_ids: list[int]
    rows: list[dict[str, Any]]
    signal_counts: dict[int, dict[str, int]]  # boat_id -> table -> count

    def signal_bearing_ids(self) -> list[int]:
        """Boat IDs with one or more rows in a STRONG signal table."""
        out = []
        for bid in self.boat_ids:
            sc = self.signal_counts.get(bid, {})
            if any(sc.get(t, 0) > 0 for t in SIGNAL_TABLES):
                out.append(bid)
        return out


def fetch_cluster_facts(conn: Connection, boat_ids: list[int]) -> ClusterFacts:
    # Rows
    rows = conn.execute(
        text(
            "SELECT id, boat_name, country, design, design_canonical, year_built "
            "FROM boats WHERE id = ANY(:ids)"
        ),
        {"ids": boat_ids},
    ).mappings().all()
    row_list = [dict(r) for r in rows]

    # Per-boat per-table FK counts — for both STRONG signal tables
    # (used by the safety gate) and COUNT_ONLY tables (used only by
    # the winner-picker tie-break). Collecting both here keeps the
    # classification and winner-picking passes consistent.
    sig: dict[int, dict[str, int]] = {bid: {} for bid in boat_ids}
    for tbl in (*SIGNAL_TABLES, *COUNT_ONLY_TABLES):
        result = conn.execute(
            text(
                f"SELECT boat_id, COUNT(*) AS n FROM {tbl} "
                f"WHERE boat_id = ANY(:ids) GROUP BY boat_id"
            ),
            {"ids": boat_ids},
        ).mappings().all()
        for r in result:
            sig[r["boat_id"]][tbl] = r["n"]

    return ClusterFacts(boat_ids=boat_ids, rows=row_list, signal_counts=sig)


def classify_cluster(
    facts: ClusterFacts,
) -> tuple[str, str | None, int | None]:
    """Return (decision, reason_if_review, signal_bearer_id_if_auto).

    decision in {"AUTO_MERGE", "REVIEW"}.

    Rules (in order; all must hold for AUTO_MERGE):
      4 (size): cluster size after high-conf pass <= MAX_CLUSTER_SIZE
      1 (signal): at most one row in the cluster bears STRONG signal
                  (certificates / race_results / orders / insight_cache)
      2 (design conflict): no two rows have non-NULL design_canonical
                           values that normalize to different canonical
                           names
      3 (year conflict): no two rows have non-NULL year_built differing
                         by more than YEAR_BUILT_TOLERANCE years
    """
    # Rule 4 (size): check first — cheapest.
    if len(facts.boat_ids) > MAX_CLUSTER_SIZE:
        return "REVIEW", "big_cluster", None

    # Rule 1: at most one signal-bearing row.
    bearers = facts.signal_bearing_ids()
    if len(bearers) == 0:
        # Zero STRONG-signal-bearing rows — all empty stubs. Still a
        # valid merge: pick winner via the standard rule. Treat as
        # AUTO_MERGE with bearer = None.
        bearer_id = None
    elif len(bearers) == 1:
        bearer_id = bearers[0]
    else:
        return "REVIEW", "multi_signal", None

    rows_by_id = {r["id"]: r for r in facts.rows}

    # Rule 2: design conflict — pairwise check over design_canonical only
    # (per spec). We use normalize_design() to fold spelling variants.
    design_norms: dict[int, str | None] = {}
    for bid in facts.boat_ids:
        r = rows_by_id.get(bid, {})
        design_norms[bid] = _norm_design_canon(r.get("design_canonical"))
    non_null_designs = {d for d in design_norms.values() if d is not None}
    if len(non_null_designs) > 1:
        return "REVIEW", "design_conflict", None

    # Rule 3: year_built tolerance — pairwise check.
    non_null_years = [
        int(y) for y in (
            rows_by_id.get(bid, {}).get("year_built") for bid in facts.boat_ids
        ) if y is not None
    ]
    if non_null_years and (max(non_null_years) - min(non_null_years) > YEAR_BUILT_TOLERANCE):
        return "REVIEW", "year_conflict", None

    return "AUTO_MERGE", None, bearer_id


# ---------------------------------------------------------------------------
# Winner / FK re-point helpers (mirrors merge_boat_dupes_high.py)
# ---------------------------------------------------------------------------

def pick_winner(conn: Connection, boat_ids: list[int]) -> int:
    """Pick the canonical winner from a cluster.

    Order of preference:
      1. Row with most-recent irc_certificates.issue_date.
      2. Tie-break: highest race_results count.
      3. Tie-break: highest (orders + insight_cache) count.
      4. Tie-break: earliest boats.created_at.
    """
    rows = conn.execute(
        text(
            """
            SELECT b.id,
                   b.created_at,
                   (SELECT MAX(c.issue_date)
                      FROM irc_certificates c WHERE c.boat_id = b.id) AS latest_cert,
                   (SELECT COUNT(*) FROM race_results r WHERE r.boat_id = b.id) AS rr_count,
                   (SELECT COUNT(*) FROM orders        o WHERE o.boat_id = b.id) AS ord_count,
                   (SELECT COUNT(*) FROM insight_cache i WHERE i.boat_id = b.id) AS ins_count
              FROM boats b
             WHERE b.id = ANY(:ids)
            """
        ),
        {"ids": boat_ids},
    ).mappings().all()

    def sort_key(r):
        latest_cert = r["latest_cert"]
        return (
            0 if latest_cert is not None else 1,
            -(latest_cert.toordinal()) if latest_cert else 0,
            -r["rr_count"],
            -(r["ord_count"] + r["ins_count"]),
            r["created_at"],
        )

    return sorted(rows, key=sort_key)[0]["id"]


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
            conn.execute(
                text("UPDATE irc_certificates SET boat_id = :w WHERE id = :id"),
                {"w": winner_id, "id": lc["id"]},
            )
            repointed += 1
            continue

        winner_date = wc["issue_date"]
        loser_date = lc["issue_date"]
        winner_is_newer = (
            (winner_date or 0) >= (loser_date or 0)
            if (winner_date is not None or loser_date is not None)
            else True
        )

        if winner_is_newer:
            extras_sink.append({"kind": "certificate_collision_dropped", "row": lc["j"]})
            conn.execute(
                text("DELETE FROM irc_certificates WHERE id = :id"), {"id": lc["id"]}
            )
        else:
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

    res = conn.execute(
        text("UPDATE race_results SET boat_id = :w WHERE boat_id = :l"),
        {"w": winner_id, "l": loser_id},
    )
    return res.rowcount or 0, collisions


# ---------------------------------------------------------------------------
# Merge per cluster
# ---------------------------------------------------------------------------

def merge_cluster(engine, cluster_key: str, boat_ids: list[int]) -> MergeReport:
    rep = MergeReport(cluster_key=cluster_key, winner_id=0)

    with engine.begin() as conn:
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

            cert_rp, cert_col = resolve_cert_collisions(conn, winner_id, loser_id, extras)
            tcc_rp, tcc_col = resolve_tcc_collisions(conn, winner_id, loser_id, extras)
            rr_rp, _rr_col = resolve_race_results_collisions(conn, winner_id, loser_id, extras)

            rep.rows_repointed["irc_certificates"] = rep.rows_repointed.get("irc_certificates", 0) + cert_rp
            rep.rows_repointed["tcc_snapshots"] = rep.rows_repointed.get("tcc_snapshots", 0) + tcc_rp
            rep.rows_repointed["race_results"] = rep.rows_repointed.get("race_results", 0) + rr_rp
            rep.cert_collisions_resolved += cert_col
            rep.tcc_collisions_resolved += tcc_col

            for tbl in SIMPLE_FK_TABLES:
                n = repoint_simple(conn, tbl, winner_id, loser_id)
                rep.rows_repointed[tbl] = rep.rows_repointed.get(tbl, 0) + n

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    engine = get_engine()
    with engine.begin() as conn:
        ensure_boat_merges_table(conn)

    clusters = load_clusters(engine)
    print(f"Loaded {len(clusters)} clusters from DB")

    auto_clusters: list[tuple[str, list[int]]] = []
    review_clusters: list[tuple[str, list[int], str]] = []

    # Classification pass (one read-only connection).
    with engine.connect() as conn:
        for cluster_key, boat_ids in clusters.items():
            existing = [
                r[0]
                for r in conn.execute(
                    text("SELECT id FROM boats WHERE id = ANY(:ids)"),
                    {"ids": boat_ids},
                ).fetchall()
            ]
            if len(existing) < 2:
                # already merged (likely from high-conf pass overlap); skip
                continue
            facts = fetch_cluster_facts(conn, existing)
            decision, reason, _bearer = classify_cluster(facts)
            if decision == "AUTO_MERGE":
                auto_clusters.append((cluster_key, existing))
            else:
                review_clusters.append((cluster_key, existing, reason or "unknown"))

    print(f"Classification: AUTO_MERGE={len(auto_clusters)} REVIEW={len(review_clusters)}")

    # Write review CSV first — that way if the merge crashes mid-way, the
    # operator still has the review queue.
    with CSV_REVIEW_OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cluster_id", "boat_id", "reason"])
        for cluster_key, ids, reason in review_clusters:
            for bid in ids:
                w.writerow([cluster_key, bid, reason])
    print(f"Wrote review CSV: {CSV_REVIEW_OUT} ({len(review_clusters)} clusters)")

    # Merge pass.
    reports: list[MergeReport] = []
    errors: list[tuple[str, str]] = []
    for cluster_key, boat_ids in auto_clusters:
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
    total_losers = 0
    total_cert_col = 0
    total_tcc_col = 0
    fk_totals: dict[str, int] = {}
    for r in reports:
        total_losers += len(r.loser_ids)
        total_cert_col += r.cert_collisions_resolved
        total_tcc_col += r.tcc_collisions_resolved
        for k, v in r.rows_repointed.items():
            fk_totals[k] = fk_totals.get(k, 0) + v
    reason_counts: dict[str, int] = {}
    for _ck, _ids, reason in review_clusters:
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    print(f"clusters_total      = {len(clusters)}")
    print(f"clusters_auto_merge = {len(auto_clusters)}")
    print(f"clusters_review     = {len(review_clusters)}")
    print(f"boats_removed       = {total_losers}")
    print(f"cert_collisions     = {total_cert_col}")
    print(f"tcc_collisions      = {total_tcc_col}")
    print(f"errors              = {len(errors)}")
    print("FK rows re-pointed:")
    for k in sorted(fk_totals):
        print(f"  {k:<20} {fk_totals[k]}")
    print("Review breakdown:")
    for k in sorted(reason_counts):
        print(f"  {k:<20} {reason_counts[k]}")

    if errors:
        print("\nERRORS:")
        for ck, msg in errors:
            print(f"  {ck}: {msg}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
