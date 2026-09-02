"""Verification tests for DP-06-05 — continuous collection + failure drill.

Verification criterion from the issue:

    "Soak test and failure drill artifacts pass."

Acceptance criteria under test:

* **Seven consecutive scheduled cycles complete within SLO** — the soak
  harness runs 7 cycles, each keyed by an idempotency ``run_key``, each
  writing exactly one run-ledger row, each within the per-cycle SLO; the
  report records ``consecutive_cycles_within_slo == 7``.
* **Deliberate source failure alerts and recovers without duplicate
  publication** — the failure drill exercises the kill switch (source
  disable pauses the schedule and the gate refuses collection), the health
  alert (watchdog breach → exactly one alert → cooldown → recovery → no open
  alert), the checkpoint backup (export / destroy / verified restore), and an
  idempotent reparse (consumer view unchanged, no duplicate publication).

The soak runs against an isolated SQLite engine and filesystem working dir in
a temp dir; every store layer uses portable SQL so the measured behaviour
carries over to Postgres in production.  Where a live Temporal server is
unavailable the *desired* schedule state is exercised through the registry's
pure reconciliation logic, which is exactly what
``ScheduleRegistry.ensure_schedule`` computes against a live server.
"""

from __future__ import annotations

import pytest

from irc_data.operations import (
    OPS_SOAK_SCHEMA_VERSION,
    SOAK_ARTIFACT_IDS,
    CycleStatus,
    OpsSoakReportV1,
    SoakConfig,
    SourceOpsSoak,
    run_soak,
    sign_report,
    verify_report_signature,
)
from irc_data.operations.contracts import CycleResultV1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def signing_key() -> bytes:
    return b"dp06-test-signing-key"


@pytest.fixture()
def report(signing_key: bytes) -> OpsSoakReportV1:
    """A full 7-cycle soak + failure drill, signed with a known key."""
    return run_soak(
        SoakConfig(cycles=7, signing_key=signing_key, signing_key_id="test-key")
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 1 — seven consecutive scheduled cycles within SLO
# ---------------------------------------------------------------------------


def test_seven_consecutive_cycles_complete_within_slo(report: OpsSoakReportV1):
    assert report.cycles_required == 7
    assert len(report.cycles) == 7
    assert report.cycles_within_slo == 7
    assert report.consecutive_cycles_within_slo == 7
    for cycle in report.cycles:
        assert cycle.status == CycleStatus.PASSED.value, cycle.error
        assert cycle.within_slo, f"cycle {cycle.cycle} breached SLO"
        assert cycle.duration_seconds <= cycle.slo_seconds


def test_every_cycle_writes_exactly_one_ledger_row(report: OpsSoakReportV1):
    # A duplicate schedule fire would open a second ledger row for the same
    # run_key; exactly one row per cycle proves idempotent run accounting.
    assert all(c.ledger_rows == 1 for c in report.cycles)


def test_acceptance_criteria_roll_up(report: OpsSoakReportV1):
    ac = report.passed_acceptance_criteria
    assert ac["seven_consecutive_cycles_within_slo"] is True
    assert ac["failure_alerts_and_recovers_without_duplicate_publication"] is True
    assert report.overall_status == CycleStatus.PASSED.value


# ---------------------------------------------------------------------------
# Acceptance criterion 2 — deliberate failure alerts + recovers, no dup
# ---------------------------------------------------------------------------


def test_source_disable_pauses_schedule_and_gate_refuses(report: OpsSoakReportV1):
    d = report.failure_drill
    assert d["disable_pauses_schedule"] is True
    assert d["schedule_preserved_not_deleted"] is True  # paused, never deleted
    assert d["gate_refuses_when_disabled"] is True
    assert d["run_fails_fast_when_disabled"] is True


def test_health_alert_lifecycle(report: OpsSoakReportV1):
    d = report.failure_drill
    assert d["watchdog_detects_breach"] is True
    assert d["exactly_one_alert_sent"] is True
    assert d["cooldown_suppresses_duplicate_alert"] is True
    assert d["exactly_one_email_total"] is True
    assert d["recovery_closes_alert"] is True
    assert d["recovery_email_sent"] is True
    assert d["no_open_alert_after_recovery"] is True


def test_checkpoint_backup_round_trip(report: OpsSoakReportV1):
    d = report.failure_drill
    assert d["checkpoint_present"] is True
    assert d["backup_written"] is True
    assert d["live_checkpoint_destroyed"] is True
    assert d["restore_round_trips"] is True
    assert d["resume_state_intact"] is True
    assert d["resume_produces_no_refetch"] is True


def test_recovery_and_reparse_no_duplicate_publication(report: OpsSoakReportV1):
    d = report.failure_drill
    assert d["recovery_cycle_within_slo"] is True
    assert d["reparse_consumer_view_unchanged"] is True
    assert d["reparse_no_duplicate_publication"] is True
    assert report.no_duplicate_publication is True


# ---------------------------------------------------------------------------
# Artifacts + signed report contract
# ---------------------------------------------------------------------------


def test_all_named_artifacts_present_and_passing(report: OpsSoakReportV1):
    by_id = {a["artifact"]: a for a in report.artifacts}
    assert set(by_id) == set(SOAK_ARTIFACT_IDS)
    for artifact in SOAK_ARTIFACT_IDS:
        assert by_id[artifact]["status"] == CycleStatus.PASSED.value, artifact


def test_report_signature_verifies(report: OpsSoakReportV1, signing_key: bytes):
    assert report.signature
    assert report.signing_key_id == "test-key"
    assert verify_report_signature(report, signing_key)


def test_tampered_report_fails_verification(
    report: OpsSoakReportV1, signing_key: bytes
):
    tampered = OpsSoakReportV1.from_json(report.to_json())
    tampered.cycles_within_slo = 0  # forge a worse result
    assert not verify_report_signature(tampered, signing_key)

    tampered2 = OpsSoakReportV1.from_json(report.to_json())
    tampered2.failure_drill["reparse_no_duplicate_publication"] = False
    assert not verify_report_signature(tampered2, signing_key)


def test_report_round_trips_through_json(report: OpsSoakReportV1):
    again = OpsSoakReportV1.from_json(report.to_json())
    assert again.to_dict() == report.to_dict()
    assert again.schema_version == OPS_SOAK_SCHEMA_VERSION


def test_sign_report_is_deterministic(report: OpsSoakReportV1, signing_key: bytes):
    # Re-signing the same payload with the same key yields the same signature.
    again = OpsSoakReportV1.from_json(report.to_json())
    again.signature = ""
    sig = sign_report(again, signing_key, key_id="test-key")
    assert sig == report.signature


# ---------------------------------------------------------------------------
# Negative paths — the harness must be able to FAIL
# ---------------------------------------------------------------------------


def test_slo_breach_fails_the_cycle(tmp_path):
    """A cycle that exceeds its SLO must be marked failed (not hidden)."""
    config = SoakConfig(
        cycles=2,
        cycle_slo_seconds=0.0,  # impossible SLO → every cycle breaches
        work_dir=tmp_path,
    )
    rep = run_soak(config)
    assert rep.consecutive_cycles_within_slo == 0
    assert rep.passed_acceptance_criteria["seven_consecutive_cycles_within_slo"] is False
    assert rep.overall_status == CycleStatus.FAILED.value


def test_cycle_result_finalise_marks_error_failed():
    res = CycleResultV1(cycle=1, source_slug="s", error="boom")
    res.finalise()
    assert res.status == CycleStatus.FAILED.value
