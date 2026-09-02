"""Contract and acceptance tests for DP-04-01 — identity candidate and
match-decision contracts.

Three halves:

1. **Schema-fixture coverage** — the issue's verification criterion:
   fixtures exist and round-trip for match, non-match, uncertain, split
   and superseded decisions, each carrying pair, features, scores,
   model/rule version, evidence, decision, actor, timestamp and
   supersession.
2. **Contract validation** — score/similarity bounds, version
   requirements, deterministic ids, JSON round-trip, bitemporal
   supersession.
3. **Acceptance criteria** — (a) no merge occurs without stored evidence
   and threshold/policy, enforced at both decision time and the registry
   gate; (b) decisions are reversed without deleting source assertions,
   with the pre-decision state reconstructable for any prior system
   time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from irc_data.assertions import AssertionV1
from irc_data.domain import DomainModel
from irc_data.domain.matching import (
    ActorKind,
    BelowThresholdError,
    CandidatePairV1,
    DecisionStateError,
    DecisionType,
    EvidenceRef,
    FeatureScoreV1,
    MatchDecisionError,
    MatchDecisionV1,
    MatchJournal,
    MatchPolicy,
    MissingEvidenceError,
    MissingPolicyError,
    apply_decision,
    decide,
    reverse_decision,
)

from .fixtures import (
    ALL_DECISIONS,
    BOAT_LEFT,
    BOAT_OTHER_A,
    BOAT_OTHER_B,
    BOAT_RIGHT,
    BOAT_UNCERTAIN_A,
    BOAT_UNCERTAIN_B,
    CANDIDATE_FIXTURES,
    CANDIDATE_MATCH,
    CANDIDATE_NON_MATCH,
    CANDIDATE_UNCERTAIN,
    DECISION_FIXTURES,
    DECISION_MATCH,
    DECISION_MATCH_SUPERSEDED,
    DECISION_NON_MATCH,
    DECISION_SPLIT,
    DECISION_UNCERTAIN,
    FIXTURE_DOCS,
    POLICY,
    SPLIT_ASSERTION_IDS,
    SUPERSESSION_CHAIN,
    T0,
    T1,
    T2,
    T3,
    T4,
    fixtures_json,
)

UTC = timezone.utc
DAY = timedelta(days=1)


def T(y: int, m: int = 1, d: int = 1) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. Schema-fixture coverage (verification criterion)
# ---------------------------------------------------------------------------


class TestSchemaFixtureCoverage:
    """Every required decision state has a schema fixture, and each
    fixture is fully populated per the issue's scope."""

    REQUIRED = ("match", "non_match", "uncertain", "split", "superseded")

    def test_all_five_decision_states_have_fixtures(self):
        assert tuple(sorted(DECISION_FIXTURES)) == tuple(sorted(self.REQUIRED))
        assert tuple(sorted(CANDIDATE_FIXTURES)) == tuple(sorted(self.REQUIRED))

    @pytest.mark.parametrize("name", REQUIRED)
    def test_decision_fixture_scope_fields(self, name):
        d = DECISION_FIXTURES[name]
        # decision + actor + timestamp + policy
        assert d["decision"] in ("match", "non_match", "uncertain", "split")
        assert d["actor"]
        assert d["actor_kind"] in ("auto_rule", "model", "human")
        assert d["decided_at"] is not None
        assert d["threshold_policy"] == POLICY.name
        # candidate pair
        cand = d["candidate"]
        assert cand["left_id"] and cand["right_id"]
        assert cand["left_id"] != cand["right_id"]
        assert cand["pair_key"] == sorted([cand["left_id"], cand["right_id"]])
        # features + scores
        assert 0.0 <= cand["score"] <= 1.0
        assert cand["features"], "a candidate must carry its compared features"
        for f in cand["features"]:
            assert f["name"]
            assert f["similarity"] is None or 0.0 <= f["similarity"] <= 1.0
            assert f["weight"] >= 0.0
        # model / rule version
        assert cand["model_version"] or cand["rule_version"]
        # serialisable to JSON
        assert isinstance(fixtures_json(), str)

    @pytest.mark.parametrize("name", REQUIRED)
    def test_decision_fixtures_round_trip(self, name):
        d = MatchDecisionV1.from_dict(DECISION_FIXTURES[name])
        assert d.to_dict() == DECISION_FIXTURES[name]
        assert MatchDecisionV1.from_json(d.to_json()) == d

    @pytest.mark.parametrize("name", REQUIRED)
    def test_candidate_fixtures_round_trip(self, name):
        c = CandidatePairV1.from_dict(CANDIDATE_FIXTURES[name])
        assert c.to_dict() == CANDIDATE_FIXTURES[name]
        assert CandidatePairV1.from_json(c.to_json()) == c

    def test_match_fixture_carries_stored_evidence(self):
        d = DECISION_FIXTURES["match"]
        assert len(d["evidence"]) >= 1
        for e in d["evidence"]:
            assert e["kind"] and e["ref"]

    def test_split_fixture_names_assertions_to_move(self):
        d = DECISION_FIXTURES["split"]
        assert d["decision"] == "split"
        assert tuple(d["split_assertion_ids"]) == SPLIT_ASSERTION_IDS
        assert d["actor_kind"] == "human"

    def test_superseded_fixture_links_both_ways(self):
        sup = DECISION_FIXTURES["superseded"]
        split = DECISION_FIXTURES["split"]
        assert sup["decision"] == "match"
        assert sup["superseded_by"] == split["decision_id"]
        assert sup["superseded_at"] == split["decided_at"]
        assert split["supersedes"] == sup["decision_id"]
        assert split["reversal_of"] == sup["decision_id"]

    def test_fixture_document_shape(self):
        assert FIXTURE_DOCS["schema_version"] == "match-decision-v1"
        assert FIXTURE_DOCS["policy"]["name"] == POLICY.name
        assert set(FIXTURE_DOCS["candidates"]) == set(self.REQUIRED)
        assert set(FIXTURE_DOCS["decisions"]) == set(self.REQUIRED)


