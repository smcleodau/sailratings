"""Canonical entity boundaries and identifiers (DP-03-01).

This module is the **stable language** of the sailing knowledge base.  It
defines, in code-of-record form:

1.  The fifteen **canonical entity types** and their boundaries — what
    belongs to the entity, what is only ever asserted *about* it, and how
    it relates to its neighbours.
2.  **Opaque identifiers** — ``<prefix>_<26-char ULID>`` identifiers that
    are randomly generated and are *never* derived from mutable names
    (boat name, sail number, event title, …).  Names are aliases, not
    keys.
3.  The **assertion vs. resolved-truth separation** — a canonical entity
    carries *no* fact values.  Facts arrive as immutable
    :class:`AssertionV1` records (DP-03-02) referencing the entity's
    opaque id; the resolved truth per field is derived, bitemporal and
    reproducible.
4.  **Temporal history** — entity creation, merge, split and alias
    changes are recorded as an append-only, timestamped event log; the
    state of the registry is reconstructable for any prior system time.

Downstream contracts
--------------------

* ``AssertionV1.entity_type`` / ``entity_key`` (DP-03-02,
  :mod:`irc_data.assertions`) are the opaque id's *parts*:
  ``entity_type = boat`` and ``entity_key = 01J4Z…`` together spell the
  opaque id ``boat_01J4Z…``.  The assertion store therefore needs no
  schema change to reference canonical entities.
* The compatibility views (DP-03-05) keep the *legacy* integer keys
  readable while the canonical ids become the join currency of the
  knowledge base.

Identifiers are ULIDs (Crockford base-32, 26 chars): they embed 48 bits
of time and 80 bits of randomness, are lexicographically sortable by
creation time, and encode **no** domain meaning.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from irc_data.assertions import AssertionV1, ResolutionV1, resolve


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

#: Version tag for the canonical entity contract.
SCHEMA_VERSION = "canonical-entity-v1"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DomainError(ValueError):
    """Base class for canonical-domain contract violations."""


class IdentifierDerivationError(DomainError):
    """Raised when an identifier is (or would be) derived from a mutable name."""


class EntityNotFoundError(DomainError, KeyError):
    """Raised when an opaque id does not resolve to a live canonical entity."""


class AliasError(DomainError):
    """Raised when an alias violates the alias contract."""


class DuplicateAliasError(AliasError):
    """Raised when attaching an alias that is already bound to another entity."""


class AliasInconsistentError(AliasError):
    """Raised when the requested alias does not match the observed assertion."""


class AliasedToRemovedError(AliasError):
    """Raised when an alias points at a removed (merged-away / split-away) entity."""


class MergeSameEntityError(DomainError):
    """Raised when attempting to merge an entity into itself."""


class SplitError(DomainError):
    """Raised when a split would corrupt the assertion record."""


# ---------------------------------------------------------------------------
# Canonical entity types and their boundaries
# ---------------------------------------------------------------------------


class EntityType(str, enum.Enum):
    """The fifteen canonical entity types of the sailing knowledge base.

    The *boundary* of each type — what it is, what it is not, and what
    identifies it — is documented in :data:`ENTITY_BOUNDARIES`.
    """

    BOAT = "boat"
    BOAT_IDENTITY = "boat_identity"
    DESIGN = "design"
    ORGANISATION = "organisation"
    PERSON = "person"
    EVENT = "event"
    RACE = "race"
    ENTRY = "entry"
    RESULT = "result"
    CERTIFICATE = "certificate"
    RATING = "rating"
    MEASUREMENT = "measurement"
    SAIL = "sail"
    VENUE = "venue"
    SOURCE_ASSERTION = "source_assertion"


#: Boundary description of one canonical entity type.
@dataclass(frozen=True)
class BoundaryCheck:
    """Documentation-as-data for one entity type's boundary."""

    #: One-sentence definition of the entity.
    definition: str
    #: What is *inside* the boundary (intrinsic, rarely-changing attributes).
    contains: tuple[str, ...]
    #: What is *outside* the boundary (asserted about it, or other entities).
    excludes: tuple[str, ...]
    #: Mutable source labels used to *recognise* the entity (never to id it).
    aliases: tuple[str, ...]
    #: Other canonical types this one canonically references.
    references: tuple[EntityType, ...] = ()


