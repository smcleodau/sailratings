"""Triage the 393 boat-duplicate clusters flagged by the medium-confidence
merge pass into four tiers, and auto-merge only the SAFE tier.

Input:  /tmp/boat_dupes_medium_review.csv  (cluster_id, boat_id, reason)
Output:
  - Tier A: auto-merged into the DB (audit appended to `boat_merges`).
  - Tier B: /tmp/boat_dupes_tier_b.csv  (likely-merge — human eyeball)
  - Tier C: rows inserted into `boat_not_dupe` audit table.
  - Tier D: /tmp/boat_dupes_tier_d.csv  (manual — genuinely ambiguous)

Tiers
-----
A. SAFE_MERGE — multiple signal-bearing rows BUT they share a strong identifier
   (matching normalised sail_number, or matching cert_number on boats / via the
   certificates table). REQUIRES no design_conflict and no year_conflict.
B. LIKELY_MERGE — no shared identifier, same design_canonical (after
   normalize_design), within 5 years on year_built, same country.
C. KEEP_SEPARATE — fails design_conflict, year_conflict, or big_cluster (the
   cluster is genuinely different boats sharing a name).
D. MANUAL — everything else: name+country matches, but no decisive evidence
   either way.

Merge machinery is identical to the medium-confidence pass:
  - per-cluster transaction
  - winner-pick by cert/result/order counts + recency (overridden for ties
    here: see pick_winner_signal_aware)
  - FK re-point order with cert/tcc/race-result collision resolution
  - append full reversal data to boat_merges
  - skip boats already in boat_merges.loser_id (idempotent)
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
from irc_data.matching.identity import normalize_sail_tokens

# Re-use machinery from the medium pass. The scripts/ directory isn't a
# package, so import the sibling module by path.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
from merge_boat_dupes_medium import (  # type: ignore  # noqa: E402
    ensure_boat_merges_table,
    fetch_boat_row,
    repoint_simple,
    resolve_cert_collisions,
    resolve_tcc_collisions,
    resolve_race_results_collisions,
    SIMPLE_FK_TABLES,
)

CSV_IN = Path("/tmp/boat_dupes_medium_review.csv")
CSV_TIER_B = Path("/tmp/boat_dupes_tier_b.csv")
CSV_TIER_D = Path("/tmp/boat_dupes_tier_d.csv")

YEAR_BUILT_TOLERANCE_TIER_A = 2
YEAR_BUILT_TOLERANCE_TIER_B = 5

# Tables whose presence on a row counts as STRONG signal — same as the
# medium-confidence pass.
SIGNAL_TABLES = (
    "certificates",
    "race_results",
    "orders",
    "insight_cache",
)
COUNT_ONLY_TABLES = (
    "tcc_snapshots",
    "orc_certificates",
    "boat_identities",
    "boat_corrections",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_boat_not_dupe_table(conn: Connection) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS boat_not_dupe (
              id bigserial PRIMARY KEY,
              marked_at timestamptz NOT NULL DEFAULT now(),
              cluster_key text NOT NULL,
              boat_ids integer[] NOT NULL,
              reason text NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_boat_not_dupe_cluster
              ON boat_not_dupe(cluster_key);
            """
        )
    )


def load_clusters(path: Path) -> tuple[dict[str, list[int]], dict[str, str]]:
    """Load the review CSV, returning {cluster_key: [boat_ids]} and
    {cluster_key: original_reason}."""
    clusters: dict[str, list[int]] = defaultdict(list)
    reasons: dict[str, str] = {}
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ck = row["cluster_id"]
            clusters[ck].append(int(row["boat_id"]))
            reasons[ck] = row["reason"]
    return dict(clusters), reasons


def already_merged_loser_ids(conn: Connection) -> set[int]:
    return {
        r[0]
        for r in conn.execute(text("SELECT loser_id FROM boat_merges")).fetchall()
    }