# ---------------------------------------------------------------------------
# 2. Contract validation
# ---------------------------------------------------------------------------


class TestCandidateContract:
    def test_pair_must_be_distinct(self):
        with pytest.raises(MatchDecisionError):
            CandidatePairV1(
                entity_type="boat",
                left_id=BOAT_LEFT,
                right_id=BOAT_LEFT,
                rule_version="r1",
            )

    def test_version_required(self):
        with pytest.raises(MatchDecisionError):
            CandidatePairV1(
                entity_type="boat", left_id=BOAT_LEFT, right_id=BOAT_RIGHT
            )

    def test_score_bounds(self):
        with pytest.raises(MatchDecisionError):
            CandidatePairV1(
                entity_type="boat",
                left_id=BOAT_LEFT,
                right_id=BOAT_RIGHT,
                score=1.01,
                rule_version="r1",
            )

    def test_similarity_bounds(self):
        with pytest.raises(MatchDecisionError):
            FeatureScoreV1(name="sail_number", similarity=1.5)

    def test_pair_key_is_order_independent(self):
        flipped = CandidatePairV1(
            entity_type="boat",
            left_id=CANDIDATE_MATCH.right_id,
            right_id=CANDIDATE_MATCH.left_id,
            features=CANDIDATE_MATCH.features,
            score=CANDIDATE_MATCH.score,
            model_version=CANDIDATE_MATCH.model_version,
            rule_version=CANDIDATE_MATCH.rule_version,
            generated_at=CANDIDATE_MATCH.generated_at,
        )
        assert flipped.pair_key == CANDIDATE_MATCH.pair_key
        assert flipped.candidate_id == CANDIDATE_MATCH.candidate_id

    def test_deterministic_content_id(self):
        again = CandidatePairV1.from_dict(CANDIDATE_MATCH.to_dict())
        assert again.candidate_id == CANDIDATE_MATCH.candidate_id
        assert again.candidate_id.startswith("cand_")


