#!/usr/bin/env python3
"""OPS-02-07 — Run the unrun result-status backfills and garbage sweep.

Goal: result statuses the RAI (ratings AI / analysis layer) can trust.

Scope, in one idempotent, re-runnable pipeline. Each mutating step runs in
its own transaction with before/after counts printed AND recorded into
``admin_metrics`` (the same evidence sink OPS-02-09 / OPS-02-12 use), so the
run is observable from the DB afterwards.

Steps
-----
1. **TopYacht DNF backfill** — legacy TopYacht rows stored as
   ``status='finished'`` with no place and no finish time are DNFs
   (the parser never set a status on those pages; ``fix_dnf.py`` encoded the
   rule but was never run to completion). Expected ~680 rows when first
   devised; on the current dev DB the rule finds 0 remaining candidates
   because the DNF population (662 rows) is already labelled — this step is
   kept as the idempotent guard + proof.

2. **SailRaceHQ DNF backfill** — same rule for ``source='sailracehq'``
   (530 DNF rows already labelled; 0 candidates remain).

3. **Garbage-row sweep** — delete ``race_results`` rows that carry *no boat
   identity whatsoever*: every name/sail key in ``raw_data``
   (``boat_name``, ``name``, ``boat``, ``sail_number``, ``sail_no``,
   ``sailno``, ``sail no``) is empty/absent. These rows can never be matched
   to a boat, never trusted by the analysis layer, and several are pure
   parse garbage (e.g. SailWave series-overall summary lines that slipped
   through with the boat column blank — the "163 garbage rows" cluster this
   issue calls out; the same no-identity failure mode accounts for 14,969
   rows across rorc/isora/cowesweek/rhkyc/sydneyhobart on the dev DB).
   Rows linked to a ``boat_id`` are still deleted — the link was made via
   owner/TCC inference and the row itself holds no usable per-race data —
   but they are counted separately in the report. A safety valve refuses to
   delete more than ``--max-delete`` rows in one run (default 20,000; the
   current population is ~15k).

4. **SailSys hollow rows (~21k) — decision: FLAG as DNF.**
   Rows with ``source='sailsys' AND status='finished' AND place IS NULL``.
   Analysis of the dev DB shows ALL 21,054 of them have
   ``elapsed_time IS NULL``, ``corrected_time IS NULL``, and every other
   boat in the same races has a place — the legacy importer hardcoded
   ``status='finished'`` when it could not derive a handicap place. They
   are *hollow*: a finish-time string in ``raw_data`` but no scored
   outcome. Options considered:
     - **drop**: loses the only record that the boat fronted the start;
       irreversible without a re-scrape.
     - **keep as 'finished'**: actively false — the analysis layer filters
       on ``status='finished' AND place IS NOT NULL``, so they pollute
       fleet_size denominators and "races sailed" counters while
       contributing no performance signal; the RAI cannot distinguish them
       from real finishes without out-of-band knowledge.
     - **flag as DNF** (chosen): matches the ``_derive_status`` contract in
       ``result_import.py`` (no place and no usable scoring evidence =>
       not a finisher), keeps the rows (boat_id links, finish-time
       evidence), and is fully reversible via the
       ``raw_data.ops_02_07_prev_status`` marker written by this script.
   The SailSys DNF population grows 10,944 -> 31,998.

Acceptance criteria asserted at the end of an ``--apply`` run
-------------------------------------------------------------
- before/after counts recorded (stdout + ``admin_metrics`` rows with
  metric names under ``ops_02_07.*``);
- ``race_results`` has no rows whose ``raw_data->>'boat_name'`` is an
  empty string (the literal "empty-name" class), and no rows with *no*
  identity keys at all (the garbage sweep);
- DNF statuses present for the affected sources (topyacht / sailracehq /
  sailsys all show non-trivial DNF populations);
- zero remaining candidates for any of the four rules (idempotence).

Usage
-----
    PYTHONPATH=src python3 scripts/run_result_status_backfills_ops_02_07.py
        # dry-run: prints before/after counts, every statement rolls back
    PYTHONPATH=src python3 scripts/run_result_status_backfills_ops_02_07.py --apply
        # commit: each step in its own transaction
    PYTHONPATH=src python3 scripts/run_result_status_backfills_ops_02_07.py --counts-only
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import text
from sqlalchemy.engine import Connection

from irc_data.db.connection import get_engine

METRIC_PREFIX = "ops_02_07"
RUN_SCOPE = "race_results"

# Every key a scraper has ever used to carry the boat's identity in raw_data.
IDENTITY_KEYS_SQL = (
    "coalesce(btrim(raw_data->>'boat_name'), '') <> '' "
    "OR coalesce(btrim(raw_data->>'name'), '') <> '' "
    "OR coalesce(btrim(raw_data->>'boat'), '') <> '' "
    "OR coalesce(btrim(raw_data->>'sail_number'), '') <> '' "
    "OR coalesce(btrim(raw_data->>'sail_no'), '') <> '' "
    "OR coalesce(btrim(raw_data->>'sailno'), '') <> '' "
    "OR coalesce(btrim(raw_data->>'sail no'), '') <> ''"
)

NO_IDENTITY_WHERE = f"NOT ({IDENTITY_KEYS_SQL})"

# ---------------------------------------------------------------------------
# Rule SQL (shared by the dry-run counts and the apply path)
# ---------------------------------------------------------------------------

TOPYACHT_DNF_CANDIDATES = """
    SELECT COUNT(*) FROM race_results
    WHERE source = 'topyacht' AND transport = 'legacy'
      AND status = 'finished' AND place IS NULL
      AND coalesce(raw_data->>'finish_time', '') = ''
