"""Deterministic blocking and candidate generation (DP-04-02).

Goal
----

Find plausible boat-identity matches at scale **without all-pairs
comparison**.  Given a collection of entity observations (normalised
records produced by the DP-03 pipeline), :class:`CandidateGenerator`
reduces the quadratic ``O(n²)`` comparison space to a small set of
*candidate pairs* that a downstream scorer can evaluate cheaply.

The problem this solves
-----------------------

A corpus of ``n`` observations has ``n·(n−1)/2`` unordered pairs — at
100k observations that is ~5 billion comparisons.  Real duplicate
fractions in the sailing data are a tiny fraction of a percent, so
nearly all of that work is wasted.  **Blocking** partitions the
observations into groups ("blocks") keyed on strong shared signals; only
pairs that share at least one block are ever compared.  Well-chosen
blocking keys keep **recall** (the fraction of true duplicate pairs that
survive blocking) high while cutting **candidate volume** by orders of
magnitude.

Blocking rules (versioned)
--------------------------

Blocking is expressed as a *ruleset* — an ordered tuple of
:class:`BlockingRule` objects, each emitting zero or more
:class:`BlockingKey` values per observation.  Rules are **pure and
deterministic**: the same observation always yields the same keys, so
re-running the pipeline on unchanged input reproduces the exact same
candidate set.

The shipped ruleset is :data:`BLOCKING_RULESET_V1`
(``blocking-rules-v1``):

======  ===========================  ============================================
id      name                         key signal
======  ===========================  ============================================
R01     ``sail_number_token``        each equivalent sail-number token
                                     (class/country-prefix variants, bare number)
R02     ``registry_id``              normalised registry / hull id (HIN, ORC ref,
                                     national registration number, IMO)
R03     ``design_exact``             alias-resolved canonical design name
R04     ``dimensions_band``          design family + LOA banded to 0.5 m
R05     ``name_exact``               full normalised boat name
R06     ``name_soundex_geo``         soundex of name tokens + country
R07     ``temporal_overlap_design``  design family + overlapping validity years
======  ===========================  ============================================

Design notes
------------

* **Every candidate records which rules fired** — :class:`CandidatePair.rules_fired`
  is the sorted tuple of rule ids that produced the pair, satisfying the
  acceptance criterion that no candidate exists without an explanation.
* **Pathological blocks are capped** — a block larger than
  ``max_block_size`` (e.g. thousands of boats with no name sharing the
  design ``Beneteau First 40.7`` era block) is skipped to bound worst-case
  candidate volume; the skip is recorded in
  :attr:`BlockingStats.skipped_oversized_blocks` so the evaluation can
  detect recall loss from capping.
* **Guarded keys** — weak keys (short ambiguous sail numbers, generic
  one-letter designs, dimension bands) only fire when accompanied by a
  corroborating signal (country match) so recall is kept without flooding
  the candidate set.

Builds on: DP-03-01 (canonical entity vocabulary), DP-03-03 (normalised
observations feeding the input records).
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from irc_data.matching.identity import (
    _strip_country_prefix,
    normalize_sail,
    normalize_sail_tokens,
)

# ---------------------------------------------------------------------------
# Schema / ruleset versioning
# ---------------------------------------------------------------------------

#: Version tag for the candidate contract (serialisation format).
SCHEMA_VERSION = "blocking-v1"

#: Identifier of the shipped ruleset.  Bumping this (and adding a new
#: ruleset constant below) is how rules are *versioned* — old runs remain
#: reproducible because the ruleset id is recorded on every report.
RULESET_V1_ID = "blocking-rules-v1"

#: All known ruleset ids, oldest first.
KNOWN_RULESETS: tuple[str, ...] = (RULESET_V1_ID,)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BlockingError(ValueError):
    """Base class for blocking contract violations."""


class UnknownRulesetError(BlockingError):
    """Raised when a caller requests a ruleset id we do not ship."""


# ---------------------------------------------------------------------------
# Input contract: an entity observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityObservation:
    """One normalised observation of a boat-like entity.

    This is the *input* contract for blocking: the normalised record the
    DP-03 pipeline hands to identity resolution.  All fields are optional
    except ``observation_id`` — a missing field simply means the rules
    that depend on it emit no keys.

    ``valid_from`` / ``valid_to`` describe the *source-valid* interval of
    the observation (e.g. a certificate's issue/expiry dates, a result's
    event date).  They drive the temporal-overlap rule.
    """

    observation_id: str
    sail_number: str | None = None
    registry_id: str | None = None
    name: str | None = None
    design: str | None = None
    country: str | None = None
    loa_m: float | None = None
    beam_m: float | None = None
    displacement_kg: float | None = None
    year_built: int | None = None
    valid_from: date | None = None
    valid_to: date | None = None

    def __post_init__(self) -> None:
        if not self.observation_id or not self.observation_id.strip():
            raise BlockingError("EntityObservation requires a non-empty observation_id")
        if self.loa_m is not None and not (0 < float(self.loa_m) < 200):
            raise BlockingError(f"implausible loa_m={self.loa_m!r}")
        if self.year_built is not None and not (1850 <= int(self.year_built) <= 2100):
            raise BlockingError(f"implausible year_built={self.year_built!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "sail_number": self.sail_number,
            "registry_id": self.registry_id,
            "name": self.name,
            "design": self.design,
            "country": self.country,
            "loa_m": self.loa_m,
            "beam_m": self.beam_m,
            "displacement_kg": self.displacement_kg,
            "year_built": self.year_built,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "EntityObservation":
        return cls(
            observation_id=d["observation_id"],
            sail_number=d.get("sail_number"),
            registry_id=d.get("registry_id"),
            name=d.get("name"),
            design=d.get("design"),
            country=d.get("country"),
            loa_m=d.get("loa_m"),
            beam_m=d.get("beam_m"),
            displacement_kg=d.get("displacement_kg"),
            year_built=d.get("year_built"),
            valid_from=_parse_date(d.get("valid_from")),
            valid_to=_parse_date(d.get("valid_to")),
        )


def _parse_date(v: Any) -> date | None:
    if v is None or isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    return date.fromisoformat(str(v))


# ---------------------------------------------------------------------------
# Normalisation helpers (deterministic, dependency-free)
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9 ]+")
_ALPHA_ONLY_RE = re.compile(r"^[A-Z]+$")

# Tokens that carry no discriminating power in boat names.
_NAME_STOPWORDS = frozenset({
    "THE", "OF", "AND", "A", "AN", "II", "III", "IV",
})

# Generic design tokens that would create useless mega-blocks.
_GENERIC_DESIGN_TOKENS = frozenset({
    "UNKNOWN", "OTHER", "CUSTOM", "ONE OFF", "ONEOFF", "PRODUCTION",
})


def _ascii_fold(text: str) -> str:
    """Fold accents/diacritics to ASCII so 'Émile' and 'EMILE' block together."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_name_key(name: str | None) -> str:
    """Canonical name key: ASCII-folded, uppercased, punctuation stripped."""
    if not name:
        return ""
    text = _ascii_fold(name).upper()
    text = _NON_ALNUM_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _name_tokens(name: str | None) -> tuple[str, ...]:
    """Significant name tokens (stopwords removed), order-preserving."""
    key = normalize_name_key(name)
    if not key:
        return ()
    return tuple(t for t in key.split(" ") if t and t not in _NAME_STOPWORDS)