class TestDecisionContract:
    def test_actor_required(self):
        with pytest.raises(MatchDecisionError):
            decide(
                CANDIDATE_MATCH,
                DecisionType.MATCH,
                actor="",
                actor_kind=ActorKind.AUTO_RULE,
                policy=POLICY,
            )

    def test_policy_required(self):
        with pytest.raises(MissingPolicyError):
            MatchDecisionV1(
                candidate=CANDIDATE_MATCH,
                decision="match",
                actor="x",
                actor_kind="human",
                threshold_policy="",
            )

    def test_unknown_decision_rejected(self):
        with pytest.raises(MatchDecisionError):
            decide(
                CANDIDATE_MATCH,
                "maybe",
                actor="x",
                actor_kind=ActorKind.HUMAN,
                policy=POLICY,
            )

    def test_auto_match_below_threshold_rejected_at_decision_time(self):
        with pytest.raises(BelowThresholdError):
            decide(
                CANDIDATE_UNCERTAIN,  # score 0.66 < 0.90 merge threshold
                DecisionType.MATCH,
                actor="auto-merger",
                actor_kind=ActorKind.AUTO_RULE,
                policy=POLICY,
                evidence=(EvidenceRef(kind="assertion", ref="abc"),),
                decided_at=T3,
            )

    def test_auto_non_match_above_threshold_rejected(self):
        with pytest.raises(BelowThresholdError):
            decide(
                CANDIDATE_MATCH,  # score 0.96 >= merge threshold
                DecisionType.NON_MATCH,
                actor="auto-merger",
                actor_kind=ActorKind.AUTO_RULE,
                policy=POLICY,
                decided_at=T1,
            )

    def test_split_requires_assertion_ids(self):
        with pytest.raises(MatchDecisionError):
            decide(
                CANDIDATE_MATCH,
                DecisionType.SPLIT,
                actor="steward",
                actor_kind=ActorKind.HUMAN,
                policy=POLICY,
                decided_at=T4,
            )

    def test_decision_ids_deterministic_and_prefixed(self):
        for d in (DECISION_MATCH, DECISION_NON_MATCH, DECISION_UNCERTAIN, DECISION_SPLIT):
            assert d.id.startswith("dec_")
            assert MatchDecisionV1.from_dict(d.to_dict()).id == d.id

    def test_cannot_supersede_self(self):
        with pytest.raises(MatchDecisionError):
            DECISION_MATCH.superseded(DECISION_MATCH)

    def test_naive_timestamps_normalised_to_utc(self):
        d = decide(
            CANDIDATE_NON_MATCH,
            DecisionType.NON_MATCH,
            actor="auto-merger",
            actor_kind=ActorKind.AUTO_RULE,
            policy=POLICY,
            decided_at=datetime(2025, 6, 3, 9, 0, 0),  # naive
        )
        assert d.decided_at.tzinfo is not None
        assert d.decided_at == DECISION_NON_MATCH.decided_at


# ---------------------------------------------------------------------------
# 3. Acceptance criterion 1 — no merge without stored evidence + policy
# ---------------------------------------------------------------------------


def _registry_with_pair():
    """A DomainModel holding the two fixture hulls, each with one fact."""
    m = DomainModel()
    left = m.create_entity("boat", at=T(2025, 1))
    right = m.create_entity("boat", at=T(2025, 1))
    for boat, tcc in ((left, 1.012), (right, 1.014)):
        m.assert_about(
            boat.entity_id,
            AssertionV1(
                entity_type="boat",
                entity_key=boat.entity_key,
                field="tcc",
                value=tcc,
                source_slug="sailsys",
                recorded_at=T(2025, 1),
                valid_from=T(2025, 1),
            ),
        )
    return m, left, right


