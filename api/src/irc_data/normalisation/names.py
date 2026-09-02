"""Unicode / name cleanup, sail numbers and country codes (DP-03-03).

Name cleanup
------------

:func:`clean_name` (via :func:`normalise_name`) applies a small,
transparent pipeline:

1. **Unicode NFKC** compatibility normalisation — full-width characters,
   ligatures (``ﬁ`` → ``fi``) and compatibility symbols fold to their
   canonical forms.
2. **Control / zero-width character removal** — ``Cf``, ``Cc``, ZWSP,
   BOM and friends are stripped (they corrupt matching and display).
3. **Whitespace collapse** — all runs of whitespace (including NBSP)
   become single spaces, ends trimmed.
4. **Punctuation spacing cleanup** — ``O'Brien`` keeps its apostrophe;
   ``Foo  ,  Bar`` becomes ``Foo, Bar``.
5. **Title casing** — only when the input is ALL-CAPS or all-lowercase
   (a strong signal the source had no real casing); mixed-case input is
   preserved.  Mc/Mac names get the customary internal capital
   (``MCDONALD`` → ``McDonald``).

No diacritic stripping and no fuzzy rewrites: "São Paulo" stays
"São Paulo".  The original string is always retained on the result.

Sail numbers
------------

:func:`normalise_sail_number` uppercases, strips, and splits an optional
national prefix: ``"gbr8310"`` → sail ``"GBR 8310"`` with country
``"GBR"``.  A national prefix is only recognised when it is a known
country code **and** is separated (``"GBR 8310"``) or adjacent to a
digit (``"GBR8310"``).  ``"BELLADONNA"`` is a sail number, not country
``"BEL"`` + ``"LADONNA"``.  Pure letters longer than 3 with no digits
are kept as the sail number with ``country=None``.

Country codes
-------------

:func:`normalise_country_code` normalises to the **ISO 3166-1 alpha-3**
code used across sailing data (World Sailing nation codes).  Accepts
alpha-3 (``"GBR"``), alpha-2 (``"GB"`` → ``"GBR"``) and a small set of
common full names / historical sailing codes (``"Great Britain"`` →
``"GBR"``, ``"Soviet Union"`` → ``"URS"``).  Unknown tokens are
quarantined, never guessed.
"""

from __future__ import annotations

import re
import unicodedata
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
# Unicode / name cleanup
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
#: Spaces before punctuation: "Foo , Bar" → "Foo, Bar".
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?)\]])")
#: Opening brackets followed by a space: "( Foo" → "(Foo".
_SPACE_AFTER_OPEN_RE = re.compile(r"([\(\[])\s+")

#: Whole-token Roman numerals (1–39 plus the common larger values) that
#: should keep upper casing when title-casing an ALL-CAPS source name
#: ("WILD OATS XI" → "Wild Oats XI", not "Xi").
_ROMAN_RE = re.compile(
    r"^(?=[MDCLXVI]+$)M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$",
    re.IGNORECASE,
)


def clean_name(text: str) -> str:
    """Deterministic Unicode + whitespace + punctuation cleanup.

    Pure function.  Does not strip diacritics or re-case anything.
    """
    s = unicodedata.normalize("NFKC", text)
    # Drop control / format characters (zero-width spaces, BOM, …).
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Cc", "Cf"))
    s = _WS_RE.sub(" ", s).strip()
    s = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", s)
    s = _SPACE_AFTER_OPEN_RE.sub(r"\1", s)
    return s


_MC_MAC_RE = re.compile(r"\b(Mc|Mac)([a-z])")


def _smart_title(s: str) -> str:
    """Title-case an all-caps / all-lower name, preserving particles.

    Handles the customary internal capital in Mc/Mac names
    (``MCDONALD`` → ``McDonald``) and keeps lowercase name particles
    (``van``, ``de``, ``der`` …) that should not be capitalised.
    """
    titled = s.title()
    # Restore Mc/Mac internal capitals: str.title() gives "Mcdonald".
    titled = _MC_MAC_RE.sub(lambda m: m.group(1) + m.group(2).upper(), titled)
    # Lowercase common particles when they appear mid-name.
    particles = {"van", "de", "der", "den", "di", "da", "del", "della", "le", "la", "of", "the", "von", "ten", "ter"}
    words = titled.split(" ")
    out = []
    for i, w in enumerate(words):
        if _ROMAN_RE.match(w):
            # Whole-token Roman numeral (XI, IV, …) — keep upper case.
            out.append(w.upper())
        elif i > 0 and w.lower() in particles:
            out.append(w.lower())
        else:
            out.append(w)
    return " ".join(out)


def _is_all_caps_or_lower(s: str) -> bool:
    """True when the name carries no meaningful casing (ALL CAPS or all lower).

    A mixed-case name (``"Wild Oats XI"``) is preserved exactly; only
    uniformly-cased input is considered for title-casing.
    """
    letters = [ch for ch in s if ch.isalpha()]
    if not letters:
        return False
    joined = "".join(letters)
    return joined == joined.upper() or joined == joined.lower()