def _soundex(token: str) -> str:
    """Classic American Soundex for a single *already-uppercased* token."""
    if not token or not _ALPHA_ONLY_RE.match(token):
        return ""
    codes = {
        **dict.fromkeys("BFPV", "1"),
        **dict.fromkeys("CGJKQSXZ", "2"),
        **dict.fromkeys("DT", "3"),
        "L": "4",
        **dict.fromkeys("MN", "5"),
        "R": "6",
    }
    first = token[0]
    digits: list[str] = []
    prev = codes.get(first, "")
    for ch in token[1:]:
        d = codes.get(ch, "")
        if d and d != prev:
            digits.append(d)
        if ch in "HW":
            # H and W do not break a run of like-coded consonants
            # (classic rule: ASHCRAFT encodes S and C once → A261).
            continue
        prev = d
    return (first + "".join(digits) + "000")[:4]


def normalize_registry_id(registry_id: str | None) -> str:
    """Normalise a registry / hull identifier: upper, alnum only."""
    if not registry_id:
        return ""
    text = _ascii_fold(registry_id).upper()
    return re.sub(r"[^A-Z0-9]+", "", text)


def normalize_design_key(design: str | None) -> str:
    """Normalise a design/model string to a canonical block key.

    Collapses punctuation variants and common suffix noise so
    ``"J/122"``, ``"J 122"`` and ``"J122"`` share a block.
    """
    if not design:
        return ""
    text = _ascii_fold(design).upper()
    text = text.replace("&", " AND ")
    text = text.replace("/", " ").replace("-", " ").replace(".", " ")
    text = _NON_ALNUM_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if text in _GENERIC_DESIGN_TOKENS:
        return ""
    return text