def _candidate_for(left, right, *, score=0.96) -> CandidatePairV1:
    return CandidatePairV1(
        entity_type="boat",
        left_id=left.entity_id,
        right_id=right.entity_id,
        features=(
            FeatureScoreV1(
                name="sail_number",
                value_left="AUS 8338",
                value_right="AUS8338",
                similarity=1.0,
                weight=1.0,
            ),
        ),
        score=score,
        rule_version="blocking-rules-v3",
        generated_at=T(2025, 2),
    )


class TestMergeGate:
    def test_match_decision_applies_merge(self):
        m, left, right = _registry_with_pair()
        decision = decide(
            _candidate_for(left, right),
            DecisionType.MATCH,
            actor="auto-merger",
            actor_kind=ActorKind.AUTO_RULE,
            policy=POLICY,
            evidence=(EvidenceRef(kind="assertion", ref="deadbeef" * 8),),
            decided_at=T(2025, 3),
        )
        survivor = apply_decision(m, decision, policy=POLICY, at=T(2025, 3))
        assert survivor is not None
        merged_away = left if survivor.entity_id == right.entity_id else right
        # The removed entity is *preserved* with its merge provenance…
        removed = m.get(merged_away.entity_id)
        assert removed.removed_at == T(2025, 3)
        assert removed.merged_into == survivor.entity_id
        # …and its assertions moved to the survivor without deletion.
        assert len(survivor.assertions) == 2
        # …but it is not *live* after the merge.
        with pytest.raises(Exception):
            m.get(merged_away.entity_id, at=T(2025, 4))
        # …and the pre-merge view is still reproducible: both were live
        # before the decision was applied.
        assert m.get(merged_away.entity_id, at=T(2025, 2)).entity_id == merged_away.entity_id
        # The merge is in the append-only registry log.
        assert any(e.event_type == "merge" for e in m.event_log)

    def test_merge_refused_without_evidence(self):
        m, left, right = _registry_with_pair()
        decision = decide(
            _candidate_for(left, right),
            DecisionType.MATCH,
            actor="auto-merger",
            actor_kind=ActorKind.AUTO_RULE,
            policy=POLICY,
            evidence=(),  # no stored evidence
            decided_at=T(2025, 3),
        )
        with pytest.raises(MissingEvidenceError):
            apply_decision(m, decision, policy=POLICY, at=T(2025, 3))
        # No merge happened: both entities still live.
        assert m.get(left.entity_id).removed_at is None
        assert m.get(right.entity_id).removed_at is None
        assert not any(e.event_type == "merge" for e in m.event_log)

    def test_merge_refused_below_policy_threshold(self):
        m, left, right = _registry_with_pair()
        cand = _candidate_for(left, right, score=0.96)
        strict = MatchPolicy(
            name="boat-merge-policy-v2-strict",
            merge_threshold=0.99,
            review_threshold=0.50,
            non_match_threshold=0.20,
        )
        # A *human* may record a match decision over the band…
        decision = decide(
            cand,
            DecisionType.MATCH,
            actor="steward",
            actor_kind=ActorKind.HUMAN,
            policy=POLICY,
            evidence=(EvidenceRef(kind="assertion", ref="deadbeef" * 8),),
            decided_at=T(2025, 3),
        )
        # …but applying it under a policy whose threshold the score fails
        # is refused, and a policy/name mismatch is caught too.
        with pytest.raises(MissingPolicyError):
            apply_decision(m, decision, policy=strict, at=T(2025, 3))
        assert not any(e.event_type == "merge" for e in m.event_log)

    def test_auto_decision_cannot_be_minted_below_threshold(self):
        # The decision layer itself refuses: no decision row ⇒ nothing to
        # even try to apply.
        m, left, right = _registry_with_pair()
        with pytest.raises(BelowThresholdError):
            decide(
                _candidate_for(left, right, score=0.60),
                DecisionType.MATCH,
                actor="auto-merger",
                actor_kind=ActorKind.AUTO_RULE,
                policy=POLICY,
                evidence=(EvidenceRef(kind="assertion", ref="deadbeef" * 8),),
            )

    def test_non_match_and_uncertain_never_mutate_registry(self):
        m, left, right = _registry_with_pair()
        for verdict in (DecisionType.NON_MATCH, DecisionType.UNCERTAIN):
            decision = decide(
                _candidate_for(left, right, score=0.10 if verdict is DecisionType.NON_MATCH else 0.66),
                verdict,
                actor="auto-merger",
                actor_kind=ActorKind.AUTO_RULE,
                policy=POLICY,
                decided_at=T(2025, 3),
            )
            assert apply_decision(m, decision, policy=POLICY) is None
        assert m.get(left.entity_id).removed_at is None
        assert m.get(right.entity_id).removed_at is None
        assert len(m.event_log) == 2  # just the two creations