ENTITY_BOUNDARIES: dict[EntityType, BoundaryCheck] = {
    EntityType.BOAT: BoundaryCheck(
        definition=(
            "The physical sailing vessel — the hull and its appendages — "
            "independent of what any source calls it at any point in time."
        ),
        contains=(
            "opaque id",
            "creation / merge / split history",
            "links to its current and historical boat identities",
        ),
        excludes=(
            "boat name (mutable — asserted, aliased)",
            "sail number (mutable — asserted, aliased)",
            "ratings and measurements (separate entities)",
            "design (referenced, not embedded)",
        ),
        aliases=("boat name", "sail number", "hull identification number (HIN)"),
        references=(EntityType.BOAT_IDENTITY, EntityType.DESIGN),
    ),
    EntityType.BOAT_IDENTITY: BoundaryCheck(
        definition=(
            "A time-bounded naming/registration state of a boat: one "
            "combination of name, sail number, flag and owner as observed."
        ),
        contains=(
            "opaque id",
            "the boat it belongs to",
            "valid-time interval of the naming state",
        ),
        excludes=(
            "the physical boat itself (referenced)",
            "ratings measured while this identity was current (separate entities)",
        ),
        aliases=("boat name", "sail number", "flag / country code"),
        references=(EntityType.BOAT,),
    ),
    EntityType.DESIGN: BoundaryCheck(
        definition=(
            "The design or model the hull was built to (e.g. 'Sydney 38', "
            "'J/122') — a class concept, not a physical object."
        ),
        contains=("opaque id", "links to designer/builder organisations and persons"),
        excludes=(
            "individual hulls (boats reference a design)",
            "per-boat measurements (measurement entities)",
        ),
        aliases=("class name", "model name", "designer name"),
        references=(EntityType.PERSON, EntityType.ORGANISATION),
    ),
    EntityType.ORGANISATION: BoundaryCheck(
        definition=(
            "A club, rating office, class association, builder or event "
            "organiser — a legal/organisational body."
        ),
        contains=("opaque id",),
        excludes=(
            "people affiliated with it (person entities)",
            "events it runs (event entities referencing it)",
        ),
        aliases=("organisation name", "acronym", "country code"),
        references=(EntityType.VENUE,),
    ),
    EntityType.PERSON: BoundaryCheck(
        definition=(
            "An individual sailor, owner, designer or measurer.  Only data "
            "already published in race administration is ever attached "
            "(SOURCE-POLICY §4.8)."
        ),
        contains=("opaque id",),
        excludes=("contact details (prohibited)", "boats owned (boat assertions)"),
        aliases=("person name (as published)",),
        references=(),
    ),
    EntityType.EVENT: BoundaryCheck(
        definition=(
            "A regatta or race meeting: an organised occurrence with an "
            "organiser, a venue and a date window (e.g. 'Sydney Hobart 2019')."
        ),
        contains=("opaque id", "organiser reference", "venue reference"),
        excludes=(
            "the races sailed within it (race entities referencing the event)",
            "entries (entry entities referencing the event)",
        ),
        aliases=("event name", "edition / year"),
        references=(EntityType.ORGANISATION, EntityType.VENUE),
    ),
    EntityType.RACE: BoundaryCheck(
        definition=(
            "A single start within an event: one course, one start time, "
            "one set of finishing observations."
        ),
        contains=("opaque id", "parent event reference", "scheduled start"),
        excludes=(
            "entries (they reference the race's event)",
            "results (they reference the race)",
        ),
        aliases=("race number / name within the event",),
        references=(EntityType.EVENT,),
    ),
    EntityType.ENTRY: BoundaryCheck(
        definition=(
            "The registration of one boat (under one boat identity) in one "
            "event/division.  The join object between boat and event."
        ),
        contains=("opaque id", "boat reference", "event reference", "division"),
        excludes=(
            "results (they reference the entry)",
            "the rating the entry sailed under (a rating entity referenced by the result)",
        ),
        aliases=("sail number + event (alias pair)",),
        references=(EntityType.BOAT, EntityType.BOAT_IDENTITY, EntityType.EVENT),
    ),
    EntityType.RESULT: BoundaryCheck(
        definition=(
            "One scored finishing observation for one entry in one race: "
            "place, elapsed/corrected time, status (DNF etc.)."
        ),
        contains=("opaque id", "entry reference", "race reference"),
        excludes=("the rating applied (a rating entity)", "series scores (derived)"),
        aliases=("place + race + sail number (observation)",),
        references=(EntityType.ENTRY, EntityType.RACE),
    ),
    EntityType.CERTIFICATE: BoundaryCheck(
        definition=(
            "A rating-office document (e.g. an IRC certificate PDF) issued "
            "to one boat at one time: a *document*, identified by office "
            "certificate number, carrying measurements and a rating."
        ),
        contains=("opaque id", "boat reference", "issuing office reference"),
        excludes=(
            "the measurements printed on it (measurement entities asserted from it)",
            "the rating printed on it (a rating entity asserted from it)",
        ),
        aliases=("certificate number", "issue date + sail number (observation)"),
        references=(EntityType.BOAT, EntityType.ORGANISATION),
    ),
    EntityType.RATING: BoundaryCheck(
        definition=(
            "A rule-system score (e.g. IRC TCC, ORC APH) assigned to a boat "
            "for a valid-time interval.  A rating is asserted, never owned, "
            "by the boat."
        ),
        contains=("opaque id", "boat reference", "rule system", "valid-time interval"),
        excludes=("the measurements behind it (measurement entities)",),
        aliases=("sail number + rule system + year (observation)",),
        references=(EntityType.BOAT, EntityType.CERTIFICATE),
    ),
    EntityType.MEASUREMENT: BoundaryCheck(
        definition=(
            "One measured dimension of a boat (hull length, beam, "
            "displacement, rig dimensions, …) with a unit and a valid-time "
            "interval."
        ),
        contains=("opaque id", "boat reference", "measurement field", "unit"),
        excludes=("the rating derived from it (rating entity)",),
        aliases=("field name + sail number (observation)",),
        references=(EntityType.BOAT, EntityType.CERTIFICATE),
    ),
    EntityType.SAIL: BoundaryCheck(
        definition=(
            "An individual sail (or measured sail inventory item) belonging "
            "to a boat, with measured dimensions."
        ),
        contains=("opaque id", "boat reference", "sail kind"),
        excludes=("sail dimensions (measurement entities)",),
        aliases=("sail number + sail kind (observation)",),
        references=(EntityType.BOAT,),
    ),
    EntityType.VENUE: BoundaryCheck(
        definition=(
            "A place where racing happens: a body of water / race area "
            "associated with a club or region."
        ),
        contains=("opaque id",),
        excludes=("events held there (event entities referencing the venue)",),
        aliases=("venue name", "region / country code"),
        references=(),
    ),
    EntityType.SOURCE_ASSERTION: BoundaryCheck(
        definition=(
            "One immutable observed claim from a governed source: *who said "
            "what, about which entity, and when the truth changed*.  The "
            "atom of the knowledge base."
        ),
        contains=(
            "opaque id",
            "subject entity reference",
            "field, value, unit",
            "valid-time interval and system-observed time",
            "provenance (source slug, raw artifact)",
        ),
        excludes=("resolved truth (derived, never stored on the entity)",),
        aliases=("provenance URI", "raw artifact hash"),
        references=(EntityType.BOAT,),  # + every other assertable type
    ),
}