def _design_family(design_key: str) -> str:
    """Compact design key with spaces removed ('FIRST 40 7' -> 'FIRST407')."""
    return design_key.replace(" ", "")


def _year(d: date | None) -> int | None:
    return d.year if d is not None else None


def _observation_year_lo(obs: EntityObservation) -> int | None:
    """Earliest year this observation can plausibly describe the boat."""
    years = [y for y in (
        _year(obs.valid_from), obs.year_built,
    ) if y is not None]
    return min(years) if years else None


def _observation_year_hi(obs: EntityObservation) -> int | None:
    """Latest year this observation can plausibly describe the boat."""
    years = [y for y in (
        _year(obs.valid_to), _year(obs.valid_from), obs.year_built,
    ) if y is not None]
    return max(years) if years else None


# ---------------------------------------------------------------------------
# Blocking keys
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockingKey:
    """One blocking key emitted for one observation.

    ``value`` is the bucket the observation is filed under; observations
    sharing ``(rule_id, value)`` become a candidate pair.  ``detail``
    records *why* the key was emitted (e.g. the matched sail token) for
    auditability — it never affects grouping.
    """

    rule_id: str
    value: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"rule_id": self.rule_id, "value": self.value, "detail": self.detail}


# ---------------------------------------------------------------------------
# Blocking rules
# ---------------------------------------------------------------------------


class BlockingRule:
    """Base class for a deterministic blocking rule.

    Subclasses implement :meth:`keys_for`, returning the blocking keys for
    one observation.  Rules must be **pure functions of the observation**
    (no clocks, no randomness) so candidate generation is reproducible.
    """

    rule_id: str = ""
    name: str = ""
    description: str = ""
    #: Weak keys only fire alongside a corroborating signal.
    guarded: bool = False

    def keys_for(self, obs: EntityObservation) -> tuple[BlockingKey, ...]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers ------------------------------------------------------------

    def _key(self, value: str, detail: str = "") -> BlockingKey:
        return BlockingKey(rule_id=self.rule_id, value=value, detail=detail)


class SailNumberTokenRule(BlockingRule):
    """R01 — block on every equivalent sail-number token.

    Uses :func:`irc_data.matching.identity.normalize_sail_tokens` so the
    class/country-prefix expansion (``EAUS1213`` → ``{EAUS1213, AUS1213,
    1213}``) is shared with the legacy matcher.  Bare short numeric tokens
    are *guarded*: they only fire when the observation also carries a
    country, keyed ``<token>|<country>``, because a bare ``4343`` recurs
    across nations.
    """

    rule_id = "R01"
    name = "sail_number_token"
    description = "Equivalent sail-number tokens (country/class-prefix variants)."

    def keys_for(self, obs: EntityObservation) -> tuple[BlockingKey, ...]:
        tokens = normalize_sail_tokens(obs.sail_number)
        if not tokens:
            return ()
        country = (obs.country or "").strip().upper()
        keys: list[BlockingKey] = []
        for token in sorted(tokens):
            if self._is_bare_ambiguous(token):
                if country:
                    keys.append(self._key(f"{token}|{country}",
                                          detail=f"guarded sail token {token}"))
                # unguarded bare short numbers are too noisy to block on
            else:
                keys.append(self._key(token, detail=f"sail token {token}"))
        return tuple(keys)

    @staticmethod
    def _is_bare_ambiguous(token: str) -> bool:
        return len(token) <= 4 and token.isdigit()


