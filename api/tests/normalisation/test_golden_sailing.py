"""Golden tests — known sailing examples (DP-03-03).

Pins the exact expected normalised value for real-world sailing inputs
(Rolex Sydney Hobart starts, IRC TCC conventions, World Sailing nation
codes, Unicode-scraped boat names).  Every case also asserts the
transparency contract: the original representation and the rule version
are retained on the result.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pytest

from irc_data.normalisation import (
    RULES_VERSION,
    NormalisationKind,
    normalise_country_code,
    normalise_date,
    normalise_datetime,
    normalise_elapsed_time,
    normalise_length,
    normalise_name,
    normalise_rating,
    normalise_rating_system_version,
    normalise_sail_number,
    normalise_weight,
    race_start_instant,
    split_sail_number,
)

from .goldens import (
    COUNTRY_GOLDENS,
    COUNTRY_QUARANTINE_GOLDENS,
    DATE_GOLDENS,
    DATE_QUARANTINE_GOLDENS,
    DATETIME_GOLDENS,
    DATETIME_QUARANTINE_GOLDENS,
    LENGTH_GOLDENS,
    MISSING_GOLDENS,
    NAME_GOLDENS,
    NAME_QUARANTINE_GOLDENS,
    RACE_START_GOLDENS,
    RATING_GOLDENS,
    RATING_QUARANTINE_GOLDENS,
    RATING_SYSTEM_GOLDENS,
    RATING_SYSTEM_QUARANTINE_GOLDENS,
    SAIL_NUMBER_GOLDENS,
    TIME_GOLDENS,
    UNIT_QUARANTINE_GOLDENS,
    WEIGHT_GOLDENS,
)


def _assert_transparent(result, raw, rule: str) -> None:
    """Every normalised value retains its original + rule version."""
    assert result.original == raw
    assert result.rules_version == RULES_VERSION
    assert result.rule == rule


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", NAME_GOLDENS)
def test_name_goldens(raw, expected):
    res = normalise_name(raw)
    assert res.ok, res
    assert res.value == expected
    _assert_transparent(res, raw, "name-v1")


@pytest.mark.parametrize("raw", NAME_QUARANTINE_GOLDENS)
def test_name_quarantine_goldens(raw):
    res = normalise_name(raw)
    assert res.is_quarantined
    assert res.original == raw  # original preserved even when quarantined


# ---------------------------------------------------------------------------
# Sail numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected,country", SAIL_NUMBER_GOLDENS)
def test_sail_number_goldens(raw, expected, country):
    res = normalise_sail_number(raw)
    assert res.ok, res
    assert res.value == expected
    _assert_transparent(res, raw, "sail-number-v1")
    got_country, got_sail = split_sail_number(raw)
    assert got_country == country
    assert got_sail == expected.split(" ", 1)[1] if country else expected


# ---------------------------------------------------------------------------
# Country codes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", COUNTRY_GOLDENS)
def test_country_goldens(raw, expected):
    res = normalise_country_code(raw)
    assert res.ok, res
    assert res.value == expected
    _assert_transparent(res, raw, "country-code-v1")


@pytest.mark.parametrize("raw", COUNTRY_QUARANTINE_GOLDENS)
def test_country_quarantine_goldens(raw):
    res = normalise_country_code(raw)
    assert res.is_quarantined
    assert res.original == raw


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", LENGTH_GOLDENS)
def test_length_goldens(raw, expected):
    res = normalise_length(raw)
    assert res.ok, res
    assert res.unit == "m"
    assert math.isclose(res.value, expected, rel_tol=1e-9)
    _assert_transparent(res, raw, "length-v1")


@pytest.mark.parametrize("raw,expected", WEIGHT_GOLDENS)
def test_weight_goldens(raw, expected):
    res = normalise_weight(raw)
    assert res.ok, res
    assert res.unit == "kg"
    assert math.isclose(res.value, expected, rel_tol=1e-9)
    _assert_transparent(res, raw, "weight-v1")


@pytest.mark.parametrize("raw,expected", TIME_GOLDENS)
def test_time_goldens(raw, expected):
    res = normalise_elapsed_time(raw)
    assert res.ok, res
    assert res.unit == "s"
    assert math.isclose(res.value, expected, rel_tol=1e-9)
    _assert_transparent(res, raw, "time-v1")


@pytest.mark.parametrize("raw,expected", RATING_GOLDENS)
def test_rating_goldens(raw, expected):
    res = normalise_rating(raw)
    assert res.ok, res
    assert math.isclose(res.value, expected, rel_tol=1e-9)
    _assert_transparent(res, raw, "rating-v1")


@pytest.mark.parametrize("raw", RATING_QUARANTINE_GOLDENS)
def test_rating_quarantine_goldens(raw):
    res = normalise_rating(raw)
    assert res.is_quarantined
    assert res.original == raw


@pytest.mark.parametrize("raw", UNIT_QUARANTINE_GOLDENS)
def test_unit_quarantine_goldens(raw):
    """Ambiguous/malformed numerics quarantine rather than guess."""
    for fn in (normalise_length, normalise_weight, normalise_rating):
        res = fn(raw)
        assert not res.ok, (fn.__name__, raw, res)
        assert res.original == raw


# ---------------------------------------------------------------------------
# Dates & timezones (race level granularity)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", DATE_GOLDENS)
def test_date_goldens(raw, expected):
    res = normalise_date(raw)
    assert res.ok, res
    assert res.value == expected
    assert isinstance(res.value, date)
    _assert_transparent(res, raw, "date-v1")


@pytest.mark.parametrize("raw", DATE_QUARANTINE_GOLDENS)
def test_date_quarantine_goldens(raw):
    res = normalise_date(raw)
    assert res.is_quarantined
    assert res.original == raw


@pytest.mark.parametrize("raw,tz,expected", DATETIME_GOLDENS)
def test_datetime_goldens(raw, tz, expected):
    res = normalise_datetime(raw, local_timezone=tz)
    assert res.ok, res
    assert res.value == expected
    assert res.value.tzinfo is not None  # aware UTC
    _assert_transparent(res, raw, "datetime-v1")


@pytest.mark.parametrize("raw,tz", DATETIME_QUARANTINE_GOLDENS)
def test_datetime_quarantine_goldens(raw, tz):
    res = normalise_datetime(raw, local_timezone=tz)
    assert res.is_quarantined
    assert res.original == raw


@pytest.mark.parametrize("rdate, ltime, zone, expected", RACE_START_GOLDENS)
def test_race_start_goldens(rdate, ltime, zone, expected):
    res = race_start_instant(rdate, ltime, zone)
    assert res.ok, res
    assert res.value == expected


def test_sydney_hobart_dst_boundary():
    """The same 13:00 wall-clock Boxing Day start is UTC+11 (AEDT), while
    a midwinter 13:00 start is UTC+10 (AEST) — IANA zoneinfo, not a
    hard-coded offset, owns the difference."""
    summer = race_start_instant(date(2024, 12, 26), datetime.min.time().replace(hour=13), "Australia/Sydney")
    winter = race_start_instant(date(2024, 7, 21), datetime.min.time().replace(hour=13), "Australia/Sydney")
    assert summer.value == datetime(2024, 12, 26, 2, 0, tzinfo=timezone.utc)
    assert winter.value == datetime(2024, 7, 21, 3, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Rating-system versions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", RATING_SYSTEM_GOLDENS)
def test_rating_system_goldens(raw, expected):
    res = normalise_rating_system_version(raw)
    assert res.ok, res
    assert res.value == expected
    _assert_transparent(res, raw, "rating-system-version-v1")


@pytest.mark.parametrize("raw", RATING_SYSTEM_QUARANTINE_GOLDENS)
def test_rating_system_quarantine_goldens(raw):
    res = normalise_rating_system_version(raw)
    assert res.is_quarantined
    assert res.original == raw


# ---------------------------------------------------------------------------
# Missing-value semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", MISSING_GOLDENS)
def test_missing_tokens_are_missing_everywhere(raw):
    """A recognised missing token yields kind=missing for every rule."""
    from irc_data.normalisation import normalise_dimensionless

    for fn in (
        normalise_length,
        normalise_weight,
        normalise_elapsed_time,
        normalise_rating,
        normalise_dimensionless,
        normalise_name,
        normalise_sail_number,
        normalise_country_code,
        normalise_date,
        normalise_datetime,
        normalise_rating_system_version,
    ):
        res = fn(raw)
        assert res.kind is NormalisationKind.MISSING, (fn.__name__, raw, res)
        assert res.value is None
