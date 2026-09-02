"""Tests for the scheduling policy (OPS-01-01 / docs/SCHEDULING-POLICY.md).

Covers the OPS-01-01 acceptance criteria:

* The policy defines per-source cadence, nightly window, staleness budget,
  retry/backoff, cooldown and kill-switch semantics as register fields.
* Every *active* source in the register has values (register validation
  enforces the required fields).
* Design examples from the issue scope: 8 d staleness budget for weekly
  certificate lists, watchdog interval 15 min, cooldown 4 h, nightly window
  inherited from the collection policy.

ORM tests use an in-memory SQLite engine — no Postgres / Alembic required.
"""

from __future__ import annotations

from datetime import time

import pytest
from sqlalchemy import create_engine

from irc_data.sources.models import DataSourceRecordV1
from irc_data.sources.registry import (
    CURRENT_POLICY_VERSION,
    DataSource,
    get_source_record,
    seed_sources,
    validate_scheduling,
)
from irc_data.sources.scheduling import (
    CADENCE_CLASS_DEFAULTS,
    DEFAULT_COOLDOWN_HOURS,
    DEFAULT_NIGHTLY_WINDOW,
    REQUIRED_SCHEDULING_FIELDS,
    SCHEDULING_POLICY,
    SCHEDULING_POLICY_VERSION,
    TAKEDOWN_ACK_WINDOW_HOURS,
    WATCHDOG_INTERVAL_MINUTES,
    CadenceClass,
    KillSwitchPolicy,
    RetryPolicy,
    SchedulingPolicyError,
    classify_cadence,
    parse_hhmm,
    validate_source_scheduling,
)
from irc_data.sources.seed_data import SEED_SOURCES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    DataSource.__table__.create(eng, checkfirst=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def seeded_engine(engine):
    seed_sources(engine)
    return engine


def _active_seed(slug: str) -> DataSourceRecordV1:
    rec = next(s for s in SEED_SOURCES if s.slug == slug)
    assert rec.legal_status == "approved" and rec.enabled
    return rec


# ---------------------------------------------------------------------------
# Policy constants — the design numbers from the OPS-01-01 scope
# ---------------------------------------------------------------------------


class TestPolicyConstants:
    def test_watchdog_interval_is_15_minutes(self):
        assert WATCHDOG_INTERVAL_MINUTES == 15
        assert SCHEDULING_POLICY.watchdog_interval_minutes == 15

    def test_default_cooldown_is_4_hours(self):
        assert DEFAULT_COOLDOWN_HOURS == 4
        assert SCHEDULING_POLICY.default_cooldown_hours == 4
        assert KillSwitchPolicy().ack_window_hours == TAKEDOWN_ACK_WINDOW_HOURS == 4

    def test_nightly_window_inherited_from_collection_policy(self):
        # SOURCE-POLICY.md §4.3 — 01:00–06:00
        assert DEFAULT_NIGHTLY_WINDOW == ("01:00", "06:00")
        assert SCHEDULING_POLICY.nightly_window == ("01:00", "06:00")

    def test_weekly_certificates_budget_is_8_days(self):
        # The OPS-01-01 design example: "staleness budgets (design example: 8 d)"
        assert CADENCE_CLASS_DEFAULTS[CadenceClass.WEEKLY_CERTIFICATES][
            "staleness_budget_hours"
        ] == 8 * 24

    def test_cadence_classes_cover_the_three_scopes(self):
        # daily results platforms, weekly certificate lists, annual identifier lists
        assert {c.value for c in CadenceClass} >= {
            "daily_results",
            "weekly_certificates",
            "annual_identifiers",
        }

    def test_policy_version_and_pending_approval_status(self):
        assert SCHEDULING_POLICY.version == SCHEDULING_POLICY_VERSION == "sched-v1.0"
        # Acceptance requires Stuart's approval; until then the policy is
        # defined and enforced, but marked pending.
        assert SCHEDULING_POLICY.status == "pending-approval"
        assert SCHEDULING_POLICY.authority == "Stuart McLeod"


# ---------------------------------------------------------------------------
# Cadence classification
# ---------------------------------------------------------------------------


class TestClassifyCadence:
    @pytest.mark.parametrize(
        "cadence,expected",
        [
            ("nightly", CadenceClass.DAILY_RESULTS),
            ("daily", CadenceClass.DAILY_RESULTS),
            ("30min", CadenceClass.DAILY_RESULTS),
            ("hourly", CadenceClass.DAILY_RESULTS),
            ("weekly", CadenceClass.WEEKLY_CERTIFICATES),
            ("7d", CadenceClass.WEEKLY_CERTIFICATES),
            ("annual", CadenceClass.ANNUAL_IDENTIFIERS),
            ("yearly", CadenceClass.ANNUAL_IDENTIFIERS),
            ("manual", CadenceClass.MANUAL),
            ("decommissioned", CadenceClass.MANUAL),
        ],
    )
    def test_mapping(self, cadence, expected):
        assert classify_cadence(cadence) is expected

    def test_unknown_is_conservative(self):
        assert classify_cadence("whenever") is CadenceClass.DAILY_RESULTS
        assert classify_cadence(None) is CadenceClass.DAILY_RESULTS


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    def test_delay_sequence(self):
        rp = RetryPolicy(max_attempts=3, backoff_seconds=(600, 1800, 7200))
        assert rp.delay_for_attempt(1) == 600
        assert rp.delay_for_attempt(2) == 1800
        assert rp.delay_for_attempt(3) == 7200
        # last delay repeats beyond the sequence
        assert rp.delay_for_attempt(4) == 7200

    def test_from_value_mapping(self):
        rp = RetryPolicy.from_value(
            {"max_attempts": 2, "backoff_seconds": [60, 120]}
        )
        assert rp.max_attempts == 2
        assert rp.backoff_seconds == (60, 120)

    def test_invalid_rejected(self):
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=0)
        with pytest.raises(ValueError):
            RetryPolicy(backoff_seconds=())
        with pytest.raises(ValueError):
            RetryPolicy(backoff_seconds=(-5, 10))


