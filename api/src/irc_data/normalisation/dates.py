"""Date and timezone normalisation (DP-03-03).

Absorbs the old-tracker *"Race Level Granularity and Timezones"* epic:
race data arrives at **mixed granularity** (a date, a date + local time,
a full timestamp) and in **local civil time**, and the platform stores
everything as aware UTC.

Granularity is explicit
-----------------------

Every result carries a :class:`Granularity`:

* ``DATE`` — only the calendar day is known (``"2024-07-21"``).
  ``value`` is a :class:`datetime.date`; no timezone is invented.
* ``DATETIME`` — a full instant is known.  ``value`` is an aware UTC
  :class:`~datetime.datetime`.

A bare date is **never** silently promoted to midnight — the caller
decides.  :func:`race_start_instant` exists for the common "date +
scheduled local time + venue timezone" pattern and returns a real
instant.

Timezone handling
-----------------

``local_timezone`` must be an **IANA zone name** (``"Australia/Sydney"``,
``"Europe/Cowes"``).  Because IANA zoneinfo models DST, the same rule
code handles both AEDT and AEST correctly — the tz database, not this
library, owns the offsets.  Non-IANA input is quarantined.  When no
``local_timezone`` is given for a time-bearing value, the input must
carry its own offset (``Z`` / ``+10:00`` / ``AEST`` …); otherwise it is
quarantined rather than guessed.

Ambiguity policy
----------------

* ``"03/04/2024"`` (numeric slash date) is ambiguous day-first vs
  month-first → **quarantined** unless the caller declares
  ``day_first=True/False``.
* ``"1/2/24"`` — additionally ambiguous year → quarantined even with
  ``day_first`` (we refuse to guess the century).
* Unparseable / impossible dates (``"2024-13-45"``) → quarantined.
* Unknown named timezone abbreviations → quarantined; a small known
  table (``AEST``/``AEDT``/``BST``/``CEST``/``UTC`` …) is accepted.
"""

from __future__ import annotations

import enum
import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from irc_data.normalisation.normalise import (
    Rule,
    NormalisedValueV1,
    is_missing_token,
    missing,
    normalised,
    quarantined,
)


# ---------------------------------------------------------------------------
# Granularity
# ---------------------------------------------------------------------------


class Granularity(str, enum.Enum):
    """The precision a race/event timestamp actually carries."""

    DATE = "date"
    DATETIME = "datetime"


# ---------------------------------------------------------------------------
# Timezones
# ---------------------------------------------------------------------------

#: Small, explicit table of *named* timezone abbreviations we accept.
#: These are fixed offsets (no DST inference) — used only when the
#: source itself prints the abbreviation next to the time.
NAMED_TIMEZONES: dict[str, timezone] = {
    "UTC": timezone.utc,
    "GMT": timezone.utc,
    "Z": timezone.utc,
    "AEST": timezone(timedelta(hours=10)),   # Australian Eastern Standard
    "AEDT": timezone(timedelta(hours=11)),   # Australian Eastern Daylight
    "AWST": timezone(timedelta(hours=8)),    # Australian Western Standard
    "BST": timezone(timedelta(hours=1)),     # British Summer Time
    "CEST": timezone(timedelta(hours=2)),    # Central European Summer
    "CET": timezone(timedelta(hours=1)),     # Central European
    "EDT": timezone(timedelta(hours=-4)),    # US Eastern Daylight
    "EST": timezone(timedelta(hours=-5)),    # US Eastern Standard
    "PDT": timezone(timedelta(hours=-7)),    # US Pacific Daylight
    "PST": timezone(timedelta(hours=-8)),    # US Pacific Standard
    "NZST": timezone(timedelta(hours=12)),   # New Zealand Standard
    "NZDT": timezone(timedelta(hours=13)),   # New Zealand Daylight
}