#: Ordered tuple of all canonical entity types (registry order).
CANONICAL_FIELDS: tuple[str, ...] = tuple(t.value for t in EntityType)


# ---------------------------------------------------------------------------
# Opaque identifiers
# ---------------------------------------------------------------------------

#: One-to-one id prefix per entity type.  An opaque id reads
#: ``<prefix>_<ULID>``, e.g. ``boat_01J4Z9K7W2Q8E0R1T3Y5U7I9O0``.
ID_PREFIXES: dict[EntityType, str] = {t: t.value for t in EntityType}

#: Reverse map, prefix → entity type.
PREFIX_TO_TYPE: dict[str, EntityType] = {p: t for t, p in ID_PREFIXES.items()}

# Crockford base-32 alphabet (no I, L, O, U) used by ULIDs.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Note: ``U`` is absent from the alphabet but *allowed* by the parser so
# that opacity auditing can give a precise ``IdentifierDerivationError``
# (instead of a generic parse error) for ids that embed name material
# containing a ``U`` (e.g. a country prefix like ``AUS``).
_ID_RE = re.compile(r"^(?P<prefix>[a-z_]+)_(?P<ulid>[0-9A-Z]{26})$")

#: Substrings that betray derivation from a mutable name.  An opaque id
#: must contain none of these after its prefix separator.
MUTABLE_NAME_TOKENS: tuple[str, ...] = (
    "GBR", "AUS", "USA", "NZL", "FRA", "GER",  # sail-number country prefixes
    "IRC", "ORC",  # rule systems
)


@dataclass(frozen=True)
class IdParts:
    """Parsed opaque identifier."""

    prefix: str
    entity_type: EntityType
    ulid: str

    @property
    def entity_key(self) -> str:
        """The key stored in ``AssertionV1.entity_key`` (DP-03-02)."""
        return self.ulid

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.prefix}_{self.ulid}"


