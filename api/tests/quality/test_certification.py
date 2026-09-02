"""Verification tests for DP-06-04 — resolve identities and certify
quality for the vertical slice.

Acceptance criteria under test:

* **Accuracy and quality meet approved thresholds** — the labelled
  adjudication sample's error rate must meet the approved threshold; a
  wrong adjudicator is blocked by the accuracy gate.
* **False-merge audit passes** — every merge decision (auto + human) is
  cross-checked against the steward gold labels; a single false merge
  fails the run.
* **Every published record is reproducible** — the slice re-runs to an
  identical ``reproducibility_hash``, and the receipt binds the exact
  batch version + hash on steward sign-off.

Runs against in-memory SQLite (no Postgres/Alembic dependency); the
store layers use portable SQL so behaviour matches production.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine

from irc_data.diagnostics import reconciliation
from irc_data.matching.blocking import EntityObservation
from irc_data.quality import certification as C
from irc_data.quality import gate_store


@pytest.fixture()
def engine():
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    gate_store.init_quality_tables(eng)
    reconciliation.init_reconciliation_tables(eng)
    return eng


def _so(oid, sail, name, design, gold, source, *, registry=None, loa=None,
        beam=None, year=None, flags=()) -> C.SliceObservation:
    return C.SliceObservation(
        observation=EntityObservation(
            observation_id=oid, sail_number=sail, name=name,
            registry_id=registry, design=design, country="AUS", loa_m=loa,
            beam_m=beam, year_built=year, valid_from=date(2026, 1, 1),
        ),
        source_slug=source, gold_entity_key=gold, impact_flags=flags,
    )


def _slice() -> list[C.SliceObservation]:
    """3 corroborated duplicates, 1 hard uncertain duplicate, 2 distinct."""
    return [
        _so("irc-wo", "AUS4343", "Wild Oats XI", "Reichel/Pugh 100",
            "boat-wildoats", "irc-certs", registry="IRC4343", loa=30.5,
            beam=5.2, year=2015, flags=("rated", "has_certificate")),
        _so("orc-wo", "4343", "WILD OATS XI", "Reichel/Pugh 100",
            "boat-wildoats", "orc", registry="IRC4343", loa=30.5, beam=5.2,
            year=2015, flags=("has_certificate",)),
        _so("irc-cj", "AUS12358", "Comanche", "Verdier 100",
            "boat-comanche", "irc-certs", registry="IRC12358", loa=30.5,
            beam=7.1, year=2014, flags=("rated",)),
        _so("orc-cj", "12358", "COMANCHE", "Verdier 100",
            "boat-comanche", "orc", registry="IRC12358", loa=30.5, beam=7.1,
            year=2014),
        _so("irc-ib", "AUS52", "Ichi Ban", "TP52", "boat-ichiban",
            "irc-certs", registry="IRC52", loa=15.85, beam=4.42, year=2017),
        _so("orc-ib", "52", "ICHI BAN", "TP52", "boat-ichiban", "orc",
            registry="IRC52", loa=15.85, beam=4.42, year=2017),
        _so("irc-bj", "52570", "Black Jack", "Reichel/Pugh 66",
            "boat-blackjack", "irc-certs", loa=20.0, beam=5.5, year=2013,
            flags=("rated",)),
        _so("orc-bj", "AUS52570", "BLACK JACK", "Reichel/Pugh 66",
            "boat-blackjack", "orc", loa=20.0, beam=5.5, year=2013),
        _so("irc-al", "TAS8333", "Alive", "Reichel/Pugh 66",
            "boat-alive", "irc-certs", registry="IRC8333", loa=20.0,
            beam=5.5, year=2013),
        _so("ss-al2", "Q8333", "Alive II", "Cookson 50", "boat-alive2",
            "sailsys", registry="IRC8334", loa=15.2, beam=4.4, year=2011,
            flags=("has_results",)),
        _so("irc-zen", "52001", "Zen", "Sydney 38", "boat-zen",
            "irc-certs", registry="IRC52001", loa=11.8, beam=3.8, year=2001),
        _so("ss-zen", "52002", "Zen Again", "Sydney 38", "boat-zenagain",
            "sailsys", registry="IRC52002", loa=11.8, beam=3.8, year=2002,
            flags=("has_results",)),
    ]


def _clock():
    return datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


class TestHappyPath:
    def test_certifies_and_promotes(self, engine):
        res = C.certify_vertical_slice(engine, _slice(), clock=_clock)
        assert res.certified is True
        assert res.receipt is not None
        assert res.receipt.schema_version == C.SCHEMA_VERSION
        assert res.receipt.verdict == "certified"
        assert res.promotion_receipt is not None
        assert res.published_row_count > 0

    def test_runs_all_seven_stages(self, engine):
        res = C.certify_vertical_slice(engine, _slice(), clock=_clock)
        # 1 candidates
        assert res.candidates.candidate_pairs == 8
        assert res.candidates.ruleset_id == "blocking-rules-v1"
        # 2 scores
        assert res.scores.routing_counts == {
            "auto_merge": 2, "auto_reject": 0, "uncertain": 6
        }
        # 3 adjudication sample
        assert res.adjudication.measured_cases == 6
        # 5 quality gate
        assert res.gate_verdict.passed
        assert res.dimension_report.publishable
        # 6 reconciliation
        assert res.reconciliation.promotion_allowed
        assert res.reconciliation.decision == "allow"
        # 7 promotion
        assert res.receipt.promotion_receipt_id == res.promotion_receipt.receipt_id

    def test_accuracy_meets_threshold(self, engine):
        res = C.certify_vertical_slice(engine, _slice(), clock=_clock)
        assert res.adjudication.error_rate <= C.APPROVED_MAX_ADJUDICATION_ERROR_RATE
        assert res.adjudication.n_errors == 0

    def test_false_merge_audit_passes(self, engine):
        res = C.certify_vertical_slice(engine, _slice(), clock=_clock)
        assert res.false_merge_audit.passed
        assert res.false_merge_audit.false_merges == ()
        assert res.false_merge_audit.auto_merge_precision == 1.0


class TestReproducibility:
    def test_published_rows_reproducible(self):
        e1 = create_engine("sqlite+pysqlite:///:memory:", future=True)
        gate_store.init_quality_tables(e1)
        reconciliation.init_reconciliation_tables(e1)
        e2 = create_engine("sqlite+pysqlite:///:memory:", future=True)
        gate_store.init_quality_tables(e2)
        reconciliation.init_reconciliation_tables(e2)
        r1 = C.certify_vertical_slice(e1, _slice(), clock=_clock)
        r2 = C.certify_vertical_slice(e2, _slice(), clock=_clock)
        assert r1.reproducibility_hash == r2.reproducibility_hash

    def test_published_rows_carry_config_fingerprints(self, engine):
        res = C.certify_vertical_slice(engine, _slice(), clock=_clock)
        fps = res.receipt.config_fingerprints
        assert fps["blocking_ruleset"]
        assert fps["scorer_config"]
        assert fps["thresholds"]


class TestPublicationControls:
    def test_false_merge_blocked(self, engine):
        lenient = C.CertificationThresholdsV1(
            max_adjudication_error_rate=1.0, min_auto_merge_precision=0.0
        )
        with pytest.raises(C.FalseMergeError):
            C.certify_vertical_slice(
                engine, _slice(),
                adjudicator_policy=lambda item: "merge",
                thresholds=lenient, clock=_clock,
            )

    def test_wrong_adjudicator_blocked_by_accuracy(self, engine):
        with pytest.raises(C.AccuracyThresholdError):
            C.certify_vertical_slice(
                engine, _slice(),
                adjudicator_policy=lambda item: "separate",
                clock=_clock,
            )

    def test_low_quality_data_blocked(self, engine):
        bad = _slice() + [
            _so("irc-mystery", None, "Mystery Boat", "Unknown 40",
                "boat-mystery", "irc-certs", registry="IRC9000"),
        ]
        with pytest.raises(C.QualityGateBlockedError):
            C.certify_vertical_slice(engine, bad, clock=_clock)

    def test_silent_loss_blocks_promotion(self, engine):
        from irc_data.diagnostics.reconciliation import (
            PipelineCountsV1, PromotionBlockedError, assert_promotable,
            reconcile_run,
        )

        report = reconcile_run(
            engine,
            PipelineCountsV1(
                run_id=99, source_id="dp06-vertical-slice",
                discovered=12, fetched=12, parsed=12, transformed=12,
                published=8,
            ),
        )
        assert report.decision == "block"
        with pytest.raises(PromotionBlockedError):
            assert_promotable(report)


class TestStewardSignOff:
    def test_sign_binds_batch_version(self, engine):
        res = C.certify_vertical_slice(engine, _slice(), clock=_clock)
        signed = res.receipt.sign(
            "data-steward:stuart-mcleod",
            at=datetime(2026, 9, 5, 13, 0, 0, tzinfo=timezone.utc),
        )
        assert signed.signed_by == "data-steward:stuart-mcleod"
        assert signed.verdict == "certified"
        assert signed.batch_key == res.receipt.batch_key
        assert signed.version == res.receipt.version
        assert signed.reproducibility_hash == res.receipt.reproducibility_hash

    def test_receipt_json_round_trips(self, engine):
        res = C.certify_vertical_slice(engine, _slice(), clock=_clock)
        d = res.receipt.to_dict()
        assert d["schema_version"] == C.SCHEMA_VERSION
        assert d["promotion_receipt_id"] == res.promotion_receipt.receipt_id
        assert '"receipt_id"' in res.receipt.to_json()

    def test_refuses_to_sign_non_certified(self):
        receipt = C.PublishedDatasetReceiptV1(
            batch_key="b", pipeline="p", source_slug="s", version=1,
            promotion_receipt_id="r", verdict="rejected",
        )
        with pytest.raises(C.CertificationError):
            receipt.sign("data-steward:x")


class TestThresholds:
    def test_approved_thresholds_are_sane(self):
        t = C.APPROVED_THRESHOLDS_V1
        assert 0.0 <= t.max_adjudication_error_rate <= 1.0
        assert t.max_false_merges == 0
        assert 0.0 <= t.min_auto_merge_precision <= 1.0
        assert t.fingerprint()

    def test_invalid_thresholds_rejected(self):
        with pytest.raises(C.CertificationError):
            C.CertificationThresholdsV1(max_adjudication_error_rate=1.5)