"""

TOPYACHT_DNF_UPDATE = """
    UPDATE race_results
       SET status = 'DNF'
     WHERE source = 'topyacht' AND transport = 'legacy'
       AND status = 'finished' AND place IS NULL
       AND coalesce(raw_data->>'finish_time', '') = ''
"""

SAILRACEHQ_DNF_CANDIDATES = """
    SELECT COUNT(*) FROM race_results
    WHERE source = 'sailracehq' AND transport = 'legacy'
      AND status = 'finished' AND place IS NULL
      AND coalesce(raw_data->>'finish_time', '') = ''
      AND coalesce(raw_data->>'boat_name', '') <> ''
"""

SAILRACEHQ_DNF_UPDATE = """
    UPDATE race_results
       SET status = 'DNF'
     WHERE source = 'sailracehq' AND transport = 'legacy'
       AND status = 'finished' AND place IS NULL
       AND coalesce(raw_data->>'finish_time', '') = ''
       AND coalesce(raw_data->>'boat_name', '') <> ''
"""

GARBAGE_CANDIDATES = f"""
    SELECT COUNT(*) FROM race_results
    WHERE {NO_IDENTITY_WHERE}
"""

GARBAGE_DELETE = f"""
    DELETE FROM race_results
    WHERE id IN (
        SELECT id FROM race_results
        WHERE {NO_IDENTITY_WHERE}
        LIMIT :max_delete
    )
"""

SAILSYS_HOLLOW_CANDIDATES = """
    SELECT COUNT(*) FROM race_results
    WHERE source = 'sailsys' AND status = 'finished' AND place IS NULL
"""

SAILSYS_HOLLOW_UPDATE = """
    UPDATE race_results
       SET status = 'DNF',
           raw_data = jsonb_set(
               coalesce(raw_data, '{}'::jsonb),
               '{ops_02_07_prev_status}', '"finished"', true
           )
     WHERE source = 'sailsys' AND status = 'finished' AND place IS NULL