class RegistryIdRule(BlockingRule):
    """R02 — block on normalised registry / hull identifier.

    Registry identifiers (HIN, ORC ref numbers, national registration
    numbers, IMO numbers for the big yachts) are the strongest available
    identity signal after the sail number: they are issued once per hull
    and survive renames.
    """

    rule_id = "R02"
    name = "registry_id"
    description = "Normalised registry / hull identifier (HIN, ORC ref, national reg)."

    def keys_for(self, obs: EntityObservation) -> tuple[BlockingKey, ...]:
        norm = normalize_registry_id(obs.registry_id)
        if not norm:
            return ()
        return (self._key(norm, detail=f"registry_id {norm}"),)


#: Name tokens too generic to discriminate a design block ("Boat", "Yacht"…).
_GENERIC_NAME_TOKENS = frozenset({
    "BOAT", "YACHT", "SAILING", "SAIL", "RACING", "TEAM", "SPIRIT",
})


class DesignExactRule(BlockingRule):
    """R03 — block on the normalised design/model string.

    Boats of the same design are *not* duplicates on their own, so this
    rule alone would flood the candidate set.  It is only useful in
    *conjunction* with the name / dimension rules; to keep the candidate
    volume bounded this rule is **guarded**: it emits a key only when the
    observation also has a discriminating name token or a sail number, and
    the key combines the design family with that token.  Generic first
    tokens ("Boat 7", "Yacht 12") and very short tokens are skipped in
    favour of a longer, more specific token.
    """

    rule_id = "R03"
    name = "design_exact"
    description = "Design family + discriminating name/sail token."
    guarded = True

    def keys_for(self, obs: EntityObservation) -> tuple[BlockingKey, ...]:
        design_key = normalize_design_key(obs.design)
        if not design_key:
            return ()
        family = _design_family(design_key)
        token = self._discriminating_name_token(obs.name)
        if token:
            return (self._key(f"{family}|N:{token}",
                              detail=f"design {family} + name token {token}"),)
        tokens = sorted(normalize_sail_tokens(obs.sail_number))
        if tokens:
            return (self._key(f"{family}|S:{tokens[0]}",
                              detail=f"design {family} + sail token {tokens[0]}"),)
        return ()

    @staticmethod
    def _discriminating_name_token(name: str | None) -> str | None:
        """Pick the most discriminating name token, or None.

        Preference order: longest non-generic token; generic/short tokens
        are only used when nothing better exists (and tokens shorter than
        3 chars are never used — ``"XI"`` on a design says nothing).
        """
        tokens = _name_tokens(name)
        if not tokens:
            return None
        specific = [t for t in tokens
                    if t not in _GENERIC_NAME_TOKENS and len(t) >= 3]
        if specific:
            return max(specific, key=lambda t: (len(t), t))
        return None


class DimensionsBandRule(BlockingRule):
    """R04 — block on design family + LOA banded to 0.5 m.

    Two observations of the same physical boat almost always agree on the
    model (design family) and on LOA to well under half a metre; banding
    LOA absorbs certificate rounding (``12.19`` vs ``12.2``).  Guarded so
    it only fires when LOA and a design are both present.
    """

    rule_id = "R04"
    name = "dimensions_band"
    description = "Design family + LOA rounded to 0.5 m bands."
    guarded = True

    BAND_M = 0.5

    def keys_for(self, obs: EntityObservation) -> tuple[BlockingKey, ...]:
        design_key = normalize_design_key(obs.design)
        if not design_key or obs.loa_m is None:
            return ()
        family = _design_family(design_key)
        keys: list[BlockingKey] = []
        # Emit the two adjacent bands the LOA could round into so a
        # measurement exactly on a boundary still co-blocks with its twin.
        base = math.floor(float(obs.loa_m) / self.BAND_M)
        for band in {base, base - 1, base + 1}:
            lo = band * self.BAND_M
            hi = lo + self.BAND_M
            keys.append(self._key(f"{family}|LOA:{lo:.2f}-{hi:.2f}",
                                  detail=f"loa {obs.loa_m} in band {lo:.1f}–{hi:.1f}"))
        return tuple({k.value: k for k in keys}[v] for v in sorted({k.value for k in keys}))


