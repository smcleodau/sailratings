"""OPS-02-08 — Boat identity: Tier-S/A auto-merges with loser_snapshot;
Tier-B/D to the admin dupe_review_queue.

One-shot, idempotent ops pipeline. Re-runnable; every step is a no-op when
there is nothing left to do.

Pipeline
--------
0. Report BEFORE counts (dupe_review_queue by tier/verdict, live medium
   clusters, boat_merges total, merges missing a snapshot).
1. Verify the 37 Tier-S '- SEC' merges are applied and dry-run clean:
   re-runs the Tier-S / S-prime / S-double-prime discovery queries from
   dry_run_tier_s_merges.py against the live DB — all must return zero
   live clusters (they were applied 2026-05-20 / 2026-07-26; this step is
   the proof, not the merge).
2. Tier-A of the medium (boat_name + country) clusters: discover live
   clusters from the boats table, apply the medium-confidence safety gate
   (merge_boat_dupes_medium.classify_cluster) AND the triage Tier-A
   strong-identifier requirement (shared normalised sail token or shared
   cert number, no design/year conflict). Qualifying clusters are merged
   with merge_boat_dupes_medium.merge_cluster — per-cluster transaction,
   FK re-pointing with collision resolution, and a boat_merges audit row
   whose loser_snapshot contains the full loser boat row + collision
   extras.
3. Tier-B/D of the live medium clusters are upserted into
   dupe_review_queue (verdict=PENDING) for the admin Duplicate boats
   screen. Boats already queued PENDING for the same cluster are left
   alone; new boats (e.g. created by the last rematch Phase-2 pass) get
   fresh rows. Tier-B = same normalised design and year_built within 5
   years; everything else = Tier-D. Queue rows whose boat was deleted by
   a merge (or whose cluster is otherwise resolved) are marked
   verdict='AUTO_RESOLVED'.
4. Report AFTER counts so the queue-count before/after delta can go
   straight into the issue evidence.

Usage
-----
    python3 scripts/apply_boat_dedupe_ops_02_08.py            # dry-run
    python3 scripts/apply_boat_dedupe_ops_02_08.py --apply    # commit
    python3 scripts/apply_boat_dedupe_ops_02_08.py --counts-only
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from irc_data.db.connection import get_engine
from irc_data.matching.identity import normalize_sail_tokens

# Re-use the proven merge machinery + safety gate from the medium pass.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
from merge_boat_dupes_medium import (  # type: ignore  # noqa: E402
    classify_cluster,
    ensure_boat_merges_table,
    fetch_cluster_facts,
    merge_cluster,
)
from dry_run_tier_s_merges import (  # type: ignore  # noqa: E402
    TIER_S_QUERY,
    TIER_S_PRIME_QUERY,
    TIER_S_DOUBLE_PRIME_QUERY,
)

MEDIUM_CLUSTERS_QUERY = """
SELECT boat_name || '|' || country AS cluster_id, array_agg(id ORDER BY id) AS boat_ids
  FROM boats
 WHERE boat_name IS NOT NULL AND boat_name != ''
   AND country IS NOT NULL AND country != ''
 GROUP BY boat_name, country
HAVING COUNT(*) > 1
 ORDER BY 1
