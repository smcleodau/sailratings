"""Unit conversion rules (DP-03-03).

Deterministic, quarantine-on-ambiguity conversions for the quantities
that appear in sailing data:

* **Lengths** (LOA, beam, draft, freeboard …) → metres.
* **Weights** (displacement, ballast …) → kilograms.
* **Elapsed times** (race finish/elapsed columns) → seconds.
* **Ratings** (IRC TCC, ORC GPH …) → decimal.
* **Dimensionless decimals** (crew numbers, age allowances …) → decimal.

Ambiguity policy
----------------

A bare number is accepted **only** when the caller declares the
:attr:`UnitAssumption` (e.g. ``UnitAssumption.METRES`` for an
IRC-certificate LOA column).  Without a declared assumption, a bare
number is **quarantined** — the library never silently picks metres over
feet.  A non-finite number (``NaN``, ``inf``) is always quarantined.

Locale formats
--------------

``parse_decimal`` accepts both dot and comma decimal separators
(``"10.5"``, ``"10,5"``) but **refuses to guess** when grouping could be
involved: a comma followed by exactly three digits (``"1,234"``) is
ambiguous between a thousands group and a decimal comma and is
quarantined.  Space-separated groups (``"12 500"``, ``"12\u00a0500"``)
are treated as thousands separators (spaces are never decimal marks).

Elapsed times
-------------

``parse_elapsed_seconds`` accepts ``H:MM:SS[.fff]``, ``MM:SS[.fff]``,
``Hh MMm SSs`` spellings and plain numeric seconds.  Bare ``SS`` alone
is **not** guessed as a time — ``"45"`` is just a number; route it
through :func:`normalise_dimensionless` or declare ``unit="s"``.
"""

from __future__ import annotations

import enum
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from irc_data.normalisation.normalise import (
    Rule,
    NormalisedValueV1,
    is_missing_token,
    missing,
    normalised,
    quarantined,
)


# ---------------------------------------------------------------------------
# Canonical units and assumptions
# ---------------------------------------------------------------------------


class UnitAssumption(str, enum.Enum):
    """Caller-declared unit for a bare number.

    This is the *explicit* mechanism for disambiguation: a scraper that
    knows a column is "metres" declares it; the library records the
    assumption in the result's ``reason`` for transparency.
    """

    METRES = "m"
    FEET = "ft"
    KILOGRAMS = "kg"
    POUNDS = "lb"
    SECONDS = "s"


# ---------------------------------------------------------------------------
# Decimal parsing (locale-aware, no guessing)
# ---------------------------------------------------------------------------

#: Comma + exactly three digits (one or more groups) is ambiguous between
#: a thousands group and a decimal comma → quarantined (never guessed).
_GROUP_COMMA_RE = re.compile(r"^\d{1,3}(,\d{3})+$")

#: A *repeated* dot group ("1.234.567") is unambiguous European grouping —
#: normalised to the integer.  A single dot is **not** ambiguous (it is the
#: standard decimal mark in sailing data, e.g. TCC "1.025"), so a lone
#: ``d.ddd`` is treated as a decimal, not a thousands group.
_GROUP_DOT_MULTI_RE = re.compile(r"^\d{1,3}(\.\d{3}){2,}$")