class NameExactRule(BlockingRule):
    """R05 — block on the full normalised boat name.

    Names are *not* unique identifiers (there are many ``"Blue Eyes"``),
    so the raw block could in principle be large; in practice distinct
    boats sharing an exact full name are rare, and the evaluation harness
    measures the resulting volume.  The key is the full normalised name,
    so ``"Wild Oats XI"`` and ``"WILD  OATS XI"`` co-block.
    """

    rule_id = "R05"
    name = "name_exact"
    description = "Full normalised boat name."

    def keys_for(self, obs: EntityObservation) -> tuple[BlockingKey, ...]:
        key = normalize_name_key(obs.name)
        if len(key) < 3:
            return ()
        return (self._key(key, detail=f"name {key}"),)


class NameSoundexGeoRule(BlockingRule):
    """R06 — block on soundex of the name + country.

    Catches the common scraper typo / transliteration case (``"Ragamuffin"
    `` vs ``"Raggamuffin"``) without an expensive fuzzy comparison: the
    soundex of the concatenated significant name tokens collapses
    homophone-ish spellings into one block, scoped by country so
    like-named boats from different fleets stay apart.  Guarded: fires
    only when both a name and a country are present.
    """

    rule_id = "R06"
    name = "name_soundex_geo"
    description = "Soundex of significant name tokens + country."
    guarded = True

    def keys_for(self, obs: EntityObservation) -> tuple[BlockingKey, ...]:
        country = (obs.country or "").strip().upper()
        tokens = _name_tokens(obs.name)
        if not country or not tokens:
            return ()
        codes = [c for c in (_soundex(t) for t in tokens) if c]
        if not codes:
            return ()
        return (self._key(f"{'-'.join(codes)}|{country}",
                          detail=f"soundex {'-'.join(codes)} in {country}"),)


class TemporalOverlapDesignRule(BlockingRule):
    """R07 — block on design family + overlapping temporal era.

    The same design raced across decades contains many distinct hulls;
    era-banding the observation's validity years (5-year eras) keeps
    same-design/different-era boats apart.  Guarded: needs a design and at
    least one temporal signal (validity date or year built).
    """

    rule_id = "R07"
    name = "temporal_overlap_design"
    description = "Design family + 5-year temporal era from validity/year-built."
    guarded = True

    ERA_YEARS = 5

    def keys_for(self, obs: EntityObservation) -> tuple[BlockingKey, ...]:
        design_key = normalize_design_key(obs.design)
        if not design_key:
            return ()
        lo = _observation_year_lo(obs)
        hi = _observation_year_hi(obs)
        if lo is None and hi is None:
            return ()
        lo = hi if lo is None else lo
        hi = lo if hi is None else hi
        family = _design_family(design_key)
        keys = []
        for era in range(lo // self.ERA_YEARS, hi // self.ERA_YEARS + 1):
            era_lo = era * self.ERA_YEARS
            era_hi = era_lo + self.ERA_YEARS - 1
            keys.append(self._key(f"{family}|ERA:{era_lo}-{era_hi}",
                                  detail=f"{lo}–{hi} overlaps {era_lo}–{era_hi}"))
        return tuple(keys)


# ---------------------------------------------------------------------------
# Versioned rulesets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockingRuleset:
    """An immutable, versioned, ordered tuple of blocking rules."""

    ruleset_id: str
    rules: tuple[BlockingRule, ...]
    description: str = ""

    def rule_ids(self) -> tuple[str, ...]:
        return tuple(r.rule_id for r in self.rules)

    def keys_for(self, obs: EntityObservation) -> tuple[BlockingKey, ...]:
        """All blocking keys an observation emits under this ruleset."""
        out: list[BlockingKey] = []
        for rule in self.rules:
            out.extend(rule.keys_for(obs))
        return tuple(out)

    def fingerprint(self) -> str:
        """Stable content fingerprint of the ruleset (for provenance)."""
        h = hashlib.sha256()
        h.update(self.ruleset_id.encode())
        for rule in self.rules:
            h.update(rule.rule_id.encode())
            h.update(rule.name.encode())
        return h.hexdigest()[:16]


#: The shipped v1 ruleset — the seven rules in evaluation order.
BLOCKING_RULESET_V1 = BlockingRuleset(
    ruleset_id=RULESET_V1_ID,
    rules=(
        SailNumberTokenRule(),
        RegistryIdRule(),
        DesignExactRule(),
        DimensionsBandRule(),
        NameExactRule(),
        NameSoundexGeoRule(),
        TemporalOverlapDesignRule(),
    ),
    description=(
        "Seven deterministic rules over sail-number tokens, registry ids, "
        "design, banded dimensions, exact + soundex/geography names, and "
        "design-era temporal overlap."
    ),
)

_RULESETS: dict[str, BlockingRuleset] = {
    BLOCKING_RULESET_V1.ruleset_id: BLOCKING_RULESET_V1,
}


def get_ruleset(ruleset_id: str = RULESET_V1_ID) -> BlockingRuleset:
    """Fetch a shipped ruleset by id (versioned, reproducible)."""
    try:
        return _RULESETS[ruleset_id]
    except KeyError:
        raise UnknownRulesetError(
            f"unknown blocking ruleset {ruleset_id!r}; known: {sorted(_RULESETS)}"
        ) from None


# ---------------------------------------------------------------------------
# Candidate pairs and generation stats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidatePair:
    """One unordered pair of observations that share a blocking block.

    ``rules_fired`` is the sorted tuple of rule ids that placed *both*
    observations in a common block — **every candidate records which rules
    fired**, satisfying the auditability acceptance criterion.
    ``matching_keys`` lists the shared ``(rule_id, value)`` block keys.
    """

    left_id: str
    right_id: str
    rules_fired: tuple[str, ...]
    matching_keys: tuple[str, ...]
    ruleset_id: str = RULESET_V1_ID

    def __post_init__(self) -> None:
        if self.left_id == self.right_id:
            raise BlockingError("a candidate pair must join two distinct observations")
        if not self.rules_fired:
            raise BlockingError("a candidate pair must record at least one firing rule")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "left_id": self.left_id,
            "right_id": self.right_id,
            "rules_fired": list(self.rules_fired),
            "matching_keys": list(self.matching_keys),
            "ruleset_id": self.ruleset_id,
        }


