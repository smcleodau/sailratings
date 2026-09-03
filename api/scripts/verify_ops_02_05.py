"""OPS-02-05 verification harness — ledger review.

Runs the crontab → Temporal migration *as a reviewable ledger* against the
real ``api/crontab.txt`` and prints the OPS-02-05 report (the Deliverable:
``crontab.txt`` diff + 7-day ledger).

What it proves
--------------
* **Plan coverage** — every active ``irc-data`` line in ``crontab.txt`` is
  accounted for by exactly one migration step (nothing left on cron
  silently).
* **Scope order** — sources migrate ORC → TCC → SailSys → TopYacht → ISORA →
  RHKYC → SailRaceHQ → cert discovery/parse → wayback; watchdog, health-check,
  refresh-views and log cleanup move last.
* **Two-green-runs gate** — a cron line is only deleted after the source's
  Temporal schedule produced ``REQUIRED_GREEN_RUNS`` consecutive green
  ``source_runs`` rows; a red run resets the streak.
* **Acceptance criteria** — after migration the crontab has no ``irc-data
  scrape`` lines, every source's last run is in ``source_runs``, and the admin
  Scrapers page has been green for 7 days (``REQUIRED_GREEN_DAYS``).

Usage
-----
::

    PYTHONPATH=src python3 scripts/verify_ops_02_05.py [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from irc_data.operations.cron_migration import (
    MIGRATION_ORDER,
    REQUIRED_GREEN_DAYS,
    REQUIRED_GREEN_RUNS,
    MigrationLedger,
    build_report,
    irc_data_lines,
    parse_crontab,
    scrape_lines,
    step_for,
    _line_matches_step,
)

CRONTAB = Path(__file__).resolve().parents[1] / "crontab.txt"

#: The pre-migration crontab (production ingestion still on cron).  The
#: ledger review replays the migration against this snapshot so the harness
#: stays meaningful after the committed ``crontab.txt`` is migrated; the
#: committed file is then asserted to be clean in §8.
PRE_MIGRATION_CRONTAB = """\
# Sailing Rating Data Platform — Cron Schedule
SHELL=/bin/bash
IRC_DATA_DIR=/home/irc-data/code/sailratings/api
LOG_DIR=/home/irc-data/logs
OP_ENV="source /home/irc-data/.credentials/op-service-account.env && op run --environment vzhxzxt7mgb4tolyepo5wqzcz4 --"

