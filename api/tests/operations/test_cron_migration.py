"""OPS-02-05 — crontab → Temporal schedules, one source at a time.

Verification criterion from the issue: **"Ledger review."**

These tests exercise the migration plan/ledger
(:mod:`irc_data.operations.cron_migration`) against the *real*
``api/crontab.txt`` and assert the three acceptance criteria:

1. ``crontab.txt`` contains no ``irc-data scrape`` lines (after migration).
2. Every source's last run is in ``source_runs`` — modelled by the ledger
   requiring ``REQUIRED_GREEN_RUNS`` consecutive green Temporal runs before a
   cron line may be removed.
3. Admin Scrapers page green for 7 days — the trailing green-day streak.

The scope's migration order (ORC, TCC, SailSys, TopYacht, ISORA, RHKYC,
SailRaceHQ, cert discovery/parse, wayback; watchdog/health-check/
refresh-views/log-cleanup last) is asserted explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from irc_data.operations.cron_migration import (
    MIGRATION_ORDER,
    MIGRATION_PLAN,
    REQUIRED_GREEN_DAYS,
    REQUIRED_GREEN_RUNS,
    MigrationLedger,
    build_report,
    irc_data_lines,
    parse_crontab,
    render_diff,
    remove_lines,
    scrape_lines,
    scrapers_page_green_days,
    step_for,
)

CRONTAB_PATH = (
    Path(__file__).resolve().parents[2] / "crontab.txt"
)


#: A representative *pre-migration* crontab (production ingestion still on
#: cron).  The classifier / coverage / order / ledger tests run against this
#: so they stay meaningful after the live ``crontab.txt`` is migrated.
PRE_MIGRATION_CRONTAB = """\
# Sailing Rating Data Platform — Cron Schedule
SHELL=/bin/bash
IRC_DATA_DIR=/home/irc-data/code/sailratings/api
LOG_DIR=/home/irc-data/logs
OP_ENV="source /home/irc-data/.credentials/op-service-account.env && op run --environment vzhxzxt7mgb4tolyepo5wqzcz4 --"