@dataclass
class BlockingStats:
    """Run statistics used by the evaluation harness."""

    observations: int = 0
    keys_emitted: int = 0
    blocks_formed: int = 0
    skipped_oversized_blocks: int = 0
    candidate_pairs: int = 0
    all_pairs: int = 0
    per_rule_pairs: dict[str, int] = field(default_factory=dict)
    per_rule_keys: dict[str, int] = field(default_factory=dict)
    runtime_seconds: float = 0.0

    @property
    def reduction_ratio(self) -> float:
        """``1 - candidates/all_pairs`` — 0 means no reduction, →1 is better."""
        if self.all_pairs == 0:
            return 1.0
        return 1.0 - (self.candidate_pairs / self.all_pairs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "keys_emitted": self.keys_emitted,
            "blocks_formed": self.blocks_formed,
            "skipped_oversized_blocks": self.skipped_oversized_blocks,
            "candidate_pairs": self.candidate_pairs,
            "all_pairs": self.all_pairs,
            "reduction_ratio": self.reduction_ratio,
            "per_rule_pairs": dict(self.per_rule_pairs),
            "per_rule_keys": dict(self.per_rule_keys),
            "runtime_seconds": self.runtime_seconds,
        }


@dataclass(frozen=True)
class CandidateReport:
    """The full output of a candidate-generation run (handoff contract)."""

    ruleset_id: str
    ruleset_fingerprint: str
    pairs: tuple[CandidatePair, ...]
    stats: BlockingStats

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "ruleset_id": self.ruleset_id,
            "ruleset_fingerprint": self.ruleset_fingerprint,
            "stats": self.stats.to_dict(),
            "pairs": [p.to_dict() for p in self.pairs],
        }


# ---------------------------------------------------------------------------
# The candidate generator
# ---------------------------------------------------------------------------

#: Blocks larger than this are skipped as pathological (bounds worst-case
#: candidate volume).  Oversized skips are recorded in stats.
DEFAULT_MAX_BLOCK_SIZE = 500


