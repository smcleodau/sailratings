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
]