def _encode_ulid(time_ms: int, rand_int: int) -> str:
    """Encode 48-bit time + 80-bit randomness as a 26-char Crockford ULID."""
    value = (time_ms & 0xFFFFFFFFFFFF) << 80 | (rand_int & ((1 << 80) - 1))
    chars = []
    for i in range(26):
        shift = (25 - i) * 5
        chars.append(_CROCKFORD[(value >> shift) & 0x1F])
    return "".join(chars)


def new_entity_id(
    entity_type: EntityType | str,
    *,
    _time_ms: int | None = None,
    _rand: random.Random | None = None,
) -> str:
    """Mint a new opaque identifier for ``entity_type``.

    The id is ``<prefix>_<ULID>`` where the ULID carries *only* creation
    time and randomness.  It is **impossible** for the id to contain a
    boat name, sail number, event title or any other mutable domain
    string — the function takes no such input.

    The ``_time_ms`` / ``_rand`` hooks exist so tests can pin the
    generated value; production callers never pass them.
    """
    et = entity_type if isinstance(entity_type, EntityType) else EntityType(entity_type)
    if _time_ms is None:
        _time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    r = _rand if _rand is not None else random.SystemRandom()
    rand_int = r.getrandbits(80)
    return f"{ID_PREFIXES[et]}_{_encode_ulid(_time_ms, rand_int)}"


def parse_entity_id(opaque_id: str) -> IdParts:
    """Parse and validate an opaque identifier.

    Raises :class:`DomainError` if the string is not a canonical opaque id.
    """
    m = _ID_RE.match(opaque_id or "")
    if not m:
        raise DomainError(f"not an opaque canonical id: {opaque_id!r}")
    prefix = m.group("prefix")
    if prefix not in PREFIX_TO_TYPE:
        raise DomainError(f"unknown entity prefix in id: {opaque_id!r}")
    return IdParts(prefix=prefix, entity_type=PREFIX_TO_TYPE[prefix], ulid=m.group("ulid"))


def check_id_opacity(opaque_id: str, *, aliases: Iterable[str] = ()) -> None:
    """Enforce the *no identifier from mutable names* invariant.

    Raises :class:`IdentifierDerivationError` when:
      * the id is not a valid opaque canonical id, or
      * the id's body contains non-ULID characters (hand-assembled id), or
      * the id's body contains a known mutable-name token (e.g. a country
        prefix such as ``GBR`` or a rule-system label such as ``IRC``), or
      * the id's body contains (a normalised form of) any provided alias
        string — i.e. someone smuggled the boat name into the key.
    """
    parts = parse_entity_id(opaque_id)  # raises DomainError if malformed
    body = parts.ulid
    # A body minted by :func:`new_entity_id` only contains Crockford
    # base-32 characters; anything else is hand-crafted name material.
    if any(c not in _CROCKFORD for c in body):
        raise IdentifierDerivationError(
            f"opaque id {opaque_id!r} contains non-ULID characters — "
            "ids must be minted by new_entity_id(), never hand-assembled"
        )
    for token in MUTABLE_NAME_TOKENS:
        normalised = "".join(c for c in token.upper() if c in _CROCKFORD)
        if normalised and len(normalised) >= 3 and normalised in body:
            raise IdentifierDerivationError(
                f"opaque id {opaque_id!r} embeds mutable-name token {token!r}"
            )
    for alias in aliases:
        normalised = "".join(c for c in alias.upper() if c in _CROCKFORD)
        if normalised and len(normalised) >= 4 and normalised in body:
            raise IdentifierDerivationError(
                f"opaque id {opaque_id!r} appears derived from alias {alias!r}"
            )


def new_event_id(
    event_type: str,
    *,
    _time_ms: int | None = None,
    _rand: random.Random | None = None,
) -> str:
    """Alias of :func:`new_entity_id` kept for readability at call sites."""
    return new_entity_id(event_type, _time_ms=_time_ms, _rand=_rand)


def entity_types() -> tuple[EntityType, ...]:
    """Return the canonical entity types in registry order."""
    return tuple(EntityType)


def entity_boundary(entity_type: EntityType | str) -> BoundaryCheck:
    """Return the boundary definition for one canonical entity type."""
    et = entity_type if isinstance(entity_type, EntityType) else EntityType(entity_type)
    return ENTITY_BOUNDARIES[et]