# ---------------------------------------------------------------------------
# 4. Acceptance criterion 2 — reversible without deleting source assertions
# ---------------------------------------------------------------------------


class TestReversal:
    def _merged_registry(self):
        """Registry where the pair was merged by a valid match decision."""
        m, left, right = _registry_with_pair()
        cand = _candidate_for(left, right)
        decision = decide(
            cand,
            DecisionType.MATCH,
            actor="auto-merger",
            actor_kind=ActorKind.AUTO_RULE,
            policy=POLICY,
            evidence=(EvidenceRef(kind="assertion", ref="deadbeef" * 8),),
            decided_at=T(2025, 3),
        )
        survivor = apply_decision(m, decision, policy=POLICY, at=T(2025, 3))
        return m, left, right, cand, decision, survivor

    def test_match_reversal_is_split_decision(self):
        m, left, right, cand, decision, survivor = self._merged_registry()
        # The survivor now holds both hulls' assertion refs; the reversal
        # names exactly which ones belonged to the wrongly-merged hull.
        held = [ref.assertion_id for ref in survivor.assertions]
        assert len(held) == 2

        reversal = reverse_decision(
            decision,
            actor="stuart.mcleod",
            actor_kind=ActorKind.HUMAN,
            policy=POLICY,
            evidence=(EvidenceRef(kind="certificate_ref", ref="orc:AUS8338:2019"),),
            decided_at=T(2025, 4),
            split_assertion_ids=held[1:],  # the wrongly-merged hull's fact
        )
        assert reversal.decision == DecisionType.SPLIT.value
        assert reversal.supersedes == decision.id
        assert reversal.reversal_of == decision.id

        resurrected = apply_decision(m, reversal, policy=POLICY, at=T(2025, 4))
        assert resurrected is not None
        # The split entity is the *originally merged-away* hull resurrected
        # (it was never deleted — only stamped removed) with full lineage.
        merged_away = left if survivor.entity_id == right.entity_id else right
        assert resurrected.entity_id == merged_away.entity_id
        assert resurrected.split_from == survivor.entity_id
        assert resurrected.removed_at is None

        # **No source assertion was deleted**: both facts still resolve,
        # now on their separate entities.
        t_surv = m.resolve_truth(survivor.entity_id, as_of=T(2025, 5))
        t_new = m.resolve_truth(resurrected.entity_id, as_of=T(2025, 5))
        assert t_surv.value("tcc") in (1.012, 1.014)
        assert t_new.value("tcc") in (1.012, 1.014)
        assert t_surv.value("tcc") != t_new.value("tcc")

        # Both entities are live again after the reversal…
        assert m.get(left.entity_id, at=T(2025, 5)).entity_id == left.entity_id
        assert m.get(right.entity_id, at=T(2025, 5)).entity_id == right.entity_id
        assert m.get(merged_away.entity_id, at=T(2025, 5)).removed_at is None
        # …and history is intact: merge *and* split in the append-only log.
        kinds = [e.event_type for e in m.event_log]
        assert kinds.count("merge") == 1 and kinds.count("split") == 1

    def test_journal_supersession_is_bitemporal(self):
        journal = MatchJournal()
        journal.record(DECISION_MATCH)
        journal.record(DECISION_SPLIT)

        # Both rows retained — nothing deleted.
        assert len(journal.snapshot()["decisions"]) == 2

        original = journal.get(DECISION_MATCH.id)
        assert original.superseded_by == DECISION_SPLIT.id
        assert original.superseded_at == DECISION_SPLIT.decided_at

        cid = CANDIDATE_MATCH.candidate_id
        # Before the reversal, the match was in force.
        assert journal.current_decision(cid, as_of=T3).id == DECISION_MATCH.id
        assert journal.current_decision(cid, as_of=T3).decision == "match"
        # After the reversal, the split governs.
        assert journal.current_decision(cid, as_of=T4 + DAY).id == DECISION_SPLIT.id
        assert journal.current_decision(cid, as_of=T4 + DAY).decision == "split"
        # The full chain stays auditable, oldest first.
        chain = journal.decisions_for(cid)
        assert [d.id for d in chain] == [DECISION_MATCH.id, DECISION_SPLIT.id]

    def test_journal_reverse_helper(self):
        journal = MatchJournal()
        journal.record(DECISION_MATCH)
        reversal = journal.reverse(
            DECISION_MATCH.id,
            actor="stuart.mcleod",
            actor_kind=ActorKind.HUMAN,
            policy=POLICY,
            evidence=(EvidenceRef(kind="certificate_ref", ref="orc:AUS8338:2019"),),
            decided_at=T4,
            split_assertion_ids=SPLIT_ASSERTION_IDS,
        )
        assert reversal.reversal_of == DECISION_MATCH.id
        assert journal.get(DECISION_MATCH.id).superseded_by == reversal.id

    def test_double_reversal_rejected(self):
        journal = MatchJournal()
        journal.record(DECISION_MATCH)
        journal.record(DECISION_SPLIT)
        with pytest.raises(DecisionStateError):
            journal.reverse(
                DECISION_MATCH.id,
                actor="stuart.mcleod",
                actor_kind=ActorKind.HUMAN,
                policy=POLICY,
                decided_at=T4 + DAY,
            )

    def test_supersede_unknown_decision_rejected(self):
        journal = MatchJournal()
        with pytest.raises(DecisionStateError):
            journal.record(DECISION_SPLIT)  # supersedes a decision never recorded

    def test_open_candidates_queue(self):
        journal = MatchJournal()
        journal.record_candidate(CANDIDATE_UNCERTAIN)
        journal.record(DECISION_MATCH)
        open_ids = {c.candidate_id for c in journal.open_candidates()}
        assert CANDIDATE_UNCERTAIN.candidate_id in open_ids
        assert CANDIDATE_MATCH.candidate_id not in open_ids


# ---------------------------------------------------------------------------
# 5. Journal serialisation
# ---------------------------------------------------------------------------


class TestJournalSnapshot:
    def test_snapshot_round_trip_payloads(self):
        journal = MatchJournal()
        for d in (DECISION_MATCH, DECISION_NON_MATCH, DECISION_UNCERTAIN):
            journal.record(d)
        journal.record(DECISION_SPLIT)
        snap = journal.snapshot()
        assert snap["schema_version"] == "match-decision-v1"
        assert len(snap["candidates"]) == 3
        assert len(snap["decisions"]) == 4  # match, non_match, uncertain, split
        # The superseded row is present in full (with its link).
        stored = {d["decision_id"]: d for d in snap["decisions"]}
        assert stored[DECISION_MATCH.id]["superseded_by"] == DECISION_SPLIT.id

    def test_fixtures_module_constants_consistent(self):
        assert SUPERSESSION_CHAIN == (DECISION_MATCH, DECISION_SPLIT)
        assert DECISION_MATCH_SUPERSEDED in ALL_DECISIONS
        assert DECISION_MATCH_SUPERSEDED.id == DECISION_MATCH.id