def _norm_design_canon(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    canon = normalize_design(s)
    if canon is None:
        return None
    return " ".join(canon.strip().split()).lower() or None


def _norm_sail(value: str | None) -> str | None:
    if value is None:
        return None
    s = "".join(ch for ch in str(value) if not ch.isspace()).upper()
    return s or None


def _norm_cert(value: str | None) -> str | None:
    if value is None:
        return None
    s = "".join(ch for ch in str(value) if not ch.isspace()).upper()
    return s or None


# ---------------------------------------------------------------------------
# Cluster facts collection
# ---------------------------------------------------------------------------

@dataclass
class BoatFacts:
    boat_id: int
    boat_name: str | None
    country: str | None
    sail_number: str | None
    cert_number: str | None  # boats.cert_number
    design: str | None
    design_canonical: str | None
    year_built: int | None
    created_at: Any
    # Derived
    sail_norm: str | None = None
    sail_tokens: set[str] = field(default_factory=set)
    cert_norm_boats: str | None = None
    design_norm: str | None = None
    cert_nums_from_certs: set[str] = field(default_factory=set)
    cert_nums_from_orc: set[str] = field(default_factory=set)
    signal_counts: dict[str, int] = field(default_factory=dict)
    count_only: dict[str, int] = field(default_factory=dict)
    latest_activity: Any = None  # date for tie-break (cert/race/order max)


@dataclass
class Cluster:
    cluster_key: str
    original_reason: str
    boats: list[BoatFacts]

    @property
    def boat_ids(self) -> list[int]:
        return [b.boat_id for b in self.boats]


def fetch_cluster(
    conn: Connection, cluster_key: str, original_reason: str, boat_ids: list[int]
) -> Cluster:
    rows = conn.execute(
        text(
            """
            SELECT id, boat_name, country, sail_number, cert_number,
                   design, design_canonical, year_built, created_at
              FROM boats WHERE id = ANY(:ids)
            """
        ),
        {"ids": boat_ids},
    ).mappings().all()

    boats: list[BoatFacts] = []
    for r in rows:
        bf = BoatFacts(
            boat_id=r["id"],
            boat_name=r["boat_name"],
            country=r["country"],
            sail_number=r["sail_number"],
            cert_number=r["cert_number"],
            design=r["design"],
            design_canonical=r["design_canonical"],
            year_built=r["year_built"],
            created_at=r["created_at"],
        )
        bf.sail_norm = _norm_sail(bf.sail_number)
        bf.sail_tokens = normalize_sail_tokens(bf.sail_number)
        bf.cert_norm_boats = _norm_cert(bf.cert_number)
        bf.design_norm = _norm_design_canon(bf.design_canonical)
        boats.append(bf)

    by_id = {b.boat_id: b for b in boats}

    # cert_number from certificates table
    cert_rows = conn.execute(
        text(
            """
            SELECT boat_id, cert_number FROM certificates
             WHERE boat_id = ANY(:ids) AND cert_number IS NOT NULL
            """
        ),
        {"ids": boat_ids},
    ).fetchall()
    for bid, cn in cert_rows:
        n = _norm_cert(cn)
        if n is not None and bid in by_id:
            by_id[bid].cert_nums_from_certs.add(n)

    # ref_no from orc_certificates — treat as cert identifier
    orc_rows = conn.execute(
        text(
            """
            SELECT boat_id, ref_no FROM orc_certificates
             WHERE boat_id = ANY(:ids) AND ref_no IS NOT NULL
            """
        ),
        {"ids": boat_ids},
    ).fetchall()
    for bid, rn in orc_rows:
        n = _norm_cert(rn)
        if n is not None and bid in by_id:
            by_id[bid].cert_nums_from_orc.add(n)

    # Signal-bearing & count-only FK counts
    for tbl in (*SIGNAL_TABLES, *COUNT_ONLY_TABLES):
        result = conn.execute(
            text(
                f"SELECT boat_id, COUNT(*) AS n FROM {tbl} "
                f"WHERE boat_id = ANY(:ids) GROUP BY boat_id"
            ),
            {"ids": boat_ids},
        ).fetchall()
        for bid, n in result:
            if bid in by_id:
                if tbl in SIGNAL_TABLES:
                    by_id[bid].signal_counts[tbl] = n
                else:
                    by_id[bid].count_only[tbl] = n

    # Latest activity per boat (max across cert.issue_date, race.event_date,
    # order.created_at). Used as the winner tie-breaker for tier-A merges.
    act_rows = conn.execute(
        text(
            """
            SELECT b.id,
                   GREATEST(
                     (SELECT MAX(c.issue_date)::timestamp FROM certificates c WHERE c.boat_id = b.id),
                     (SELECT MAX(r.event_date)::timestamp FROM race_results r WHERE r.boat_id = b.id),
                     (SELECT MAX(o.created_at)::timestamp FROM orders o WHERE o.boat_id = b.id)
                   ) AS latest
              FROM boats b WHERE b.id = ANY(:ids)
            """
        ),
        {"ids": boat_ids},
    ).fetchall()
    for bid, latest in act_rows:
        if bid in by_id:
            by_id[bid].latest_activity = latest

    return Cluster(cluster_key=cluster_key, original_reason=original_reason, boats=boats)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _has_design_conflict(cluster: Cluster) -> bool:
    designs = {b.design_norm for b in cluster.boats if b.design_norm}
    return len(designs) > 1


def _has_year_conflict(cluster: Cluster, tolerance: int) -> bool:
    years = [b.year_built for b in cluster.boats if b.year_built is not None]
    if not years:
        return False
    return (max(years) - min(years)) > tolerance


def _shared_identifier(cluster: Cluster) -> tuple[str, str] | None:
    """Return ('sail', value) or ('cert', value) if any pair shares it.

    We require the shared identifier to be borne by at least two distinct
    boats in the cluster. cert match across {boats.cert_number,
    certificates.cert_number, orc_certificates.ref_no} all count.
    """
    # sail — token-set intersection: handles concatenated strings
    # ("2561&011") and class-prefix variants ("EAUS1213" ↔ "AUS1213").
    sail_to_boats: dict[str, set[int]] = defaultdict(set)
    for b in cluster.boats:
        for tok in b.sail_tokens:
            sail_to_boats[tok].add(b.boat_id)
    for v, ids in sail_to_boats.items():
        if len(ids) >= 2:
            return ("sail", v)

    # cert (union of all sources per boat)
    cert_to_boats: dict[str, set[int]] = defaultdict(set)
    for b in cluster.boats:
        all_certs = set(b.cert_nums_from_certs) | set(b.cert_nums_from_orc)
        if b.cert_norm_boats:
            all_certs.add(b.cert_norm_boats)
        for cn in all_certs:
            cert_to_boats[cn].add(b.boat_id)
    for v, ids in cert_to_boats.items():
        if len(ids) >= 2:
            return ("cert", v)

    return None


def _all_same_country(cluster: Cluster) -> bool:
    countries = {
        (b.country or "").strip().upper() for b in cluster.boats if b.country
    }
    # Treat absent country as compatible (don't disqualify on missing data).
    return len(countries) <= 1


def _same_design_canonical(cluster: Cluster) -> bool:
    """All non-NULL designs in cluster share canonical form, AND at least one
    boat actually has a design. Returns False if all are NULL."""
    norms = {b.design_norm for b in cluster.boats if b.design_norm}
    if not norms:
        return False
    return len(norms) == 1


def classify(cluster: Cluster) -> tuple[str, dict[str, Any]]:
    """Return (tier, details). tier in {'A', 'B', 'C', 'D'}."""
    details: dict[str, Any] = {"original_reason": cluster.original_reason}

    # Hard-fail conditions for KEEP_SEPARATE.
    if _has_design_conflict(cluster):
        designs = sorted({b.design_norm for b in cluster.boats if b.design_norm})
        details["why"] = "design_conflict"
        details["designs"] = designs
        return "C", details

    if _has_year_conflict(cluster, YEAR_BUILT_TOLERANCE_TIER_A):
        years = sorted([b.year_built for b in cluster.boats if b.year_built is not None])
        # Even a wide year split could be tier-B-eligible if within 5y, else
        # it's KEEP_SEPARATE.
        if (years[-1] - years[0]) > YEAR_BUILT_TOLERANCE_TIER_B:
            details["why"] = "year_conflict"
            details["years"] = years
            return "C", details
        # Year spread 3-5y: not safe for A, but maybe B/D below.

    if cluster.original_reason == "big_cluster" and len(cluster.boats) > 6:
        # Treat oversized clusters as KEEP_SEPARATE unless a clean shared
        # identifier exists. Big clusters of name-matches tend to be common
        # boat names with distinct boats.
        si = _shared_identifier(cluster)
        if si is None:
            details["why"] = "big_cluster_distinct"
            details["size"] = len(cluster.boats)
            return "C", details
        # else fall through — shared identifier rescues a big cluster.

    # Tier A: shared strong identifier, no design conflict (already gated),
    # no year conflict at the 2y tolerance.
    si = _shared_identifier(cluster)
    if si is not None and not _has_year_conflict(cluster, YEAR_BUILT_TOLERANCE_TIER_A):
        details["shared_id_kind"] = si[0]
        details["shared_id_value"] = si[1]
        return "A", details

    # Tier B: same design_canonical AND within 5 years AND same country.
    if (
        _same_design_canonical(cluster)
        and not _has_year_conflict(cluster, YEAR_BUILT_TOLERANCE_TIER_B)
        and _all_same_country(cluster)
    ):
        designs = sorted({b.design_norm for b in cluster.boats if b.design_norm})
        details["design_norm"] = designs[0] if designs else None
        details["why"] = "same_design_close_year_same_country"
        return "B", details

    # Tier D: catch-all.
    details["why"] = "ambiguous"
    return "D", details


# ---------------------------------------------------------------------------
# Winner picking (signal-aware) and merge
# ---------------------------------------------------------------------------

def pick_winner(cluster: Cluster) -> int:
    """For tier-A clusters where both rows are signal-bearing.

    1. Highest total (cert + result + order) row count.
    2. Tie: latest activity (max of cert.issue_date, race.event_date, order.created_at).
    3. Tie: earliest created_at.
    """
    def score(b: BoatFacts) -> tuple:
        total = (
            b.signal_counts.get("certificates", 0)
            + b.signal_counts.get("race_results", 0)
            + b.signal_counts.get("orders", 0)
        )
        # Negate so that higher counts sort first.
        latest = b.latest_activity
        latest_key = (0, -latest.toordinal()) if latest is not None else (1, 0)
        return (-total, latest_key, b.created_at)

    return sorted(cluster.boats, key=score)[0].boat_id


def merge_cluster(engine, cluster: Cluster) -> dict[str, Any]:
    report: dict[str, Any] = {
        "cluster_key": cluster.cluster_key,
        "winner_id": None,
        "loser_ids": [],
        "rows_repointed": {},
        "cert_collisions": 0,
        "tcc_collisions": 0,
    }
    boat_ids = cluster.boat_ids

    with engine.begin() as conn:
        existing = [
            r[0]
            for r in conn.execute(
                text("SELECT id FROM boats WHERE id = ANY(:ids) ORDER BY id"),
                {"ids": boat_ids},
            ).fetchall()
        ]
        if len(existing) < 2:
            report["note"] = "already-merged"
            report["winner_id"] = existing[0] if existing else None
            return report

        # Restrict cluster.boats to existing
        filtered = [b for b in cluster.boats if b.boat_id in set(existing)]
        cluster.boats = filtered

        winner_id = pick_winner(cluster)
        loser_ids = [bid for bid in existing if bid != winner_id]
        report["winner_id"] = winner_id
        report["loser_ids"] = loser_ids

        for loser_id in loser_ids:
            extras: list[dict] = []
            loser_full = fetch_boat_row(conn, loser_id)
            if loser_full is None:
                raise RuntimeError(f"loser {loser_id} vanished mid-merge")

            cert_rp, cert_col = resolve_cert_collisions(conn, winner_id, loser_id, extras)
            tcc_rp, tcc_col = resolve_tcc_collisions(conn, winner_id, loser_id, extras)
            rr_rp, _rr_col = resolve_race_results_collisions(conn, winner_id, loser_id, extras)

            rp = report["rows_repointed"]
            rp["certificates"] = rp.get("certificates", 0) + cert_rp
            rp["tcc_snapshots"] = rp.get("tcc_snapshots", 0) + tcc_rp
            rp["race_results"] = rp.get("race_results", 0) + rr_rp
            report["cert_collisions"] += cert_col
            report["tcc_collisions"] += tcc_col

            for tbl in SIMPLE_FK_TABLES:
                n = repoint_simple(conn, tbl, winner_id, loser_id)
                rp[tbl] = rp.get(tbl, 0) + n

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
                    "ck": cluster.cluster_key,
                    "snap": json.dumps(snapshot, default=str),
                },
            )

            for tbl in [
                "certificates", "tcc_snapshots", "race_results",
                "orc_certificates", "boat_identities", "insight_cache",
                "orders", "boat_corrections",
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

    return report


# ---------------------------------------------------------------------------
# CSV / DB output helpers
# ---------------------------------------------------------------------------

def write_review_csv(
    path: Path,
    rows: list[tuple[str, list[int], dict[str, Any]]],
    extra_cols: list[str],
) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cluster_id", "boat_id", "boat_name", "country",
                    "sail_number", "design_canonical", "year_built",
                    *extra_cols])
        w.writerows(rows)