# ---------------------------------------------------------------------------
# Aliases — mutable names, attached and detached over time
# ---------------------------------------------------------------------------


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Alias:
    """A mutable source label attached to an entity for a time interval.

    Aliases are **lookup aids, never keys**.  The same alias string (e.g.
    the sail number ``GBR8310``) may move from one boat to another over
    time; the registry therefore indexes aliases with their valid-time
    interval and only ever resolves them to a *live* entity.
    """

    kind: str  # "sail_number" | "boat_name" | "cert_number" | "event_name" | ...
    value: str
    valid_from: datetime
    valid_to: datetime | None = None  # None → still current
    source_slug: str = ""

    def __post_init__(self) -> None:
        if not self.kind:
            raise AliasError("alias kind is required")
        if not self.value:
            raise AliasError("alias value is required")
        object.__setattr__(self, "valid_from", _utc(self.valid_from))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", _utc(self.valid_to))
            if self.valid_to < self.valid_from:
                raise AliasError("alias valid_to precedes valid_from")

    def covers(self, when: datetime) -> bool:
        when = _utc(when)
        return self.valid_from <= when and (self.valid_to is None or when < self.valid_to)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "source_slug": self.source_slug,
        }


# ---------------------------------------------------------------------------
# Source assertion refs and resolved truth
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceAssertionRef:
    """A pointer to one observed :class:`AssertionV1` about an entity.

    The knowledge base never copies fact values onto the entity; it keeps
    a reference (content-addressed assertion id, field and both
    timestamps) so the full immutable record can be fetched from the
    assertion store and the resolution recomputed for any prior time.
    """

    assertion_id: str
    field: str
    recorded_at: datetime
    valid_from: datetime
    source_slug: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at))
        object.__setattr__(self, "valid_from", _utc(self.valid_from))

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "field": self.field,
            "recorded_at": self.recorded_at.isoformat(),
            "valid_from": self.valid_from.isoformat(),
            "source_slug": self.source_slug,
        }


@dataclass(frozen=True)
class ResolvedTruth:
    """The resolved view of one entity at one system time.

    Produced by running the DP-03-02 deterministic resolution rules over
    the entity's assertions.  ``as_of`` is part of the contract: the same
    entity resolved at the same ``as_of`` always yields the same truth.
    """

    entity_id: str
    entity_type: EntityType
    as_of: datetime
    #: field → winning AssertionV1
    fields: Mapping[str, AssertionV1]
    #: field → losing coexisting assertions (observable conflicts)
    conflicts: Mapping[str, tuple[AssertionV1, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _utc(self.as_of))

    def value(self, field_name: str, default: Any = None) -> Any:
        """Return the resolved value of ``field_name`` (or ``default``)."""
        winner = self.fields.get(field_name)
        return winner.value if winner is not None else default

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type.value,
            "as_of": self.as_of.isoformat(),
            "fields": {k: a.to_dict() for k, a in self.fields.items()},
            "conflicts": {k: [a.to_dict() for a in v] for k, v in self.conflicts.items()},
        }


# ---------------------------------------------------------------------------
# Canonical entity shell
# ---------------------------------------------------------------------------


@dataclass
class CanonicalEntity:
    """One canonical entity — an **opaque id plus history**, nothing more.

    The shell deliberately contains *no fact values*.  Everything the
    platform believes about the entity lives in the assertion store and
    is referenced through :attr:`assertions`; names live in
    :attr:`aliases`.  This is the structural guarantee that **observed
    assertions are separate from resolved truth**.
    """

    entity_id: str
    entity_type: EntityType
    created_at: datetime
    removed_at: datetime | None = None  # set on merge-away / split-away
    merged_into: str | None = None    # opaque id of the surviving entity
    split_from: str | None = None     # opaque id of the pre-split entity
    aliases: list[Alias] = field(default_factory=list)
    assertions: list[SourceAssertionRef] = field(default_factory=list)

    def __post_init__(self) -> None:
        parts = parse_entity_id(self.entity_id)
        if parts.entity_type is not self.entity_type:
            raise DomainError(
                f"id prefix {parts.prefix!r} disagrees with entity_type "
                f"{self.entity_type.value!r} for {self.entity_id!r}"
            )
        object.__setattr__(self, "created_at", _utc(self.created_at))
        if self.removed_at is not None:
            object.__setattr__(self, "removed_at", _utc(self.removed_at))

    @property
    def entity_key(self) -> str:
        """The ``AssertionV1.entity_key`` form (prefix stripped)."""
        return parse_entity_id(self.entity_id).entity_key

    @property
    def is_live(self) -> bool:
        return self.removed_at is None

    def live_at(self, when: datetime) -> bool:
        when = _utc(when)
        return self.created_at <= when and (
            self.removed_at is None or when < self.removed_at
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type.value,
            "created_at": self.created_at.isoformat(),
            "removed_at": self.removed_at.isoformat() if self.removed_at else None,
            "merged_into": self.merged_into,
            "split_from": self.split_from,
            "aliases": [a.to_dict() for a in self.aliases],
            "assertions": [a.to_dict() for a in self.assertions],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


# ---------------------------------------------------------------------------
# Registry events — the append-only temporal history of the registry itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistryEvent:
    """One entry in the registry's append-only event log."""

    seq: int
    event_type: str  # "create" | "merge" | "split" | "alias_attach"
    at: datetime
    entity_id: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", _utc(self.at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "at": self.at.isoformat(),
            "entity_id": self.entity_id,
            "detail": dict(self.detail),
        }