def parse_decimal(text: Any, on_ambiguous: str = "quarantine") -> Decimal | None:
    """Parse a decimal from *text* without ever guessing.

    Returns a :class:`~decimal.Decimal`, or ``None`` when the value is
    missing.  Raises :class:`AmbiguousNormalisationError` (or returns a
    sentinel via the caller) — this helper raises on malformed/ambiguous
    input; the ``normalise_*`` wrappers translate that into quarantine.

    Accepted:
      * ``int`` / ``float`` / ``Decimal`` (finite only)
      * ``"10.5"``, ``"10,5"``, ``"1.025"`` — dot or comma decimal mark
      * ``"12 500"`` / ``"12 500"`` — space / NBSP thousand groups
      * leading ``+`` / ``-``

    Quarantined (raises):
      * ``"1,234"`` — comma + 3 digits is ambiguous between a thousands
        group and a decimal comma
      * ``"1.234.567,89"`` — mixed grouping (not supported, do not guess)
      * ``"abc"``, ``"1.2.3"`` (non-grouped), non-finite floats
    """
    if text is None:
        return None
    if isinstance(text, Decimal):
        if not text.is_finite():
            raise ValueError(f"non-finite decimal: {text!r}")
        return text
    if isinstance(text, bool):  # bool is an int subclass; refuse silently
        raise ValueError(f"not a number: {text!r}")
    if isinstance(text, int):
        return Decimal(text)
    if isinstance(text, float):
        import math

        if not math.isfinite(text):
            raise ValueError(f"non-finite float: {text!r}")
        return Decimal(str(text))

    if not isinstance(text, str):
        raise ValueError(f"unsupported numeric type: {type(text).__name__}")

    s = text.strip()
    if is_missing_token(s):
        return None

    # Normalise unicode minus and spaces (incl. NBSP) before parsing.
    s = s.replace("−", "-").replace(" ", " ").replace(" ", " ")

    # Space-separated groups are thousands separators (spaces are never
    # a decimal mark).
    if " " in s:
        parts = s.split(" ")
        if not all(p.lstrip("+-").isdigit() for p in parts):
            raise ValueError(f"malformed spaced number: {text!r}")
        s = "".join(parts)

    if _GROUP_COMMA_RE.match(s):
        raise ValueError(
            f"ambiguous grouped number {text!r}: comma + 3 digits could be "
            "a thousands group or a decimal comma; refusing to guess"
        )

    # Repeated dot groups ("1.234.567") are unambiguous European
    # thousands grouping → normalise to the integer.  A single dot is the
    # standard decimal mark (e.g. TCC "1.025") and is left alone.
    if _GROUP_DOT_MULTI_RE.match(s):
        s = s.replace(".", "")
    elif s.count(".") > 1:
        raise ValueError(f"malformed number: {text!r}")

    # Comma decimal mark: exactly one comma, not a grouping pattern.
    if "," in s:
        if s.count(",") > 1:
            raise ValueError(f"ambiguous multi-comma number: {text!r}")
        s = s.replace(",", ".")

    try:
        d = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"malformed number: {text!r}") from exc
    if not d.is_finite():
        raise ValueError(f"non-finite number: {text!r}")
    return d


def _to_float(d: Decimal) -> float:
    return float(d)


# ---------------------------------------------------------------------------
# Lengths
# ---------------------------------------------------------------------------

#: Unit suffix → (canonical metres factor, label).  Keys are lowercase.
_LENGTH_UNITS: dict[str, tuple[Decimal, str]] = {
    "m": (Decimal("1"), "m"),
    "meter": (Decimal("1"), "m"),
    "meters": (Decimal("1"), "m"),
    "metre": (Decimal("1"), "m"),
    "metres": (Decimal("1"), "m"),
    "ft": (Decimal("0.3048"), "ft"),
    "foot": (Decimal("0.3048"), "ft"),
    "feet": (Decimal("0.3048"), "ft"),
    "'": (Decimal("0.3048"), "ft"),
    "′": (Decimal("0.3048"), "ft"),  # prime
}

_LENGTH_RE = re.compile(
    r"^\s*(?P<num>[+-]?\d[\d\s.,]*)\s*(?P<unit>m|meters?|metres?|ft|foot|feet|'|′)\s*$",
    re.IGNORECASE,
)

#: feet'inches" pattern, e.g. 42'6" or 42' 6"
_FT_IN_RE = re.compile(
    r"^\s*(?P<ft>\d+)\s*['′ft]\s*(?P<in>\d+(?:\.\d+)?)\s*(?:[\"″]|in)?\s*$",
    re.IGNORECASE,
)