def normalise_name(
    raw: Any,
    *,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Normalise a boat/skipper/owner name.

    Quarantines (never guesses) when the cleaned name would be empty
    (e.g. input was only punctuation/control characters).
    """
    if is_missing_token(raw):
        return missing(Rule.NAME, raw)
    if not isinstance(raw, str):
        return quarantined(
            Rule.NAME, raw, f"expected a string, got {type(raw).__name__}", on_ambiguous
        )
    cleaned = clean_name(raw)
    if not cleaned:
        return quarantined(
            Rule.NAME, raw, "name reduced to empty after cleanup", on_ambiguous
        )
    if _is_all_caps_or_lower(cleaned):
        titled = _smart_title(cleaned)
        return normalised(
            Rule.NAME, titled, None, raw,
            reason="title-cased (source had no meaningful casing)",
        )
    return normalised(Rule.NAME, cleaned, None, raw)


# ---------------------------------------------------------------------------
# Country codes (ISO 3166-1 alpha-3, as used by World Sailing)
# ---------------------------------------------------------------------------

#: alpha-3 → alpha-2 for the sailing nations we see in practice.
_A3_TO_A2: dict[str, str] = {
    "AUS": "AU", "AUT": "AT", "BEL": "BE", "BER": "BM", "BRA": "BR",
    "CAN": "CA", "CHI": "CL", "CHN": "CN", "CRO": "HR", "CYP": "CY",
    "CZE": "CZ", "DEN": "DK", "ESP": "ES", "EST": "EE", "FIN": "FI",
    "FRA": "FR", "GBR": "GB", "GER": "DE", "GRE": "GR", "HKG": "HK",
    "HUN": "HU", "IRL": "IE", "ISR": "IL", "ITA": "IT", "JPN": "JP",
    "KOR": "KR", "LAT": "LV", "LTU": "LT", "MAS": "MY", "MEX": "MX",
    "MON": "MC", "NED": "NL", "NOR": "NO", "NZL": "NZ", "PHI": "PH",
    "POL": "PL", "POR": "PT", "RSA": "ZA", "RUS": "RU", "SGP": "SG",
    "SLO": "SI", "SUI": "CH", "SVK": "SK", "SWE": "SE", "THA": "TH",
    "TUR": "TR", "UKR": "UA", "URS": "SU", "USA": "US",
}

_A2_TO_A3: dict[str, str] = {a2: a3 for a3, a2 in _A3_TO_A2.items()}

#: Common full names / historical codes seen in results and certs.
_COUNTRY_NAMES: dict[str, str] = {
    "australia": "AUS", "austria": "AUT", "belgium": "BEL",
    "bermuda": "BER", "brazil": "BRA", "canada": "CAN", "chile": "CHI",
    "china": "CHN", "croatia": "CRO", "cyprus": "CYP",
    "czech republic": "CZE", "czechia": "CZE", "denmark": "DEN",
    "spain": "ESP", "estonia": "EST", "finland": "FIN", "france": "FRA",
    "great britain": "GBR", "united kingdom": "GBR", "uk": "GBR",
    "england": "GBR", "germany": "GER", "greece": "GRE",
    "hong kong": "HKG", "hungary": "HUN", "ireland": "IRL",
    "israel": "ISR", "italy": "ITA", "japan": "JPN",
    "south korea": "KOR", "korea": "KOR", "latvia": "LAT",
    "lithuania": "LTU", "malaysia": "MAS", "mexico": "MEX",
    "monaco": "MON", "netherlands": "NED", "holland": "NED",
    "norway": "NOR", "new zealand": "NZL", "philippines": "PHI",
    "poland": "POL", "portugal": "POR", "south africa": "RSA",
    "russia": "RUS", "russian federation": "RUS", "singapore": "SGP",
    "slovenia": "SLO", "switzerland": "SUI", "slovakia": "SVK",
    "sweden": "SWE", "thailand": "THA", "turkey": "TUR",
    "türkiye": "TUR", "ukraine": "UKR", "soviet union": "URS",
    "united states": "USA", "united states of america": "USA",
    "usa": "USA", "us": "USA",
}


def normalise_country_code(
    raw: Any,
    *,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Normalise a country token to ISO 3166-1 alpha-3.

    Unknown tokens are quarantined (never guessed).
    """
    if is_missing_token(raw):
        return missing(Rule.COUNTRY_CODE, raw)
    if not isinstance(raw, str):
        return quarantined(
            Rule.COUNTRY_CODE, raw, f"expected a string, got {type(raw).__name__}", on_ambiguous
        )
    s = clean_name(raw)
    if not s:
        return quarantined(Rule.COUNTRY_CODE, raw, "empty country code", on_ambiguous)

    up = s.upper()
    if up in _A3_TO_A2:
        return normalised(Rule.COUNTRY_CODE, up, None, raw)
    if up in _A2_TO_A3:
        return normalised(
            Rule.COUNTRY_CODE, _A2_TO_A3[up], None, raw,
            reason=f"alpha-2 {up} mapped to alpha-3",
        )
    name_hit = _COUNTRY_NAMES.get(s.lower())
    if name_hit is not None:
        return normalised(
            Rule.COUNTRY_CODE, name_hit, None, raw,
            reason="country name mapped to alpha-3",
        )
    return quarantined(
        Rule.COUNTRY_CODE, raw,
        f"unrecognised country code/name {s!r}; not guessing",
        on_ambiguous,
    )


def country_code_or_none(raw: Any) -> str | None:
    """Best-effort helper: alpha-3 code or ``None`` (missing/unknown).

    Unlike :func:`normalise_country_code` this never quarantines — it is
    a building block for :func:`normalise_sail_number`, where the country
    component is optional and ambiguity is resolved by structure, not by
    guessing.
    """
    res = normalise_country_code(raw)
    return res.value if res.ok else None


# ---------------------------------------------------------------------------
# Sail numbers
# ---------------------------------------------------------------------------

#: Separated prefix: "GBR 8310", "gbr-8310", "USA123" (letters then digits).
_SAIL_SEPARATED_RE = re.compile(
    r"^\s*(?P<prefix>[A-Za-z]{2,3})[\s\-]*(?P<number>\d[\d\s]*)\s*$"
)
#: Letters-only sail identifier (e.g. classics) — kept as-is, no country.
_SAIL_LETTERS_ONLY_RE = re.compile(r"^\s*(?P<number>[A-Za-z][A-Za-z\s\-']*)\s*$")
#: Digits-only sail number.
_SAIL_DIGITS_ONLY_RE = re.compile(r"^\s*(?P<number>\d[\d\s]*)\s*$")


def normalise_sail_number(
    raw: Any,
    *,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Normalise a sail number, splitting an optional national prefix.

    The result's ``value`` is the canonical sail number string
    (``"GBR 8310"``, ``"8310"``, ``"KISMET"``).  For machine access to
    the split, use :func:`split_sail_number` — the recognised alpha-3
    prefix is always recoverable from the canonical value, so no extra
    field is needed on :class:`NormalisedValueV1`.
    """
    if is_missing_token(raw):
        return missing(Rule.SAIL_NUMBER, raw)
    if not isinstance(raw, str):
        return quarantined(
            Rule.SAIL_NUMBER, raw,
            f"expected a string, got {type(raw).__name__}", on_ambiguous,
        )
    s = clean_name(raw).upper()
    if not s:
        return quarantined(Rule.SAIL_NUMBER, raw, "empty sail number", on_ambiguous)

    m = _SAIL_SEPARATED_RE.match(s)
    if m:
        prefix = m.group("prefix")
        number = m.group("number").replace(" ", "")
        country = country_code_or_none(prefix)
        if country is not None:
            return normalised(
                Rule.SAIL_NUMBER,
                f"{country} {number}",
                None,
                raw,
                reason=f"national prefix recognised: {country}",
            )
        # 2–3 letters then digits but not a known country — the letters
        # are part of the sail identifier (e.g. a class code).  Keep them.
        return normalised(
            Rule.SAIL_NUMBER,
            f"{prefix}{number}",
            None,
            raw,
            reason=f"prefix {prefix!r} is not a known country; kept as sail number",
        )

    m = _SAIL_DIGITS_ONLY_RE.match(s)
    if m:
        return normalised(Rule.SAIL_NUMBER, m.group("number").replace(" ", ""), None, raw)

    m = _SAIL_LETTERS_ONLY_RE.match(s)
    if m:
        return normalised(
            Rule.SAIL_NUMBER, m.group("number"), None, raw,
            reason="letters-only sail identifier; no country inferred",
        )

    return quarantined(
        Rule.SAIL_NUMBER, raw,
        f"could not parse sail number {raw!r}; not guessing",
        on_ambiguous,
    )


def split_sail_number(raw: Any) -> tuple[str | None, str | None]:
    """Return ``(country_alpha3, sail_number)`` for *raw*.

    ``country_alpha3`` is ``None`` when no recognised national prefix is
    present; ``sail_number`` is ``None`` only when the input is missing
    or unparseable (quarantined).  Pure helper over
    :func:`normalise_sail_number`.
    """
    res = normalise_sail_number(raw)
    if not res.ok:
        return None, None
    value = res.value
    assert isinstance(value, str)
    if " " in value:
        prefix, number = value.split(" ", 1)
        if prefix in _A3_TO_A2:
            return prefix, number
    return None, value
