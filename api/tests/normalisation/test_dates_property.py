"""Property-style tests for date/timezone normalisation (DP-03-03).

Covers the absorbed *Race Level Granularity and Timezones* epic:

* **Granularity is explicit**: a bare date stays a ``date``; a
  time-bearing value becomes an aware UTC ``datetime``.
* **No invented timezones**: a naive time with no ``local_timezone``
  quarantines, always.
* **IANA zones own DST**: the same wall time in ``Australia/Sydney``
  maps to different UTC instants in summer (AEDT) vs winter (AEST).
* **Round-trip**: ``local → UTC → local`` returns the original wall
  time in that zone.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from irc_data.normalisation import (
    Granularity,
    load_timezone,
    normalise_date,
    normalise_datetime,
    race_start_instant,
)

try:  # pragma: no cover - optional dependency
    from hypothesis import given, settings
    from hypothesis import strategies as st

    HAVE_HYPOTHESIS = True
except ImportError:  # pragma: no cover
    HAVE_HYPOTHESIS = False

    def given(*_a, **_k):  # type: ignore
        def deco(fn):
            return fn
        return deco

    def settings(*_a, **_k):  # type: ignore
        def deco(fn):
            return fn
        return deco

    class _S:
        def __getattr__(self, _n):
            def _mk(*_a, **_k):
                return None
            return _mk

    st = _S()  # type: ignore


_SAMPLE_DATES = [date(2024, 1, 1), date(2024, 7, 21), date(2024, 12, 26), date(1999, 2, 28)]
_SAMPLE_TIMES = [time(0, 0), time(10, 30), time(13, 0), time(23, 59)]
_ZONES = ["Australia/Sydney", "Europe/London", "America/New_York", "UTC"]


# ---------------------------------------------------------------------------
# Granularity is explicit
# ---------------------------------------------------------------------------


def test_bare_date_stays_date_granularity():
    res = normalise_datetime("2024-07-21")
    assert res.ok
    assert isinstance(res.value, date)
    assert not isinstance(res.value, datetime)  # no invented midnight


def test_time_bearing_value_is_aware_utc():
    res = normalise_datetime("2024-07-21 10:30", local_timezone="Australia/Sydney")
    assert res.ok
    assert isinstance(res.value, datetime)
    assert res.value.tzinfo is timezone.utc


def test_granularity_enum_values():
    assert Granularity.DATE.value == "date"
    assert Granularity.DATETIME.value == "datetime"


# ---------------------------------------------------------------------------
# No invented timezones
# ---------------------------------------------------------------------------


def _check_naive_needs_zone(y: int, mo: int, d: int, h: int, mi: int) -> None:
    s = f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"
    res = normalise_datetime(s)  # no local_timezone
    assert res.is_quarantined, res


if HAVE_HYPOTHESIS:  # pragma: no cover

    @given(
        st.integers(2000, 2030), st.integers(1, 12), st.integers(1, 28),
        st.integers(0, 23), st.integers(0, 59),
    )
    @settings(max_examples=100)
    def test_naive_needs_zone(y, mo, d, h, mi):
        _check_naive_needs_zone(y, mo, d, h, mi)

else:

    @pytest.mark.parametrize(
        "y,mo,d,h,mi",
        [(2024, 7, 21, 10, 30), (2024, 12, 26, 13, 0), (2024, 1, 1, 0, 0)],
    )
    def test_naive_needs_zone(y, mo, d, h, mi):
        _check_naive_needs_zone(y, mo, d, h, mi)


def test_unknown_iana_zone_quarantines():
    res = normalise_datetime("2024-07-21 10:30", local_timezone="Not/AZone")
    assert res.is_quarantined


def test_load_timezone_unknown_returns_none():
    assert load_timezone("Not/AZone") is None
    assert load_timezone("Australia/Sydney") is not None


# ---------------------------------------------------------------------------
# IANA zones own DST
# ---------------------------------------------------------------------------


def test_dst_summer_vs_winter_same_wall_time():
    """Same 10:30 wall time in Sydney → different UTC offsets."""
    summer = normalise_datetime("2024-01-21 10:30", local_timezone="Australia/Sydney")
    winter = normalise_datetime("2024-07-21 10:30", local_timezone="Australia/Sydney")
    assert summer.value == datetime(2024, 1, 20, 23, 30, tzinfo=timezone.utc)   # AEDT +11
    assert winter.value == datetime(2024, 7, 21, 0, 30, tzinfo=timezone.utc)    # AEST +10


def _check_roundtrip_in_zone(rdate: date, t: time, zone: str) -> None:
    res = race_start_instant(rdate, t, zone)
    assert res.ok, res
    back = res.value.astimezone(ZoneInfo(zone))
    assert back.date() == rdate
    assert (back.hour, back.minute) == (t.hour, t.minute)


if HAVE_HYPOTHESIS:  # pragma: no cover

    @given(
        st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31)),
        st.times(),
        st.sampled_from(_ZONES),
    )
    @settings(max_examples=200)
    def test_roundtrip_in_zone(rdate, t, zone):
        _check_roundtrip_in_zone(rdate, t, zone)

else:

    @pytest.mark.parametrize("rdate", _SAMPLE_DATES)
    @pytest.mark.parametrize("t", _SAMPLE_TIMES)
    @pytest.mark.parametrize("zone", _ZONES)
    def test_roundtrip_in_zone(rdate, t, zone):
        _check_roundtrip_in_zone(rdate, t, zone)


# ---------------------------------------------------------------------------
# Date format boundaries / locale formats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2024-02-29", date(2024, 2, 29)),    # leap year ok
        ("2023-02-29", None),                  # not a leap year → quarantine
        ("2024-04-31", None),                  # April has 30 days → quarantine
        ("2024-12-31", date(2024, 12, 31)),
        ("2024-01-01", date(2024, 1, 1)),
    ],
)
def test_date_boundaries(raw, expected):
    res = normalise_date(raw)
    if expected is None:
        assert res.is_quarantined
    else:
        assert res.ok and res.value == expected


def test_slash_date_declared_day_first():
    res = normalise_date("07/04/2024", day_first=True)
    assert res.ok and res.value == date(2024, 4, 7)


def test_slash_date_declared_month_first():
    res = normalise_date("07/04/2024", day_first=False)
    assert res.ok and res.value == date(2024, 7, 4)


def test_slash_date_undeclared_quarantines():
    assert normalise_date("07/04/2024").is_quarantined


def test_two_digit_year_quarantines():
    assert normalise_date("1/2/24").is_quarantined


# ---------------------------------------------------------------------------
# datetime objects pass through (aware → UTC; naive needs zone)
# ---------------------------------------------------------------------------


def test_aware_datetime_converts_to_utc():
    dt = datetime(2024, 7, 21, 10, 30, tzinfo=ZoneInfo("Australia/Sydney"))
    res = normalise_datetime(dt)
    assert res.ok
    assert res.value == datetime(2024, 7, 21, 0, 30, tzinfo=timezone.utc)


def test_naive_datetime_quarantines_without_zone():
    res = normalise_datetime(datetime(2024, 7, 21, 10, 30))
    assert res.is_quarantined


def test_naive_datetime_uses_declared_zone():
    res = normalise_datetime(datetime(2024, 7, 21, 10, 30), local_timezone="Australia/Sydney")
    assert res.ok
    assert res.value == datetime(2024, 7, 21, 0, 30, tzinfo=timezone.utc)


def test_datetime_truncated_to_date_keeps_reason():
    res = normalise_date(datetime(2024, 7, 21, 10, 30, tzinfo=timezone.utc))
    assert res.ok
    assert res.value == date(2024, 7, 21)
    assert "truncated" in (res.reason or "")
