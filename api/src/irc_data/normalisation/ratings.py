"""Rating-system version normalisation (DP-03-03).

Rating authorities version their rules by **year** (IRC 2024, ORC 2024
VPP, ORCi 2023 …).  This module normalises the many spellings
(``"irc2024"``, ``"IRC 2024 Rule"``, ``"ORCi 2023"``) to a canonical
``"<SYSTEM> <YEAR>"`` form.

Ambiguity policy
----------------

* Unknown systems are **quarantined**, never guessed.
* A 2-digit year is disambiguated **only** when it falls inside the
  system's known year range (e.g. ``"IRC 24"`` → 2024); otherwise it is
  quarantined.
* A **yearless** system token (``"IRC"`` alone) is quarantined — a TCC
  of 1.025 means different things under IRC 2023 vs IRC 2024, and this
  library refuses to guess which.
"""

from __future__ import annotations

import re
from typing import Any

from irc_data.normalisation.normalise import (
    Rule,
    NormalisedValueV1,
    is_missing_token,
    missing,
    normalised,
    quarantined,
)
from irc_data.normalisation.names import clean_name


# ---------------------------------------------------------------------------
# Known rating systems and their year ranges
# ---------------------------------------------------------------------------

#: Canonical system name → (aliases, first year, last known year).
#: Ranges are inclusive.  Extend the upper bound as new rule years land.
_SYSTEMS: dict[str, tuple[tuple[str, ...], int, int]] = {
    "IRC": (("irc",), 1999, 2026),
    "ORC": (("orc", "orc international", "orci"), 1970, 2026),
    "ORC CLUB": (("orc club", "orcc"), 1970, 2026),
    "PHRF": (("phrf",), 1980, 2026),
    "ORR": (("orr",), 1994, 2026),
    "IMS": (("ims",), 1990, 2010),
    "IOR": (("ior",), 1970, 1994),
    "AMS": (("ams",), 1990, 2026),      # Australian Measurement System
    "CBH": (("cbh",), 2000, 2026),      # Channel Handicap (RORC)
}

#: alias (lowercase, cleaned) → canonical system name.
_ALIASES: dict[str, str] = {
    alias: canon for canon, (aliases, _lo, _hi) in _SYSTEMS.items() for alias in aliases
}

#: "SYSTEM [RULE ]YEAR[ RULE]" — a 4-digit or 2-digit year flanked by
#: optional filler words ("Rule", "VPP", "Rating") before and/or after.
_VERSION_RE = re.compile(
    r"^(?P<system>[A-Za-z][A-Za-z ]*?)\s*"
    r"(?:rule|vpp|rating|handicap|certificate)?\s*"
    r"(?P<year>\d{4}|\d{2})\s*"
    r"(?:rule|vpp|rating|handicap|certificate)?\s*$",
    re.IGNORECASE,
)

#: Yearless system token.
_SYSTEM_ONLY_RE = re.compile(r"^(?P<system>[A-Za-z][A-Za-z ]*?)\s*$")


def _lookup_system(token: str) -> str | None:
    """Resolve a system alias (case-insensitive) to its canonical name."""
    return _ALIASES.get(token.strip().lower())


def normalise_rating_system_version(
    raw: Any,
    *,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Normalise a rating-system version to ``"<SYSTEM> <YEAR>"``.

    Examples::

        "irc2024"          → "IRC 2024"
        "IRC 2024 Rule"    → "IRC 2024"
        "ORCi 2023"        → "ORC 2023"
        "IRC 24"           → "IRC 2024"   (in-range 2-digit year)
        "IRC 97"           → quarantined  (out of IRC range)
        "IRC"              → quarantined  (no year — not guessed)
        "XYZ 2024"         → quarantined  (unknown system)
    """
    if is_missing_token(raw):
        return missing(Rule.RATING_SYSTEM_VERSION, raw)
    if not isinstance(raw, str):
        return quarantined(
            Rule.RATING_SYSTEM_VERSION, raw,
            f"expected a string, got {type(raw).__name__}", on_ambiguous,
        )
    s = clean_name(raw)
    if not s:
        return quarantined(
            Rule.RATING_SYSTEM_VERSION, raw, "empty rating system version", on_ambiguous
        )

    m = _VERSION_RE.match(s)
    if m:
        system = _lookup_system(m.group("system"))
        if system is None:
            return quarantined(
                Rule.RATING_SYSTEM_VERSION, raw,
                f"unknown rating system {m.group('system')!r}; not guessing",
                on_ambiguous,
            )
        _aliases, lo, hi = _SYSTEMS[system]
        year_text = m.group("year")
        if len(year_text) == 2:
            yy = int(year_text)
            # Try the two plausible centuries; accept only when exactly
            # one candidate is in range.
            candidates = [y for y in (1900 + yy, 2000 + yy) if lo <= y <= hi]
            if len(candidates) != 1:
                return quarantined(
                    Rule.RATING_SYSTEM_VERSION, raw,
                    f"2-digit year {year_text!r} ambiguous/out-of-range for "
                    f"{system} ({lo}–{hi}); not guessing",
                    on_ambiguous,
                )
            year = candidates[0]
            reason = f"2-digit year {year_text!r} resolved to {year}"
        else:
            year = int(year_text)
            if not (lo <= year <= hi):
                return quarantined(
                    Rule.RATING_SYSTEM_VERSION, raw,
                    f"year {year} outside {system} range ({lo}–{hi}); not guessing",
                    on_ambiguous,
                )
            reason = None
        return normalised(
            Rule.RATING_SYSTEM_VERSION, f"{system} {year}", None, raw, reason=reason
        )

    # Yearless: refuse to guess.
    m = _SYSTEM_ONLY_RE.match(s)
    if m and _lookup_system(m.group("system")) is not None:
        return quarantined(
            Rule.RATING_SYSTEM_VERSION, raw,
            f"rating system {_lookup_system(m.group('system'))!r} given without a "
            "year; a rating's meaning depends on the rule year, so not guessing",
            on_ambiguous,
        )
    return quarantined(
        Rule.RATING_SYSTEM_VERSION, raw,
        f"unrecognised rating system version {raw!r}; not guessing",
        on_ambiguous,
    )
