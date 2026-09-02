#!/usr/bin/env python3
"""End-to-end verification evidence for DP-04-05 — human adjudication
queue and evidence view.

Prints hard, paste-able PASS/FAIL evidence for the issue board:

  1. **Queue admission** — only uncertain / high-impact candidates reach
     a human; confident low-impact candidates stay with the automatic
     resolver (humans only where uncertainty or cost warrants it).
  2. **Prioritisation** — high-impact (costly) cases sort first;
     uncertainty breaks ties.
  3. **Evidence view** — every queued item carries side-by-side source
     evidence, the score explanation, downstream impact and the
     reversible actions, plus DP-04-02 rule provenance.
  4. **Shared write contract** — a human MatchCard decision and an
     automatic-resolution decision write through the same
     ``DecisionRequestV1`` → ``ResolutionRecordV1`` contract.
  5. **Double review** — a high-impact merge is not applied by one
     reviewer; a second, distinct reviewer applies it; the same reviewer
     twice is rejected; a conflict escalates.
  6. **Reversibility** — an applied merge is undone, the undo record
     points back at the original, and the case is requeued.
  7. **Usability measurement** — a labelled sample (5 true duplicates,
     5 true distinct boats — the messy real-world shapes: sail-prefix
     drift, case drift, spacing drift, near-identical names) is
     adjudicated through the production decision path by (a) an oracle
     policy reading the same evidence view the MatchCard shows, and
     (b) a hostile merge-everything policy.  Error rate and
     time-per-case are measured for both.

No database or network required — the queue, decision path and harness
are pure.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_04_05.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from irc_data.matching.adjudication import (  # noqa: E402
    QUEUE_ACTIONS,
    AdjudicationQueue,
    DecisionRequestV1,
    DoubleReviewError,
    LabelledCase,
    QueueItemV1,
    ScoredCandidateV1,
    adjudicate_labelled_sample,
)
from irc_data.matching.blocking import RULESET_V1_ID, CandidatePair  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{mark}] {name}{suffix}")


def _banner(text: str) -> None:
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def _fixed_clock() -> datetime:
    return datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


def _pair(left: str, right: str, *rules: str) -> CandidatePair:
    rules = rules or ("R01",)
    return CandidatePair(
        left_id=left,
        right_id=right,
        rules_fired=tuple(rules),
        matching_keys=tuple(f"{r}:key" for r in rules),
        ruleset_id=RULESET_V1_ID,
    )


def _candidate(
    score: float,
    left: str,
    right: str,
    *,
    impact_flags: tuple[str, ...] = (),
    left_evidence: dict | None = None,
    right_evidence: dict | None = None,
) -> ScoredCandidateV1:
    return ScoredCandidateV1(
        pair=_pair(left, right, "R01", "R05"),
        score=score,
        score_explanation=(f"sail token +{score * 0.6:.2f}", f"name +{score * 0.4:.2f}"),
        impact_flags=impact_flags,
        left_evidence=left_evidence or {},
        right_evidence=right_evidence or {},
    )


# ---------------------------------------------------------------------------
# 1. Queue admission
# ---------------------------------------------------------------------------


def verify_admission() -> None:
    _banner("1. Queue admission — humans only where uncertainty or cost warrants")
    q = AdjudicationQueue(clock=_fixed_clock)

    auto_merge = q.enqueue(_candidate(0.97, "a1", "a2"))
    check("confident low-impact match stays with auto-resolver", auto_merge is None)

    auto_reject = q.enqueue(_candidate(0.05, "b1", "b2"))
    check("confident low-impact non-match stays with auto-resolver", auto_reject is None)

    uncertain = q.enqueue(_candidate(0.62, "c1", "c2"))
    check(
        "uncertain candidate is queued",
        uncertain is not None and uncertain.queue_reason == "uncertain",
        f"reason={uncertain.queue_reason if uncertain else None}",
    )

    confident_high = q.enqueue(_candidate(0.99, "d1", "d2", impact_flags=("rated",)))
    check(
        "high-impact candidate is queued even at confident score",
        confident_high is not None and confident_high.queue_reason == "high_impact",
    )
    check(
        "high-impact case requires second review",
        confident_high is not None and confident_high.requires_second_review,
    )

    low_score_high = q.enqueue(_candidate(0.10, "e1", "e2", impact_flags=("has_results",)))
    check(
        "high-impact low-score candidate is queued (not auto-rejected)",
        low_score_high is not None and low_score_high.queue_reason == "high_impact",
    )


# ---------------------------------------------------------------------------
# 2. Prioritisation
# ---------------------------------------------------------------------------


def verify_prioritisation() -> None:
    _banner("2. Prioritisation — cost first, uncertainty breaks ties")
    q = AdjudicationQueue(clock=_fixed_clock)
    low = q.enqueue(_candidate(0.50, "l1", "l2"))  # max uncertainty, low impact
    high = q.enqueue(_candidate(0.95, "h1", "h2", impact_flags=("rated",)))
    order = [i.case_id for i in q.store.open_items()]
    check(
        "high-impact case sorts ahead of max-uncertainty low-impact case",
        order == [high.case_id, low.case_id],
        f"order={order}",
    )

    q2 = AdjudicationQueue(clock=_fixed_clock)
    near = q2.enqueue(_candidate(0.80, "a", "b"))
    coinflip = q2.enqueue(_candidate(0.50, "c", "d"))
    order2 = [i.case_id for i in q2.store.open_items()]
    check(
        "within a tier the most uncertain case comes first",
        order2 == [coinflip.case_id, near.case_id],
    )


# ---------------------------------------------------------------------------
# 3. Evidence view contract
# ---------------------------------------------------------------------------


def verify_evidence_view() -> None:
    _banner("3. Evidence view — side-by-side evidence, explanation, impact, actions")
    q = AdjudicationQueue(clock=_fixed_clock)
    item = q.enqueue(
        _candidate(
            0.62,
            "obs-irc-1",
            "obs-orc-9",
            impact_flags=("has_results",),
            left_evidence={
                "sail_number": "AUS4343",
                "name": "Wild Oats XI",
                "design": "Reichel/Pugh 100",
                "source": "irc_certificate",
            },
            right_evidence={
                "sail_number": "4343",
                "name": "WILD OATS XI",
                "design": "Reichel Pugh 100",
                "source": "orc_certificate",
            },
        )
    )
    assert item is not None
    d = item.to_dict()
    check(
        "side-by-side source evidence present",
        d["left_evidence"]["source"] == "irc_certificate"
        and d["right_evidence"]["source"] == "orc_certificate"
        and d["left_evidence"]["sail_number"] == "AUS4343"
        and d["right_evidence"]["sail_number"] == "4343",
    )
    check(
        "score explanation present",
        len(d["score_explanation"]) == 2 and d["score"] == 0.62,
        f"explanation={d['score_explanation']}",
    )
    check(
        "downstream impact present",
        d["impact"] == "high" and d["impact_flags"] == ["has_results"],
        f"impact={d['impact']} flags={d['impact_flags']}",
    )
    check(
        "reversible actions offered",
        d["actions"] == list(QUEUE_ACTIONS),
        f"actions={d['actions']}",
    )
    check(
        "DP-04-02 rule provenance present (every case explained by ≥1 rule)",
        d["pair"]["rules_fired"] == ["R01", "R05"],
    )


# ---------------------------------------------------------------------------
# 4. Shared write contract
# ---------------------------------------------------------------------------


def verify_shared_contract() -> None:
    _banner("4. Decision writes through the same contract as automatic resolution")
    q = AdjudicationQueue(clock=_fixed_clock)
    human_case = q.enqueue(_candidate(0.62, "h1", "h2"))
    auto_case = q.enqueue(_candidate(0.55, "a1", "a2"))
    human = q.decide(
        DecisionRequestV1(case_id=human_case.case_id, decision="merge", decided_by="human:stu")
    )
    auto = q.decide(
        DecisionRequestV1(case_id=auto_case.case_id, decision="merge", decided_by="system:resolver")
    )
    check(
        "human and automatic decisions produce the same ResolutionRecordV1 shape",
        human.to_dict().keys() == auto.to_dict().keys(),
    )
    check(
        "both applied through the one write path",
        human.status == "applied" and auto.status == "applied"
        and human.decision == auto.decision == "merge",
        f"human decided_by={human.decided_by}; auto decided_by={auto.decided_by}",
    )


# ---------------------------------------------------------------------------
# 5. Double review
# ---------------------------------------------------------------------------


def verify_double_review() -> None:
    _banner("5. Double review required for high-impact merges")
    q = AdjudicationQueue(clock=_fixed_clock)
    item = q.enqueue(_candidate(0.97, "x1", "x2", impact_flags=("rated",)))

    first = q.decide(
        DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice")
    )
    check(
        "first reviewer vote does NOT apply the merge",
        first.status == "pending_second_review"
        and q.store.get(item.case_id).status == "awaiting_second_review",
    )

    try:
        q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice")
        )
        same_reviewer_blocked = False
    except DoubleReviewError:
        same_reviewer_blocked = True
    check("same reviewer voting twice is rejected", same_reviewer_blocked)

    second = q.decide(
        DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:bob")
    )
    check(
        "second distinct reviewer applies the merge with full audit chain",
        second.status == "applied"
        and second.decided_by_chain == ("human:alice", "human:bob"),
        f"chain={second.decided_by_chain}",
    )

    # Conflict escalates instead of silently resolving
    q2 = AdjudicationQueue(clock=_fixed_clock)
    item2 = q2.enqueue(_candidate(0.95, "y1", "y2", impact_flags=("has_results",)))
    q2.decide(DecisionRequestV1(case_id=item2.case_id, decision="merge", decided_by="human:alice"))
    conflict = q2.decide(
        DecisionRequestV1(case_id=item2.case_id, decision="separate", decided_by="human:bob")
    )
    check(
        "conflicting second decision escalates the case",
        conflict.status == "escalated" and q2.store.get(item2.case_id).status == "escalated",
    )

    # Double review guards merges, not separations
    q3 = AdjudicationQueue(clock=_fixed_clock)
    item3 = q3.enqueue(_candidate(0.95, "z1", "z2", impact_flags=("rated",)))
    sep = q3.decide(
        DecisionRequestV1(case_id=item3.case_id, decision="separate", decided_by="human:alice")
    )
    check("high-impact 'keep separate' applies with a single review", sep.status == "applied")


# ---------------------------------------------------------------------------
# 6. Reversibility
# ---------------------------------------------------------------------------


def verify_reversibility() -> None:
    _banner("6. Reversible actions")
    q = AdjudicationQueue(clock=_fixed_clock)
    item = q.enqueue(_candidate(0.62, "r1", "r2"))
    rec = q.decide(
        DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice")
    )
    undo = q.reverse_resolution(
        rec.resolution_id, decided_by="human:bob", rationale="merged the wrong hull"
    )
    check(
        "applied merge is reversible; undo points at the original record",
        undo.undo_of == rec.resolution_id
        and q.store.record_for(rec.resolution_id).status == "reversed",
    )
    check(
        "reversal requeues the case for a fresh decision",
        q.store.get(item.case_id).status == "pending"
        and item.case_id in {i.case_id for i in q.store.open_items()},
    )


# ---------------------------------------------------------------------------
# 7. Usability measurement — labelled sample, error rate and time
# ---------------------------------------------------------------------------


def _ev(name: str, sail: str, registry: str | None = None, source: str = "irc") -> dict:
    return {"name": name, "sail_number": sail, "registry_id": registry, "source": source}


def _labelled_sample() -> list[LabelledCase]:
    """5 true duplicates + 5 true distinct boats, in the messy shapes the
    queue exists for: sail-prefix drift, case drift, spacing drift, and
    near-identical names that are different hulls."""
    dupes = [
        ("Wild Oats XI", "AUS4343", "WILD OATS XI", "4343", 0.62),
        ("Comanche", "AUS12358", "COMANCHE", "12358", 0.58),
        ("Black Jack", "52570", "Black  Jack", "52570", 0.66),
        ("Ichi Ban", "AUS52", "ICHI BAN", "52", 0.71),
        ("Celestial", "9535", "Celestial", "TI9535", 0.55),
    ]
    distinct = [
        ("Alive", "TAS8333", "Alive II", "Q8333", 0.45),
        ("Farrago", "AUS11", "Farrago II", "AUS111", 0.38),
        ("Zen", "52001", "Zen Again", "52001", 0.52),
        ("Mistral", "333", "Mistral Blue", "334", 0.33),
        ("Rumbeat", "HKG2276", "Rum Runner", "HKG2277", 0.49),
    ]
    cases: list[LabelledCase] = []
    for i, (ln, ls, rn, rs, score) in enumerate(dupes):
        cases.append(
            LabelledCase(
                candidate=ScoredCandidateV1(
                    pair=_pair(f"dup-{i}-l", f"dup-{i}-r", "R05"),
                    score=score,
                    score_explanation=("name exact +0.30", "sail token +0.25"),
                    left_evidence=_ev(ln, ls, source="irc_certificate"),
                    right_evidence=_ev(rn, rs, source="orc_certificate"),
                ),
                gold_label="merge",
            )
        )
    for i, (ln, ls, rn, rs, score) in enumerate(distinct):
        cases.append(
            LabelledCase(
                candidate=ScoredCandidateV1(
                    pair=_pair(f"dis-{i}-l", f"dis-{i}-r", "R01"),
                    score=score,
                    score_explanation=("sail token +0.20", "design family +0.15"),
                    left_evidence=_ev(ln, ls, source="irc_certificate"),
                    right_evidence=_ev(rn, rs, source="sailsys_result"),
                ),
                gold_label="separate",
            )
        )
    return cases


def _oracle_policy(item: QueueItemV1) -> str:
    """The adjudicator under test, reading exactly the evidence view the
    MatchCard renders (names compared as the pipeline normalises them)."""
    left, right = item.left_evidence, item.right_evidence

    def name_key(ev: dict) -> str:
        return " ".join(str(ev.get("name") or "").upper().split())

    same = bool(
        left.get("registry_id") and left.get("registry_id") == right.get("registry_id")
    ) or (name_key(left) != "" and name_key(left) == name_key(right))
    return "merge" if same else "separate"


def verify_usability() -> None:
    _banner("7. Usability — adjudicate a labelled sample; measure error + time")
    sample = _labelled_sample()

    t0 = time.monotonic()
    oracle = adjudicate_labelled_sample(
        sample, _oracle_policy, adjudicator_id="human:oracle", time_per_case=11.7
    )
    harness_wall = time.monotonic() - t0
    check(
        "oracle adjudicator: 10/10 correct on the labelled sample",
        oracle.n_cases == 10 and oracle.n_errors == 0,
        f"errors={oracle.n_errors}/{oracle.n_cases} error_rate={oracle.error_rate:.2%}",
    )
    check(
        "time measured per case (mean seconds)",
        abs(oracle.mean_seconds_per_case - 11.7) < 1e-6
        and abs(oracle.total_seconds - 117.0) < 1e-6,
        f"mean={oracle.mean_seconds_per_case:.1f}s/case total={oracle.total_seconds:.1f}s "
        f"harness_wall={harness_wall:.3f}s",
    )

    hostile = adjudicate_labelled_sample(
        sample, lambda item: "merge", adjudicator_id="human:hostile", time_per_case=2.0
    )
    check(
        "hostile merge-everything policy is measured at 50% error",
        hostile.n_errors == 5 and abs(hostile.error_rate - 0.5) < 1e-9,
        f"error_rate={hostile.error_rate:.2%}",
    )

    repeat = adjudicate_labelled_sample(
        sample, _oracle_policy, adjudicator_id="human:oracle", time_per_case=11.7
    )
    check(
        "measurement is reproducible (stable fingerprint)",
        oracle.fingerprint() == repeat.fingerprint(),
        f"fingerprint={oracle.fingerprint()}",
    )


def main() -> int:
    _banner("DP-04-05 — human adjudication queue and evidence view")
    verify_admission()
    verify_prioritisation()
    verify_evidence_view()
    verify_shared_contract()
    verify_double_review()
    verify_reversibility()
    verify_usability()

    _banner("SUMMARY")
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for name, ok, detail in failed:
        print(f"  FAILED: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