def load_timezone(name: str) -> tzinfo | None:
    """Load an IANA zone by name; ``None`` when unknown (never guessed)."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"^\s*(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\s*$")
_DOT_DATE_RE = re.compile(r"^\s*(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})\s*$")
_SLASH_DATE_RE = re.compile(r"^\s*(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y>\d{4})\s*$")
_SLASH_SHORT_YEAR_RE = re.compile(r"^\s*(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y>\d{2})\s*$")
_TEXT_DATE_RE = re.compile(
    r"^\s*(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?P<mon>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)[\s,]+(?P<y>\d{4})\s*$",
    re.IGNORECASE,
)

_MONTHS: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _safe_date(y: int, m: int, d: int) -> date:
    """Build a date or raise ValueError (impossible day/month)."""
    return date(y, m, d)


def normalise_date(
    raw: Any,
    *,
    day_first: bool | None = None,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Normalise a date to a :class:`datetime.date` (granularity DATE).

    * ISO (``2024-07-21``), dotted (``21.07.2024``, unambiguously
      day-first) and textual (``21 July 2024``) formats are accepted.
    * Numeric slash dates (``03/04/2024``) are **quarantined** unless
      ``day_first`` is declared.
    * Two-digit years are always quarantined (century not guessed).
    """
    if is_missing_token(raw):
        return missing(Rule.DATE, raw)
    if isinstance(raw, datetime):
        # A datetime carries more granularity than a date; keep the date
        # but note the truncation.
        return normalised(
            Rule.DATE, raw.date(), None, raw,
            reason="truncated datetime to date",
        )
    if isinstance(raw, date):
        return normalised(Rule.DATE, raw, None, raw)
    if not isinstance(raw, str):
        return quarantined(
            Rule.DATE, raw, f"expected a string, got {type(raw).__name__}", on_ambiguous
        )
    s = raw.strip()

    m = _ISO_DATE_RE.match(s)
    if m:
        try:
            d = _safe_date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        except ValueError as exc:
            return quarantined(Rule.DATE, raw, f"impossible date: {exc}", on_ambiguous)
        return normalised(Rule.DATE, d, None, raw)

    m = _TEXT_DATE_RE.match(s)
    if m:
        mon = _MONTHS[m.group("mon").lower()]
        try:
            d = _safe_date(int(m.group("y")), mon, int(m.group("d")))
        except ValueError as exc:
            return quarantined(Rule.DATE, raw, f"impossible date: {exc}", on_ambiguous)
        return normalised(Rule.DATE, d, None, raw)

    m = _DOT_DATE_RE.match(s)
    if m:
        # 21.07.2024 — European dotted format is day-first by convention.
        try:
            d = _safe_date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        except ValueError as exc:
            return quarantined(Rule.DATE, raw, f"impossible date: {exc}", on_ambiguous)
        return normalised(Rule.DATE, d, None, raw, reason="day-first dotted format")

    m = _SLASH_SHORT_YEAR_RE.match(s)
    if m:
        return quarantined(
            Rule.DATE, raw,
            "two-digit year: century is ambiguous; not guessing",
            on_ambiguous,
        )

    m = _SLASH_DATE_RE.match(s)
    if m:
        a, b, y = int(m.group("a")), int(m.group("b")), int(m.group("y"))
        if day_first is None:
            return quarantined(
                Rule.DATE, raw,
                "numeric slash date is ambiguous (day-first vs month-first); "
                "declare day_first= to disambiguate",
                on_ambiguous,
            )
        day, mon = (a, b) if day_first else (b, a)
        try:
            d = _safe_date(y, mon, day)
        except ValueError as exc:
            return quarantined(Rule.DATE, raw, f"impossible date: {exc}", on_ambiguous)
        return normalised(
            Rule.DATE, d, None, raw,
            reason=f"slash date parsed {'day' if day_first else 'month'}-first (declared)",
        )

    return quarantined(Rule.DATE, raw, f"unrecognised date format {raw!r}", on_ambiguous)


# ---------------------------------------------------------------------------
# Datetimes (instants, with timezone handling)
# ---------------------------------------------------------------------------

#: ISO-ish datetime with optional offset / Z / named zone.
_ISO_DT_RE = re.compile(
    r"^\s*(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"
    r"[T\s](?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}(?:\.\d+)?))?"
    r"\s*(?P<tz>Z|z|[+-]\d{2}:?\d{2}|[A-Za-z]{2,5})?\s*$"
)


def _resolve_tz_token(token: str | None) -> tzinfo | None:
    """Resolve a trailing tz token (``Z``, ``+10:00``, ``AEST``) to tzinfo.

    Returns ``None`` when the token is absent or unknown (caller decides
    whether that is quarantine-worthy).
    """
    if token is None:
        return None
    t = token.strip()
    if t.upper() in ("Z", "UTC", "GMT"):
        return timezone.utc
    if re.fullmatch(r"[+-]\d{2}:?\d{2}", t):
        sign = 1 if t[0] == "+" else -1
        digits = t[1:].replace(":", "")
        return timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    return NAMED_TIMEZONES.get(t.upper())