# ---------------------------------------------------------------------------
# Per-field validation
# ---------------------------------------------------------------------------


def _valid_active_record() -> dict:
    return {
        "slug": "unit-source",
        "enabled": True,
        "legal_status": "approved",
        "cadence": "nightly",
        "cadence_class": "daily_results",
        "staleness_budget_hours": 48.0,
        "nightly_window_start": "01:00",
        "nightly_window_end": "06:00",
        "retry_policy": {"max_attempts": 3, "backoff_seconds": [600, 1800, 7200]},
        "cooldown_hours": 4.0,
        "kill_switch_ack_hours": 4,
    }


class TestValidateSourceScheduling:
    def test_valid_record_passes(self):
        assert validate_source_scheduling(_valid_active_record()) == []

    def test_every_required_field_is_enforced(self):
        # The acceptance criterion: cadence, nightly window, staleness budget,
        # retry/backoff, cooldown and kill-switch semantics are all required.
        for field_name in REQUIRED_SCHEDULING_FIELDS:
            rec = _valid_active_record()
            rec[field_name] = None
            errors = validate_source_scheduling(rec)
            assert any(field_name in e for e in errors), (
                f"missing {field_name} did not fail validation"
            )

    def test_bad_cadence_class_rejected(self):
        rec = _valid_active_record()
        rec["cadence_class"] = "sometimes"
        assert any("cadence_class" in e for e in validate_source_scheduling(rec))

    def test_non_positive_budget_rejected(self):
        rec = _valid_active_record()
        rec["staleness_budget_hours"] = 0
        assert any("staleness_budget_hours" in e for e in validate_source_scheduling(rec))

    @pytest.mark.parametrize("bad", ["25:00", "1pm", "0600", "6:99"])
    def test_malformed_window_rejected(self, bad):
        rec = _valid_active_record()
        rec["nightly_window_start"] = bad
        assert any("nightly_window_start" in e for e in validate_source_scheduling(rec))

    def test_bad_retry_policy_rejected(self):
        rec = _valid_active_record()
        rec["retry_policy"] = {"max_attempts": 0, "backoff_seconds": [60]}
        assert any("retry_policy" in e for e in validate_source_scheduling(rec))

    def test_inactive_sources_exempt_by_default(self):
        rec = _valid_active_record()
        rec["legal_status"] = "hold"
        for field_name in REQUIRED_SCHEDULING_FIELDS:
            rec[field_name] = None
        assert validate_source_scheduling(rec) == []
        # …but validated when explicitly requested
        assert validate_source_scheduling(rec, require_when_inactive=True)


