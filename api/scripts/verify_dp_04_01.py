#!/usr/bin/env python3
"""End-to-end verification evidence for DP-04-01 — identity candidate and
match-decision contracts.

Walks the issue's scope, acceptance criteria and verification criterion
through the contracts in ``irc_data.domain.matching`` and prints hard,
paste-able PASS/FAIL evidence for the issue board:

* **Scope** — a candidate pair carries pair ids, features, score and
  model/rule version; a decision carries decision, actor, timestamp,
  threshold/policy, evidence and supersession.
* **Acceptance 1** — no merge occurs without stored evidence and
  threshold/policy (refusals demonstrated live against the registry).
* **Acceptance 2** — decisions are reversed without deleting source
  assertions (merge → split reversal; both facts still resolve).
* **Verification** — schema fixtures cover match, non-match, uncertain,
  split and superseded decisions, each JSON round-trippable.

No database or network required — the contracts are pure, in-memory,
and layered on the DP-03-01 registry / DP-03-02 assertion store.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_04_01.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
# For the schema-fixture module (tests.matching.fixtures).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from irc_data.assertions import AssertionV1  # noqa: E402
from irc_data.domain import DomainModel  # noqa: E402
from irc_data.domain.matching import (  # noqa: E402
    ActorKind,
    BelowThresholdError,
    CandidatePairV1,
    DecisionStateError,
    DecisionType,
    EvidenceRef,
    FeatureScoreV1,
    MatchDecisionV1,
    MatchJournal,
    MatchPolicy,
    MissingEvidenceError,
    MissingPolicyError,
    apply_decision,
    decide,
    reverse_decision,
)

UTC = timezone.utc
DAY = timedelta(days=1)
RESULTS: list[tuple[str, bool, str]] = []


def T(y: int, m: int = 1, d: int = 1) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


POLICY = MatchPolicy(
    name="boat-merge-policy-v1",
    merge_threshold=0.90,
    review_threshold=0.50,
    non_match_threshold=0.20,
)

EVIDENCE = (EvidenceRef(kind="assertion", ref="deadbeef" * 8, note="results assertion on both hulls"),)


def registry_with_pair():
    """Two hulls in the canonical registry, each holding one fact."""
    m = DomainModel()
    left = m.create_entity("boat", at=T(2025, 1))
    right = m.create_entity("boat", at=T(2025, 1))
    for boat, tcc in ((left, 1.012), (right, 1.014)):
        m.assert_about(
            boat.entity_id,
            AssertionV1(
                entity_type="boat", entity_key=boat.entity_key, field="tcc",
                value=tcc, source_slug="sailsys",
                recorded_at=T(2025, 1), valid_from=T(2025, 1),
            ),
        )
    return m, left, right


def candidate_for(left, right, *, score=0.96, at=None) -> CandidatePairV1:
    return CandidatePairV1(
        entity_type="boat",
        left_id=left.entity_id,
        right_id=right.entity_id,
        features=(
            FeatureScoreV1(name="sail_number", value_left="AUS 8338",
                           value_right="AUS8338", similarity=1.0, weight=1.0),
        ),
        score=score,
        rule_version="blocking-rules-v3",
        generated_at=at or T(2025, 2),
    )


print("=" * 76)
print("DP-04-01 verification — identity candidate and match-decision contracts")
print("=" * 76)

# ---------------------------------------------------------------------------
print("\n0. Scope — candidate pair: pair, features, score, model/rule version")
cand = CandidatePairV1(
    entity_type="boat",
    left_id="boat_01J4Z9K7W2Q8E0R1T3Y5U7I9O0A1",
    right_id="boat_01J4ZA3M0X5N7B2V8C1D4F6G8H2",
    features=(
        FeatureScoreV1(name="sail_number", value_left="AUS 8338",
                       value_right="AUS8338", similarity=1.0, weight=0.5),
        FeatureScoreV1(name="boat_name", value_left="KERIBA",
                       value_right="KERIBA II", similarity=0.88, weight=0.3),
        FeatureScoreV1(name="design", value_left="C&C 115",
                       value_right="C&C 115", similarity=1.0, weight=0.2),
    ),
    score=0.96,
    model_version="boat-dedupe-model-v2",
    rule_version="blocking-rules-v3",
    generated_at=T(2025, 6, 1),
)
check("candidate id content-addressed", cand.candidate_id.startswith("cand_"), cand.candidate_id)
check("pair key order-independent", cand.pair_key == tuple(sorted((cand.left_id, cand.right_id))))
check("features explain the score", len(cand.features) == 3
      and all(f.similarity is not None and f.weight > 0 for f in cand.features))
check("model + rule version recorded", cand.model_version == "boat-dedupe-model-v2"
      and cand.rule_version == "blocking-rules-v3")
check("generation timestamp UTC-aware", cand.generated_at.tzinfo is not None)

print("\n0b. Scope — decision: decision, actor, timestamp, policy, evidence, supersession")
d = decide(cand, DecisionType.MATCH, actor="auto-merger", actor_kind=ActorKind.AUTO_RULE,
           policy=POLICY, evidence=EVIDENCE, decided_at=T(2025, 6, 2),
           rationale="sail identical after normalisation")
check("decision id content-addressed", d.id.startswith("dec_"), d.id)
check("decision + actor + kind recorded", d.decision == "match" and d.actor == "auto-merger"
      and d.actor_kind == "auto_rule")
check("threshold/policy recorded", d.threshold_policy == POLICY.name)
check("stored evidence attached", len(d.evidence) == 1 and d.evidence[0].kind == "assertion")
check("supersession fields present", d.supersedes is None and d.superseded_by is None
      and "superseded_by" in d.to_dict() and "reversal_of" in d.to_dict())
check("JSON round-trip", MatchDecisionV1.from_json(d.to_json()) == d
      and CandidatePairV1.from_json(cand.to_json()) == cand)

# ---------------------------------------------------------------------------
print("\n1. Acceptance 1 — no merge without stored evidence and threshold/policy")
m, left, right = registry_with_pair()
good = decide(candidate_for(left, right), DecisionType.MATCH, actor="auto-merger",
              actor_kind=ActorKind.AUTO_RULE, policy=POLICY, evidence=EVIDENCE,
              decided_at=T(2025, 3))
survivor = apply_decision(m, good, policy=POLICY, at=T(2025, 3))
check("merge with evidence + policy applies", survivor is not None
      and any(e.event_type == "merge" for e in m.event_log))
check("merged-away entity preserved with provenance",
      m.get(right.entity_id if survivor.entity_id == left.entity_id else left.entity_id).merged_into
      == survivor.entity_id)

m, left, right = registry_with_pair()
bare = decide(candidate_for(left, right), DecisionType.MATCH, actor="auto-merger",
              actor_kind=ActorKind.AUTO_RULE, policy=POLICY, evidence=(), decided_at=T(2025, 3))
try:
    apply_decision(m, bare, policy=POLICY, at=T(2025, 3))
    check("merge without evidence refused", False, "no exception raised!")
except MissingEvidenceError:
    check("merge without evidence refused", True, "MissingEvidenceError")
check("registry untouched after refusal", not any(e.event_type == "merge" for e in m.event_log))

m, left, right = registry_with_pair()
try:
    decide(candidate_for(left, right, score=0.60), DecisionType.MATCH, actor="auto-merger",
           actor_kind=ActorKind.AUTO_RULE, policy=POLICY, evidence=EVIDENCE)
    check("auto match below threshold refused at decision time", False, "no exception raised!")
except BelowThresholdError:
    check("auto match below threshold refused at decision time", True, "BelowThresholdError")

strict = MatchPolicy(name="boat-merge-policy-v2-strict", merge_threshold=0.99,
                     review_threshold=0.50, non_match_threshold=0.20)
human = decide(candidate_for(left, right), DecisionType.MATCH, actor="steward",
               actor_kind=ActorKind.HUMAN, policy=POLICY, evidence=EVIDENCE, decided_at=T(2025, 3))
try:
    apply_decision(m, human, policy=strict, at=T(2025, 3))
    check("policy mismatch at merge gate refused", False, "no exception raised!")
except MissingPolicyError:
    check("policy mismatch at merge gate refused", True, "MissingPolicyError")
check("registry still untouched", not any(e.event_type == "merge" for e in m.event_log))

m, left, right = registry_with_pair()
for verdict, score in ((DecisionType.NON_MATCH, 0.10), (DecisionType.UNCERTAIN, 0.66)):
    nd = decide(candidate_for(left, right, score=score), verdict, actor="auto-merger",
                actor_kind=ActorKind.AUTO_RULE, policy=POLICY, decided_at=T(2025, 3))
    applied = apply_decision(m, nd, policy=POLICY, at=T(2025, 3))
check("non_match/uncertain never mutate registry", applied is None
      and not any(e.event_type == "merge" for e in m.event_log))

# ---------------------------------------------------------------------------
print("\n2. Acceptance 2 — decisions reversed without deleting source assertions")
m, left, right = registry_with_pair()
cand2 = candidate_for(left, right)
match = decide(cand2, DecisionType.MATCH, actor="auto-merger", actor_kind=ActorKind.AUTO_RULE,
               policy=POLICY, evidence=EVIDENCE, decided_at=T(2025, 3))
survivor = apply_decision(m, match, policy=POLICY, at=T(2025, 3))
merged_away_id = right.entity_id if survivor.entity_id == left.entity_id else left.entity_id
held = [ref.assertion_id for ref in survivor.assertions]
check("survivor holds both assertion refs after merge", len(held) == 2)

reversal = reverse_decision(match, actor="stuart.mcleod", actor_kind=ActorKind.HUMAN,
                            policy=POLICY,
                            evidence=(EvidenceRef(kind="certificate_ref", ref="orc:AUS8338:2019",
                                                  note="2019 cert is a different hull"),),
                            decided_at=T(2025, 4), split_assertion_ids=held[1:])
check("reversal of a match is a split decision", reversal.decision == "split")
check("reversal links both ways", reversal.supersedes == match.id and reversal.reversal_of == match.id)

resurrected = apply_decision(m, reversal, policy=POLICY, at=T(2025, 4))
check("wrongly-merged hull resurrected under its original id",
      resurrected is not None and resurrected.entity_id == merged_away_id)
check("split provenance recorded", resurrected.split_from == survivor.entity_id
      and resurrected.removed_at is None)

t_surv = m.resolve_truth(survivor.entity_id, as_of=T(2025, 5))
t_new = m.resolve_truth(resurrected.entity_id, as_of=T(2025, 5))
check("no source assertion deleted: both facts still resolve",
      {t_surv.value("tcc"), t_new.value("tcc")} == {1.012, 1.014},
      f"survivor={t_surv.value('tcc')} resurrected={t_new.value('tcc')}")
kinds = [e.event_type for e in m.event_log]
check("append-only registry log keeps merge AND split",
      kinds.count("merge") == 1 and kinds.count("split") == 1)

# ---------------------------------------------------------------------------
print("\n3. Verification — schema fixtures cover all five decision states")
from tests.matching.fixtures import (  # noqa: E402
    CANDIDATE_FIXTURES,
    DECISION_FIXTURES,
    DECISION_MATCH,
    DECISION_SPLIT,
    FIXTURE_DOCS,
    POLICY as FIXTURE_POLICY,
    fixtures_json,
)

REQUIRED = ("match", "non_match", "uncertain", "split", "superseded")
check("all five decision states have fixtures",
      tuple(sorted(DECISION_FIXTURES)) == tuple(sorted(REQUIRED))
      and tuple(sorted(CANDIDATE_FIXTURES)) == tuple(sorted(REQUIRED)))
ok_roundtrip = all(
    MatchDecisionV1.from_dict(DECISION_FIXTURES[n]).to_dict() == DECISION_FIXTURES[n]
    and CandidatePairV1.from_dict(CANDIDATE_FIXTURES[n]).to_dict() == CANDIDATE_FIXTURES[n]
    for n in REQUIRED
)
check("every fixture round-trips through the schema", ok_roundtrip)
sup = DECISION_FIXTURES["superseded"]
check("superseded fixture links to its reversal",
      sup["superseded_by"] == DECISION_SPLIT.id
      and DECISION_SPLIT.supersedes == DECISION_MATCH.id)
check("fixture document is schema-versioned JSON",
      FIXTURE_DOCS["schema_version"] == "match-decision-v1"
      and FIXTURE_DOCS["policy"]["name"] == FIXTURE_POLICY.name
      and isinstance(fixtures_json(), str), f"{len(fixtures_json())} bytes of JSON")

journal = MatchJournal()
journal.record(DECISION_MATCH)
journal.record(DECISION_SPLIT)
cid = DECISION_MATCH.candidate.candidate_id
check("bitemporal journal: match in force before reversal",
      journal.current_decision(cid, as_of=DECISION_SPLIT.decided_at - DAY).decision == "match")
check("bitemporal journal: split in force after reversal",
      journal.current_decision(cid, as_of=DECISION_SPLIT.decided_at + DAY).decision == "split")
check("both rows retained in journal", len(journal.snapshot()["decisions"]) == 2)
try:
    journal.reverse(DECISION_MATCH.id, actor="x", actor_kind=ActorKind.HUMAN, policy=POLICY)
    check("double reversal rejected", False, "no exception raised!")
except DecisionStateError:
    check("double reversal rejected", True, "DecisionStateError")

# ---------------------------------------------------------------------------
print("\n" + "=" * 76)
failed = [r for r in RESULTS if not r[1]]
print(f"RESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed"
      + (f" — FAILURES: {[r[0] for r in failed]}" if failed else " — ALL PASS"))
print("=" * 76)
sys.exit(1 if failed else 0)