def insert_not_dupe(conn: Connection, cluster_key: str, boat_ids: list[int],
                    reason: str) -> None:
    conn.execute(
        text(
            """
            INSERT INTO boat_not_dupe (cluster_key, boat_ids, reason)
            VALUES (:ck, :ids, :reason)
            """
        ),
        {"ck": cluster_key, "ids": boat_ids, "reason": reason},
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not CSV_IN.exists():
        print(f"ERROR: {CSV_IN} not found", file=sys.stderr)
        return 1

    engine = get_engine()
    with engine.begin() as conn:
        ensure_boat_merges_table(conn)
        ensure_boat_not_dupe_table(conn)

    raw_clusters, reasons = load_clusters(CSV_IN)
    print(f"Loaded {len(raw_clusters)} clusters from {CSV_IN}")

    # Drop any boats already merged in a previous pass.
    with engine.connect() as conn:
        merged_losers = already_merged_loser_ids(conn)
    if merged_losers:
        print(f"Excluding {len(merged_losers)} boats already in boat_merges.loser_id")

    cleaned: dict[str, list[int]] = {}
    skipped_already_merged = 0
    for ck, ids in raw_clusters.items():
        filtered = [b for b in ids if b not in merged_losers]
        if len(filtered) < 2:
            skipped_already_merged += 1
            continue
        cleaned[ck] = filtered

    print(f"After exclusion: {len(cleaned)} live clusters "
          f"({skipped_already_merged} skipped as already-merged)")

    # Classification pass.
    classified: dict[str, list[tuple[Cluster, dict[str, Any]]]] = {
        "A": [], "B": [], "C": [], "D": [],
    }
    with engine.connect() as conn:
        for ck, ids in cleaned.items():
            cluster = fetch_cluster(conn, ck, reasons.get(ck, "unknown"), ids)
            if len(cluster.boats) < 2:
                continue
            tier, details = classify(cluster)
            classified[tier].append((cluster, details))

    for tier in "ABCD":
        print(f"  Tier {tier}: {len(classified[tier])} clusters")

    # ---- Tier C: write to boat_not_dupe audit table.
    not_dupe_rows = 0
    with engine.begin() as conn:
        for cluster, details in classified["C"]:
            insert_not_dupe(
                conn,
                cluster.cluster_key,
                cluster.boat_ids,
                details.get("why", "unknown"),
            )
            not_dupe_rows += 1
    print(f"Inserted {not_dupe_rows} rows into boat_not_dupe")

    # ---- Tier B and D: write to CSV.
    def _flatten(cluster: Cluster, details: dict[str, Any]) -> list[list[Any]]:
        out = []
        for b in cluster.boats:
            out.append([
                cluster.cluster_key, b.boat_id, b.boat_name, b.country,
                b.sail_number, b.design_canonical, b.year_built,
                details.get("why", ""),
                details.get("design_norm", ""),
            ])
        return out

    b_rows: list[list[Any]] = []
    for cluster, details in classified["B"]:
        b_rows.extend(_flatten(cluster, details))
    write_review_csv(CSV_TIER_B, b_rows, ["why", "design_norm"])
    print(f"Wrote {CSV_TIER_B} ({len(classified['B'])} clusters, {len(b_rows)} boats)")

    d_rows: list[list[Any]] = []
    for cluster, details in classified["D"]:
        d_rows.extend(_flatten(cluster, details))
    write_review_csv(CSV_TIER_D, d_rows, ["why", "design_norm"])
    print(f"Wrote {CSV_TIER_D} ({len(classified['D'])} clusters, {len(d_rows)} boats)")

    # ---- Tier A: auto-merge.
    print(f"\nAuto-merging Tier A: {len(classified['A'])} clusters")
    reports: list[dict[str, Any]] = []
    errors: list[tuple[str, str]] = []
    sample_log: list[tuple[Cluster, dict[str, Any], dict[str, Any]]] = []
    for cluster, details in classified["A"]:
        try:
            rep = merge_cluster(engine, cluster)
            reports.append(rep)
            sample_log.append((cluster, details, rep))
            if rep.get("note") == "already-merged":
                print(f"[skip] {cluster.cluster_key}: already merged")
            else:
                print(
                    f"[ok]   {cluster.cluster_key}: winner={rep['winner_id']} "
                    f"losers={rep['loser_ids']} "
                    f"id_match={details.get('shared_id_kind')}={details.get('shared_id_value')}"
                )
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            errors.append((cluster.cluster_key, msg))
            print(f"[fail] {cluster.cluster_key}: {msg}", file=sys.stderr)

    # ---- Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    total_losers = sum(len(r.get("loser_ids", [])) for r in reports)
    total_cert_col = sum(r.get("cert_collisions", 0) for r in reports)
    total_tcc_col = sum(r.get("tcc_collisions", 0) for r in reports)
    fk_totals: dict[str, int] = {}
    for r in reports:
        for k, v in r.get("rows_repointed", {}).items():
            fk_totals[k] = fk_totals.get(k, 0) + v

    print(f"clusters_total       = {len(cleaned)}")
    print(f"tier_A_clusters      = {len(classified['A'])}")
    print(f"tier_B_clusters      = {len(classified['B'])}")
    print(f"tier_C_clusters      = {len(classified['C'])}")
    print(f"tier_D_clusters      = {len(classified['D'])}")
    print(f"boats_removed_tier_A = {total_losers}")
    print(f"not_dupe_rows        = {not_dupe_rows}")
    print(f"cert_collisions      = {total_cert_col}")
    print(f"tcc_collisions       = {total_tcc_col}")
    print(f"errors               = {len(errors)}")
    print("FK rows re-pointed:")
    for k in sorted(fk_totals):
        print(f"  {k:<20} {fk_totals[k]}")

    if errors:
        print("\nERRORS:")
        for ck, msg in errors:
            print(f"  {ck}: {msg}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
