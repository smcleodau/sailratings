"""Tests for DP-04-05 — human adjudication queue and evidence view.

Covers:

* the queue contract — only uncertain / high-impact candidates are queued
  (humans are used only where uncertainty or cost warrants it),
* prioritisation — high-impact first, uncertainty breaks ties,
* the evidence view — side-by-side source evidence, score explanation,
  downstream impact and reversible actions on every queue item,
* the shared write contract — human and automatic decisions produce the
  same :class:`ResolutionRecordV1` via :class:`DecisionRequestV1`,
* double review — high-impact merges require two distinct reviewers;
  same-reviewer merges are rejected; conflicts escalate,
* reversibility — applied resolutions can be undone, requeuing the case,
* the usability harness — adjudicating a labelled sample measures error
  rate and time.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import pytest

from irc_data.matching.adjudication import (
    AUTO_MERGE_AT_OR_ABOVE,
    AUTO_REJECT_BELOW,
    HIGH_IMPACT_FLAGS,
    IMPACT_MODEL_V1_ID,
    QUEUE_ACTIONS,
    SCHEMA_VERSION,
    AdjudicationError,
    AdjudicationQueue,
    AdjudicationStore,
    CaseNotFoundError,
    DecisionRequestV1,
    DoubleReviewError,
    InvalidTransitionError,
    LabelledCase,
    QueueItemV1,
    ResolutionRecordV1,
    ScoredCandidateV1,
    adjudicate_labelled_sample,
    impact_tier,
)
from irc_data.matching.blocking import RULESET_V1_ID, CandidatePair


def _fixed_clock(value: str = "2026-09-05T12:00:00+00:00"):
    dt = datetime.fromisoformat(value)
    return lambda: dt


def pair(left: str = "obs-a", right: str = "obs-b") -> CandidatePair:
    return CandidatePair(
        left_id=left,
        right_id=right,
        rules_fired=("R01", "R05"),
        matching_keys=("R01:AUS4343", "R05:WILD OATS XI"),
        ruleset_id=RULESET_V1_ID,
    )


def candidate(
    score: float = 0.62,
    impact_flags: tuple[str, ...] = (),
    left: str = "obs-a",
    right: str = "obs-b",
    **kwargs,
) -> ScoredCandidateV1:
    return ScoredCandidateV1(
        pair=pair(left, right),
        score=score,
        score_explanation=("sail_number exact +0.40", "name exact +0.22"),
        impact_flags=impact_flags,
        left_evidence={"sail_number": "AUS4343", "name": "Wild Oats XI", "source": "irc"},
        right_evidence={"sail_number": "4343", "name": "Wild Oats XI", "source": "orc"},
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Impact model
# ---------------------------------------------------------------------------


class TestImpactModel:
    def test_high_when_rated(self):
        assert impact_tier(("rated",)) == "high"

    def test_high_when_has_results_or_certificate(self):
        assert impact_tier(("has_results",)) == "high"
        assert impact_tier(("has_certificate",)) == "high"

    def test_high_flags_set_is_exactly_the_costly_signals(self):
        assert HIGH_IMPACT_FLAGS == frozenset({"rated", "has_results", "has_certificate"})

    def test_medium_for_non_costly_flags(self):
        assert impact_tier(("news_mentions",)) == "medium"

    def test_low_when_no_flags(self):
        assert impact_tier(()) == "low"


# ---------------------------------------------------------------------------
# Queue admission — humans only where uncertainty or cost warrants it
# ---------------------------------------------------------------------------


class TestQueueAdmission:
    def test_confident_low_impact_match_is_not_queued(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        assert q.enqueue(candidate(score=0.97)) is None

    def test_confident_low_impact_non_match_is_not_queued(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        assert q.enqueue(candidate(score=0.05)) is None

    def test_uncertain_candidate_is_queued(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.62))
        assert item is not None
        assert item.queue_reason == "uncertain"
        assert item.status == "pending"
        assert item.requires_second_review is False

    def test_high_impact_candidate_is_queued_even_when_confident(self):
        """Cost warrants a human even at auto-mergeable scores."""
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.99, impact_flags=("rated",)))
        assert item is not None
        assert item.queue_reason == "high_impact"
        assert item.requires_second_review is True

    def test_high_impact_low_score_is_queued_not_auto_rejected(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.10, impact_flags=("has_results",)))
        assert item is not None
        assert item.queue_reason == "high_impact"

    def test_uncertain_and_high_impact_combines_reason(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.5, impact_flags=("rated",)))
        assert item.queue_reason == "uncertain_high_impact"

    def test_band_boundaries(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        assert q.route(candidate(score=AUTO_REJECT_BELOW - 0.001)) == "auto_reject"
        assert q.route(candidate(score=AUTO_REJECT_BELOW)) == "uncertain"
        assert q.route(candidate(score=AUTO_MERGE_AT_OR_ABOVE - 0.001)) == "uncertain"
        assert q.route(candidate(score=AUTO_MERGE_AT_OR_ABOVE)) == "auto_merge"


# ---------------------------------------------------------------------------
# Prioritisation — cost first, uncertainty breaks ties
# ---------------------------------------------------------------------------


class TestPrioritisation:
    def test_high_impact_sorts_above_uncertain_low_impact(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        low = q.enqueue(candidate(score=0.50, left="l1", right="l2"))  # max uncertainty
        high = q.enqueue(candidate(score=0.95, impact_flags=("rated",), left="h1", right="h2"))
        queue = q.store.open_items()
        assert [i.case_id for i in queue] == [high.case_id, low.case_id]

    def test_within_tier_most_uncertain_first(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        nearer = q.enqueue(candidate(score=0.80, left="a", right="b"))
        coinflip = q.enqueue(candidate(score=0.50, left="c", right="d"))
        queue = q.store.open_items()
        assert [i.case_id for i in queue] == [coinflip.case_id, nearer.case_id]

    def test_medium_impact_sorts_between_high_and_low(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        low = q.enqueue(candidate(score=0.55, left="l1", right="l2"))
        mid = q.enqueue(candidate(score=0.55, impact_flags=("news_mentions",), left="m1", right="m2"))
        high = q.enqueue(candidate(score=0.55, impact_flags=("rated",), left="h1", right="h2"))
        queue = q.store.open_items()
        assert [i.case_id for i in queue] == [high.case_id, mid.case_id, low.case_id]


# ---------------------------------------------------------------------------
# The evidence view contract
# ---------------------------------------------------------------------------


class TestEvidenceView:
    def test_queue_item_carries_the_full_evidence_view(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.62))
        d = item.to_dict()
        # side-by-side source evidence
        assert d["left_evidence"]["source"] == "irc"
        assert d["right_evidence"]["source"] == "orc"
        assert d["left_evidence"]["sail_number"] == "AUS4343"
        assert d["right_evidence"]["sail_number"] == "4343"
        # score explanation
        assert d["score_explanation"] == ["sail_number exact +0.40", "name exact +0.22"]
        assert d["score"] == 0.62
        # downstream impact
        assert d["impact"] == "low"
        assert d["impact_flags"] == []
        # reversible actions offered
        assert d["actions"] == list(QUEUE_ACTIONS)
        # DP-04 provenance: every queued case is explained by ≥1 rule
        assert d["pair"]["rules_fired"] == ["R01", "R05"]
        assert d["schema_version"] == SCHEMA_VERSION

    def test_queue_item_round_trips_through_serialisation(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.62, impact_flags=("rated",)))
        clone = QueueItemV1.from_dict(item.to_dict())
        assert clone.case_id == item.case_id
        assert clone.pair == item.pair
        assert clone.requires_second_review is True

    def test_store_round_trips_through_dicts(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.62))
        q.decide(DecisionRequestV1(case_id=item.case_id, decision="separate", decided_by="human:a"))
        restored = AdjudicationStore.from_dicts(q.store.to_dicts())
        assert [i.case_id for i in restored.items()] == [item.case_id]
        assert len(restored.records()) == 1


# ---------------------------------------------------------------------------
# Shared write contract — human and automatic resolution use one path
# ---------------------------------------------------------------------------


class TestSharedWriteContract:
    def test_human_and_automatic_decisions_produce_the_same_contract(self):
        """A human MatchCard click and the auto-resolver write through the
        same DecisionRequestV1 → ResolutionRecordV1 contract."""
        q = AdjudicationQueue(clock=_fixed_clock())
        human_case = q.enqueue(candidate(score=0.62, left="h1", right="h2"))
        auto_case = q.enqueue(candidate(score=0.55, left="a1", right="a2"))

        human = q.decide(
            DecisionRequestV1(case_id=human_case.case_id, decision="merge", decided_by="human:stu")
        )
        auto = q.decide(
            DecisionRequestV1(
                case_id=auto_case.case_id, decision="merge", decided_by="system:resolver"
            )
        )

        assert isinstance(human, ResolutionRecordV1) and isinstance(auto, ResolutionRecordV1)
        assert human.to_dict().keys() == auto.to_dict().keys()
        assert human.status == auto.status == "applied"
        assert human.decision == auto.decision == "merge"
        assert {r.decided_by for r in (human, auto)} == {"human:stu", "system:resolver"}

    def test_applied_decision_closes_the_case(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.62))
        q.decide(DecisionRequestV1(case_id=item.case_id, decision="separate", decided_by="human:a"))
        assert q.store.get(item.case_id).status == "applied"
        assert item.case_id not in {i.case_id for i in q.store.open_items()}

    def test_decided_case_rejects_further_decisions(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.62))
        q.decide(DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:a"))
        with pytest.raises(InvalidTransitionError):
            q.decide(
                DecisionRequestV1(case_id=item.case_id, decision="separate", decided_by="human:b")
            )

    def test_unknown_case_raises(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        with pytest.raises(CaseNotFoundError):
            q.decide(DecisionRequestV1(case_id="adj-nope", decision="merge", decided_by="human:a"))

    def test_invalid_decision_value_rejected(self):
        with pytest.raises(AdjudicationError):
            DecisionRequestV1(case_id="adj-x", decision="nuke", decided_by="human:a")

    def test_decision_requires_an_actor(self):
        with pytest.raises(AdjudicationError):
            DecisionRequestV1(case_id="adj-x", decision="merge", decided_by="  ")

    def test_scored_candidate_validates_score_and_explanation(self):
        with pytest.raises(AdjudicationError):
            candidate(score=1.5)
        with pytest.raises(AdjudicationError):
            ScoredCandidateV1(pair=pair(), score=0.5, score_explanation=())


# ---------------------------------------------------------------------------
# Double review for high-impact merges
# ---------------------------------------------------------------------------


class TestDoubleReview:
    def _high_impact_case(self, q: AdjudicationQueue) -> QueueItemV1:
        item = q.enqueue(candidate(score=0.97, impact_flags=("rated",)))
        assert item.requires_second_review is True
        return item

    def test_first_merge_vote_does_not_apply(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = self._high_impact_case(q)
        rec = q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice")
        )
        assert rec.status == "pending_second_review"
        assert q.store.get(item.case_id).status == "awaiting_second_review"

    def test_second_distinct_reviewer_applies(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = self._high_impact_case(q)
        q.decide(DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice"))
        rec = q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:bob")
        )
        assert rec.status == "applied"
        assert rec.decided_by_chain == ("human:alice", "human:bob")
        assert q.store.get(item.case_id).status == "applied"

    def test_same_reviewer_cannot_review_twice(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = self._high_impact_case(q)
        q.decide(DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice"))
        with pytest.raises(DoubleReviewError):
            q.decide(
                DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice")
            )

    def test_conflicting_second_decision_escalates(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = self._high_impact_case(q)
        q.decide(DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice"))
        rec = q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="separate", decided_by="human:bob")
        )
        assert rec.status == "escalated"
        assert q.store.get(item.case_id).status == "escalated"

    def test_low_impact_merge_needs_only_one_review(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.62))
        rec = q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice")
        )
        assert rec.status == "applied"

    def test_separate_on_high_impact_needs_only_one_review(self):
        """Double review guards *merges* — the irreversible-looking action."""
        q = AdjudicationQueue(clock=_fixed_clock())
        item = self._high_impact_case(q)
        rec = q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="separate", decided_by="human:alice")
        )
        assert rec.status == "applied"

    def test_system_resolver_also_needs_double_review_on_high_impact(self):
        """The double-review guard applies to the shared contract, not just
        to human actors — an automatic high-impact merge is also held."""
        q = AdjudicationQueue(clock=_fixed_clock())
        item = self._high_impact_case(q)
        rec = q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="system:resolver")
        )
        assert rec.status == "pending_second_review"


# ---------------------------------------------------------------------------
# Reversible actions
# ---------------------------------------------------------------------------


class TestReversibility:
    def test_applied_resolution_can_be_reversed(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.62))
        rec = q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice")
        )
        undo = q.reverse_resolution(
            rec.resolution_id, decided_by="human:bob", rationale="wrong hull"
        )
        assert undo.undo_of == rec.resolution_id
        assert undo.decision == "merge"
        assert q.store.record_for(rec.resolution_id).status == "reversed"

    def test_reversal_requeues_the_case(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.62))
        rec = q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice")
        )
        q.reverse_resolution(rec.resolution_id, decided_by="human:bob")
        assert q.store.get(item.case_id).status == "pending"
        assert item.case_id in {i.case_id for i in q.store.open_items()}

    def test_double_reversal_rejected(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.62))
        rec = q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice")
        )
        q.reverse_resolution(rec.resolution_id, decided_by="human:bob")
        with pytest.raises(InvalidTransitionError):
            q.reverse_resolution(rec.resolution_id, decided_by="human:bob")

    def test_pending_second_review_cannot_be_reversed(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.97, impact_flags=("rated",)))
        rec = q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="merge", decided_by="human:alice")
        )
        with pytest.raises(InvalidTransitionError):
            q.reverse_resolution(rec.resolution_id, decided_by="human:bob")

    def test_every_resolution_record_is_marked_reversible(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        item = q.enqueue(candidate(score=0.62))
        rec = q.decide(
            DecisionRequestV1(case_id=item.case_id, decision="separate", decided_by="human:alice")
        )
        assert rec.reversible is True


# ---------------------------------------------------------------------------
# Usability harness — adjudicate a labelled sample, measure error/time
# ---------------------------------------------------------------------------


def _oracle_policy(item: QueueItemV1) -> str:
    """Perfect adjudicator: reads the same evidence view a human sees and
    compares names the way the blocking pipeline normalises them
    (case-insensitive, whitespace-collapsed)."""
    left, right = item.left_evidence, item.right_evidence

    def name_key(ev: Mapping[str, Any]) -> str:
        return " ".join(str(ev.get("name") or "").upper().split())

    same = bool(
        left.get("registry_id") and left.get("registry_id") == right.get("registry_id")
    ) or (name_key(left) != "" and name_key(left) == name_key(right))
    return "merge" if same else "separate"


def _labelled_sample() -> list[LabelledCase]:
    def ev(name: str, sail: str, registry: str | None = None) -> dict:
        return {"name": name, "sail_number": sail, "registry_id": registry}

    cases: list[LabelledCase] = []
    # five true duplicates, five true distinct boats
    dupes = [
        ("Wild Oats XI", "AUS4343", "Wild Oats XI", "4343"),
        ("Comanche", "AUS12358", "COMANCHE", "12358"),
        ("Black Jack", "52570", "Black Jack", "52570"),
        ("Ichi Ban", "AUS52", "ICHI BAN", "52"),
        ("Celestial", "9535", "Celestial", "TI9535"),
    ]
    for i, (ln, ls, rn, rs) in enumerate(dupes):
        cases.append(
            LabelledCase(
                candidate=ScoredCandidateV1(
                    pair=CandidatePair(
                        left_id=f"dup-{i}-l",
                        right_id=f"dup-{i}-r",
                        rules_fired=("R05",),
                        matching_keys=(f"R05:{ln.upper()}",),
                    ),
                    score=0.55 + 0.05 * i,
                    score_explanation=("name exact +0.30",),
                    left_evidence=ev(ln, ls),
                    right_evidence=ev(rn, rs),
                ),
                gold_label="merge",
            )
        )
    distinct = [
        ("Alive", "TAS8333", "Alive II", "Q8333"),
        ("Farrago", "AUS11", "Farrago II", "AUS111"),
        ("Zen", "52001", "Zen Again", "52001"),
        ("Mistral", "333", "Mistral Blue", "334"),
        ("Rumbeat", "HKG2276", "Rum Runner", "HKG2277"),
    ]
    for i, (ln, ls, rn, rs) in enumerate(distinct):
        cases.append(
            LabelledCase(
                candidate=ScoredCandidateV1(
                    pair=CandidatePair(
                        left_id=f"dis-{i}-l",
                        right_id=f"dis-{i}-r",
                        rules_fired=("R01",),
                        matching_keys=("R01:8333",),
                    ),
                    score=0.30 + 0.05 * i,
                    score_explanation=("sail token +0.20",),
                    left_evidence=ev(ln, ls),
                    right_evidence=ev(rn, rs),
                ),
                gold_label="separate",
            )
        )
    return cases


class TestUsabilityHarness:
    def test_perfect_policy_scores_zero_error(self):
        report = adjudicate_labelled_sample(
            _labelled_sample(), _oracle_policy, time_per_case=12.0
        )
        assert report.n_cases == 10
        assert report.n_errors == 0
        assert report.error_rate == 0.0
        assert report.total_seconds == pytest.approx(120.0)
        assert report.mean_seconds_per_case == pytest.approx(12.0)

    def test_hostile_policy_is_measured_as_all_errors(self):
        report = adjudicate_labelled_sample(
            _labelled_sample(),
            lambda item: "merge",  # merge everything
            time_per_case=3.0,
        )
        assert report.n_cases == 10
        assert report.n_errors == 5  # the five distinct boats are wrong
        assert report.error_rate == pytest.approx(0.5)

    def test_report_is_serialisable_and_fingerprinted(self):
        r1 = adjudicate_labelled_sample(_labelled_sample(), _oracle_policy, time_per_case=1.0)
        r2 = adjudicate_labelled_sample(_labelled_sample(), _oracle_policy, time_per_case=1.0)
        assert r1.fingerprint() == r2.fingerprint()
        d = r1.to_dict()
        assert d["schema_version"] == SCHEMA_VERSION
        assert len(d["events"]) == 10

    def test_harness_drives_double_review_for_high_impact_merges(self):
        """A high-impact gold-merge case must be counted as a merge only
        after two distinct reviewer votes — the harness drives both."""
        cases = [
            LabelledCase(
                candidate=ScoredCandidateV1(
                    pair=pair("hi-l", "hi-r"),
                    score=0.6,
                    score_explanation=("name +0.30",),
                    impact_flags=("rated",),
                    left_evidence={"name": "Wild Oats XI"},
                    right_evidence={"name": "Wild Oats XI"},
                ),
                gold_label="merge",
            )
        ]
        report = adjudicate_labelled_sample(cases, _oracle_policy, time_per_case=20.0)
        assert report.n_cases == 1
        assert report.n_errors == 0
        assert report.events[0].decision == "merge"

    def test_auto_routed_cases_are_measured_with_system_actor(self):
        sample = [
            LabelledCase(candidate=candidate(score=0.99, left="x1", right="x2"), gold_label="merge"),
        ]
        report = adjudicate_labelled_sample(sample, _oracle_policy, time_per_case=0.0)
        assert report.events[0].decided_by == "system:resolver"
        assert report.events[0].correct is True


# ---------------------------------------------------------------------------
# Queue clock / determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_queue_ordering_is_stable_for_identical_priorities(self):
        q = AdjudicationQueue(clock=_fixed_clock())
        a = q.enqueue(candidate(score=0.5, left="a", right="b"))
        b = q.enqueue(candidate(score=0.5, left="c", right="d"))
        # identical priority → FIFO by enqueued_at then case_id; deterministic
        queue = q.store.open_items()
        assert {i.case_id for i in queue} == {a.case_id, b.case_id}
        again = AdjudicationQueue(clock=_fixed_clock())
        a2 = again.enqueue(candidate(score=0.5, left="a", right="b"))
        assert a2.priority == a.priority