# ---------------------------------------------------------------------------
# Per-source spec resolution
# ---------------------------------------------------------------------------


class TestSpecResolution:
    def test_weekly_cert_source_gets_8d_budget(self):
        rec = _active_seed("irc-certs")
        spec = SCHEDULING_POLICY.spec_for(rec)
        assert spec.cadence_class is CadenceClass.WEEKLY_CERTIFICATES
        assert spec.staleness_budget_hours == 8 * 24
        assert spec.retry_policy.max_attempts == 3
        assert spec.retry_policy.backoff_seconds == (3600, 14400, 86400)
        assert spec.cooldown_hours == 4
        assert spec.nightly_window_start == time(1, 0)
        assert spec.nightly_window_end == time(6, 0)
        assert spec.watchdog_interval_minutes == 15

    def test_sailsys_two_hour_budget(self):
        spec = SCHEDULING_POLICY.spec_for(_active_seed("sailsys"))
        assert spec.staleness_budget_hours == 2.0
        assert spec.cadence == "30min"

    def test_annual_source_spec(self):
        spec = SCHEDULING_POLICY.spec_for(_active_seed("cowesweek"))
        assert spec.cadence_class is CadenceClass.ANNUAL_IDENTIFIERS
        assert spec.staleness_budget_hours == 370 * 24
        assert spec.retry_policy.max_attempts == 1

    def test_is_stale_semantics(self):
        spec = SCHEDULING_POLICY.spec_for(_active_seed("topyacht"))  # 30 h
        assert spec.is_stale(None) is True          # never succeeded
        assert spec.is_stale(29.0) is False
        assert spec.is_stale(31.0) is True

    def test_spec_serialises(self):
        spec = SCHEDULING_POLICY.spec_for(_active_seed("orc"))
        d = spec.to_dict()
        assert d["slug"] == "orc"
        assert d["nightly_window"] == "01:00-06:00"
        assert d["policy_version"] == SCHEDULING_POLICY_VERSION


# ---------------------------------------------------------------------------
# Register-level validation — every active source has values
# ---------------------------------------------------------------------------


class TestRegisterValidation:
    def test_every_active_seed_source_has_values(self):
        """Acceptance criterion: every active source has scheduling values."""
        failures = validate_scheduling()
        assert failures == {}, f"register validation failures: {failures}"

    def test_full_register_including_inactive_has_values(self):
        failures = validate_scheduling(include_inactive=True)
        assert failures == {}, f"register validation failures: {failures}"

    def test_raise_on_error(self):
        rec = _valid_active_record()
        rec["staleness_budget_hours"] = None
        with pytest.raises(SchedulingPolicyError) as exc:
            SCHEDULING_POLICY.validate_register([rec], raise_on_error=True)
        assert "staleness_budget_hours" in str(exc.value)

    def test_seeded_db_rows_validate(self, seeded_engine):
        """The same enforcement passes against the DB-backed register."""
        failures = validate_scheduling(seeded_engine)
        assert failures == {}, f"register validation failures: {failures}"

    def test_seeded_db_row_round_trips_scheduling_fields(self, seeded_engine):
        rec = get_source_record(seeded_engine, "irc-certs")
        assert rec.cadence_class == "weekly_certificates"
        assert rec.staleness_budget_hours == 192.0
        assert rec.nightly_window_start == "01:00"
        assert rec.nightly_window_end == "06:00"
        assert rec.retry_policy == {
            "max_attempts": 3,
            "backoff_seconds": [3600, 14400, 86400],
        }
        assert rec.cooldown_hours == 4.0
        assert rec.kill_switch_ack_hours == 4


