"""Schema fixtures for the identity candidate / match-decision contracts
(DP-04-01).

These fixtures implement the issue's verification criterion:

    "Schema fixtures cover match, non-match, uncertain, split and
    superseded decision."

Every candidate and decision is fully populated — candidate pair,
features, scores, model/rule version, evidence, decision, actor,
timestamp and supersession — and built on **fixed timestamps** so ids
are deterministic and JSON fixtures can be diffed.  ``FIXTURE_DOCS``
carries the serialised (JSON-ready) form of all five scenarios: it is
the *schema fixture* in the literal sense — a contract consumer can
validate its parser against these dicts without touching the registry.

Scenario timeline (all UTC)
---------------------------

The scenario follows two boats that a scraper run believed were one hull
(re-issued sail number ``AUS 8338``), through the full decision
lifecycle:

  T0 = 2025-06-01  Blocking/scoring emits CANDIDATE_MATCH: same
                   normalised sail number, near-identical name, same
                   design.  Score 0.96 — above the merge threshold.
  T1 = 2025-06-02  DECISION_MATCH: the auto rule merges them, resting on
                   two stored evidence refs (assertion id + raw artifact
                   hash).  → the **match** fixture.
  T2 = 2025-06-03  An unrelated pair scores 0.08 → DECISION_NON_MATCH:
                   considered and rejected, stored so the pair is never
                   silently re-merged.  → the **non-match** fixture.
  T3 = 2025-06-04  A third pair scores 0.66 — inside the uncertain band
                   → DECISION_UNCERTAIN, routed to human review.  → the
                   **uncertain** fixture.
  T4 = 2025-06-05  A steward discovers the T1 merge was wrong: the 2008
                   results and the 2019 certificate are *different
                   hulls* that shared a re-issued sail number.
                   DECISION_SPLIT supersedes DECISION_MATCH and names
                   exactly which assertion ids move to the resurrected
                   hull.  → the **split** fixture and, because
                   DECISION_MATCH gains its ``superseded_by`` link while
                   remaining in the journal, the **superseded decision**
                   fixture.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from irc_data.domain.matching import (
    ActorKind,
    CandidatePairV1,
    DecisionType,
    EvidenceRef,
    FeatureScoreV1,
    MatchDecisionV1,
    MatchPolicy,
    decide,
    reverse_decision,
)


# ---------------------------------------------------------------------------
# Fixed clock — every fixture event happens at one of these.
# ---------------------------------------------------------------------------

T0 = datetime(2025, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2025, 6, 2, 9, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2025, 6, 3, 9, 0, 0, tzinfo=timezone.utc)
T3 = datetime(2025, 6, 4, 9, 0, 0, tzinfo=timezone.utc)
T4 = datetime(2025, 6, 5, 9, 0, 0, tzinfo=timezone.utc)

#: The policy every fixture decision is taken under.
POLICY = MatchPolicy(
    name="boat-merge-policy-v1",
    merge_threshold=0.90,
    review_threshold=0.50,
    non_match_threshold=0.20,
)

#: Deterministic entity ids (valid DP-03-01 shapes: prefix + 26-char body).
BOAT_LEFT = "boat_01J4Z9K7W2Q8E0R1T3Y5U7I9O0A1"
BOAT_RIGHT = "boat_01J4ZA3M0X5N7B2V8C1D4F6G8H2"
BOAT_OTHER_A = "boat_01J4ZB8N2Y6P3D9W4E7R1T5Y6"
BOAT_OTHER_B = "boat_01J4ZC1P3X8Q5E7R2T9Y4U6I8"
BOAT_UNCERTAIN_A = "boat_01J4ZD4R5Y1T8U3I6O9P2A4S6"
BOAT_UNCERTAIN_B = "boat_01J4ZE7T8U4I1O6P9A2S5D8F1"


# ---------------------------------------------------------------------------
# Evidence builders
# ---------------------------------------------------------------------------


def _evidence_for_match() -> tuple[EvidenceRef, ...]:
    return (
        EvidenceRef(
            kind="assertion",
            ref="c9f8e7d6b5a4938172635465a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
            note="sailsys results assertion: sail_number 'AUS 8338' on both hull records",
        ),
        EvidenceRef(
            kind="artifact",
            ref="sha256:4b2c1a0f9e8d7c6b5a4938271605f4e3d2c1b0a99887766554433221100ffee0",
            note="raw results page both records were parsed from",
        ),
    )


# ---------------------------------------------------------------------------
# MATCH — candidate + auto-rule decision (T0 / T1)
# ---------------------------------------------------------------------------

CANDIDATE_MATCH = CandidatePairV1(
    entity_type="boat",
    left_id=BOAT_LEFT,
    right_id=BOAT_RIGHT,
    features=(
        FeatureScoreV1(
            name="sail_number",
            value_left="AUS 8338",
            value_right="AUS8338",
            similarity=1.0,
            weight=0.50,
        ),
        FeatureScoreV1(
            name="boat_name",
            value_left="KERIBA",
            value_right="KERIBA II",
            similarity=0.88,
            weight=0.30,
        ),
        FeatureScoreV1(
            name="design",
            value_left="C&C 115",
            value_right="C&C 115",
            similarity=1.0,
            weight=0.20,
        ),
    ),
    score=0.96,
    model_version="boat-dedupe-model-v2",
    rule_version="blocking-rules-v3",
    generated_at=T0,
    metadata={"blocking_key": "sail:AUS8338"},
)

DECISION_MATCH = decide(
    CANDIDATE_MATCH,
    DecisionType.MATCH,
    actor="auto-merger",
    actor_kind=ActorKind.AUTO_RULE,
    policy=POLICY,
    evidence=_evidence_for_match(),
    decided_at=T1,
    rationale="sail number identical after normalisation; name and design concur",
)


# ---------------------------------------------------------------------------
# NON-MATCH — candidate + auto-rule decision (T2)
# ---------------------------------------------------------------------------

CANDIDATE_NON_MATCH = CandidatePairV1(
    entity_type="boat",
    left_id=BOAT_OTHER_A,
    right_id=BOAT_OTHER_B,
    features=(
        FeatureScoreV1(
            name="sail_number",
            value_left="GBR 1",
            value_right="GBR 100",
            similarity=0.35,
            weight=0.50,
        ),
        FeatureScoreV1(
            name="boat_name",
            value_left="JAVELIN",
            value_right="JAVELIN",
            similarity=1.0,
            weight=0.30,
        ),
        FeatureScoreV1(
            name="design",
            value_left="J/122",
            value_right="First 40",
            similarity=0.0,
            weight=0.20,
        ),
    ),
    score=0.08,
    model_version="boat-dedupe-model-v2",
    rule_version="blocking-rules-v3",
    generated_at=T2,
    metadata={"blocking_key": "name:JAVELIN"},
)

DECISION_NON_MATCH = decide(
    CANDIDATE_NON_MATCH,
    DecisionType.NON_MATCH,
    actor="auto-merger",
    actor_kind=ActorKind.AUTO_RULE,
    policy=POLICY,
    evidence=(
        EvidenceRef(
            kind="assertion",
            ref="1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091",
            note="same name but different designs and sail numbers — distinct hulls",
        ),
    ),
    decided_at=T2,
    rationale="name collision across designs; stored so the pair is not re-merged",
)


# ---------------------------------------------------------------------------
# UNCERTAIN — candidate routed to human review (T3)
# ---------------------------------------------------------------------------

CANDIDATE_UNCERTAIN = CandidatePairV1(
    entity_type="boat",
    left_id=BOAT_UNCERTAIN_A,
    right_id=BOAT_UNCERTAIN_B,
    features=(
        FeatureScoreV1(
            name="sail_number",
            value_left="AUS 5200",
            value_right="5200",
            similarity=0.90,
            weight=0.50,
        ),
        FeatureScoreV1(
            name="boat_name",
            value_left="ZENITH",
            value_right="ZENITH AGAIN",
            similarity=0.55,
            weight=0.30,
        ),
        FeatureScoreV1(
            name="design",
            value_left=None,
            value_right="Sydney 38",
            similarity=None,  # missing data — not comparable
            weight=0.20,
        ),
    ),
    score=0.66,
    model_version="boat-dedupe-model-v2",
    rule_version="blocking-rules-v3",
    generated_at=T3,
)

DECISION_UNCERTAIN = decide(
    CANDIDATE_UNCERTAIN,
    DecisionType.UNCERTAIN,
    actor="auto-merger",
    actor_kind=ActorKind.AUTO_RULE,
    policy=POLICY,
    evidence=(
        EvidenceRef(
            kind="assertion",
            ref="77aa88bb99cc00dd11ee22ff3344556677889900aabbccddeeff001122334455",
            note="bare-number sail match without country prefix; design missing on one side",
        ),
    ),
    decided_at=T3,
    rationale="score inside the uncertain band [0.50, 0.90): routed to human review",
)


# ---------------------------------------------------------------------------
# SPLIT + SUPERSEDED — the T1 merge is reversed at T4
# ---------------------------------------------------------------------------

#: Assertion ids the split moves to the resurrected hull (the 2019-era
#: certificate facts that never belonged on the 2008 hull).
SPLIT_ASSERTION_IDS = (
    "aabbccddeeff0011223344556677889900112233445566778899aabbccddeeff00",
    "bbccddeeff00112233445566778899aabbccddeeff00112233445566778899aabb",
)

DECISION_SPLIT = reverse_decision(
    DECISION_MATCH,
    actor="stuart.mcleod",
    actor_kind=ActorKind.HUMAN,
    policy=POLICY,
    evidence=(
        EvidenceRef(
            kind="certificate_ref",
            ref="orc:AUS8338:2019",
            note="2019 ORC certificate shows a different HIN than the 2008 results boat",
        ),
        EvidenceRef(
            kind="artifact",
            ref="sha256:0011223344556677889900aabbccddeeff0011223344556677889900aabbccdd",
            note="2019 certificate PDF — raw provenance envelope",
        ),
    ),
    decided_at=T4,
    rationale=(
        "sail number AUS 8338 was re-issued: 2008 results and 2019 certificate "
        "are different hulls — reversing the merge"
    ),
    split_assertion_ids=SPLIT_ASSERTION_IDS,
)

#: The match decision *after* the split was recorded: same row, now
#: carrying its supersession link.  Nothing was deleted — both rows live
#: side-by-side in the journal.
DECISION_MATCH_SUPERSEDED = DECISION_MATCH.superseded(DECISION_SPLIT)

assert DECISION_SPLIT.supersedes == DECISION_MATCH.id
assert DECISION_SPLIT.reversal_of == DECISION_MATCH.id
assert DECISION_MATCH_SUPERSEDED.superseded_by == DECISION_SPLIT.id


# ---------------------------------------------------------------------------
# Serialised schema fixtures (the literal contract payloads)
# ---------------------------------------------------------------------------

#: Candidate fixtures keyed by scenario name.
CANDIDATE_FIXTURES: dict[str, dict] = {
    "match": CANDIDATE_MATCH.to_dict(),
    "non_match": CANDIDATE_NON_MATCH.to_dict(),
    "uncertain": CANDIDATE_UNCERTAIN.to_dict(),
    # The split decision is *about* the match candidate — same pair.
    "split": CANDIDATE_MATCH.to_dict(),
    "superseded": CANDIDATE_MATCH.to_dict(),
}

#: Decision fixtures keyed by scenario name.  "superseded" is the match
#: decision *with its supersession link in place* — the state it has in
#: the journal after T4.
DECISION_FIXTURES: dict[str, dict] = {
    "match": DECISION_MATCH.to_dict(),
    "non_match": DECISION_NON_MATCH.to_dict(),
    "uncertain": DECISION_UNCERTAIN.to_dict(),
    "split": DECISION_SPLIT.to_dict(),
    "superseded": DECISION_MATCH_SUPERSEDED.to_dict(),
}

#: Combined document: what a fixture file on disk would contain.
FIXTURE_DOCS: dict[str, dict] = {
    "schema_version": "match-decision-v1",
    "policy": POLICY.to_dict(),
    "candidates": CANDIDATE_FIXTURES,
    "decisions": DECISION_FIXTURES,
}

#: Ordered (original, reversal) pair for the supersession scenario.
SUPERSESSION_CHAIN: tuple[MatchDecisionV1, MatchDecisionV1] = (
    DECISION_MATCH,
    DECISION_SPLIT,
)

ALL_DECISIONS: tuple[MatchDecisionV1, ...] = (
    DECISION_MATCH,
    DECISION_NON_MATCH,
    DECISION_UNCERTAIN,
    DECISION_SPLIT,
    DECISION_MATCH_SUPERSEDED,
)


def fixtures_json() -> str:
    """The whole fixture document as deterministic JSON."""
    return json.dumps(FIXTURE_DOCS, indent=2, sort_keys=True)
