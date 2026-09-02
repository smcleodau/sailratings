"""Normalisation outcome contract and rule-version registry (DP-03-03).

This module defines the **handoff / output contract** for the
normalisation library: how a raw value, as extracted from a source
artifact, is turned into a canonical value *consistently and
transparently*.

Two guarantees govern everything in this package:

1. **Transparency.**  Every normalised value retains
   :attr:`NormalisedValueV1.original` (the untouched original
   representation), the :attr:`NormalisedValueV1.rule` id that produced
   it and the :data:`RULES_VERSION` of the rule set.  Nothing is
   normalised "in place" without a trace.

2. **Quarantine, never guess.**  When a conversion is ambiguous — the
   same text could legitimately mean two different things (``"10.5"``
   metres *or* feet, ``"03/04/2024"`` day-first *or* month-first), or
   the value is malformed — the library **refuses to guess** and marks
   the value ``quarantined`` with a human-readable reason.  Callers may
   catch :class:`AmbiguousNormalisationError` (raised only when
   ``on_ambiguous="raise"``) or inspect the quarantined result and route
   it to manual review.

NormalisedValueV1 vs AssertionV1 (DP-03-02)
-------------------------------------------

``NormalisedValueV1`` is the result of applying *deterministic rules*
to one raw token: it answers *"how should this value be written
canonically?"*.  ``AssertionV1`` is a *bitemporal provenance record*:
it answers *"who said what, when?"*.  A ``NormalisedValueV1`` is
typically embedded in (or referenced by) an ``AssertionV1``; it is
deliberately not itself bitemporal — rule versioning, not time, is its
reproducibility axis.

Missing-value semantics
-----------------------

Missing data is **explicit, never implicit**:

* ``None`` or a recognised missing token (``""``, ``"-"``, ``"N/A"``,
  ``"DNF"`` …, see :data:`MISSING_TOKENS`) →
  :attr:`NormalisationKind.MISSING` with ``value=None``.
* Anything else that cannot be confidently normalised →
  :attr:`NormalisationKind.QUARANTINED` with the raw value preserved.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Rule-set version
# ---------------------------------------------------------------------------

#: Version of the entire normalisation rule set.  Bump when any rule in
#: this package changes so historical normalisations remain attributable
#: and replayable.
RULES_VERSION = "norm-v1"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class NormalisationError(ValueError):
    """Base class for normalisation failures."""


class AmbiguousNormalisationError(NormalisationError):
    """Raised (only under ``on_ambiguous="raise"``) when a conversion is
    ambiguous or malformed and the library refuses to guess."""


# ---------------------------------------------------------------------------
# Kinds / rule ids
# ---------------------------------------------------------------------------


class NormalisationKind(str, enum.Enum):
    """The outcome of a normalisation attempt.

    ``NORMALISED``
        A canonical value was produced deterministically.
    ``MISSING``
        The input was an explicit/implicit missing token; ``value`` is
        ``None``.
    ``QUARANTINED``
        The input was ambiguous or malformed; the original is preserved
        and the ``reason`` explains why no guess was made.
    """

    NORMALISED = "normalised"
    MISSING = "missing"
    QUARANTINED = "quarantined"


class Rule(str, enum.Enum):
    """Stable identifiers for the normalisation rules in this package.

    Embedded in every :class:`NormalisedValueV1` so downstream consumers
    can see exactly which rule produced (or declined to produce) a value.
    """

    LENGTH = "length-v1"
    WEIGHT = "weight-v1"
    TIME = "time-v1"
    RATING = "rating-v1"
    DIMENSIONLESS = "dimensionless-v1"
    NAME = "name-v1"
    SAIL_NUMBER = "sail-number-v1"
    COUNTRY_CODE = "country-code-v1"
    DATE = "date-v1"
    DATETIME = "datetime-v1"
    RATING_SYSTEM_VERSION = "rating-system-version-v1"


#: Normalisation behaviour for ambiguous / malformed input.
AMBIGUOUS_POLICIES: tuple[str, ...] = ("quarantine", "raise")


# ---------------------------------------------------------------------------
# Missing-value tokens
# ---------------------------------------------------------------------------

#: Tokens (case-insensitive, after whitespace cleanup) that are treated
#: as *explicitly missing* rather than as data.  Includes common sailing
#: scoring abbreviations that sources use in place of a numeric value.
MISSING_TOKENS: frozenset[str] = frozenset(
    {
        "",
        "-",
        "--",
        "—",  # em dash
        "–",  # en dash
        "n/a",
        "na",
        "n.a.",
        "none",
        "null",
        "nan",
        "unknown",
        "tbd",
        "tba",
        "?",
        "dnf",
        "dns",
        "dsq",
        "dnc",
        "ocs",
        "ret",
        "raf",
        "bfd",
        "scp",
        "zfp",
        "dne",
        "ns",
    }
)


def is_missing_token(raw: Any) -> bool:
    """Return True if *raw* is an explicit/implicit missing token."""
    if raw is None:
        return True
    if isinstance(raw, str):
        return raw.strip().lower() in MISSING_TOKENS
    return False


# ---------------------------------------------------------------------------
# NormalisedValueV1 — the handoff / output contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalisedValueV1:
    """The outcome of normalising one raw value.

    Immutable.  Always carries the untouched ``original`` and the
    ``rule`` / ``rules_version`` that produced it, so the result is
    transparent and replayable.
    """

    #: The canonical value (e.g. metres as ``float``, an aware-UTC
    #: ``datetime``, a cleaned name).  ``None`` when ``kind`` is
    #: ``MISSING`` or ``QUARANTINED``.
    value: Any
    #: The canonical unit of ``value`` (e.g. ``"m"``, ``"kg"``, ``"s"``),
    #: or ``None`` for unit-less kinds (names, dates, codes).
    unit: str | None
    #: The original representation, **never** modified.
    original: Any
    #: The outcome kind.
    kind: NormalisationKind
    #: The rule id that produced this result.
    rule: str
    #: The rule-set version (``RULES_VERSION`` by default).
    rules_version: str = RULES_VERSION
    #: Human-readable explanation, set for quarantines and informative
    #: for some normalisations (e.g. assumed units).
    reason: str | None = None

    # -- Convenience predicates ------------------------------------------------

    @property
    def ok(self) -> bool:
        """True when a canonical value was produced."""
        return self.kind is NormalisationKind.NORMALISED

    @property
    def is_missing(self) -> bool:
        """True when the input was a recognised missing token."""
        return self.kind is NormalisationKind.MISSING

    @property
    def is_quarantined(self) -> bool:
        """True when the input was ambiguous/malformed (no guess made)."""
        return self.kind is NormalisationKind.QUARANTINED

    # -- Serialisation -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict; datetimes become ISO-8601 strings."""
        return {
            "value": _json_safe(self.value),
            "unit": self.unit,
            "original": _json_safe(self.original),
            "kind": self.kind.value,
            "rule": self.rule,
            "rules_version": self.rules_version,
            "reason": self.reason,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"NormalisedValueV1(kind={self.kind.value}, value={self.value!r}, "
            f"unit={self.unit!r}, original={self.original!r}, rule={self.rule!r}, "
            f"reason={self.reason!r})"
        )


