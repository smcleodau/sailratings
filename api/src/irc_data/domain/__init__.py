"""Canonical domain language for the sailing knowledge base (DP-03-01).

This package defines the **stable language** every other data-platform
component speaks:

* :class:`EntityType` — the fifteen canonical entity types (boat, boat
  identity, design, organisation, person, event, race, entry, result,
  certificate, rating, measurement, sail, venue, source assertion) with
  their boundaries and identity sources.
* :func:`new_entity_id` / :func:`parse_entity_id` — opaque, prefixed
  identifiers that are **never derived from mutable names**.
* :class:`CanonicalEntity`, :class:`Alias`, :class:`SourceAssertionRef`,
  :class:`ResolvedTruth` — the entity shell separating *observed
  assertions* (immutable, provenance-carrying) from *resolved truth*
  (reproducible, bitemporal).
* :class:`DomainModel` — the in-memory registry implementing merge,
  split, alias attachment and resolution history.
"""

from irc_data.domain.entities import (
    AliasedToRemovedError,
    AliasError,
    AliasInconsistentError,
    BoundaryCheck,
    DomainError,
    DomainModel,
    DuplicateAliasError,
    EntityNotFoundError,
    IdentifierDerivationError,
    MergeSameEntityError,
    SplitError,
    check_id_opacity,
    entity_boundary,
    entity_types,
    new_entity_id,
    new_event_id,
    parse_entity_id,
    Alias,
    CanonicalEntity,
    EntityType,
    IdParts,
    RegistryEvent,
    ResolvedTruth,
    SourceAssertionRef,
    CANONICAL_FIELDS,
    ENTITY_BOUNDARIES,
    ID_PREFIXES,
    MUTABLE_NAME_TOKENS,
    PREFIX_TO_TYPE,
    SCHEMA_VERSION,
)

# DP-04-01: identity candidate and match-decision contracts.  Imported
# under a private alias first so the two SCHEMA_VERSION tags stay distinct
# (entities export theirs as SCHEMA_VERSION; matching's is
# MATCH_SCHEMA_VERSION).
from irc_data.domain import matching as _matching
from irc_data.domain.matching import (
    ActorKind,
    BelowThresholdError,
    CandidatePairV1,
    CandidateStatus,
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

MATCH_SCHEMA_VERSION = _matching.SCHEMA_VERSION
DECISION_TYPES = _matching.DECISIONS
DEFAULT_BOAT_POLICY = _matching.DEFAULT_BOAT_POLICY

__all__ = [
    "Alias",
    "AliasedToRemovedError",
    "AliasError",
    "AliasInconsistentError",
    "BoundaryCheck",
    "CANONICAL_FIELDS",
    "CanonicalEntity",
    "DomainError",
    "DomainModel",
    "DuplicateAliasError",
    "ENTITY_BOUNDARIES",
    "EntityNotFoundError",
    "EntityType",
    "ID_PREFIXES",
    "IdParts",
    "IdentifierDerivationError",
    "MUTABLE_NAME_TOKENS",
    "MergeSameEntityError",
    "PREFIX_TO_TYPE",
    "RegistryEvent",
    "ResolvedTruth",
    "SCHEMA_VERSION",
    "SourceAssertionRef",
    "SplitError",
    "check_id_opacity",
    "entity_boundary",
    "entity_types",
    "new_entity_id",
    "new_event_id",
    "parse_entity_id",
    # DP-04-01: identity candidate & match-decision contracts
    "ActorKind",
    "BelowThresholdError",
    "CandidatePairV1",
    "CandidateStatus",
    "DecisionStateError",
    "DecisionType",
    "DECISION_TYPES",
    "DEFAULT_BOAT_POLICY",
    "EvidenceRef",
    "FeatureScoreV1",
    "MATCH_SCHEMA_VERSION",
    "MatchDecisionError",
    "MatchDecisionV1",
    "MatchJournal",
    "MatchPolicy",
    "MissingEvidenceError",
    "MissingPolicyError",
    "apply_decision",
    "decide",
    "reverse_decision",
]
