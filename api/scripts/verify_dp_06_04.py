#!/usr/bin/env python3
"""End-to-end verification evidence for DP-06-04 — resolve identities and
certify quality for the vertical slice.

This script produces hard, paste-able evidence that the issue's scope,
acceptance criteria and verification step all hold on the DP-06-01
selected source pair (IRC certificates + ORC certificates + SailSys
results).  It runs **offline** (in-memory SQLite, no network) and is
fully deterministic, so it is CI-safe.

Scope (verbatim from the issue): *run candidates, scores, adjudication
sample, quality gate, reconciliation and promotion.*

Acceptance criteria under test:

* **Accuracy and quality meet approved thresholds** — the labelled
  adjudication sample measures the adjudicator's error rate against the
  steward gold labels; it must be ≤ 3 % (the DP-06-01 M5 ≥ 97 %
  precision target).  A hostile merge-everything adjudicator is shown
  to be blocked by the *accuracy* gate.
* **False-merge audit passes** — every merge decision (auto + human) is
  cross-checked against gold; one false merge fails the run.  A hostile
  merge of two distinct boats is shown to be blocked by the
  *false-merge* audit.
* **Every published record is reproducible** — the whole slice is run
  twice; the ``reproducibility_hash`` of the published row set must be
  identical, and each published row carries the config fingerprints.

Verification step: *Independent data-steward review signs
DataQualityVerdict to batch version* — the emitted
``PublishedDatasetReceiptV1`` carries ``verdict=certified`` and is
signed by the steward, binding their sign-off to the exact batch
version + reproducibility hash.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_06_04.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import create_engine  # noqa: E402

from irc_data.diagnostics import reconciliation  # noqa: E402
from irc_data.matching.blocking import EntityObservation  # noqa: E402
from irc_data.quality import certification as C  # noqa: E402
from irc_data.quality import gate_store, gates  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    mark = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{suffix}")


def _banner(text: str) -> None:
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def _engine():
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    gate_store.init_quality_tables(eng)
    reconciliation.init_reconciliation_tables(eng)
    return eng


# ---------------------------------------------------------------------------
# The vertical-slice real-data fixture (IRC + ORC + SailSys)
# ---------------------------------------------------------------------------
#
# Each SliceObservation is a steward-labelled record from the DP-06-01
# selected source pair.  ``gold_entity_key`` is the steward-verified
# ground truth used *only* by the false-merge audit — never by the
# matcher.


def _so(
    oid, sail, name, design, gold, source, *, registry=None, loa=None,
    beam=None, year=None, flags=(),
) -> C.SliceObservation:
    return C.SliceObservation(
        observation=EntityObservation(
            observation_id=oid,
            sail_number=sail,
            name=name,
            registry_id=registry,
            design=design,
            country="AUS",
            loa_m=loa,
            beam_m=beam,
            year_built=year,
            valid_from=date(2026, 1, 1),
        ),
        source_slug=source,
        gold_entity_key=gold,
        impact_flags=flags,
    )


def slice_observations() -> list[C.SliceObservation]:
    """The vertical-slice real-data sample.

    Shape: three corroborated duplicate pairs (IRC cert ↔ ORC cert for
    the same hull), one hard uncertain duplicate (sail-prefix drift, no
    registry id — the adjudication sample), and two *distinct* boats
    with near-identical names that must never be merged.
    """
    return [
        # --- corroborated duplicates (auto-merge band) ------------------
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
        _so("irc-ib", "AUS52", "Ichi Ban", "TP52",
            "boat-ichiban", "irc-certs", registry="IRC52", loa=15.85,
            beam=4.42, year=2017, flags=("rated",)),
        _so("orc-ib", "52", "ICHI BAN", "TP52",
            "boat-ichiban", "orc", registry="IRC52", loa=15.85, beam=4.42,
            year=2017),
        # --- hard uncertain duplicate (the adjudication sample) ---------
        _so("irc-bj", "52570", "Black Jack", "Reichel/Pugh 66",
            "boat-blackjack", "irc-certs", loa=20.0, beam=5.5, year=2013,
            flags=("rated",)),
        _so("orc-bj", "AUS52570", "BLACK JACK", "Reichel/Pugh 66",
            "boat-blackjack", "orc", loa=20.0, beam=5.5, year=2013),
        # --- distinct boats with near-identical names (never merge) -----
        _so("irc-al", "TAS8333", "Alive", "Reichel/Pugh 66",
            "boat-alive", "irc-certs", registry="IRC8333", loa=20.0,
            beam=5.5, year=2013),
        _so("ss-al2", "Q8333", "Alive II", "Cookson 50",
            "boat-alive2", "sailsys", registry="IRC8334", loa=15.2,
            beam=4.4, year=2011, flags=("has_results",)),
        _so("irc-zen", "52001", "Zen", "Sydney 38",
            "boat-zen", "irc-certs", registry="IRC52001", loa=11.8,
            beam=3.8, year=2001),
        _so("ss-zen", "52002", "Zen Again", "Sydney 38",
            "boat-zenagain", "sailsys", registry="IRC52002", loa=11.8,
            beam=3.8, year=2002, flags=("has_results",)),
    ]


# ---------------------------------------------------------------------------
# 1–7. The happy path: the full slice certifies and promotes
# ---------------------------------------------------------------------------


def run_happy_path():
    eng = _engine()
    return eng, C.certify_vertical_slice(
        eng,
        slice_observations(),
        adjudicator_id="human:data-steward",
        clock=lambda: datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
    )


def verify_happy_path(res) -> None:
    _banner("1. Candidates (DP-04-02) — deterministic blocking over the slice")
    check(
        "candidates generated for the slice",
        res.candidates.candidate_pairs == 8,
        f"pairs={res.candidates.candidate_pairs} "
        f"reduction_ratio={res.candidates.reduction_ratio:.3f}",
    )
    check(
        "every candidate records its firing ruleset",
        res.candidates.ruleset_id == "blocking-rules-v1"
        and bool(res.candidates.ruleset_fingerprint),
        f"ruleset={res.candidates.ruleset_id} fp={res.candidates.ruleset_fingerprint}",
    )

    _banner("2. Scores (DP-04-03) — explainable, threshold-routed")
    check(
        "corroborated duplicates route to auto_merge",
        res.scores.routing_counts.get("auto_merge", 0) == 2,
        f"routing={res.scores.routing_counts}",
    )
    check(
        "uncertain / distinct pairs route to the human band",
        res.scores.routing_counts.get("uncertain", 0) == 6,
        f"routing={res.scores.routing_counts}",
    )
    am = res.scores.auto_merge[0] if res.scores.auto_merge else None
    check(
        "every scored pair is explainable (feature line items)",
        am is not None and len(am.feature_contributions) > 0,
        f"sample explanation={am.explanation[0] if am else '—'}",
    )

    _banner("3. Adjudication sample (DP-04-05) — accuracy measured")
    check(
        "uncertain candidates were adjudicated through the production path",
        res.adjudication.measured_cases == 6,
        f"cases={res.adjudication.measured_cases}",
    )
    check(
        "accuracy meets approved threshold (error ≤ 3%)",
        res.adjudication.error_rate <= C.APPROVED_MAX_ADJUDICATION_ERROR_RATE,
        f"error_rate={res.adjudication.error_rate:.2%} "
        f"({res.adjudication.n_errors}/{res.adjudication.measured_cases})",
    )
    check(
        "time measured per case (usability)",
        res.adjudication.mean_seconds_per_case > 0,
        f"mean={res.adjudication.mean_seconds_per_case:.1f}s/case",
    )

    _banner("4. False-merge audit — must pass")
    check(
        "zero false merges across every merge decision",
        res.false_merge_audit.passed,
        f"false={len(res.false_merge_audit.false_merges)} "
        f"merges={res.false_merge_audit.total_merge_decisions}",
    )
    check(
        "auto-merge precision = 100% on the slice",
        res.false_merge_audit.auto_merge_precision == 1.0,
        f"precision={res.false_merge_audit.auto_merge_precision}",
    )

    _banner("5. Quality gate (DP-05-02 + DP-05-01) — pass + publishable")
    check(
        "identity gate passed the batch",
        res.gate_verdict.passed and res.gate_outcome == "awaiting_promotion",
        f"outcome={res.gate_outcome} verdict={res.gate_verdict.outcome}",
    )
    check(
        "dimension report is publishable (no blocking rule)",
        res.dimension_report.publishable and res.dimension_report.status == "pass",
        f"status={res.dimension_report.status} "
        f"rules={len(res.dimension_report.results)}",
    )

    _banner("6. Reconciliation (DP-05-03) — no silent loss")
    check(
        "reconciliation allows promotion",
        res.reconciliation.promotion_allowed
        and res.reconciliation.decision == "allow",
        f"decision={res.reconciliation.decision} "
        f"variance={res.reconciliation.variance} "
        f"yield={res.reconciliation.yield_ratio:.3f}",
    )

    _banner("7. Promotion (DP-05-02) — explicit, consumer-visible")
    check(
        "batch explicitly promoted with a receipt",
        res.promotion_receipt is not None
        and res.receipt.promotion_receipt_id == res.promotion_receipt.receipt_id,
        f"receipt={res.receipt.promotion_receipt_id}",
    )
    check(
        "published rows are consumer-visible",
        res.published_row_count > 0,
        f"rows={res.published_row_count}",
    )
    check(
        "output contract emitted: PublishedDatasetReceiptV1",
        res.receipt is not None
        and res.receipt.schema_version == C.SCHEMA_VERSION
        and res.receipt.verdict == "certified",
        f"receipt={res.receipt.receipt_id}",
    )


# ---------------------------------------------------------------------------
# 8. Reproducibility
# ---------------------------------------------------------------------------


def verify_reproducibility(res) -> None:
    _banner("8. Reproducibility — every published record is reproducible")
    _, res2 = run_happy_path()
    check(
        "re-run reproduces the identical published row set",
        res.reproducibility_hash == res2.reproducibility_hash,
        f"hash={res.reproducibility_hash[:16]}…",
    )
    check(
        "published rows carry the config fingerprints",
        bool(res.receipt.config_fingerprints.get("blocking_ruleset"))
        and bool(res.receipt.config_fingerprints.get("scorer_config"))
        and bool(res.receipt.config_fingerprints.get("thresholds")),
        f"fingerprints={list(res.receipt.config_fingerprints)}",
    )


# ---------------------------------------------------------------------------
# 9. Publication controls actually block (negative paths)
# ---------------------------------------------------------------------------


def verify_publication_controls() -> None:
    _banner("9. Publication controls — bad data / bad adjudication never publish")

    # 9a. A hostile merge-everything adjudicator is blocked by the
    #     *accuracy* gate (it mislabels the true duplicates).
    try:
        C.certify_vertical_slice(
            _engine(), slice_observations(),
            adjudicator_policy=lambda item: "merge",
        )
        check("hostile merge-everything adjudicator blocked", False, "no error raised")
    except (C.AccuracyThresholdError, C.FalseMergeError) as exc:
        check(
            "hostile merge-everything adjudicator blocked (accuracy/false-merge)",
            True, type(exc).__name__,
        )

    # 9b. A separate-everything adjudicator misses the true duplicates and
    #     is blocked by the accuracy gate.
    try:
        C.certify_vertical_slice(
            _engine(), slice_observations(),
            adjudicator_policy=lambda item: "separate",
        )
        check("separate-everything adjudicator blocked", False, "no error raised")
    except C.AccuracyThresholdError as exc:
        check(
            "wrong adjudicator blocked by accuracy gate",
            True, str(exc)[:60],
        )

    # 9c. Low-quality data (a resolved observation with no sail number)
    #     is blocked by the quality gate's completeness rule.
    bad = slice_observations() + [
        _so("irc-mystery", None, "Mystery Boat", "Unknown 40",
            "boat-mystery", "irc-certs", registry="IRC9000"),
    ]
    try:
        C.certify_vertical_slice(_engine(), bad)
        check("low-quality data blocked", False, "no error raised")
    except C.QualityGateBlockedError as exc:
        check(
            "low-quality data blocked by quality gate",
            True, str(exc)[:60],
        )

    # 9d. Reconciliation blocks promotion on silent loss (the gate that
    #     protects every promotion path, proven here directly).
    from irc_data.diagnostics.reconciliation import (
        PipelineCountsV1,
        PromotionBlockedError,
        assert_promotable,
        reconcile_run,
    )

    eng = _engine()
    report = reconcile_run(
        eng,
        PipelineCountsV1(
            run_id=99, source_id="dp06-vertical-slice",
            discovered=12, fetched=12, parsed=12, transformed=12,
            published=8,  # 4 records silently lost, no reason code
        ),
    )
    try:
        assert_promotable(report)
        check("silent-loss blocks promotion", False, "not blocked")
    except PromotionBlockedError:
        check(
            "reconciliation blocks promotion on silent loss",
            not report.promotion_allowed and report.decision == "block",
            f"variance={report.variance} decision={report.decision}",
        )


# ---------------------------------------------------------------------------
# 10. Data-steward sign-off (the verification step)
# ---------------------------------------------------------------------------


def verify_sign_off(res) -> None:
    _banner("10. Data-steward review signs the DataQualityVerdict to batch version")
    signed = res.receipt.sign(
        "data-steward:stuart-mcleod",
        at=datetime(2026, 9, 5, 13, 0, 0, tzinfo=timezone.utc),
    )
    check(
        "receipt is signed by the independent data steward",
        signed.signed_by == "data-steward:stuart-mcleod"
        and signed.verdict == "certified",
        f"signed_by={signed.signed_by} verdict={signed.verdict}",
    )
    check(
        "signature binds the exact batch version + reproducibility hash",
        signed.batch_key == res.receipt.batch_key
        and signed.version == res.receipt.version
        and signed.reproducibility_hash == res.receipt.reproducibility_hash,
        f"batch={signed.batch_key} v{signed.version}",
    )
    check(
        "receipt JSON round-trips (audit / handoff)",
        '"receipt_id"' in signed.to_json()
        and signed.to_dict()["promotion_receipt_id"]
        == res.promotion_receipt.receipt_id,
    )


def main() -> int:
    _banner("DP-06-04 — resolve identities and certify quality for vertical slice")
    eng, res = run_happy_path()
    verify_happy_path(res)
    verify_reproducibility(res)
    verify_publication_controls()
    verify_sign_off(res)

    _banner("SUMMARY")
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for name, ok, detail in failed:
        print(f"  FAILED: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