class CandidateGenerator:
    """Deterministic blocking-based candidate generator.

    Usage::

        generator = CandidateGenerator()          # ruleset v1
        report = generator.generate(observations)  # CandidateReport

    The generator is stateless and deterministic: equal inputs yield an
    identical candidate set (pair ordering is sorted by id).
    """

    def __init__(
        self,
        ruleset: BlockingRuleset | str = RULESET_V1_ID,
        *,
        max_block_size: int = DEFAULT_MAX_BLOCK_SIZE,
        clock: Any = None,
    ) -> None:
        if isinstance(ruleset, str):
            ruleset = get_ruleset(ruleset)
        if max_block_size < 2:
            raise BlockingError("max_block_size must be >= 2")
        self.ruleset = ruleset
        self.max_block_size = int(max_block_size)
        # ``clock`` is injectable purely so runtime measurement is mockable
        # in tests; it never influences *which* candidates are produced.
        self._clock = clock or _monotonic

    def blocking_index(
        self, observations: Iterable[EntityObservation]
    ) -> dict[str, dict[str, list[str]]]:
        """Build the block index: rule_id → block value → observation ids."""
        index: dict[str, dict[str, list[str]]] = {}
        for obs in observations:
            for key in self.ruleset.keys_for(obs):
                index.setdefault(key.rule_id, {}).setdefault(key.value, []).append(
                    obs.observation_id
                )
        return index

    def generate(
        self, observations: Iterable[EntityObservation]
    ) -> CandidateReport:
        """Generate the deduplicated candidate set for *observations*.

        Returns a :class:`CandidateReport` carrying the pairs, the ruleset
        provenance, and run statistics (including runtime) for evaluation.
        """
        started = self._clock()
        obs_list = list(observations)
        stats = BlockingStats(observations=len(obs_list))
        stats.all_pairs = len(obs_list) * (len(obs_list) - 1) // 2

        # rule_id → block_value → [observation_id, ...]
        index: dict[str, dict[str, list[str]]] = {}
        seen_ids: set[str] = set()
        for obs in obs_list:
            if obs.observation_id in seen_ids:
                raise BlockingError(f"duplicate observation_id {obs.observation_id!r}")
            seen_ids.add(obs.observation_id)
            keys = self.ruleset.keys_for(obs)
            stats.keys_emitted += len(keys)
            for key in keys:
                stats.per_rule_keys[key.rule_id] = (
                    stats.per_rule_keys.get(key.rule_id, 0) + 1
                )
                index.setdefault(key.rule_id, {}).setdefault(key.value, []).append(
                    obs.observation_id
                )

        # pair (left, right) → {rule_id: set(block values)}
        pair_rules: dict[tuple[str, str], dict[str, set[str]]] = {}
        for rule_id, blocks in index.items():
            for value, members in blocks.items():
                if len(members) < 2:
                    continue
                stats.blocks_formed += 1
                if len(members) > self.max_block_size:
                    stats.skipped_oversized_blocks += 1
                    continue
                members = sorted(members)
                for i in range(len(members)):
                    for j in range(i + 1, len(members)):
                        pair = (members[i], members[j])
                        rule_map = pair_rules.setdefault(pair, {})
                        rule_map.setdefault(rule_id, set()).add(value)

        pairs = tuple(
            CandidatePair(
                left_id=left,
                right_id=right,
                rules_fired=tuple(sorted(rule_map)),
                matching_keys=tuple(
                    sorted(
                        f"{rule_id}:{value}"
                        for rule_id, values in rule_map.items()
                        for value in values
                    )
                ),
                ruleset_id=self.ruleset.ruleset_id,
            )
            for (left, right), rule_map in sorted(pair_rules.items())
        )

        stats.candidate_pairs = len(pairs)
        for pair in pairs:
            for rule_id in pair.rules_fired:
                stats.per_rule_pairs[rule_id] = stats.per_rule_pairs.get(rule_id, 0) + 1
        stats.runtime_seconds = self._clock() - started

        return CandidateReport(
            ruleset_id=self.ruleset.ruleset_id,
            ruleset_fingerprint=self.ruleset.fingerprint(),
            pairs=pairs,
            stats=stats,
        )


