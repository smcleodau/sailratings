"""Golden fixtures — known sailing examples (DP-03-03).

Each case pins the *exact* expected normalised value for a real-world
sailing input, so a rule change that alters a known conversion is
immediately visible.  Values were chosen from familiar boats, races and
rating conventions (Rolex Sydney Hobart, IRC certificates, World Sailing
nation codes).

Every case asserts not just the value but that the original
representation and rule version survive — the transparency contract.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

# ---------------------------------------------------------------------------
# Names (Unicode / casing cleanup)
# ---------------------------------------------------------------------------

# (raw, expected_value)
NAME_GOLDENS: list[tuple[str, str]] = [
    # NBSP + zero-width space from a scrape, ALL-CAPS source.
    ("  WILD\u00a0OATS\u200b XI ", "Wild Oats XI"),
    # Ligature + full-width letters (NFKC).
    ("BLACK\u00a0JACK", "Black Jack"),
    ("COMANCHE", "Comanche"),
    # Mixed case is preserved exactly.
    ("Wild Oats XI", "Wild Oats XI"),
    ("Ichi Ban", "Ichi Ban"),
    # Diacritics are kept, whitespace collapsed.
    ("  São\u00a0\u00a0Paulo  Racing ", "São Paulo Racing"),
    # Mc/Mac customary casing.
    ("MCDONALD", "McDonald"),
    ("MACINTOSH", "MacIntosh"),
    # Name particles stay lowercase mid-name.
    ("VAN DEN BERG", "Van den Berg"),
    # Apostrophes preserved.
    ("O'BRIEN", "O'Brien"),
    # Ligature folds under NFKC (all-lower source → title-cased).
    ("\uFB01nish \uFB01re", "Finish Fire"),
]

# (raw,) — inputs that must quarantine (never guessed).
NAME_QUARANTINE_GOLDENS: list[str] = [
    "\u200b\u200b",   # only zero-width spaces → empty after cleanup
]

# ---------------------------------------------------------------------------
# Sail numbers
# ---------------------------------------------------------------------------

# (raw, expected_value, expected_country_or_None)
SAIL_NUMBER_GOLDENS: list[tuple[str, str, str | None]] = [
    ("gbr8310", "GBR 8310", "GBR"),
    ("GBR 8310", "GBR 8310", "GBR"),
    ("USA 123", "USA 123", "USA"),
    ("aus-5295", "AUS 5295", "AUS"),
    ("8310", "8310", None),
    # Letters-only identifier: no country inferred even though it starts
    # with a plausible-looking prefix ("BEL").
    ("BELLADONNA", "BELLADONNA", None),
    ("KISMET", "KISMET", None),
    # Unknown 2–3 letter prefix stays part of the sail number.
    ("XYZ 42", "XYZ42", None),
]

# ---------------------------------------------------------------------------
# Country codes
# ---------------------------------------------------------------------------

# (raw, expected_alpha3)
COUNTRY_GOLDENS: list[tuple[str, str]] = [
    ("GBR", "GBR"),
    ("gb", "GBR"),
    ("uk", "GBR"),
    ("Great Britain", "GBR"),
    ("Australia", "AUS"),
    ("au", "AUS"),
    ("SUI", "SUI"),
    ("ch", "SUI"),
    ("Soviet Union", "URS"),
    ("usa", "USA"),
    ("United States", "USA"),
]

COUNTRY_QUARANTINE_GOLDENS: list[str] = ["ATLANTIS", "XX", "123"]

# ---------------------------------------------------------------------------
# Units — lengths, weights, elapsed times, ratings
# ---------------------------------------------------------------------------

# (raw, expected_metres)
LENGTH_GOLDENS: list[tuple[str, float]] = [
    ("10.5 m", 10.5),
    ("34.45 ft", 34.45 * 0.3048),
    ("30.48m", 30.48),
    ("10,5 m", 10.5),           # locale comma decimal
    ("42'6\"", (42 * 12 + 6) * 0.0254),
]

# (raw, expected_kg)
WEIGHT_GOLDENS: list[tuple[str, float]] = [
    ("1500 kg", 1500.0),
    ("3307 lb", 3307 * 0.45359237),
    ("12 500 kg", 12500.0),
]

# (raw, expected_seconds)
TIME_GOLDENS: list[tuple[str, float]] = [
    ("1:30:45.5", 5445.5),
    ("05:33", 333.0),
    ("2h 05m 33s", 7533.0),
    ("5400.5", 5400.5),
    # Comanche's 2017 Sydney Hobart line-honours elapsed time 1d 09h 15m
    # 42s is often printed per-day; the 24h slice as H:MM:SS is pinned:
    ("9:15:42", 33342.0),
]

# (raw, expected_rating) — IRC TCC style, 3 dp, plausible band.
RATING_GOLDENS: list[tuple[str, float]] = [
    ("1.0254", 1.025),
    ("1.025", 1.025),
    ("0.987", 0.987),
    ("0.700", 0.700),   # lower band boundary (inclusive)
    ("2.000", 2.000),   # upper band boundary (inclusive)
]

RATING_QUARANTINE_GOLDENS: list[str] = [
    "45",       # outside plausible TCC band — not clamped
    "1,305",    # comma + 3 digits is ambiguous (thousands vs decimal)
]

# (raw,) — unit values that must quarantine.
UNIT_QUARANTINE_GOLDENS: list[str] = [
    "10.5",      # bare number, no declared assumption → m or ft?
    "1,234",     # thousands group vs decimal comma
    "1.2.3",     # malformed
    "not a number",
]

# ---------------------------------------------------------------------------
# Dates & timezones (race level granularity)
# ---------------------------------------------------------------------------

# (raw, expected_date)
DATE_GOLDENS: list[tuple[str, date]] = [
    ("2024-07-21", date(2024, 7, 21)),
    ("21 July 2024", date(2024, 7, 21)),
    ("21st July 2024", date(2024, 7, 21)),
    ("21.07.2024", date(2024, 7, 21)),   # dotted → day-first by convention
    ("26 Dec 2024", date(2024, 12, 26)),  # Sydney Hobart start
]

DATE_QUARANTINE_GOLDENS: list[str] = [
    "03/04/2024",   # day-first vs month-first — ambiguous
    "1/2/24",       # additionally ambiguous century
    "2024-13-45",   # impossible
    "sometime in July",
]

# (raw, local_timezone, expected_utc_datetime)
DATETIME_GOLDENS: list[tuple[str, str | None, datetime]] = [
    # Sydney summer (AEDT, +11): 10:30 local → 23:30 UTC previous day.
    (
        "2024-01-21 10:30",
        "Australia/Sydney",
        datetime(2024, 1, 20, 23, 30, tzinfo=timezone.utc),
    ),
    # Sydney winter (AEST, +10): same wall time, different offset — the
    # IANA zone, not a hard-coded offset, owns DST.
    (
        "2024-07-21 10:30",
        "Australia/Sydney",
        datetime(2024, 7, 21, 0, 30, tzinfo=timezone.utc),
    ),
    # Explicit Z.
    (
        "2024-07-21T10:30:00Z",
        None,
        datetime(2024, 7, 21, 10, 30, tzinfo=timezone.utc),
    ),
    # Explicit numeric offset.
    (
        "2024-07-21 10:30 +10:00",
        None,
        datetime(2024, 7, 21, 0, 30, tzinfo=timezone.utc),
    ),
    # Known named abbreviation printed by the source.
    (
        "2024-07-21 10:30 AEST",
        None,
        datetime(2024, 7, 21, 0, 30, tzinfo=timezone.utc),
    ),
]

DATETIME_QUARANTINE_GOLDENS: list[tuple[str, str | None]] = [
    # Naive, time-bearing, no local_timezone → never guess a zone.
    ("2024-07-21 10:30", None),
    # Unknown named abbreviation.
    ("2024-07-21 10:30 XYZ", None),
    # Unknown IANA zone.
    ("2024-07-21 10:30", "Antarctica/Nowhere"),
]

# (race_date, local_time, iana_zone, expected_utc) — race_start_instant.
RACE_START_GOLDENS: list[tuple[date, time, str, datetime]] = [
    # Rolex Sydney Hobart start: 26 Dec, 13:00 local (AEDT = +11).
    (
        date(2024, 12, 26),
        time(13, 0),
        "Australia/Sydney",
        datetime(2024, 12, 26, 2, 0, tzinfo=timezone.utc),
    ),
    # A winter Wednesday race at 10:30 in Sydney (AEST = +10).
    (
        date(2024, 7, 21),
        time(10, 30),
        "Australia/Sydney",
        datetime(2024, 7, 21, 0, 30, tzinfo=timezone.utc),
    ),
    # Cowes Week start 10:30 BST (+1).
    (
        date(2024, 8, 3),
        time(10, 30),
        "Europe/London",
        datetime(2024, 8, 3, 9, 30, tzinfo=timezone.utc),
    ),
]

# ---------------------------------------------------------------------------
# Rating-system versions
# ---------------------------------------------------------------------------

# (raw, expected_value)
RATING_SYSTEM_GOLDENS: list[tuple[str, str]] = [
    ("irc2024", "IRC 2024"),
    ("IRC 2024 Rule", "IRC 2024"),
    ("ORCi 2023", "ORC 2023"),
    ("ORC International 2022", "ORC 2022"),
    ("IRC 24", "IRC 2024"),          # in-range 2-digit year
    ("phrf 2020", "PHRF 2020"),
]

RATING_SYSTEM_QUARANTINE_GOLDENS: list[str] = [
    "IRC",          # no year — not guessed
    "IRC 97",       # 2-digit year out of IRC range
    "XYZ 2024",     # unknown system
    "IRC 1899",     # 4-digit year out of range
]

# ---------------------------------------------------------------------------
# Missing-value semantics
# ---------------------------------------------------------------------------

MISSING_GOLDENS: list = [
    None, "", "   ", "-", "--", "N/A", "n/a", "DNF", "DNS", "DSQ", "TBD",
]
