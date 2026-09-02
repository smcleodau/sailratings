#!/usr/bin/env python3
"""End-to-end verification evidence for DP-03-05 — canonical database
migrations and compatibility tests.

Runs the full acceptance-criteria flow against a *throwaway* PostgreSQL
database (never the dev/prod DB) and prints hard, paste-able evidence:

  1. Canonical graph  — single head, linear chain, no duplicate/prefix ids.
  2. Upgrade path      — provision at the *previous supported schema*, seed a
                         production-sized synthetic dataset, upgrade to head.
  3. Integrity         — per-table counts + order-independent MD5 content
                         hashes preserved across the migration; the risky
                         0022 3NF backfill links every race_result.
  4. Compatibility     — the ``v1_*`` consumer views answer representative
                         queries.
  5. Budget            — the migration completes within the time budget.
  6. Rollback/restore  — the additive capstone revision downgrades (views +
                         bookkeeping dropped, user data intact) and re-upgrades
                         (restored, hashes identical).

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_03_05.py

Environment overrides:
    DP03_ADMIN_DATABASE_URL   admin/maintenance URL (default: derived from
                              IRC_DATABASE_URL / DATABASE_URL)
    DP03_MIGRATION_BUDGET_SECONDS   migration time budget (default 120)
    DP03_N_BOATS / DP03_N_SNAPSHOTS / DP03_N_EVENTS / DP03_N_ENTRIES /
    DP03_N_RESULTS / DP03_N_ASSERTIONS   dataset sizes (production-scale
                              defaults)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the package is importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from irc_data.db import migration_verify as mv  # noqa: E402


def _banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    _banner("DP-03-05 — canonical migrations & compatibility verification")
    print(f"  previous supported schema : {mv.PREVIOUS_SUPPORTED_REVISION}")
    print(f"  canonical head            : {mv.CANONICAL_HEAD}")
    print(f"  budget (s)                : {mv.DEFAULT_BUDGET_SECONDS:.0f}")
    print(
        "  synthetic dataset         : "
        f"boats={mv.N_BOATS} snapshots={mv.N_SNAPSHOTS} events={mv.N_EVENTS} "
        f"entries={mv.N_ENTRIES} results={mv.N_RESULTS} assertions={mv.N_ASSERTIONS}"
    )

    failures: list[str] = []

    def expect(cond: bool, label: str) -> None:
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures.append(label)

    try:
        ev = mv.run_full_verification()
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"\n  RESULT: ERROR — verification raised: {exc}")
        return 2

    # --- 1. canonical graph -------------------------------------------------
    _banner("1. CANONICAL MIGRATION GRAPH")
    print(f"  heads: {ev.heads}")
    print(f"  chain length: {len(ev.revision_chain)} revisions")
    expect(ev.heads == ["0026"], "single canonical head == 0026")
    expect(ev.linear, "graph is linear, no duplicate revision ids")

    # --- 2/3. upgrade path + integrity --------------------------------------
    _banner("2-3. UPGRADE FROM PREVIOUS SCHEMA + COUNTS / HASHES / QUERIES")
    print(f"  seeded (pre-migration): {ev.seeded_counts}")
    print(f"  migration_seconds: {ev.migration_seconds:.2f}")
    for note in ev.notes:
        print(f"  · {note}")
    expect(ev.counts_match, "user-data table COUNTS preserved across migration")
    expect(ev.hashes_match, "user-data table content HASHES preserved + backfill complete")
    print("  consumer queries (row counts):")
    for q, n in ev.consumer_queries.items():
        print(f"    {q}: {n}")
        expect(n > 0, f"compatibility query '{q}' returns rows")

    # --- 5. budget -----------------------------------------------------------
    _banner("5. MIGRATION TIME BUDGET")
    print(
        f"  migration_seconds={ev.migration_seconds:.2f}  "
        f"budget={ev.budget_seconds:.0f}  -> {'within' if ev.within_budget() else 'OVER'} budget"
    )
    expect(ev.within_budget(), "migration completes within budget")

    # --- 6. rollback / restore ----------------------------------------------
    _banner("6. ROLLBACK / RESTORE STRATEGY")
    expect(ev.rollback_ok, "capstone downgrade+restore is data-preserving")

    # --- summary -------------------------------------------------------------
    _banner("SUMMARY")
    print(f"  heads={ev.heads} linear={ev.linear}")
    print(f"  counts_match={ev.counts_match} hashes_match={ev.hashes_match}")
    print(f"  migration_seconds={ev.migration_seconds:.2f} within_budget={ev.within_budget()}")
    print(f"  rollback_ok={ev.rollback_ok}")
    print(f"  total rows seeded: {sum(ev.seeded_counts.values())}")

    if failures:
        print(f"\n  RESULT: FAIL — {len(failures)} expectation(s) failed")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("\n  RESULT: PASS — canonical chain, integrity, budget, rollback all verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