"""

# Tier-B year tolerance mirrors triage_boat_dupes_review.py
YEAR_BUILT_TOLERANCE_TIER_B = 5

REVIEWER = "ops-02-08-pipeline"


# ---------------------------------------------------------------------------
# Discovery / counts
# ---------------------------------------------------------------------------

def queue_counts(conn: Connection) -> list[tuple[str, str, int]]:
    return [
        (r[0], r[1], r[2])
        for r in conn.execute(
            text(
                "SELECT tier, verdict, COUNT(*) FROM dupe_review_queue "
                "GROUP BY tier, verdict ORDER BY tier, verdict"
            )
        ).fetchall()
    ]


def print_counts(conn: Connection, label: str) -> dict[str, Any]:
    counts = queue_counts(conn)
    pending = sum(n for _t, v, n in counts if v == "PENDING")
    merges_total = conn.execute(text("SELECT COUNT(*) FROM boat_merges")).scalar()
    merges_no_snap = conn.execute(
        text("SELECT COUNT(*) FROM boat_merges WHERE loser_snapshot IS NULL")
    ).scalar()
    medium_live = conn.execute(
        text(f"SELECT COUNT(*) FROM ({MEDIUM_CLUSTERS_QUERY}) q")
    ).scalar()
    print(f"\n--- {label} ---")
    print(f"  boat_merges total            = {merges_total}")
    print(f"  boat_merges NULL snapshot    = {merges_no_snap}")
    print(f"  live medium clusters         = {medium_live}")
    print(f"  dupe_review_queue PENDING    = {pending}")
    for tier, verdict, n in counts:
        print(f"    tier={tier} verdict={verdict:<14} {n}")
    return {
        "merges_total": merges_total,
        "merges_null_snapshot": merges_no_snap,
        "medium_live": medium_live,
        "queue": counts,
        "queue_pending": pending,
    }


def live_tier_clusters(conn: Connection, query: str) -> list[Any]:
    """Return only clusters that still have >= 2 live members."""
    out = []
    for row in conn.execute(text(query)).fetchall():
        ids = list(row.ids)
        live = [
            r[0]
            for r in conn.execute(
                text("SELECT id FROM boats WHERE id = ANY(:ids)"), {"ids": ids}
            ).fetchall()
        ]
        if len(live) >= 2:
            out.append((row.k1, row.k2, row.k3, live))
    return out


# ---------------------------------------------------------------------------
# Medium-cluster classification (safety gate + Tier-A identifier check)
# ---------------------------------------------------------------------------

@dataclass
class ClusterAssessment:
    cluster_key: str
    boat_ids: list[int]
    decision: str               # AUTO_MERGE | REVIEW
    tier: str | None = None     # 'B' | 'D' when REVIEW
    why: str = ""
    shared_id_kind: str | None = None
    shared_id_value: str | None = None


def _shared_identifier(conn: Connection, boat_ids: list[int]) -> tuple[str | None, str | None]:
    """Tier-A strong-identifier check (mirrors triage_boat_dupes_review):
    shared normalised sail token, or shared cert number across boats /
    irc_certificates / orc_certificates."""
    # 1. Sail-token intersection
    token_sets: list[set[str]] = []
    for (sail,) in conn.execute(
        text("SELECT sail_number FROM boats WHERE id = ANY(:ids) ORDER BY id"),
        {"ids": boat_ids},
    ).fetchall():
        token_sets.append(normalize_sail_tokens(sail))
    if token_sets and all(token_sets):
        common = set.intersection(*token_sets)
        if common:
            return "sail", sorted(common)[0]

    # 2. Shared cert number
    cert_sets: dict[int, set[str]] = {b: set() for b in boat_ids}
    for bid, cn in conn.execute(
        text(
            "SELECT id, cert_number FROM boats "
            "WHERE id = ANY(:ids) AND cert_number IS NOT NULL"
        ),
        {"ids": boat_ids},
    ).fetchall():
        cert_sets[bid].add("".join(str(cn).split()).upper())
    for bid, cn in conn.execute(
        text(
            "SELECT boat_id, cert_number FROM irc_certificates "
            "WHERE boat_id = ANY(:ids) AND cert_number IS NOT NULL"
        ),
        {"ids": boat_ids},
    ).fetchall():
        cert_sets.setdefault(bid, set()).add("".join(str(cn).split()).upper())
    for bid, rn in conn.execute(
        text(
            "SELECT boat_id, ref_no FROM orc_certificates "
            "WHERE boat_id = ANY(:ids) AND ref_no IS NOT NULL"
        ),
        {"ids": boat_ids},
    ).fetchall():
        cert_sets.setdefault(bid, set()).add("".join(str(rn).split()).upper())
    non_empty = [s for s in cert_sets.values() if s]
    if len(non_empty) >= 2:
        common = set.intersection(*non_empty)
        common.discard("")
        if common:
            return "cert", sorted(common)[0]
    return None, None


def assess_medium_cluster(conn: Connection, cluster_key: str, boat_ids: list[int]) -> ClusterAssessment:
    facts = fetch_cluster_facts(conn, boat_ids)
    decision, reason, _bearer = classify_cluster(facts)
    if decision == "REVIEW":
        # Fails the medium safety gate (multi-signal, design/year conflict,
        # big cluster). Tier it for the admin queue: B when the rows look
        # like the same boat (same normalised design, years within 5),
        # otherwise D.
        tier, why = _tier_review(facts.rows, reason or "unknown")
        return ClusterAssessment(cluster_key, boat_ids, "REVIEW", tier=tier, why=why)

    # Gate passed (<=1 STRONG-signal row, no design/year conflict, size ok)
    # — but Tier-A additionally requires a shared strong identifier.
    kind, value = _shared_identifier(conn, boat_ids)
    if kind is not None:
        return ClusterAssessment(
            cluster_key, boat_ids, "AUTO_MERGE",
            why="tier_a_safe_merge", shared_id_kind=kind, shared_id_value=value,
        )
    tier, why = _tier_review(facts.rows, "no_shared_identifier")
    return ClusterAssessment(cluster_key, boat_ids, "REVIEW", tier=tier, why=why)


def _norm_design(value: Any) -> str | None:
    if value is None:
        return None
    s = " ".join(str(value).strip().split()).lower()
    return s or None


def _tier_review(rows: list[dict[str, Any]], base_reason: str) -> tuple[str, str]:
    designs = {_norm_design(r.get("design_canonical")) for r in rows}
    designs.discard(None)
    years = [int(r["year_built"]) for r in rows if r.get("year_built") is not None]
    same_design = len(designs) <= 1
    close_year = (not years) or (max(years) - min(years) <= YEAR_BUILT_TOLERANCE_TIER_B)
    if base_reason in ("design_conflict", "year_conflict", "big_cluster"):
        return "D", base_reason
    if same_design and close_year and designs and years:
        return "B", f"same_design_close_year_same_country ({base_reason})"
    return "D", f"no_decisive_evidence ({base_reason})"


# ---------------------------------------------------------------------------
# dupe_review_queue upsert / hygiene
# ---------------------------------------------------------------------------

def _queue_row_payload(conn: Connection, cluster_key: str, tier: str,
                       boat_id: int, cluster_size: int, why: str) -> dict[str, Any] | None:
    boat = conn.execute(
        text(
            "SELECT id, boat_name, country, sail_number, design, year_built "
            "FROM boats WHERE id = :b"
        ),
        {"b": boat_id},
    ).mappings().fetchone()
    if boat is None:
        return None
    rr = conn.execute(
        text("SELECT COUNT(*) FROM race_results WHERE boat_id = :b"), {"b": boat_id}
    ).scalar()
    certs = conn.execute(
        text("SELECT COUNT(*) FROM irc_certificates WHERE boat_id = :b"), {"b": boat_id}
    ).scalar()
    latest = conn.execute(
        text(
            "SELECT GREATEST("
            "  (SELECT MAX(event_date) FROM race_results WHERE boat_id = :b),"
            "  (SELECT MAX(issue_date) FROM irc_certificates WHERE boat_id = :b))"
        ),
        {"b": boat_id},
    ).scalar()
    return {
        "cluster_id": cluster_key,
        "tier": tier,
        "boat_id": boat_id,
        "boat_name": boat["boat_name"],
        "country": boat["country"],
        "sail_number": boat["sail_number"],
        "design": boat["design"],
        "year_built": boat["year_built"],
        "race_results": rr,
        "cert_count": certs,
        "latest_activity": latest,
        "cluster_size": cluster_size,
        "why": why,
    }


def sync_review_queue(
    conn: Connection,
    review: list[ClusterAssessment],
    apply: bool,
) -> dict[str, int]:
    """Insert PENDING rows for Tier-B/D boats not already queued; mark
    PENDING rows whose boat vanished (merged) as AUTO_RESOLVED.

    Queue rows are matched to live clusters case-insensitively on
    cluster_id: the earlier triage pass stored cluster keys UPPER-CASED
    (e.g. 'NUVOLA|AUS') while the medium pass discovers them verbatim
    ('Nuvola|AUS'). Same cluster either way — do not duplicate the rows.
    """
    stats = {"inserted": 0, "already_queued": 0, "auto_resolved": 0}

    # 1. Insert missing queue rows.
    for a in review:
        existing = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT boat_id FROM dupe_review_queue "
                    "WHERE UPPER(cluster_id) = UPPER(:ck) AND verdict = 'PENDING'"
                ),
                {"ck": a.cluster_key},
            ).fetchall()
        }
        for bid in a.boat_ids:
            if bid in existing:
                stats["already_queued"] += 1
                continue
            payload = _queue_row_payload(conn, a.cluster_key, a.tier or "D", bid,
                                         len(a.boat_ids), a.why)
            if payload is None:
                continue
            if apply:
                conn.execute(
                    text(
                        """
                        INSERT INTO dupe_review_queue (
                          cluster_id, tier, boat_id, boat_name, country,
                          sail_number, design, year_built, race_results,
                          cert_count, latest_activity, cluster_size, why
                        ) VALUES (
                          :cluster_id, :tier, :boat_id, :boat_name, :country,
                          :sail_number, :design, :year_built, :race_results,
                          :cert_count, :latest_activity, :cluster_size, :why
                        )
                        """
                    ),
                    payload,
                )
            stats["inserted"] += 1

    # 2. AUTO_RESOLVE PENDING rows whose boat no longer exists (merged away)
    #    — keeps the admin screen free of dead references.
    stale = conn.execute(
        text(
            "SELECT id, boat_id FROM dupe_review_queue q "
            "WHERE verdict = 'PENDING' "
            "  AND NOT EXISTS (SELECT 1 FROM boats b WHERE b.id = q.boat_id)"
        )
    ).fetchall()
    for qid, bid in stale:
        if apply:
            conn.execute(
                text(
                    "UPDATE dupe_review_queue "
                    "SET verdict = 'AUTO_RESOLVED', "
                    "    verdict_note = 'boat merged away by dedupe pipeline', "
                    "    reviewed_at = now(), reviewed_by = :who "
                    "WHERE id = :qid"
                ),
                {"qid": qid, "who": REVIEWER},
            )
        stats["auto_resolved"] += 1
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="Commit changes (default: dry-run, everything rolls back)")
    ap.add_argument("--counts-only", action="store_true",
                    help="Only print queue/merge counts")
    args = ap.parse_args()
    apply = args.apply
    mode = "APPLY" if apply else "DRY-RUN"

    engine = get_engine()
    failures: list[str] = []

    with engine.begin() as conn:
        ensure_boat_merges_table(conn)

    with engine.connect() as conn:
        before = print_counts(conn, f"BEFORE ({mode})")
    if args.counts_only:
        return 0

    # ---- 1. Verify Tier-S family is fully applied (dry-run clean) --------
    print(f"\n=== STEP 1: Tier-S family verification ({mode}) ===")
    tier_specs = [
        ("S", TIER_S_QUERY),
        ("S-prime", TIER_S_PRIME_QUERY),
        ("S-double-prime", TIER_S_DOUBLE_PRIME_QUERY),
    ]
    with engine.connect() as conn:
        for label, q in tier_specs:
            live = live_tier_clusters(conn, q)
            print(f"  Tier-{label} live clusters: {len(live)}")
            for k1, k2, k3, ids in live:
                print(f"    PENDING {k1} | {k2} | {k3} ids={ids}")
            if live:
                failures.append(f"Tier-{label} has {len(live)} live clusters")

    # ---- 2/3. Medium clusters: Tier-A auto-merge, Tier-B/D to queue -----
    print(f"\n=== STEP 2: medium-cluster classification ({mode}) ===")
    with engine.connect() as conn:
        clusters = [
            (r.cluster_id, list(r.boat_ids))
            for r in conn.execute(text(MEDIUM_CLUSTERS_QUERY)).fetchall()
        ]
        assessments: list[ClusterAssessment] = []
        for ck, ids in clusters:
            live = [
                r[0]
                for r in conn.execute(
                    text("SELECT id FROM boats WHERE id = ANY(:ids) ORDER BY id"),
                    {"ids": ids},
                ).fetchall()
            ]
            if len(live) < 2:
                continue
            assessments.append(assess_medium_cluster(conn, ck, live))

    auto = [a for a in assessments if a.decision == "AUTO_MERGE"]
    review = [a for a in assessments if a.decision == "REVIEW"]
    tier_b = [a for a in review if a.tier == "B"]
    tier_d = [a for a in review if a.tier == "D"]
    print(f"  live medium clusters : {len(assessments)}")
    print(f"  Tier-A (auto-merge)  : {len(auto)}")
    print(f"  Tier-B (queue)       : {len(tier_b)}")
    print(f"  Tier-D (queue)       : {len(tier_d)}")
    for a in review:
        print(f"    [{a.tier}] {a.cluster_key} ids={a.boat_ids} why={a.why}")

    # ---- Tier-A merges ----------------------------------------------------
    print(f"\n=== STEP 3: Tier-A merges ({mode}) ===")
    merged_losers = 0
    if not auto:
        print("  (none qualify — nothing to merge)")
    for a in auto:
        try:
            if apply:
                rep = merge_cluster(engine, a.cluster_key, a.boat_ids)
                merged_losers += len(rep.loser_ids)
                print(f"  [ok] {a.cluster_key}: winner={rep.winner_id} "
                      f"losers={rep.loser_ids} id={a.shared_id_kind}={a.shared_id_value}")
            else:
                print(f"  [preview] {a.cluster_key} ids={a.boat_ids} "
                      f"id={a.shared_id_kind}={a.shared_id_value}")
        except Exception as e:  # noqa: BLE001
            msg = f"{a.cluster_key}: {type(e).__name__}: {e}"
            failures.append(msg)
            print(f"  [fail] {msg}", file=sys.stderr)

    # ---- Tier-B/D queue sync ---------------------------------------------
    print(f"\n=== STEP 4: dupe_review_queue sync ({mode}) ===")
    if apply:
        with engine.begin() as conn:
            qstats = sync_review_queue(conn, review, apply=True)
    else:
        # apply=False performs no writes, so a plain read connection works.
        with engine.connect() as conn:
            qstats = sync_review_queue(conn, review, apply=False)
    print(f"  rows to insert (B/D PENDING) : {qstats['inserted']}")
    print(f"  boats already queued PENDING : {qstats['already_queued']}")
    print(f"  stale rows -> AUTO_RESOLVED  : {qstats['auto_resolved']}")

    # ---- AFTER counts ------------------------------------------------------
    with engine.connect() as conn:
        after = print_counts(conn, f"AFTER ({mode})")

    # ---- Acceptance-criteria check ----------------------------------------
    print("\n=== ACCEPTANCE CRITERIA ===")
    print(f"  [{'OK' if not failures else 'FAIL'}] 0 Tier-S/A clusters pending"
          f" (failures={len(failures)})")
    ok_snap = after["merges_null_snapshot"] == 0
    print(f"  [{'OK' if ok_snap else 'FAIL'}] every merge has a loser_snapshot"
          f" (NULLs={after['merges_null_snapshot']})")
    print(f"  [..] rematch delta: run `irc-data rematch-results` after --apply;"
          f" record stats from its output (matched/updated/boats_created).")
    print(f"\n  Queue pending: {before['queue_pending']} -> {after['queue_pending']}"
          f" (delta {after['queue_pending'] - before['queue_pending']:+d})")
    print(f"  boat_merges:   {before['merges_total']} -> {after['merges_total']}"
          f" (delta {after['merges_total'] - before['merges_total']:+d},"
          f" new losers merged this run: {merged_losers})")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