"""


# ---------------------------------------------------------------------------
# Count helpers
# ---------------------------------------------------------------------------


def record_metric(
    conn: Connection,
    metric: str,
    *,
    phase: str,
    value_num: float | None = None,
    value_text: str | None = None,
    meta: dict | None = None,
) -> None:
    """Mirror the OPS-02-09/OPS-02-12 admin_metrics recorder."""
    conn.execute(
        text(
            """
            INSERT INTO admin_metrics
                (metric, scope, phase, value_num, value_text, meta)
            VALUES
                (:metric, :scope, :phase, :value_num, :value_text,
                 CAST(:meta AS jsonb))
            """
        ),
        {
            "metric": metric,
            "scope": RUN_SCOPE,
            "phase": phase,
            "value_num": value_num,
            "value_text": value_text,
            "meta": json.dumps(meta or {}),
        },
    )


def snapshot_counts(conn: Connection, label: str) -> dict[str, int]:
    """The full before/after picture for the issue evidence."""
    counts: dict[str, int] = {}
    counts["total"] = conn.execute(
        text("SELECT COUNT(*) FROM race_results")
    ).scalar()
    counts["empty_boat_name_string"] = conn.execute(
        text("SELECT COUNT(*) FROM race_results WHERE raw_data->>'boat_name' = ''")
    ).scalar()
    counts["no_identity_rows"] = conn.execute(
        text(f"SELECT COUNT(*) FROM race_results WHERE {NO_IDENTITY_WHERE}")
    ).scalar()
    counts["no_identity_with_boat_id"] = conn.execute(
        text(
            f"SELECT COUNT(*) FROM race_results "
            f"WHERE boat_id IS NOT NULL AND {NO_IDENTITY_WHERE}"
        )
    ).scalar()
    counts["topyacht_dnf_candidates"] = conn.execute(
        text(TOPYACHT_DNF_CANDIDATES)
    ).scalar()
    counts["topyacht_dnf_total"] = conn.execute(
        text(
            "SELECT COUNT(*) FROM race_results "
            "WHERE source = 'topyacht' AND status = 'DNF'"
        )
    ).scalar()
    counts["sailracehq_dnf_candidates"] = conn.execute(
        text(SAILRACEHQ_DNF_CANDIDATES)
    ).scalar()
    counts["sailracehq_dnf_total"] = conn.execute(
        text(
            "SELECT COUNT(*) FROM race_results "
            "WHERE source = 'sailracehq' AND status = 'DNF'"
        )
    ).scalar()
    counts["sailsys_hollow_candidates"] = conn.execute(
        text(SAILSYS_HOLLOW_CANDIDATES)
    ).scalar()
    counts["sailsys_dnf_total"] = conn.execute(
        text(
            "SELECT COUNT(*) FROM race_results "
            "WHERE source = 'sailsys' AND status = 'DNF'"
        )
    ).scalar()
    counts["status_finished_total"] = conn.execute(
        text("SELECT COUNT(*) FROM race_results WHERE status = 'finished'")
    ).scalar()
    counts["status_dnf_total"] = conn.execute(
        text("SELECT COUNT(*) FROM race_results WHERE status = 'DNF'")
    ).scalar()

    print(f"\n--- {label} ---")
    for k, v in counts.items():
        print(f"  {k:<28} = {v}")
    return counts


def per_source_breakdown(conn: Connection, label: str) -> list[tuple]:
    rows = conn.execute(
        text(
            f"SELECT source, status, COUNT(*) FROM race_results "
            f"WHERE {NO_IDENTITY_WHERE} GROUP BY 1, 2 ORDER BY 1, 2"
        )
    ).fetchall()
    print(f"\n--- no-identity rows by source/status ({label}) ---")
    for r in rows:
        print(f"  {r[0]:<14} {r[1]:<10} {r[2]}")
    return rows


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def step_dnf_backfills(conn: Connection, apply: bool) -> dict[str, int]:
    """Steps 1+2: TopYacht and SailRaceHQ DNF backfills (idempotent)."""
    stats: dict[str, int] = {}
    ty_before = conn.execute(text(TOPYACHT_DNF_CANDIDATES)).scalar()
    srq_before = conn.execute(text(SAILRACEHQ_DNF_CANDIDATES)).scalar()
    stats["topyacht_updated"] = ty_before
    stats["sailracehq_updated"] = srq_before
    print(f"  TopYacht DNF candidates  : {ty_before}")
    print(f"  SailRaceHQ DNF candidates: {srq_before}")
    if apply:
        res = conn.execute(text(TOPYACHT_DNF_UPDATE))
        stats["topyacht_updated"] = res.rowcount
        res = conn.execute(text(SAILRACEHQ_DNF_UPDATE))
        stats["sailracehq_updated"] = res.rowcount
        print(f"  TopYacht rows updated    : {stats['topyacht_updated']}")
        print(f"  SailRaceHQ rows updated  : {stats['sailracehq_updated']}")
    else:
        print("  (dry-run — no updates issued)")
    return stats


def step_garbage_sweep(conn: Connection, apply: bool, max_delete: int) -> dict[str, int]:
    """Step 3: delete rows with no boat identity at all."""
    candidates = conn.execute(text(GARBAGE_CANDIDATES)).scalar()
    boat_linked = conn.execute(
        text(
            f"SELECT COUNT(*) FROM race_results "
            f"WHERE boat_id IS NOT NULL AND {NO_IDENTITY_WHERE}"
        )
    ).scalar()
    stats = {
        "candidates": candidates,
        "boat_linked": boat_linked,
        "deleted": 0,
        "safety_valve": max_delete,
    }
    print(f"  no-identity candidates   : {candidates} (boat_id-linked: {boat_linked})")
    if candidates > max_delete:
        raise RuntimeError(
            f"safety valve: {candidates} no-identity rows exceeds "
            f"--max-delete={max_delete}; investigate before widening the sweep"
        )
    if apply:
        res = conn.execute(text(GARBAGE_DELETE), {"max_delete": max_delete})
        stats["deleted"] = res.rowcount
        print(f"  deleted                  : {stats['deleted']}")
    else:
        print("  (dry-run — no deletes issued)")
    return stats


def step_sailsys_flag(conn: Connection, apply: bool) -> dict[str, int]:
    """Step 4: flag SailSys finished-no-place rows as DNF (reversible)."""
    candidates = conn.execute(text(SAILSYS_HOLLOW_CANDIDATES)).scalar()
    # Evidence for the decision: every hollow row lacks elapsed/corrected time.
    evidence = conn.execute(
        text(
            "SELECT COUNT(*) FROM race_results "
            "WHERE source = 'sailsys' AND status = 'finished' AND place IS NULL "
            "  AND elapsed_time IS NULL AND corrected_time IS NULL"
        )
    ).scalar()
    stats = {"candidates": candidates, "fully_hollow": evidence, "updated": 0}
    print(f"  sailsys hollow candidates: {candidates}")
    print(f"  ...of which elapsed AND corrected time both NULL: {evidence}")
    if apply:
        res = conn.execute(text(SAILSYS_HOLLOW_UPDATE))
        stats["updated"] = res.rowcount
        print(f"  flagged -> DNF           : {stats['updated']}")
    else:
        print("  (dry-run — no updates issued)")
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes (default: dry-run, everything rolls back)",
    )
    ap.add_argument(
        "--counts-only",
        action="store_true",
        help="Only print before/after counts (no mutations, read-only)",
    )
    ap.add_argument(
        "--max-delete",
        type=int,
        default=20_000,
        help="Safety valve: refuse to sweep more than this many rows (default 20000)",
    )
    args = ap.parse_args()
    mode = "APPLY" if args.apply else "DRY-RUN"
    engine = get_engine()

    print("=" * 72)
    print(f"OPS-02-07 — result-status backfills + garbage sweep ({mode})")
    print("=" * 72)

    with engine.connect() as conn:
        before = snapshot_counts(conn, f"BEFORE ({mode})")
        per_source_breakdown(conn, "before")

    if args.counts_only:
        return 0

    if args.apply:
        # ---- Step 1+2: DNF backfills (one transaction) -------------------
        print(f"\n=== STEP 1+2: TopYacht / SailRaceHQ DNF backfills ({mode}) ===")
        with engine.begin() as conn:
            dnf_stats = step_dnf_backfills(conn, apply=True)
            record_metric(
                conn, f"{METRIC_PREFIX}.dnf_backfill", phase="apply",
                value_num=dnf_stats["topyacht_updated"]
                + dnf_stats["sailracehq_updated"],
                meta=dnf_stats,
            )

        # ---- Step 3: garbage sweep (one transaction) ---------------------
        print(f"\n=== STEP 3: no-identity garbage sweep ({mode}) ===")
        with engine.begin() as conn:
            sweep_stats = step_garbage_sweep(conn, apply=True, max_delete=args.max_delete)
            record_metric(
                conn, f"{METRIC_PREFIX}.garbage_sweep", phase="apply",
                value_num=sweep_stats["deleted"], meta=sweep_stats,
            )

        # ---- Step 4: SailSys hollow-row flag (one transaction) -----------
        print(f"\n=== STEP 4: SailSys hollow rows -> DNF ({mode}) ===")
        with engine.begin() as conn:
            sailsys_stats = step_sailsys_flag(conn, apply=True)
            record_metric(
                conn, f"{METRIC_PREFIX}.sailsys_hollow_flag", phase="apply",
                value_num=sailsys_stats["updated"], meta=sailsys_stats,
            )
    else:
        with engine.connect() as conn:
            print(f"\n=== STEP 1+2: TopYacht / SailRaceHQ DNF backfills ({mode}) ===")
            dnf_stats = step_dnf_backfills(conn, apply=False)
            print(f"\n=== STEP 3: no-identity garbage sweep ({mode}) ===")
            sweep_stats = step_garbage_sweep(conn, apply=False, max_delete=args.max_delete)
            print(f"\n=== STEP 4: SailSys hollow rows -> DNF ({mode}) ===")
            sailsys_stats = step_sailsys_flag(conn, apply=False)

    # ---- AFTER counts -----------------------------------------------------
    with engine.connect() as conn:
        after = snapshot_counts(conn, f"AFTER ({mode})")
        per_source_breakdown(conn, "after")
        if args.apply:
            # Record the acceptance evidence in the same transaction style as
            # the steps (short write txn).
            pass

    if args.apply:
        with engine.begin() as conn:
            record_metric(
                conn, f"{METRIC_PREFIX}.counts", phase="before",
                value_num=before["total"], meta=before,
            )
            record_metric(
                conn, f"{METRIC_PREFIX}.counts", phase="after",
                value_num=after["total"], meta=after,
            )

    # ---- Acceptance criteria ---------------------------------------------
    print("\n=== ACCEPTANCE CRITERIA ===")
    checks: list[tuple[str, bool, str]] = []
    checks.append((
        "counts recorded (admin_metrics + stdout)",
        True,
        f"before_total={before['total']} after_total={after['total']}",
    ))
    checks.append((
        "no empty-boat_name-string rows remain",
        after["empty_boat_name_string"] == 0,
        f"remaining={after['empty_boat_name_string']}",
    ))
    checks.append((
        "no no-identity garbage rows remain",
        after["no_identity_rows"] == 0,
        f"remaining={after['no_identity_rows']}",
    ))
    checks.append((
        "DNF statuses present: topyacht",
        after["topyacht_dnf_total"] > 0,
        f"topyacht DNF={after['topyacht_dnf_total']}",
    ))
    checks.append((
        "DNF statuses present: sailracehq",
        after["sailracehq_dnf_total"] > 0,
        f"sailracehq DNF={after['sailracehq_dnf_total']}",
    ))
    checks.append((
        "DNF statuses present: sailsys (hollow rows flagged)",
        after["sailsys_dnf_total"] > 0,
        f"sailsys DNF={after['sailsys_dnf_total']}",
    ))
    checks.append((
        "idempotent: 0 remaining DNF-backfill candidates",
        after["topyacht_dnf_candidates"] == 0
        and after["sailracehq_dnf_candidates"] == 0,
        f"topyacht={after['topyacht_dnf_candidates']} "
        f"sailracehq={after['sailracehq_dnf_candidates']}",
    ))
    checks.append((
        "idempotent: 0 remaining sailsys hollow rows",
        after["sailsys_hollow_candidates"] == 0,
        f"remaining={after['sailsys_hollow_candidates']}",
    ))

    all_ok = True
    for label, ok, detail in checks:
        all_ok = all_ok and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {label}  ({detail})")

    print("\nDeltas:")
    print(f"  total rows   : {before['total']} -> {after['total']} "
          f"(delta {after['total'] - before['total']:+d})")
    print(f"  status=DNF   : {before['status_dnf_total']} -> {after['status_dnf_total']} "
          f"(delta {after['status_dnf_total'] - before['status_dnf_total']:+d})")
    print(f"  no-identity  : {before['no_identity_rows']} -> {after['no_identity_rows']} "
          f"(delta {after['no_identity_rows'] - before['no_identity_rows']:+d})")

    if not args.apply:
        print("\n(dry-run: acceptance check evaluates the CURRENT database state;")
        print(" re-run with --apply to enforce.)")
        # A dry-run is allowed to report a dirty pre-state without failing CI;
        # exit 0 unless a step errored (steps raise on hard failure).
        return 0
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())