# ---------------------------------------------------------------------------
# Seed data — the per-source register values
# ---------------------------------------------------------------------------


class TestSeedSchedulingValues:
    def test_all_30_seeds_carry_all_fields(self):
        assert len(SEED_SOURCES) == 30
        for s in SEED_SOURCES:
            for field_name in REQUIRED_SCHEDULING_FIELDS:
                assert getattr(s, field_name) is not None, (
                    f"{s.slug} missing {field_name}"
                )

    def test_cadence_classes_are_valid(self):
        valid = {c.value for c in CadenceClass}
        for s in SEED_SOURCES:
            assert s.cadence_class in valid, f"{s.slug}: {s.cadence_class}"

    def test_named_source_values(self):
        by_slug = {s.slug: s for s in SEED_SOURCES}
        assert by_slug["sailsys"].staleness_budget_hours == 2.0
        assert by_slug["sailing-news"].staleness_budget_hours == 6.0
        assert by_slug["topyacht"].staleness_budget_hours == 30.0
        assert by_slug["irc-tcc"].staleness_budget_hours == 30.0
        assert by_slug["orc"].staleness_budget_hours == 30.0
        assert by_slug["irc-certs"].cadence_class == "weekly_certificates"
        assert by_slug["irc-certs"].staleness_budget_hours == 192.0
        assert by_slug["cowesweek"].cadence_class == "annual_identifiers"
        assert by_slug["sydney-hobart"].cadence_class == "annual_identifiers"
        assert by_slug["rorc"].cadence_class == "manual"
        assert by_slug["rorc"].staleness_budget_hours == 87600.0

    def test_seeds_still_carry_current_policy_version(self):
        for s in SEED_SOURCES:
            assert s.policy_version == CURRENT_POLICY_VERSION


# ---------------------------------------------------------------------------
# resolve_source carries the scheduling fields in its rules
# ---------------------------------------------------------------------------


class TestResolvedDecisionRules:
    def test_resolve_source_includes_scheduling_rules(self):
        from irc_data.sources.policy import resolve_source

        # in-memory seed registry (no DB) — topyacht is approved + enabled
        decision = resolve_source("topyacht")
        assert decision.allowed is True
        rules = decision.rules
        assert rules.cadence_class in {c.value for c in CadenceClass}
        assert rules.staleness_budget_hours > 0
        assert rules.retry_max_attempts >= 1
        assert rules.retry_backoff_seconds
        assert rules.cooldown_hours == 4.0
        assert rules.watchdog_interval_minutes == 15
        assert rules.kill_switch_ack_hours == 4
        # window hours derived from the nightly window
        assert rules.collection_window_start == 1
        assert rules.collection_window_end == 6
        d = decision.to_dict()["rules"]
        assert d["staleness_budget_hours"] == rules.staleness_budget_hours
        assert d["retry_backoff_seconds"] == list(rules.retry_backoff_seconds)


# ---------------------------------------------------------------------------
# HH:MM parsing helper
# ---------------------------------------------------------------------------


class TestParseHhmm:
    @pytest.mark.parametrize(
        "value,expected",
        [("01:00", time(1, 0)), ("6:00", time(6, 0)), ("23:59", time(23, 59))],
    )
    def test_valid(self, value, expected):
        assert parse_hhmm(value) == expected

    @pytest.mark.parametrize("value", ["24:00", "1:60", "", "x", None, 5])
    def test_invalid(self, value):
        assert parse_hhmm(value) is None
