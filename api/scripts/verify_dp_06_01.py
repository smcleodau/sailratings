#!/usr/bin/env python3
"""Evidence generator for DP-06-01 — first source vertical-slice selection.

DP-06-01 is a *decision* ticket, not a code-change ticket.  Its acceptance
criteria are that the decision names the selected source, one backup, the exact
outcome, success metrics and the approved policy.  This script produces the
**hard, reproducible evidence** a human reviewer uses to verify the
recommendation:

  1. Register rulings (DP-01-01) — every slice/backup source is ``approved``
     under policy ``v1.0`` with its adapter/licensing status.
  2. Policy rulings (DP-01-02) — the current policy version and the ORC
     scope-limit (public ``data.orc.org`` JSON API only).
  3. **DP-00 capture volumes** — the direct evidence for the choice, measured
     live from the platform database (skip with ``--offline``).
  4. Cross-coverage joins — boats that have rating + results + identity
     together (the load-bearing numbers for the RAI value proposition).
  5. Success-metric baselines (M1–M4) so the slice exit targets are anchored.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_06_01.py            # live DB evidence
    PYTHONPATH=src python3 scripts/verify_dp_06_01.py --offline  # register/policy only

Exit code 0 when every slice + backup source resolves ``approved`` under the
current policy; non-zero otherwise (the decision would not be enforceable).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

#: The decision this ticket records.
SELECTED = {
    "irc-tcc": "IRC certificate source (TCC listings, CSV)",
    "irc-certs": "IRC certificate source (PDFs, derived data only)",
    "orc": "ORC certificate source (public data.orc.org JSON API only)",
    "sailsys": "Results platform feeding RAI",
}
BACKUP = {"topyacht": "Backup results platform for RAI"}

DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://irc:irc@localhost:5433/irc_data"
)


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def check_register() -> bool:
    """Part 1+2: every selected/backup source must be approved under v1.0."""
    from irc_data.sources.policy import CURRENT_POLICY_VERSION
    from irc_data.sources.registry import get_source

    _hr("1. Register rulings (DP-01-01) + policy gate (DP-01-02)")
    print(f"Current approved policy version: {CURRENT_POLICY_VERSION}\n")
    print(f"{'slug':<12} {'legal_status':<12} {'enabled':<8} {'policy':<9} "
          f"{'adapter':<11} {'licensing':<14} role")
    ok = True
    for slug, role in {**SELECTED, **BACKUP}.items():
        s = get_source(None, slug)
        status = getattr(s, "legal_status", None)
        status = status.value if hasattr(status, "value") else status
        enabled = getattr(s, "enabled", None)
        policy = getattr(s, "policy_version", None)
        adapter = getattr(s, "adapter_status", None) or "-"
        licensing = getattr(s, "licensing", None) or "-"
        good = (status == "approved" and enabled and policy == CURRENT_POLICY_VERSION)
        ok = ok and good
        print(f"{slug:<12} {status:<12} {str(enabled):<8} {str(policy):<9} "
              f"{adapter:<11} {licensing:<14} {role}  {'OK' if good else 'FAIL'}")
    return ok


def db_evidence() -> None:
    """Parts 3-5: live DP-00 capture volumes + cross-coverage joins."""
    from sqlalchemy import create_engine, text

    eng = create_engine(DB_URL)

    def q(sql: str):
        with eng.connect() as c:
            try:
                return c.execute(text(sql)).fetchall()
            except Exception as e:  # pragma: no cover - evidence helper
                return [("ERR", str(e).splitlines()[0])]

    _hr("3. DP-00 capture volumes (direct evidence for the choice)")
    volumes = [
        ("IRC TCC snapshots", "SELECT count(*) FROM tcc_snapshots"),
        ("IRC certificate PDFs parsed", "SELECT count(*) FROM irc_certificates"),
        ("ORC certificates", "SELECT count(*) FROM orc_certificates"),
        ("Race results (all sources)", "SELECT count(*) FROM race_results"),
        ("Distinct boats with results", "SELECT count(DISTINCT boat_id) FROM race_results"),
    ]
    for label, sql in volumes:
        print(f"  {label:<38} {q(sql)[0][0]}")

    print("\n  Race results by source:")
    for r in q("SELECT source, count(*), min(event_date), max(event_date) "
               "FROM race_results GROUP BY 1 ORDER BY 2 DESC LIMIT 8"):
        print(f"    {r[0]:<14} {r[1]:>8}   {r[2]} .. {r[3]}")

    _hr("4. Cross-coverage joins (rating + results + identity on one boat)")
    joins = [
        ("M1  SailSys boats with an IRC TCC snapshot",
         "SELECT count(DISTINCT rr.boat_id) FROM race_results rr "
         "JOIN tcc_snapshots t ON t.boat_id = rr.boat_id WHERE rr.source='sailsys'"),
        ("M2  SailSys boats with an ORC certificate",
         "SELECT count(DISTINCT rr.boat_id) FROM race_results rr "
         "JOIN orc_certificates o ON o.boat_id = rr.boat_id WHERE rr.source='sailsys'"),
        ("M3  Boats in BOTH IRC and ORC registers",
         "SELECT count(DISTINCT t.boat_id) FROM tcc_snapshots t "
         "JOIN orc_certificates o ON o.boat_id = t.boat_id"),
    ]
    for label, sql in joins:
        print(f"  {label:<46} {q(sql)[0][0]}")

    _hr("5. Success-metric baselines (anchor for slice exit targets)")
    print(f"  M1 baseline (SailSys+IRC)      {q(joins[0][1])[0][0]}   target >= 1500")
    print(f"  M2 baseline (SailSys+ORC)      {q(joins[1][1])[0][0]}   target >= 1100")
    print(f"  M3 baseline (dual-rated)       {q(joins[2][1])[0][0]}  target >= 2000")
    fresh = q("SELECT count(*), max(completed_at) FROM ingestion_log "
              "WHERE source='sailsys' AND completed_at > now() - interval '7 days'")
    print(f"  M8 SailSys 30-min feed, 7d     {fresh[0][0]} runs, latest {fresh[0][1]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="skip live DB capture-volume evidence")
    args = ap.parse_args()

    print("DP-06-01 - First source vertical-slice selection (IRC + ORC pair)")
    print("DECISION: irc-tcc + irc-certs (IRC) / orc public JSON API (ORC) / "
          "sailsys (RAI results).  Backup: topyacht.")

    ok = check_register()
    if not args.offline:
        db_evidence()

    _hr("VERDICT")
    if ok:
        print("PASS - selected slice + backup all resolve 'approved' under policy v1.0;")
        print("decision is enforceable at the collection gate.")
        return 0
    print("FAIL - one or more slice/backup sources are not approved under current policy.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
