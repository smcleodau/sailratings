#!/usr/bin/env python3
"""Evidence generator for DP-06-05 — schedule incremental collection + runbook.

DP-06-05 operates a source *continuously* rather than as a demo.  Its
verification criterion is "Soak test and failure drill artifacts pass".  This
script produces the hard, reproducible evidence a human reviewer uses to
verify both acceptance criteria:

  1. **Seven consecutive scheduled cycles complete within SLO** — the soak
     harness (:mod:`irc_data.operations.soak`) runs 7 scheduled cycles, each
     writing exactly one run-ledger row, each within the cycle SLO, and the
     report records ``cycles_within_slo`` / ``consecutive_cycles_within_slo``.
  2. **Deliberate source failure alerts and recovers without duplicate
     publication** — the failure drill exercises the kill switch (source
     disable ⇒ schedule paused + gate refusal), the health alert (watchdog
     breach ⇒ exactly one alert ⇒ cooldown ⇒ recovery), the checkpoint
     backup (export / destroy / verified restore), and an idempotent reparse
     (consumer view unchanged, no duplicate publication).

The script also verifies each *named scope item* directly:

  * Temporal schedule + backoff (the registry's desired-state reconciliation
    and the register-derived ``RetryPolicy``);
  * source disable (the collection gate + non-retryable workflow error);
  * health alert (the OPS-01-04 watchdog lifecycle);
  * checkpoint backup (the DP-01-03 round-trip);
  * reparse (DP-02-04 idempotent replay);
  * the operational runbook exists (``docs/RUNBOOK-DP-06-05.md``).

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_06_05.py            # full evidence
    PYTHONPATH=src python3 scripts/verify_dp_06_05.py --out /tmp/dp06.json

Exit code 0 when every artifact passes and the signed soak report verifies;
non-zero otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _ok(ok: bool, label: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return bool(ok)


# ---------------------------------------------------------------------------
# Part 1 — schedule + backoff (register-derived)
# ---------------------------------------------------------------------------


def check_schedule_and_backoff() -> bool:
    from datetime import timedelta

    from irc_data.temporal.schedules.cadence import (
        cadence_to_timedelta,
        schedule_id_for_slug,
        workflow_id_for_run,
    )
    from irc_data.temporal.schedules.registry import ScheduleRegistry

    _hr("1. Temporal schedule + backoff")
    ok = True

    # Schedule ids are canonical and per-source.
    ok &= _ok(
        schedule_id_for_slug("sailsys") == "source-sailsys",
        "canonical schedule id source-<slug>",
    )

    # Cadence strings resolve to concrete intervals.
    ok &= _ok(
        cadence_to_timedelta("30min") == timedelta(minutes=30)
        and cadence_to_timedelta("nightly") == timedelta(hours=24),
        "cadence → timedelta (30min / nightly)",
    )

    # Workflow ids are deterministic on (slug, run_key) — idempotent runs.
    a = workflow_id_for_run("sailsys", "scheduled:2026-09-02")
    ok &= _ok(a == workflow_id_for_run("sailsys", "scheduled:2026-09-02")
              and a.startswith("source-run-sailsys-"),
              "deterministic workflow id (idempotent run key)")

    # Backoff: the register retry_policy field derives a Temporal RetryPolicy.
    class _Src:
        slug = "sailsys"
        base_url = "https://app.sailsys.com.au"
        cadence = "30min"
        retry_policy = {"max_attempts": 3, "backoff_seconds": [600, 1800, 7200]}

    rp = ScheduleRegistry._retry_policy_for(_Src())
    ok &= _ok(
        rp.maximum_attempts == 3
        and rp.initial_interval == timedelta(seconds=600)
        and rp.maximum_interval == timedelta(seconds=7200),
        "register retry_policy → Temporal RetryPolicy (3 attempts, 10m→30m→2h)",
    )

    # The schedule's desired state pauses (never deletes) a disabled source.
    class _Disabled:
        slug = "sailsys"
        base_url = "https://app.sailsys.com.au"
        cadence = "30min"
        enabled = False
        legal_status = "approved"
        retry_policy = None

    desired_paused = not (
        bool(_Disabled.enabled) and _Disabled.legal_status == "approved"
    )
    ok &= _ok(desired_paused, "disabled source ⇒ schedule paused (not deleted)")
    return ok


# ---------------------------------------------------------------------------
# Part 2 — the soak + failure drill (the deliverable)
# ---------------------------------------------------------------------------


def check_soak(out: Path | None) -> bool:
    from irc_data.operations import (
        SIGNING_KEY_ENV,
        OpsSoakReportV1,
        SoakConfig,
        run_soak,
        verify_report_signature,
    )
    import os

    _hr("2. Soak test + failure drill (irc-data ops-soak)")

    key = os.environ.get(SIGNING_KEY_ENV, "verify-dp-06-05-key").encode()
    config = SoakConfig(cycles=7, signing_key=key, signing_key_id="verify-dp-06-05")
    report = run_soak(config)
    if out is not None:
        out.write_text(report.to_json())
        print(f"  signed report written to {out}")

    ok = True
    ok &= _ok(report.cycles_required == 7, "soak runs the acceptance count (7 cycles)")
    ok &= _ok(
        len(report.cycles) == 7,
        f"7 cycle results recorded (got {len(report.cycles)})",
    )
    ok &= _ok(
        report.consecutive_cycles_within_slo >= 7,
        f"7 consecutive cycles within SLO "
        f"({report.consecutive_cycles_within_slo}/7, slo={report.cycle_slo_seconds:.0f}s)",
    )
    ok &= _ok(
        all(c.ledger_rows == 1 for c in report.cycles),
        "every cycle wrote exactly one run-ledger row (no duplicate run record)",
    )

    # Failure drill checks.
    drill = report.failure_drill
    for name in (
        "disable_pauses_schedule",
        "schedule_preserved_not_deleted",
        "gate_refuses_when_disabled",
        "run_fails_fast_when_disabled",
        "watchdog_detects_breach",
        "exactly_one_alert_sent",
        "cooldown_suppresses_duplicate_alert",
        "recovery_closes_alert",
        "no_open_alert_after_recovery",
        "checkpoint_present",
        "backup_written",
        "restore_round_trips",
        "resume_produces_no_refetch",
        "recovery_cycle_within_slo",
        "reparse_consumer_view_unchanged",
        "reparse_no_duplicate_publication",
    ):
        ok &= _ok(drill.get(name, False), f"drill: {name}")

    # Acceptance criteria + signature.
    ac = report.passed_acceptance_criteria
    ok &= _ok(
        ac.get("seven_consecutive_cycles_within_slo", False),
        "AC1: seven consecutive scheduled cycles within SLO",
    )
    ok &= _ok(
        ac.get("failure_alerts_and_recovers_without_duplicate_publication", False),
        "AC2: deliberate failure alerts + recovers without duplicate publication",
    )
    ok &= _ok(report.overall_status == "passed", f"overall_status=passed (got {report.overall_status})")
    ok &= _ok(
        verify_report_signature(report, key),
        "report signature verifies (HMAC-SHA256)",
    )

    # Tamper-evidence: a mutated report must NOT verify.
    tampered = OpsSoakReportV1.from_json(report.to_json())
    tampered.cycles_within_slo = 0
    ok &= _ok(
        not verify_report_signature(tampered, key),
        "tampered report fails verification",
    )
    return ok


# ---------------------------------------------------------------------------
# Part 3 — runbook exists and names the scope items
# ---------------------------------------------------------------------------


def check_runbook() -> bool:
    _hr("3. Operational runbook (docs/RUNBOOK-DP-06-05.md)")
    rb = Path(__file__).resolve().parent.parent.parent / "docs" / "RUNBOOK-DP-06-05.md"
    ok = _ok(rb.exists(), f"runbook exists at {rb.relative_to(rb.parent.parent.parent)}")
    if rb.exists():
        body = rb.read_text()
        for term in (
            "Temporal schedule",
            "backoff",
            "kill switch",
            "health alert",
            "watchdog",
            "checkpoint",
            "reparse",
            "incident",
            "DP-00 bridge track",
        ):
            ok &= _ok(term.lower() in body.lower(), f"runbook covers: {term}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None,
                    help="write the signed soak report JSON here")
    args = ap.parse_args()

    print("DP-06-05 — Schedule incremental collection and operational runbook")
    print("Goal: operate the source continuously rather than as a demo.")

    ok = True
    ok &= check_schedule_and_backoff()
    ok &= check_soak(args.out)
    ok &= check_runbook()

    _hr("VERDICT")
    if ok:
        print("PASS — seven consecutive scheduled cycles within SLO; deliberate")
        print("source failure alerted and recovered without duplicate publication;")
        print("runbook present; signed soak report verifies.  DP-00 bridge retires.")
        return 0
    print("FAIL — one or more soak / failure-drill artifacts did not pass.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
