"""Verification tests for DP-05-05 — load, resilience and disaster recovery.

Verification criterion from the issue:

    "Production-sized synthetic load plus restore drill produces signed
    report."

Acceptance criteria under test:

* **Published data and provenance survive recovery** — after the
  operational database is destroyed and rebuilt by replaying from the
  raw lake, the consumer-visible rows and the per-field provenance
  (artifact id + content hash cited by every locator) match the
  pre-disaster baseline exactly.
* **RPO/RTO and throughput are measured** — the signed report carries a
  measured ``rpo_seconds`` / ``rto_seconds`` for the restore drill and a
  per-scenario / aggregate throughput.
* **No duplicate publication follows replay** — replaying the restore a
  second time leaves the consumer view and the promotion ledger
  unchanged.

Scope under test (from the issue): high artifact volume, backfills,
concurrent adapters, database outage, object-store outage, and restore
+ replay from raw.

The drill runs against an isolated SQLite engine and filesystem raw lake
in a temp dir; every store layer uses portable SQL so the measured
behaviour carries over to Postgres in production.  The volumes here are
kept small so the suite runs fast in CI; the harness's default is the
"production-sized" load.
"""

from __future__ import annotations

import json

import pytest

from irc_data.resilience import (
    DataPlaneDrill,
    DrillConfig,
    DrillReportV1,
    ScenarioStatus,
    run_drill,
)
from irc_data.resilience.contracts import (
    SCENARIO_IDS,
    sign_report,
    verify_report_signature,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def drill_config() -> DrillConfig:
    """A small but complete drill — fast enough for CI, exercises every
    scenario and code path."""
    return DrillConfig(
        artifact_volume=120,
        concurrent_adapters=4,
        per_adapter_volume=15,
        backfill_batch=60,
    )


@pytest.fixture()
def drill(drill_config):
    """Run the drill once per test that needs it, and clean up after."""
    d = DataPlaneDrill(drill_config)
    yield d
    d.close()


@pytest.fixture()
def report(drill_config):
    """A completed, signed drill report (shared by report-level tests)."""
    return run_drill(drill_config)


# ---------------------------------------------------------------------------
# Scenario coverage
# ---------------------------------------------------------------------------


def test_drill_covers_all_scenarios(report: DrillReportV1):
    """Every scenario the issue names is present in the report."""
    names = [s.scenario for s in report.scenarios]
    for expected in SCENARIO_IDS:
        assert expected in names, f"missing scenario {expected!r}"


def test_drill_overall_passes(report: DrillReportV1):
    """The drill's overall status is passed and every scenario passed."""
    assert report.overall_status == ScenarioStatus.PASSED.value
    for s in report.scenarios:
        assert s.status == ScenarioStatus.PASSED.value, (
            f"scenario {s.scenario} failed: {s.error} checks={s.checks}"
        )


# ---------------------------------------------------------------------------
# Load scenarios
# ---------------------------------------------------------------------------


def test_high_volume_ingest_no_loss_no_dup(report: DrillReportV1):
    """High artifact volume: every artifact promoted, no loss, no dup."""
    s = _scenario(report, "high_volume_ingest")
    assert s.checks["all_artifacts_promoted"]
    assert s.checks["no_duplicate_rows"]
    assert s.checks["ledger_recorded_every_run"]
    assert s.checks["raw_lake_fully_intact"]
    assert s.volume > 0
    assert s.throughput_per_second and s.throughput_per_second > 0


def test_backfill_is_idempotent_and_correct(report: DrillReportV1):
    """Backfill: replay is idempotent (same batch) and accounts for all."""
    s = _scenario(report, "backfill_under_load")
    assert s.checks["replay_idempotent_same_batch"]
    assert s.checks["all_selected_replayed"]
    assert s.checks["comparison_accounts_for_every_artifact"]
    assert s.checks["ingest_continued_during_backfill"]


def test_concurrent_adapters_no_lost_or_double_counted(report: DrillReportV1):
    """Concurrent adapters: ledger recorded each run once, all promoted."""
    s = _scenario(report, "concurrent_adapters")
    assert s.checks["no_adapter_errors"]
    assert s.checks["ledger_recorded_every_run_exactly_once"]
    assert s.checks["every_adapter_row_promoted"]


# ---------------------------------------------------------------------------
# Resilience scenarios
# ---------------------------------------------------------------------------


def test_database_outage_fails_safely_and_recovers(report: DrillReportV1):
    """DB outage: writes fail safely (no partial state), recovery is fast."""
    s = _scenario(report, "database_outage")
    assert s.checks["write_failed_safely_during_outage"]
    assert s.checks["no_partial_write_during_outage"]
    assert s.checks["pre_outage_data_intact_after_restore"]
    assert s.checks["system_accepts_writes_after_restore"]
    assert s.rto_seconds is not None
    assert s.rpo_seconds == 0.0


def test_object_store_outage_fails_safely_and_recovers(report: DrillReportV1):
    """Object-store outage: store() raises, no loss or corruption, recovers."""
    s = _scenario(report, "object_store_outage")
    assert s.checks["store_failed_safely_during_outage"]
    assert s.checks["no_object_loss"]
    assert s.checks["no_corruption_introduced"]
    assert s.checks["all_pre_outage_objects_verify"]
    assert s.rto_seconds is not None


# ---------------------------------------------------------------------------
# Disaster recovery — the restore drill
# ---------------------------------------------------------------------------


def test_restore_replay_published_data_survives(report: DrillReportV1):
    """After destroy + replay-from-raw, the published rows match baseline."""
    s = _scenario(report, "restore_and_replay")
    assert s.checks["published_data_survives"]
    assert s.checks["raw_lake_is_system_of_record"]
    m = s.metrics
    assert m["restored_rows"] == m["baseline_rows"]


def test_restore_replay_provenance_survives(report: DrillReportV1):
    """Per-field provenance (artifact id + content hash) survives recovery."""
    s = _scenario(report, "restore_and_replay")
    assert s.checks["provenance_survives"]


def test_replay_after_restore_causes_no_duplicate_publication(
    report: DrillReportV1,
):
    """A second replay after restore does not duplicate the publication."""
    s = _scenario(report, "restore_and_replay")
    assert s.checks["no_duplicate_publication"]
    m = s.metrics
    # Consumer-visible row count is unchanged by the second replay.
    assert m["consumer_rows_after_second_replay"] == (
        m["consumer_rows_before_second_replay"]
    )


def test_restore_measures_rpo_and_rto(report: DrillReportV1):
    """The restore drill reports a measured RPO and RTO."""
    s = _scenario(report, "restore_and_replay")
    assert s.rpo_seconds is not None
    assert s.rto_seconds is not None
    # RPO is 0: the raw lake preserved every committed artifact.
    assert s.rpo_seconds == 0.0
    assert s.rto_seconds >= 0.0


# ---------------------------------------------------------------------------
# Acceptance criteria + signed report
# ---------------------------------------------------------------------------


def test_acceptance_criteria_all_pass(report: DrillReportV1):
    """The report maps the issue's acceptance criteria and all hold."""
    ac = report.passed_acceptance_criteria
    assert ac["published_data_and_provenance_survive_recovery"]
    assert ac["rpo_rto_and_throughput_measured"]
    assert ac["no_duplicate_publication_follows_replay"]


def test_report_is_signed_and_verifies(drill_config: DrillConfig):
    """The signed report verifies under the drill's key."""
    with DataPlaneDrill(drill_config) as drill:
        report = drill.run()
        assert report.signature, "report must carry a signature"
        assert report.signing_key_id == drill_config.signing_key_id
        assert verify_report_signature(report, drill.signing_key)


def test_report_signature_detects_tampering(drill_config: DrillConfig):
    """Any change to the report payload invalidates the signature."""
    with DataPlaneDrill(drill_config) as drill:
        report = drill.run()
        assert verify_report_signature(report, drill.signing_key)

        # Tamper with a measured value — the RTO.
        report.measured_rto_seconds = 99999.0
        assert not verify_report_signature(report, drill.signing_key)


def test_report_signature_survives_serialisation(drill_config: DrillConfig):
    """The signature is stable across a JSON round-trip (canonical form)."""
    with DataPlaneDrill(drill_config) as drill:
        report = drill.run()
        restored = DrillReportV1.from_json(report.to_json())
        assert restored.signature == report.signature
        assert verify_report_signature(restored, drill.signing_key)


def test_report_is_json_round_trippable(report: DrillReportV1):
    """The report is a clean JSON document (can be shipped to the board)."""
    parsed = json.loads(report.to_json())
    assert parsed["schema_version"] == "v1"
    assert parsed["overall_status"] == ScenarioStatus.PASSED.value
    assert isinstance(parsed["scenarios"], list)
    assert parsed["signature"]


def test_wrong_key_does_not_verify(drill_config: DrillConfig):
    """A report signed with one key does not verify under another."""
    from cryptography.fernet import Fernet

    with DataPlaneDrill(drill_config) as drill:
        report = drill.run()
        other_key = Fernet.generate_key()
        assert not verify_report_signature(report, other_key)


def test_sign_report_is_deterministic_for_same_payload(drill_config):
    """Signing the same payload twice with one key yields one signature."""
    from irc_data.resilience.contracts import DrillReportV1 as R

    key = b"test-signing-key-0123456789abcdef"
    ts = "2026-01-01T00:00:00+00:00"
    r1 = R(report_id="x", artifact_volume=1, started_at=ts, completed_at=ts)
    r2 = R(report_id="x", artifact_volume=1, started_at=ts, completed_at=ts)
    assert sign_report(r1, key) == sign_report(r2, key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scenario(report: DrillReportV1, name: str):
    """Return the scenario result named *name* or fail the test."""
    for s in report.scenarios:
        if s.scenario == name:
            return s
    raise AssertionError(f"scenario {name!r} not present in report")