def normalise_datetime(
    raw: Any,
    *,
    local_timezone: str | None = None,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Normalise a datetime to an aware UTC instant (granularity DATETIME).

    * An aware input (offset, ``Z`` or a known named zone) is converted
      to UTC.
    * A **naive** input is interpreted in ``local_timezone`` (an IANA
      zone name) when provided — DST-correct because zoneinfo owns the
      offsets.  With no ``local_timezone``, a naive time-bearing input
      is **quarantined** (we refuse to invent a timezone).
    * A bare date string is delegated to :func:`normalise_date`
      (granularity DATE) — no midnight is invented here either.
    """
    if is_missing_token(raw):
        return missing(Rule.DATETIME, raw)

    # Pass-through for datetime objects.
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            if local_timezone is None:
                return quarantined(
                    Rule.DATETIME, raw,
                    "naive datetime with no local_timezone declared; not guessing",
                    on_ambiguous,
                )
            tz = load_timezone(local_timezone)
            if tz is None:
                return quarantined(
                    Rule.DATETIME, raw,
                    f"unknown IANA timezone {local_timezone!r}", on_ambiguous,
                )
            aware = raw.replace(tzinfo=tz).astimezone(timezone.utc)
            return normalised(
                Rule.DATETIME, aware, None, raw,
                reason=f"naive input interpreted in {local_timezone}",
            )
        return normalised(
            Rule.DATETIME, raw.astimezone(timezone.utc), None, raw,
            reason="converted aware input to UTC",
        )

    # A pure date at DATETIME granularity request → keep DATE granularity.
    if isinstance(raw, date):
        return normalised(Rule.DATETIME, raw, None, raw, reason="date granularity retained")

    if not isinstance(raw, str):
        return quarantined(
            Rule.DATETIME, raw, f"expected a string, got {type(raw).__name__}", on_ambiguous
        )
    s = raw.strip()

    # Pure date?  Delegate (DATE granularity, no invented midnight).
    if _ISO_DATE_RE.match(s) and "T" not in s and ":" not in s:
        date_res = normalise_date(s, on_ambiguous=on_ambiguous)
        if date_res.ok:
            return normalised(
                Rule.DATETIME, date_res.value, None, raw,
                reason="date granularity retained",
            )
        return quarantined(Rule.DATETIME, raw, date_res.reason or "bad date", on_ambiguous)

    m = _ISO_DT_RE.match(s)
    if not m:
        return quarantined(
            Rule.DATETIME, raw, f"unrecognised datetime format {raw!r}", on_ambiguous
        )

    try:
        secs = m.group("S")
        second = int(float(secs)) if secs else 0
        micro = int(round((float(secs) - second) * 1_000_000)) if secs else 0
        naive = datetime(
            int(m.group("y")), int(m.group("m")), int(m.group("d")),
            int(m.group("H")), int(m.group("M")), second, micro,
        )
    except ValueError as exc:
        return quarantined(Rule.DATETIME, raw, f"impossible datetime: {exc}", on_ambiguous)

    tz_token = m.group("tz")
    tz = _resolve_tz_token(tz_token)
    if tz_token is not None and tz is None:
        return quarantined(
            Rule.DATETIME, raw,
            f"unknown timezone abbreviation {tz_token!r}; not guessing",
            on_ambiguous,
        )

    if tz is not None:
        aware = naive.replace(tzinfo=tz).astimezone(timezone.utc)
        return normalised(
            Rule.DATETIME, aware, None, raw,
            reason=f"offset/zone {tz_token!r} applied, converted to UTC",
        )

    # Naive string: needs a declared local timezone.
    if local_timezone is None:
        return quarantined(
            Rule.DATETIME, raw,
            "time-bearing value with no offset and no local_timezone declared; "
            "not guessing a timezone",
            on_ambiguous,
        )
    tz = load_timezone(local_timezone)
    if tz is None:
        return quarantined(
            Rule.DATETIME, raw,
            f"unknown IANA timezone {local_timezone!r}", on_ambiguous,
        )
    aware = naive.replace(tzinfo=tz).astimezone(timezone.utc)
    return normalised(
        Rule.DATETIME, aware, None, raw,
        reason=f"naive input interpreted in {local_timezone}",
    )


# ---------------------------------------------------------------------------
# Race-start helper: date + local time + venue zone → instant
# ---------------------------------------------------------------------------


def race_start_instant(
    race_date: date,
    local_time: time,
    local_timezone: str,
    *,
    on_ambiguous: str = "quarantine",
) -> NormalisedValueV1:
    """Combine a race date, a scheduled local time and an IANA venue zone
    into an aware UTC instant.

    This is the canonical "race level granularity" pattern: the source
    publishes ``"Race 3 — 21 July 2024, first gun 10:30 (local)"`` and we
    need a real instant.  The venue zone (not a hard-coded offset) handles
    DST, so a Sydney winter (AEST) and summer (AEDT) start at the same
    wall-clock time map to different correct UTC instants.
    """
    tz = load_timezone(local_timezone)
    if tz is None:
        return quarantined(
            Rule.DATETIME, f"{race_date} {local_time} {local_timezone}",
            f"unknown IANA timezone {local_timezone!r}", on_ambiguous,
        )
    local_dt = datetime.combine(race_date, local_time, tzinfo=tz)
    return normalised(
        Rule.DATETIME, local_dt.astimezone(timezone.utc), None,
        f"{race_date.isoformat()} {local_time.isoformat()} {local_timezone}",
        reason=f"combined date + local time in {local_timezone}",
    )