def normalise_length(
    raw: Any,
    *,
    assume: UnitAssumption | None = None,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Normalise a length to metres.

    ``"10.5 m"`` → ``10.5``; ``"34.45 ft"`` → ``10.50096``; ``"42'6\""``
    → metres.  A bare number is accepted only with ``assume=``; otherwise
    it is quarantined rather than guessed.
    """
    if is_missing_token(raw):
        return missing(Rule.LENGTH, raw)

    # Explicit feet'inches" form first (contains no ambiguity).
    if isinstance(raw, str):
        m = _FT_IN_RE.match(raw)
        if m:
            ft = Decimal(m.group("ft"))
            inches = Decimal(m.group("in"))
            metres = (ft * 12 + inches) * Decimal("0.0254")
            return normalised(
                Rule.LENGTH,
                _to_float(metres),
                "m",
                raw,
                reason=f"converted from feet/inches ({ft} ft {inches} in)",
            )

    num_text: Any = raw
    unit_label: str | None = None
    if isinstance(raw, str):
        m = _LENGTH_RE.match(raw)
        if m:
            num_text = m.group("num")
            unit_label = _LENGTH_UNITS[m.group("unit").lower()][1]

    try:
        value = parse_decimal(num_text)
    except ValueError as exc:
        return quarantined(Rule.LENGTH, raw, str(exc), on_ambiguous)
    if value is None:
        return missing(Rule.LENGTH, raw)

    if unit_label is not None:
        factor = next(f for f, lbl in _LENGTH_UNITS.values() if lbl == unit_label)
        metres = value * factor
        return normalised(
            Rule.LENGTH,
            _to_float(metres),
            "m",
            raw,
            reason=f"converted from {unit_label}",
        )

    # Bare number: only with an explicit assumption.
    if assume is None:
        return quarantined(
            Rule.LENGTH,
            raw,
            "bare number without a unit: could be metres or feet; "
            "declare assume= to disambiguate",
            on_ambiguous,
        )
    if assume not in (UnitAssumption.METRES, UnitAssumption.FEET):
        return quarantined(
            Rule.LENGTH,
            raw,
            f"unsupported length assumption: {assume!r}",
            on_ambiguous,
        )
    factor = Decimal("1") if assume is UnitAssumption.METRES else Decimal("0.3048")
    metres = value * factor
    return normalised(
        Rule.LENGTH,
        _to_float(metres),
        "m",
        raw,
        reason=f"unit assumed: {assume.value}",
    )


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

_WEIGHT_UNITS: dict[str, tuple[Decimal, str]] = {
    "kg": (Decimal("1"), "kg"),
    "kgs": (Decimal("1"), "kg"),
    "kilogram": (Decimal("1"), "kg"),
    "kilograms": (Decimal("1"), "kg"),
    "lb": (Decimal("0.45359237"), "lb"),
    "lbs": (Decimal("0.45359237"), "lb"),
    "pound": (Decimal("0.45359237"), "lb"),
    "pounds": (Decimal("0.45359237"), "lb"),
}

_WEIGHT_RE = re.compile(
    r"^\s*(?P<num>[+-]?\d[\d\s.,]*)\s*(?P<unit>kgs?|kilograms?|lbs?|pounds?)\s*$",
    re.IGNORECASE,
)


def normalise_weight(
    raw: Any,
    *,
    assume: UnitAssumption | None = None,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Normalise a weight/mass to kilograms (quarantine on ambiguity)."""
    if is_missing_token(raw):
        return missing(Rule.WEIGHT, raw)

    num_text: Any = raw
    unit_label: str | None = None
    if isinstance(raw, str):
        m = _WEIGHT_RE.match(raw)
        if m:
            num_text = m.group("num")
            unit_label = _WEIGHT_UNITS[m.group("unit").lower()][1]

    try:
        value = parse_decimal(num_text)
    except ValueError as exc:
        return quarantined(Rule.WEIGHT, raw, str(exc), on_ambiguous)
    if value is None:
        return missing(Rule.WEIGHT, raw)

    if unit_label is not None:
        factor = next(f for f, lbl in _WEIGHT_UNITS.values() if lbl == unit_label)
        kg = value * factor
        return normalised(Rule.WEIGHT, _to_float(kg), "kg", raw, reason=f"converted from {unit_label}")

    if assume is None:
        return quarantined(
            Rule.WEIGHT,
            raw,
            "bare number without a unit: could be kilograms or pounds; "
            "declare assume= to disambiguate",
            on_ambiguous,
        )
    if assume not in (UnitAssumption.KILOGRAMS, UnitAssumption.POUNDS):
        return quarantined(Rule.WEIGHT, raw, f"unsupported weight assumption: {assume!r}", on_ambiguous)
    factor = Decimal("1") if assume is UnitAssumption.KILOGRAMS else Decimal("0.45359237")
    kg = value * factor
    return normalised(Rule.WEIGHT, _to_float(kg), "kg", raw, reason=f"unit assumed: {assume.value}")


# ---------------------------------------------------------------------------
# Elapsed times
# ---------------------------------------------------------------------------

_HMS_RE = re.compile(r"^\s*(?P<h>\d{1,3}):(?P<m>[0-5]?\d):(?P<s>[0-5]?\d(?:\.\d+)?)\s*$")
_MS_RE = re.compile(r"^\s*(?P<m>[0-5]?\d):(?P<s>[0-5]?\d(?:\.\d+)?)\s*$")
_WORD_RE = re.compile(
    r"^\s*(?:(?P<h>\d+)\s*h(?:ours?)?)?\s*(?:(?P<m>\d+)\s*m(?:ins?)?)?\s*(?:(?P<s>\d+(?:\.\d+)?)\s*s(?:ecs?)?)?\s*$",
    re.IGNORECASE,
)


def parse_elapsed_seconds(raw: Any) -> Decimal | None:
    """Parse an elapsed time to seconds.

    Accepts ``H:MM:SS[.fff]``, ``MM:SS[.fff]``, ``2h 05m 33s``, and
    plain numeric seconds (``5400.5``).  Returns ``None`` for missing
    tokens.  Raises ``ValueError`` on malformed input.  A bare integer
    string like ``"45"`` is treated as seconds (``MM:SS`` requires a
    colon); use :func:`normalise_dimensionless` when ``"45"`` is not a
    duration.
    """
    if is_missing_token(raw):
        return None
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        return parse_decimal(raw)
    if not isinstance(raw, str):
        raise ValueError(f"unsupported time type: {type(raw).__name__}")
    s = raw.strip()

    m = _HMS_RE.match(s)
    if m:
        return (
            Decimal(m.group("h")) * 3600
            + Decimal(m.group("m")) * 60
            + Decimal(m.group("s"))
        )
    m = _MS_RE.match(s)
    if m:
        return Decimal(m.group("m")) * 60 + Decimal(m.group("s"))
    m = _WORD_RE.match(s)
    if m and any(m.group(g) for g in ("h", "m", "s")):
        return (
            Decimal(m.group("h") or 0) * 3600
            + Decimal(m.group("m") or 0) * 60
            + Decimal(m.group("s") or 0)
        )
    # Plain seconds (e.g. "5400.5").
    return parse_decimal(s)


def normalise_elapsed_time(
    raw: Any,
    *,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Normalise an elapsed race time to seconds."""
    if is_missing_token(raw):
        return missing(Rule.TIME, raw)
    try:
        seconds = parse_elapsed_seconds(raw)
    except ValueError as exc:
        return quarantined(Rule.TIME, raw, str(exc), on_ambiguous)
    if seconds is None:
        return missing(Rule.TIME, raw)
    if seconds < 0:
        return quarantined(Rule.TIME, raw, "negative elapsed time", on_ambiguous)
    return normalised(Rule.TIME, _to_float(seconds), "s", raw)


# ---------------------------------------------------------------------------
# Ratings and dimensionless decimals
# ---------------------------------------------------------------------------

_TCC_MIN = Decimal("0.700")
_TCC_MAX = Decimal("2.000")


def normalise_rating(
    raw: Any,
    *,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Normalise a rating value (IRC TCC, ORC rating …) to a decimal.

    Ratings are rounded to three decimal places (``ROUND_HALF_UP``) —
    the precision published by rating authorities.  Values outside the
    plausible TCC band ``[0.700, 2.000]`` are quarantined, not clamped:
    a "rating" of ``45`` is far more likely to be a misplaced sail
    number than a real TCC.
    """
    if is_missing_token(raw):
        return missing(Rule.RATING, raw)
    try:
        d = parse_decimal(raw)
    except ValueError as exc:
        return quarantined(Rule.RATING, raw, str(exc), on_ambiguous)
    if d is None:
        return missing(Rule.RATING, raw)
    if not (_TCC_MIN <= d <= _TCC_MAX):
        return quarantined(
            Rule.RATING,
            raw,
            f"rating {d} outside plausible band [{_TCC_MIN}, {_TCC_MAX}]",
            on_ambiguous,
        )
    q = d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return normalised(Rule.RATING, _to_float(q), None, raw)


def normalise_dimensionless(
    raw: Any,
    *,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Normalise a plain decimal (crew number, allowance …)."""
    if is_missing_token(raw):
        return missing(Rule.DIMENSIONLESS, raw)
    try:
        d = parse_decimal(raw)
    except ValueError as exc:
        return quarantined(Rule.DIMENSIONLESS, raw, str(exc), on_ambiguous)
    if d is None:
        return missing(Rule.DIMENSIONLESS, raw)
    return normalised(Rule.DIMENSIONLESS, _to_float(d), None, raw)