# ---------------------------------------------------------------------------
# DomainModel — the in-memory registry
# ---------------------------------------------------------------------------


class DomainModel:
    """In-memory canonical registry.

    The registry is intentionally small: it stores entity *shells*,
    aliases and the event log, and derives resolved truth by replaying
    assertions through the DP-03-02 resolver.  Persistence adapters
    serialise :meth:`snapshot` / :attr:`event_log`.

    All mutating operations append to the **event log** with a system
    timestamp, so the registry's state is reconstructable for any prior
    system time (:meth:`registry_events_since` / audit replay).
    """

    def __init__(self) -> None:
        self._entities: dict[str, CanonicalEntity] = {}
        #: (kind, normalised value) → [(entity_id, alias)]
        self._alias_index: dict[tuple[str, str], list[tuple[str, Alias]]] = {}
        self._log: list[RegistryEvent] = []
        self._assertions: dict[str, AssertionV1] = {}

    # -- Creation -------------------------------------------------------------

    def create_entity(
        self,
        entity_type: EntityType | str,
        *,
        at: datetime | None = None,
        _rand: random.Random | None = None,
    ) -> CanonicalEntity:
        """Register a new canonical entity with a fresh opaque id."""
        at = _utc(at) if at else datetime.now(timezone.utc)
        et = entity_type if isinstance(entity_type, EntityType) else EntityType(entity_type)
        entity_id = new_entity_id(et, _time_ms=int(at.timestamp() * 1000), _rand=_rand)
        entity = CanonicalEntity(
            entity_id=entity_id, entity_type=et, created_at=at
        )
        self._entities[entity_id] = entity
        self._log_event("create", at, entity_id, {"entity_type": et.value})
        return entity

    # -- Lookup ---------------------------------------------------------------

    def get(self, entity_id: str, *, at: datetime | None = None) -> CanonicalEntity:
        """Return the entity, enforcing id validity and (optionally) liveness."""
        parse_entity_id(entity_id)  # raises on malformed / unknown prefix
        entity = self._entities.get(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"no such canonical entity: {entity_id!r}")
        if at is not None and not entity.live_at(at):
            raise EntityNotFoundError(
                f"entity {entity_id!r} was not live at {_utc(at).isoformat()}"
            )
        return entity

    # -- Aliases ---------------------------------------------------------------

    @staticmethod
    def _normalise_alias(value: str) -> str:
        """Normalise a mutable label the way the matching engine does.

        Mirrors ``matching.identity.normalize_sail``: uppercase and strip
        spaces/dashes/dots/slashes, so ``"GBR 8310"`` and ``"GBR8310"``
        bind the same alias.  Names ("Wild Oats XI") equally fold case and
        separators; that is the point of an alias — forgiving *lookup*,
        with the opaque id remaining the only identity.
        """
        return re.sub(r"[\s\-\./]+", "", value.strip().upper())

    def attach_alias(self, entity_id: str, alias: Alias) -> None:
        """Attach a mutable source label to a live entity.

        An alias value may already exist on *another* entity only if the
        intervals do not overlap (sail numbers are re-issued over time).
        """
        entity = self.get(entity_id)
        if not entity.is_live:
            raise AliasedToRemovedError(
                f"cannot alias removed entity {entity_id!r}"
            )
        key = (alias.kind, self._normalise_alias(alias.value))
        far_future = datetime.max.replace(tzinfo=timezone.utc)
        for other_id, existing in self._alias_index.get(key, []):
            if other_id == entity_id:
                continue
            other = self._entities[other_id]
            if not other.is_live:
                continue
            # Half-open interval overlap: [from, to) intersects iff each
            # start precedes the other's end.
            overlap = (
                alias.valid_from < (existing.valid_to or far_future)
                and existing.valid_from < (alias.valid_to or far_future)
            )
            if overlap:
                raise DuplicateAliasError(
                    f"alias {alias.kind}={alias.value!r} already bound to "
                    f"{other_id!r} with overlapping validity"
                )
        entity.aliases.append(alias)
        self._alias_index.setdefault(key, []).append((entity_id, alias))
        self._log_event(
            "alias_attach",
            alias.valid_from,
            entity_id,
            {"kind": alias.kind, "value": alias.value},
        )

    def resolve_alias(
        self, kind: str, value: str, *, at: datetime | None = None
    ) -> CanonicalEntity | None:
        """Resolve a mutable source label to its live canonical entity."""
        key = (kind, self._normalise_alias(value))
        when = _utc(at) if at else datetime.now(timezone.utc)
        for entity_id, alias in self._alias_index.get(key, []):
            entity = self._entities[entity_id]
            if entity.live_at(when) and alias.covers(when):
                return entity
        return None

    # -- Assertions --------------------------------------------------------------

    def assert_about(self, entity_id: str, assertion: AssertionV1) -> SourceAssertionRef:
        """Attach an observed assertion to its subject entity.

        The assertion's ``(entity_type, entity_key)`` must be the parts of
        the entity's opaque id — this is the join between the DP-03-01
        registry and the DP-03-02 assertion store.
        """
        entity = self.get(entity_id)
        parts = parse_entity_id(entity_id)
        if assertion.entity_type != parts.prefix:
            raise AliasInconsistentError(
                f"assertion entity_type {assertion.entity_type!r} does not "
                f"match entity prefix {parts.prefix!r}"
            )
        if assertion.entity_key != parts.entity_key:
            raise AliasInconsistentError(
                f"assertion entity_key {assertion.entity_key!r} does not "
                f"match opaque id body {parts.entity_key!r}"
            )
        self._assertions[assertion.assertion_id] = assertion
        ref = SourceAssertionRef(
            assertion_id=assertion.assertion_id,
            field=assertion.field,
            recorded_at=assertion.recorded_at,
            valid_from=assertion.valid_from,
            source_slug=assertion.source_slug,
        )
        entity.assertions.append(ref)
        return ref

    # -- Resolved truth ------------------------------------------------------------

    def resolve_truth(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
        valid_as_of: datetime | None = None,
    ) -> ResolvedTruth:
        """Derive the resolved truth of one entity at one system time.

        Pure function of the immutable assertion store: the same entity
        resolved with the same ``as_of`` always yields the same winners,
        because assertions are never mutated (DP-03-02 bitemporal rules).
        """
        entity = self.get(entity_id)
        when = _utc(as_of) if as_of else datetime.now(timezone.utc)
        by_field: dict[str, list[AssertionV1]] = {}
        for ref in entity.assertions:
            assertion = self._assertions[ref.assertion_id]
            by_field.setdefault(assertion.field, []).append(assertion)
        winners: dict[str, AssertionV1] = {}
        conflicts: dict[str, tuple[AssertionV1, ...]] = {}
        for field_name, assertions in by_field.items():
            resolution: ResolutionV1 = resolve(
                assertions,
                entity.entity_type.value,
                entity.entity_key,
                field_name,
                as_of=when,
                valid_as_of=valid_as_of,
            )
            if resolution.winner is not None:
                winners[field_name] = resolution.winner
            if resolution.conflicts:
                conflicts[field_name] = tuple(resolution.conflicts)
        return ResolvedTruth(
            entity_id=entity_id,
            entity_type=entity.entity_type,
            as_of=when,
            fields=winners,
            conflicts=conflicts,
        )

    # -- Merge / split ------------------------------------------------------------

    def merge(
        self,
        survivor_id: str,
        removed_id: str,
        *,
        at: datetime | None = None,
        reason: str = "",
    ) -> CanonicalEntity:
        """Merge ``removed_id`` into ``survivor_id``.

        The removed entity is *not deleted*: it is stamped
        ``removed_at``/``merged_into`` so history remains reproducible,
        and its assertions and aliases are re-pointed at the survivor.
        A merge is therefore information-preserving and auditable; the
        pre-merge view is reconstructable for any system time before
        ``at``.
        """
        if survivor_id == removed_id:
            raise MergeSameEntityError("cannot merge an entity into itself")
        at = _utc(at) if at else datetime.now(timezone.utc)
        survivor = self.get(survivor_id, at=at)
        removed = self.get(removed_id, at=at)
        if survivor.entity_type is not removed.entity_type:
            raise DomainError(
                f"cannot merge {removed.entity_type.value} into "
                f"{survivor.entity_type.value}"
            )
        removed.removed_at = at
        removed.merged_into = survivor_id
        # Re-point assertions: the assertion's identity payload includes the
        # entity key, so re-keying produces a *new* content-addressed id —
        # the original rows stay immutable under the old id for audit.
        for ref in list(removed.assertions):
            assertion = self._assertions[ref.assertion_id]
            rekeyed = dataclasses.replace(
                assertion, entity_key=survivor.entity_key, assertion_id=""
            )
            del self._assertions[ref.assertion_id]
            self._assertions[rekeyed.assertion_id] = rekeyed
            removed.assertions.remove(ref)
            survivor.assertions.append(
                SourceAssertionRef(
                    assertion_id=rekeyed.assertion_id,
                    field=rekeyed.field,
                    recorded_at=rekeyed.recorded_at,
                    valid_from=rekeyed.valid_from,
                    source_slug=rekeyed.source_slug,
                )
            )
        survivor.aliases.extend(removed.aliases)
        removed.aliases.clear()
        # Re-point the alias index entries.
        for key, entries in self._alias_index.items():
            self._alias_index[key] = [
                (survivor_id if eid == removed_id else eid, alias)
                for eid, alias in entries
            ]
        self._log_event(
            "merge",
            at,
            survivor_id,
            {"removed": removed_id, "reason": reason},
        )
        return survivor

    def split(
        self,
        entity_id: str,
        *,
        assertion_ids: Iterable[str],
        at: datetime | None = None,
        reason: str = "",
        _rand: random.Random | None = None,
    ) -> CanonicalEntity:
        """Split a new entity off ``entity_id``, moving the named assertions.

        Used when one physical hull was double-counted (e.g. two claimed
        sail numbers turn out to be two hulls).  The original entity keeps
        its id and the assertions not named; the new entity is stamped
        ``split_from``.  Both entities keep their full history.
        """
        at = _utc(at) if at else datetime.now(timezone.utc)
        original = self.get(entity_id, at=at)
        ids = set(assertion_ids)
        held = {ref.assertion_id for ref in original.assertions}
        unknown = ids - held
        if unknown:
            raise SplitError(
                f"cannot split: assertions not on {entity_id!r}: {sorted(unknown)}"
            )
        new_entity = self.create_entity(
            original.entity_type, at=at, _rand=_rand
        )
        new_entity.split_from = entity_id
        for ref in list(original.assertions):
            if ref.assertion_id not in ids:
                continue
            assertion = self._assertions[ref.assertion_id]
            rekeyed = dataclasses.replace(
                assertion, entity_key=new_entity.entity_key, assertion_id=""
            )
            del self._assertions[ref.assertion_id]
            self._assertions[rekeyed.assertion_id] = rekeyed
            original.assertions.remove(ref)
            new_entity.assertions.append(
                SourceAssertionRef(
                    assertion_id=rekeyed.assertion_id,
                    field=rekeyed.field,
                    recorded_at=rekeyed.recorded_at,
                    valid_from=rekeyed.valid_from,
                    source_slug=rekeyed.source_slug,
                )
            )
        self._log_event(
            "split",
            at,
            entity_id,
            {"new_entity": new_entity.entity_id, "reason": reason},
        )
        return new_entity

    # -- History / audit ----------------------------------------------------------

    @property
    def event_log(self) -> tuple[RegistryEvent, ...]:
        """The append-only temporal history of registry operations."""
        return tuple(self._log)

    def registry_events_since(self, since: datetime) -> tuple[RegistryEvent, ...]:
        """Return registry events at or after ``since`` (system time)."""
        since = _utc(since)
        return tuple(e for e in self._log if e.at >= since)

    def snapshot(self) -> dict[str, Any]:
        """Serialise the registry (entities + event log) for persistence."""
        return {
            "schema_version": SCHEMA_VERSION,
            "entities": [e.to_dict() for e in self._entities.values()],
            "event_log": [e.to_dict() for e in self._log],
        }

    # -- Internals -----------------------------------------------------------------

    def _log_event(
        self, event_type: str, at: datetime, entity_id: str, detail: Mapping[str, Any]
    ) -> None:
        self._log.append(
            RegistryEvent(
                seq=len(self._log) + 1,
                event_type=event_type,
                at=at,
                entity_id=entity_id,
                detail=detail,
            )
        )