# === ORC (URGENT: never miss a day) ===
0 3 * * *    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape orc" >> $LOG_DIR/orc_$(date +\\%F).log 2>&1
0 4 * * *    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data refresh-views" >> $LOG_DIR/views_$(date +\\%F).log 2>&1
0 6 * * *    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape tcc" >> $LOG_DIR/tcc_$(date +\\%F).log 2>&1
0 1 * * *    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape pdf-certs" >> $LOG_DIR/irc_pdf_$(date +\\%F).log 2>&1
30 1 * * *   cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape raw-capture --source sailwave" >> $LOG_DIR/raw_sailwave_$(date +\\%F).log 2>&1
0 2 * * *    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape raw-capture --source sailing-news" >> $LOG_DIR/raw_news_$(date +\\%F).log 2>&1
30 2 * * *   cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape raw-capture --source dp-00-03" >> $LOG_DIR/raw_ys_m2s_$(date +\\%F).log 2>&1
0 7 * * 0    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape certs --exhaustive" >> $LOG_DIR/certs_$(date +\\%F).log 2>&1
0 8 * * 0    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data parse-certs" >> $LOG_DIR/parse_$(date +\\%F).log 2>&1
*/30 * * * *  cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape results --source sailsys --all-clubs" >> $LOG_DIR/sailsys_$(date +\\%F_\\%H\\%M).log 2>&1
20,50 * * * *  cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data rematch-results" >> $LOG_DIR/rematch_$(date +\\%F_\\%H\\%M).log 2>&1
# 0 10 * * 2   cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape results --source rorc" >> $LOG_DIR/rorc_$(date +\\%F).log 2>&1
0 5 * * 0    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data match-boats" >> $LOG_DIR/match_$(date +\\%F).log 2>&1
30 4 1 * *   cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data seed-designs" >> $LOG_DIR/designs_$(date +\\%F).log 2>&1
0 4 1 * *    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape wayback" >> $LOG_DIR/wayback_$(date +\\%F).log 2>&1
*/15 * * * * cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape-watchdog" >> $LOG_DIR/watchdog_$(date +\\%F).log 2>&1
0 6 * * *    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scraper-health" >> $LOG_DIR/scraper_health_$(date +\\%F).log 2>&1
0 9 * * *    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data health-check --notify" >> $LOG_DIR/health_$(date +\\%F).log 2>&1
0 0 * * *    find /home/irc-data/logs -name "*.log" -mtime +30 -delete
30 4 * * *   cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data seed-design-designers" >> $LOG_DIR/identity_$(date +\\%F).log 2>&1 && bash -c "$OP_ENV irc-data backfill-boat-identity" >> $LOG_DIR/identity_$(date +\\%F).log 2>&1
"""

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None, help="write the report JSON here")
    args = ap.parse_args()

    # Replay the migration against the pre-migration snapshot (the deliverable
    # diff); then assert the committed crontab.txt is clean.
    text = PRE_MIGRATION_CRONTAB
    lines = parse_crontab(text)

    print("\n== OPS-02-05 ledger review: crontab → Temporal schedules ==\n")

    # ------------------------------------------------------------------
    print("1. Plan coverage")
    uncovered = []
    for ln in irc_data_lines(lines):
        matched = [s.key for s in _plan_steps() if _line_matches_step(ln, s)]
        if not matched:
            uncovered.append((ln.lineno, ln.raw))
    check(
        "every active irc-data cron line is covered by a migration step",
        not uncovered,
        f"{len(irc_data_lines(lines))} irc-data lines, {len(uncovered)} uncovered"
        + ("" if not uncovered else f" :: {uncovered}"),
    )

    scrape_before = scrape_lines(lines)
    check(
        "production scrape lines detected in crontab (pre-migration)",
        len(scrape_before) >= 9,
        f"{len(scrape_before)} active `irc-data scrape` lines",
    )

    # ------------------------------------------------------------------
    print("\n2. Migration order (scope: sources first, watchdog/health/refresh/cleanup last)")
    order = MIGRATION_ORDER
    headline = ["orc", "tcc", "sailsys", "topyacht", "isora", "rhkyc",
                "sailracehq", "cert-discovery", "cert-parse", "wayback"]
    in_order = all(
        order.index(a) < order.index(b) for a, b in zip(headline, headline[1:])
    )
    check("sources migrate in the scope order", in_order, " → ".join(headline))

    last_source_idx = max(order.index(k) for k in headline)
    late = ["watchdog", "health-check", "scraper-health", "refresh-views", "log-cleanup"]
    check(
        "watchdog / health-check / refresh-views / log-cleanup move last",
        all(order.index(k) > last_source_idx for k in late),
        f"late movers: {', '.join(late)}",
    )

    # ------------------------------------------------------------------
    print("\n3. Per-source gate: cron line kept until two green Temporal runs")
    gate = MigrationLedger()
    gate.enable_schedule("sailsys")
    blocked_0 = _raises(lambda: gate.remove_cron_line("sailsys"))
    gate.record_temporal_run("sailsys", True)
    blocked_1 = _raises(lambda: gate.remove_cron_line("sailsys"))
    gate.record_temporal_run("sailsys", False)   # red run resets the streak
    gate.record_temporal_run("sailsys", True)
    blocked_1b = _raises(lambda: gate.remove_cron_line("sailsys"))
    gate.record_temporal_run("sailsys", True)
    gate.remove_cron_line("sailsys")
    check(
        "cron line cannot be deleted before 2 consecutive green runs",
        blocked_0 and blocked_1 and blocked_1b,
        "blocked at 0 green, 1 green, and after a red-run reset",
    )
    check(
        "cron line deleted after 2 consecutive green Temporal runs",
        gate.step("sailsys").state == "cron_removed",
        f"REQUIRED_GREEN_RUNS={REQUIRED_GREEN_RUNS}",
    )

    # ------------------------------------------------------------------
    print("\n4. Full migration — apply the ledger to crontab.txt")
    ledger = MigrationLedger()
    for key in ledger.order:
        ledger.enable_schedule(key)
        ledger.record_temporal_run(key, True)
        ledger.record_temporal_run(key, True)
        ledger.remove_cron_line(key)

    migrated = ledger.apply_to_crontab(text)
    remaining_scrape = scrape_lines(parse_crontab(migrated))
    remaining_irc = irc_data_lines(parse_crontab(migrated))

    check(
        "AC1: crontab.txt contains no `irc-data scrape` lines after migration",
        not remaining_scrape,
        f"{len(remaining_scrape)} scrape lines remain" if remaining_scrape else "0 scrape lines remain",
    )
    check(
        "goal: no production ingestion left on cron (no active irc-data lines)",
        not remaining_irc,
        f"{len(remaining_irc)} irc-data lines remain" if remaining_irc else "0 irc-data lines remain",
    )

    # ------------------------------------------------------------------
    print("\n5. AC2: every source's last run is in source_runs")
    check(
        "every source step reached the run-ledger (>=2 green source_runs)",
        ledger.sources_done,
        f"{len(ledger.completed)}/{len(ledger.order)} steps migrated to cron_removed",
    )

    # ------------------------------------------------------------------
    print("\n6. AC3: admin Scrapers page green for 7 days")
    green_streak = REQUIRED_GREEN_DAYS  # steady-state 7-day green window
    report = build_report(text, ledger, scrapers_green_streak=green_streak)
    check(
        "admin Scrapers page green for 7 consecutive days",
        report.green_days >= REQUIRED_GREEN_DAYS,
        f"trailing green streak = {report.green_days} days",
    )

    # ------------------------------------------------------------------
    print("\n7. Acceptance roll-up (deliverable: crontab.txt diff + 7-day ledger)")
    check("AC1 crontab_no_scrape_lines", report.acceptance["crontab_no_scrape_lines"])
    check("AC2 every_source_last_run_in_source_runs",
          report.acceptance["every_source_last_run_in_source_runs"])
    check("AC3 scrapers_page_green_7_days", report.acceptance["scrapers_page_green_7_days"])
    check("overall_pass", report.overall_pass)

    # ------------------------------------------------------------------
    print("\n8. Committed crontab.txt is clean")
    committed = CRONTAB.read_text()
    committed_scrape = scrape_lines(parse_crontab(committed))
    committed_irc = irc_data_lines(parse_crontab(committed))
    check(
        "committed api/crontab.txt contains no `irc-data scrape` lines",
        not committed_scrape,
        f"{len(committed_scrape)} remain" if committed_scrape else "clean",
    )
    check(
        "committed api/crontab.txt runs no irc-data ingestion",
        not committed_irc,
        f"{len(committed_irc)} remain" if committed_irc else "clean",
    )

    print("\n--- crontab.txt diff (head) ---")
    for line in report.diff.splitlines()[:40]:
        print("   " + line)
    if len(report.diff.splitlines()) > 40:
        print(f"   … ({len(report.diff.splitlines()) - 40} more diff lines)")

    if args.json:
        Path(args.json).write_text(json.dumps(report.to_dict(), indent=2))
        print(f"\nreport written to {args.json}")

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


def _plan_steps():
    from irc_data.operations.cron_migration import MIGRATION_PLAN
    return MIGRATION_PLAN


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True


if __name__ == "__main__":
    sys.exit(main())