# === ORC (URGENT: never miss a day) ===
0 3 * * *    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data scrape orc" >> $LOG_DIR/orc_$(date +\\%F).log 2>&1
# Refresh materialized views after ORC ingestion
0 4 * * *    cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data refresh-views" >> $LOG_DIR/views_$(date +\\%F).log 2>&1
# === IRC ===
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
0 1 * * * /home/irc-data/scripts/sync-prod-db.sh >> /home/irc-data/logs/sync-prod-db.log 2>&1
@reboot nohup /home/irc-data/scripts/scrape-everything.sh >> /home/irc-data/logs/scrape-master.log 2>&1 &
30 4 * * *   cd $IRC_DATA_DIR && bash -c "$OP_ENV irc-data seed-design-designers" >> $LOG_DIR/identity_$(date +\\%F).log 2>&1 && bash -c "$OP_ENV irc-data backfill-boat-identity" >> $LOG_DIR/identity_$(date +\\%F).log 2>&1
"""


@pytest.fixture(scope="module")
def crontab_text() -> str:
    """The pre-migration crontab the plan/ledger tests operate on."""
    return PRE_MIGRATION_CRONTAB


@pytest.fixture(scope="module")
def live_crontab_text() -> str:
    """The committed ``api/crontab.txt`` (post-migration)."""
    assert CRONTAB_PATH.exists(), f"missing {CRONTAB_PATH}"
    return CRONTAB_PATH.read_text()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_every_active_irc_data_line_is_covered_by_a_step(crontab_text):
    """No production ``irc-data`` line is left unaccounted for in the plan."""
    lines = parse_crontab(crontab_text)
    from irc_data.operations.cron_migration import _line_matches_step

    for ln in irc_data_lines(lines):
        matched = [s.key for s in MIGRATION_PLAN if _line_matches_step(ln, s)]
        assert matched, f"cron line {ln.lineno} not covered by plan: {ln.raw!r}"


def test_scrape_lines_detected(crontab_text):
    """The classifier finds the active production ingestion lines."""
    lines = parse_crontab(crontab_text)
    scrapes = scrape_lines(lines)
    targets = {(ln.scrape_target, ln.source_flag) for ln in scrapes}
    # the headline sources must all be present as scrape lines
    assert ("orc", None) in targets
    assert ("tcc", None) in targets
    assert ("results", "sailsys") in targets
    assert ("wayback", None) in targets
    assert ("certs", None) in targets
    # and they are all *active* (not comments)
    assert all(ln.is_scrape for ln in scrapes)


def test_commented_rorc_line_is_not_a_scrape_line(crontab_text):
    """The commented-out RORC cron line must not be treated as active."""
    lines = parse_crontab(crontab_text)
    rorc = [ln for ln in lines if "rorc" in ln.raw and "scrape results" in ln.raw]
    assert rorc, "expected the commented RORC line to be present"
    assert all(not ln.is_scrape for ln in rorc)


# ---------------------------------------------------------------------------
# Migration order (scope)
# ---------------------------------------------------------------------------


def test_sources_migrate_in_scope_order_and_ops_last():
    order = MIGRATION_ORDER
    # headline source order per the scope
    for a, b in [
        ("orc", "tcc"),
        ("tcc", "sailsys"),
        ("sailsys", "topyacht"),
        ("topyacht", "isora"),
        ("isora", "rhkyc"),
        ("rhkyc", "sailracehq"),
        ("sailracehq", "cert-discovery"),
        ("cert-discovery", "cert-parse"),
        ("cert-parse", "wayback"),
    ]:
        assert order.index(a) < order.index(b), f"{a} should migrate before {b}"

    # late movers come after every source
    last_source_idx = max(order.index(k) for k in (
        "orc", "tcc", "sailsys", "topyacht", "isora", "rhkyc",
        "sailracehq", "cert-discovery", "cert-parse", "wayback",
    ))
    for late in ("watchdog", "health-check", "scraper-health", "refresh-views", "log-cleanup"):
        assert order.index(late) > last_source_idx, f"{late} must move last"


def test_every_register_source_step_has_a_schedule_id():
    """Source steps resolve to a Temporal schedule id ``source-<slug>``."""
    for key in ("orc", "tcc", "sailsys", "topyacht", "isora", "rhkyc",
                "sailracehq", "cert-discovery", "wayback"):
        step = step_for(key)
        assert step.register_slug, f"{key} should map to a register slug"
        # schedule id convention from the OPS-01-02 registry
        from irc_data.temporal.schedules.cadence import schedule_id_for_slug
        assert schedule_id_for_slug(step.register_slug) == f"source-{step.register_slug}"


# ---------------------------------------------------------------------------
# Per-source ledger: enable → two green runs → remove
# ---------------------------------------------------------------------------


def test_cron_line_kept_until_two_green_runs():
    """The scope invariant: cron line stays until two green Temporal runs."""
    ledger = MigrationLedger()
    ledger.enable_schedule("orc")

    step = ledger.step("orc")
    # zero green runs — cannot remove
    with pytest.raises(ValueError):
        ledger.remove_cron_line("orc")
    assert step.cron_line_present is True

    # one green run — still cannot remove
    ledger.record_temporal_run("orc", True)
    with pytest.raises(ValueError):
        ledger.remove_cron_line("orc")
    assert step.cron_line_present is True

    # second green run — now removable
    ledger.record_temporal_run("orc", True)
    assert step.ready_to_remove
    ledger.remove_cron_line("orc")
    assert step.cron_line_present is False
    assert step.state == "cron_removed"


def test_a_failed_run_resets_the_green_streak():
    """A red Temporal run means the two-green-runs clock restarts."""
    ledger = MigrationLedger()
    ledger.enable_schedule("tcc")
    ledger.record_temporal_run("tcc", True)   # 1 green
    ledger.record_temporal_run("tcc", False)  # red → streak reset
    step = ledger.step("tcc")
    assert step.green_runs == 0
    assert not step.ready_to_remove
    with pytest.raises(ValueError):
        ledger.remove_cron_line("tcc")


def test_cannot_record_run_before_schedule_enabled():
    ledger = MigrationLedger()
    with pytest.raises(ValueError):
        ledger.record_temporal_run("orc", True)


def test_cannot_enable_twice():
    ledger = MigrationLedger()
    ledger.enable_schedule("orc")
    with pytest.raises(ValueError):
        ledger.enable_schedule("orc")


# ---------------------------------------------------------------------------
# Acceptance criterion 1 — migrated crontab has no irc-data scrape lines
# ---------------------------------------------------------------------------


def _fully_migrated_ledger(crontab_text: str) -> MigrationLedger:
    """Drive every step through enable → 2 green runs → remove."""
    ledger = MigrationLedger()
    for key in ledger.order:
        ledger.enable_schedule(key)
        ledger.record_temporal_run(key, True)
        ledger.record_temporal_run(key, True)
        ledger.remove_cron_line(key)
    return ledger


def test_migrated_crontab_contains_no_irc_data_scrape_lines(crontab_text):
    ledger = _fully_migrated_ledger(crontab_text)
    migrated = ledger.apply_to_crontab(crontab_text)
    remaining = scrape_lines(parse_crontab(migrated))
    assert remaining == [], (
        "acceptance criterion failed — scrape lines remain: "
        + ", ".join(f"L{ln.lineno}:{ln.scrape_target}" for ln in remaining)
    )


def test_migrated_crontab_contains_no_active_irc_data_lines(crontab_text):
    """Goal: *no production ingestion left on cron* — nothing irc-data remains."""
    ledger = _fully_migrated_ledger(crontab_text)
    migrated = ledger.apply_to_crontab(crontab_text)
    remaining = irc_data_lines(parse_crontab(migrated))
    assert remaining == [], (
        "irc-data lines remain after migration: "
        + ", ".join(f"L{ln.lineno}:{ln.subcommand}" for ln in remaining)
    )


def test_migration_preserves_env_and_comments(crontab_text):
    """Headers, comments and env assignments survive the migration."""
    ledger = _fully_migrated_ledger(crontab_text)
    migrated = ledger.apply_to_crontab(crontab_text)
    for token in ("SHELL=/bin/bash", "IRC_DATA_DIR=", "LOG_DIR=", "# === ORC (URGENT: never miss a day) ==="):
        assert token in migrated, f"expected {token!r} to survive migration"


def test_partial_migration_keeps_unmigrated_lines(crontab_text):
    """Migrating only ORC leaves every other scrape line in place."""
    ledger = MigrationLedger()
    ledger.enable_schedule("orc")
    ledger.record_temporal_run("orc", True)
    ledger.record_temporal_run("orc", True)
    ledger.remove_cron_line("orc")

    migrated = ledger.apply_to_crontab(crontab_text)
    lines = parse_crontab(migrated)
    # orc line is gone
    assert not any(ln.scrape_target == "orc" and ln.is_scrape for ln in lines)
    # but tcc / sailsys / wayback etc. remain
    targets = {(ln.scrape_target, ln.source_flag) for ln in scrape_lines(lines)}
    assert ("tcc", None) in targets
    assert ("results", "sailsys") in targets
    assert ("wayback", None) in targets


# ---------------------------------------------------------------------------
# Acceptance criteria 2 + 3 — ledger & 7-day green roll-up
# ---------------------------------------------------------------------------


def test_scrapers_page_green_days_counts_trailing_streak():
    days = [("2026-09-01", True), ("2026-09-02", True), ("2026-09-03", False),
            ("2026-09-04", True), ("2026-09-05", True)]
    assert scrapers_page_green_days(days) == 2
    assert scrapers_page_green_days([(d, True) for d in
           ["2026-09-0%d" % i for i in range(1, 8)]]) == 7
    assert scrapers_page_green_days([]) == 0


def test_report_acceptance_roll_up(crontab_text):
    ledger = _fully_migrated_ledger(crontab_text)
    report = build_report(crontab_text, ledger, scrapers_green_streak=7)

    assert report.acceptance["crontab_no_scrape_lines"] is True
    assert report.acceptance["every_source_last_run_in_source_runs"] is True
    assert report.acceptance["scrapers_page_green_7_days"] is True
    assert report.overall_pass is True
    # deliverable: crontab.txt diff is produced
    assert report.diff
    assert "irc-data scrape" not in _diff_added_lines(report.diff)


def _diff_added_lines(diff: str) -> str:
    return "\n".join(l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))


def test_report_fails_when_scrape_lines_remain(crontab_text):
    """A partial migration must not claim the acceptance criterion."""
    ledger = MigrationLedger()  # nothing migrated
    report = build_report(crontab_text, ledger, scrapers_green_streak=7)
    assert report.acceptance["crontab_no_scrape_lines"] is False
    assert report.acceptance["every_source_last_run_in_source_runs"] is False
    assert report.overall_pass is False


def test_report_fails_when_scrapers_page_not_green_7_days(crontab_text):
    ledger = _fully_migrated_ledger(crontab_text)
    report = build_report(crontab_text, ledger, scrapers_green_streak=6)
    assert report.acceptance["scrapers_page_green_7_days"] is False
    assert report.overall_pass is False


def test_report_serialises_for_ledger_review(crontab_text):
    ledger = _fully_migrated_ledger(crontab_text)
    report = build_report(crontab_text, ledger, scrapers_green_streak=7)
    payload = report.to_dict()
    assert payload["schema_version"] == "ops-02-05-v1"
    assert isinstance(payload["steps"], list) and payload["steps"]
    # every step reached cron_removed
    assert all(s["state"] == "cron_removed" for s in payload["steps"])
    # every source step recorded >= REQUIRED_GREEN_RUNS green runs
    for s in payload["steps"]:
        assert s["consecutive_green_runs"] >= REQUIRED_GREEN_RUNS
        assert s["required_green_runs"] == REQUIRED_GREEN_RUNS


def test_required_green_constants_match_issue():
    assert REQUIRED_GREEN_RUNS == 2, "scope: keep cron line until two green Temporal runs"
    assert REQUIRED_GREEN_DAYS == 7, "acceptance: admin Scrapers page green for 7 days"


# ---------------------------------------------------------------------------
# remove_lines / render_diff primitives
# ---------------------------------------------------------------------------


def test_remove_lines_drops_only_target_lines():
    text = (
        "# header\n"
        "SHELL=/bin/bash\n"
        "0 3 * * *    cd /x && bash -c \"op irc-data scrape orc\" >> /log 2>&1\n"
        "0 6 * * *    cd /x && bash -c \"op irc-data scrape tcc\" >> /log 2>&1\n"
    )
    lines = parse_crontab(text)
    orc = [ln for ln in scrape_lines(lines) if ln.scrape_target == "orc"]
    out = remove_lines(text, orc)
    assert "scrape orc" not in out
    assert "scrape tcc" in out
    assert "SHELL=/bin/bash" in out
    assert "# header" in out


def test_render_diff_is_unified():
    before = "a\nb\nc\n"
    after = "a\nc\n"
    diff = render_diff(before, after, path="api/crontab.txt")
    assert diff.startswith("--- a/api/crontab.txt")
    assert "+++ b/api/crontab.txt" in diff
    assert "-b" in diff


# ---------------------------------------------------------------------------
# Acceptance criterion 1 against the *committed* crontab.txt
# ---------------------------------------------------------------------------


def test_committed_crontab_has_no_irc_data_scrape_lines(live_crontab_text):
    """The committed ``api/crontab.txt`` (the deliverable) has no scrape lines."""
    remaining = scrape_lines(parse_crontab(live_crontab_text))
    assert remaining == [], (
        "committed crontab still has `irc-data scrape` lines: "
        + ", ".join(f"L{ln.lineno}:{ln.scrape_target}" for ln in remaining)
    )


def test_committed_crontab_has_no_active_irc_data_ingestion(live_crontab_text):
    """Goal: no production ingestion left on cron in the committed file."""
    remaining = irc_data_lines(parse_crontab(live_crontab_text))
    assert remaining == [], (
        "committed crontab still runs irc-data: "
        + ", ".join(f"L{ln.lineno}:{ln.subcommand}" for ln in remaining)
    )