def _monotonic() -> float:
    """Wall-clock-independent monotonic timer for runtime measurement."""
    import time

    return time.monotonic()


# ---------------------------------------------------------------------------
# Evaluation harness — recall / precision ceiling / runtime
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationTargets:
    """Dataset-specific targets the evaluation corpus is measured against."""

    min_recall: float = 0.98
    max_pair_ratio: float = 0.02  # candidates / all_pairs
    max_runtime_seconds: float = 30.0

    def to_dict(self) -> dict[str, float]:
        return {
            "min_recall": self.min_recall,
            "max_pair_ratio": self.max_pair_ratio,
            "max_runtime_seconds": self.max_runtime_seconds,
        }


@dataclass(frozen=True)
class EvaluationResult:
    """Measured quality of a candidate set against a labelled corpus.

    * ``recall`` — fraction of labelled duplicate pairs present in the
      candidate set.  This is the number the acceptance criteria care
      about most: a pair lost here can never be recovered downstream.
    * ``precision_ceiling`` — the best precision any downstream scorer
      could achieve on this candidate set (labelled duplicates ÷
      candidates).  Blocking *defines* the precision ceiling.
    * ``pair_ratio`` — candidates ÷ all pairs (volume metric).
    """

    ruleset_id: str
    known_matches: int
    candidates: int
    all_pairs: int
    recall: float
    precision_ceiling: float
    pair_ratio: float
    reduction_ratio: float
    runtime_seconds: float
    missed_pairs: tuple[tuple[str, str], ...]
    targets: EvaluationTargets

    @property
    def recall_ok(self) -> bool:
        return self.recall >= self.targets.min_recall

    @property
    def volume_ok(self) -> bool:
        return self.pair_ratio <= self.targets.max_pair_ratio

    @property
    def runtime_ok(self) -> bool:
        return self.runtime_seconds <= self.targets.max_runtime_seconds

    def passed(self) -> bool:
        return self.recall_ok and self.volume_ok and self.runtime_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "ruleset_id": self.ruleset_id,
            "known_matches": self.known_matches,
            "candidates": self.candidates,
            "all_pairs": self.all_pairs,
            "recall": self.recall,
            "precision_ceiling": self.precision_ceiling,
            "pair_ratio": self.pair_ratio,
            "reduction_ratio": self.reduction_ratio,
            "runtime_seconds": self.runtime_seconds,
            "missed_pairs": [list(p) for p in self.missed_pairs],
            "targets": self.targets.to_dict(),
            "checks": {
                "recall_ok": self.recall_ok,
                "volume_ok": self.volume_ok,
                "runtime_ok": self.runtime_ok,
            },
            "passed": self.passed(),
        }


def _canon_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def evaluate_candidates(
    report: CandidateReport,
    known_matches: Iterable[tuple[str, str]],
    *,
    targets: EvaluationTargets | None = None,
) -> EvaluationResult:
    """Measure recall, precision ceiling and runtime against known matches.

    ``known_matches`` are labelled duplicate pairs ``(obs_id, obs_id)`` —
    the *evaluation corpus* ground truth.  The comparison is purely
    set-based and therefore deterministic.
    """
    targets = targets or EvaluationTargets()
    truth = {
        _canon_pair(a, b) for a, b in known_matches if a != b
    }
    candidate_set = {_canon_pair(p.left_id, p.right_id) for p in report.pairs}
    found = truth & candidate_set
    missed = tuple(sorted(truth - candidate_set))

    recall = (len(found) / len(truth)) if truth else 1.0
    precision_ceiling = (len(found) / len(candidate_set)) if candidate_set else (
        1.0 if not truth else 0.0
    )
    pair_ratio = (len(candidate_set) / report.stats.all_pairs) if report.stats.all_pairs else 0.0

    return EvaluationResult(
        ruleset_id=report.ruleset_id,
        known_matches=len(truth),
        candidates=len(candidate_set),
        all_pairs=report.stats.all_pairs,
        recall=recall,
        precision_ceiling=precision_ceiling,
        pair_ratio=pair_ratio,
        reduction_ratio=report.stats.reduction_ratio,
        runtime_seconds=report.stats.runtime_seconds,
        missed_pairs=missed,
        targets=targets,
    )