def _json_safe(v: Any) -> Any:
    """Convert a value to a JSON-safe representation (datetimes → ISO)."""
    import datetime as _dt

    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    return v


# ---------------------------------------------------------------------------
# Result constructors — the only way rules build results
# ---------------------------------------------------------------------------


def _check_policy(on_ambiguous: str) -> None:
    if on_ambiguous not in AMBIGUOUS_POLICIES:
        raise ValueError(
            f"on_ambiguous must be one of {AMBIGUOUS_POLICIES}, "
            f"got {on_ambiguous!r}"
        )


def missing(rule: Rule | str, original: Any, reason: str | None = None) -> NormalisedValueV1:
    """Build a MISSING result for an explicit/implicit missing token."""
    return NormalisedValueV1(
        value=None,
        unit=None,
        original=original,
        kind=NormalisationKind.MISSING,
        rule=str(rule.value if isinstance(rule, Rule) else rule),
        reason=reason or "missing value",
    )


def normalised(
    rule: Rule | str,
    value: Any,
    unit: str | None,
    original: Any,
    reason: str | None = None,
) -> NormalisedValueV1:
    """Build a NORMALISED result carrying original + rule version."""
    return NormalisedValueV1(
        value=value,
        unit=unit,
        original=original,
        kind=NormalisationKind.NORMALISED,
        rule=str(rule.value if isinstance(rule, Rule) else rule),
        reason=reason,
    )


def quarantined(
    rule: Rule | str,
    original: Any,
    reason: str,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Quarantine an ambiguous/malformed value (never guess).

    When ``on_ambiguous == "raise"``, raises
    :class:`AmbiguousNormalisationError` instead of returning a result.
    """
    _check_policy(on_ambiguous)
    rule_id = str(rule.value if isinstance(rule, Rule) else rule)
    if on_ambiguous == "raise":
        raise AmbiguousNormalisationError(f"[{rule_id}] {reason}: {original!r}")
    return NormalisedValueV1(
        value=None,
        unit=None,
        original=original,
        kind=NormalisationKind.QUARANTINED,
        rule=rule_id,
        reason=reason,
    )
