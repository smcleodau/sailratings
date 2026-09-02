"""Property-style tests for unit conversion rules (DP-03-03).

Properties pinned:

* **Round-trip**: a length written as feet converts back to the same
  metres (within tolerance); a weight written as pounds converts back
  to the same kilograms.
* **Bare numbers never guess**: a bare number with no declared
  ``assume=`` quarantines, for any magnitude.
* **Missing stays missing**: recognised missing tokens → ``missing``.
* **Times are non-negative seconds**; colon and word forms agree.
* **Ratings stay in band**: anything outside the plausible TCC band
  quarantines; inside, the result rounds to 3 dp.

The ``st.*`` strategies below are a hypothesis-compatible shim: when
`hypothesis` is installed they generate real property tests; otherwise a
fixed deterministic sample corpus is used so the suite always runs.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from irc_data.normalisation import (
    MISSING_TOKENS,
    UnitAssumption,
    normalise_dimensionless,
    normalise_elapsed_time,
    normalise_length,
    normalise_rating,
    normalise_weight,
    parse_decimal,
    parse_elapsed_seconds,
)

# ---------------------------------------------------------------------------
# hypothesis shim: use real strategies when available, else fixed samples
# ---------------------------------------------------------------------------

try:  # pragma: no cover - optional dependency
    from hypothesis import given, settings
    from hypothesis import strategies as st

    HAVE_HYPOTHESIS = True
except ImportError:  # pragma: no cover
    HAVE_HYPOTHESIS = False

    def given(*_args, **_kwargs):  # type: ignore
        def deco(fn):
            return fn
        return deco

    def settings(*_args, **_kwargs):  # type: ignore
        def deco(fn):
            return fn
        return deco

    class _S:  # minimal stand-in so `@given(st...)` parses
        def __getattr__(self, _name):
            def _mk(*_a, **_k):
                return None
            return _mk

    st = _S()  # type: ignore


# Fixed deterministic sample corpus used when hypothesis is absent.
_SAMPLE_LENGTHS_M = [0.5, 1.0, 5.5, 9.9, 10.5, 12.34, 30.48, 100.0]
_SAMPLE_WEIGHTS_KG = [1.0, 50.0, 150.5, 1500.0, 12_500.0]
_SAMPLE_SECONDS = [0.0, 1.0, 45.0, 59.9, 61.0, 333.0, 5400.5, 86400.0]
_SAMPLE_TCC = [0.700, 0.85, 0.987, 1.0, 1.025, 1.5, 2.000]


# ---------------------------------------------------------------------------
# parse_decimal — locale formats, boundaries, never-guess
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("10.5", Decimal("10.5")),
        ("10,5", Decimal("10.5")),       # comma decimal mark
        ("+3.25", Decimal("3.25")),
        ("-2.5", Decimal("-2.5")),
        ("12 500", Decimal("12500")),    # space thousands group
        ("12 500", Decimal("12500")),  # NBSP thousands group
        ("0.001", Decimal("0.001")),
        ("1000", Decimal("1000")),
    ],
)
def test_parse_decimal_accepts(text, expected):
    assert parse_decimal(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "1,234",        # thousands vs decimal comma — ambiguous
        "1.2.3",        # multi-dot, not a grouping pattern
        "abc",
        "not a number",
    ],
)
def test_parse_decimal_refuses_to_guess(text):
    with pytest.raises(ValueError):
        parse_decimal(text)


def test_parse_decimal_repeated_dot_group_normalises():
    # "1.234.567" is unambiguous European thousands grouping → integer.
    assert parse_decimal("1.234.567") == Decimal("1234567")


@pytest.mark.parametrize("token", sorted(MISSING_TOKENS))
def test_parse_decimal_missing_tokens(token):
    assert parse_decimal(token) is None


# ---------------------------------------------------------------------------
# Length round-trip
# ---------------------------------------------------------------------------


def _check_length_roundtrip_metres(metres: float) -> None:
    res = normalise_length(f"{metres} m")
    assert res.ok
    assert math.isclose(res.value, metres, rel_tol=1e-12)


def _check_length_roundtrip_feet(metres: float) -> None:
    feet = metres / 0.3048
    res = normalise_length(f"{feet} ft")
    assert res.ok
    assert math.isclose(res.value, metres, rel_tol=1e-9)


if HAVE_HYPOTHESIS:  # pragma: no cover - exercised when hypothesis present

    @given(st.floats(min_value=0.1, max_value=200.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_length_roundtrip_metres(m):
        _check_length_roundtrip_metres(m)

    @given(st.floats(min_value=0.1, max_value=200.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_length_roundtrip_feet(m):
        _check_length_roundtrip_feet(m)

else:

    @pytest.mark.parametrize("m", _SAMPLE_LENGTHS_M)
    def test_length_roundtrip_metres(m):
        _check_length_roundtrip_metres(m)

    @pytest.mark.parametrize("m", _SAMPLE_LENGTHS_M)
    def test_length_roundtrip_feet(m):
        _check_length_roundtrip_feet(m)


# ---------------------------------------------------------------------------
# Weight round-trip
# ---------------------------------------------------------------------------


def _check_weight_roundtrip_lb(kg: float) -> None:
    lb = kg / 0.45359237
    res = normalise_weight(f"{lb} lb")
    assert res.ok
    assert math.isclose(res.value, kg, rel_tol=1e-9)


if HAVE_HYPOTHESIS:  # pragma: no cover

    @given(st.floats(min_value=0.1, max_value=100_000.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_weight_roundtrip_lb(kg):
        _check_weight_roundtrip_lb(kg)

else:

    @pytest.mark.parametrize("kg", _SAMPLE_WEIGHTS_KG)
    def test_weight_roundtrip_lb(kg):
        _check_weight_roundtrip_lb(kg)


# ---------------------------------------------------------------------------
# Bare numbers never guess
# ---------------------------------------------------------------------------


def _check_bare_number_quarantines(x: float) -> None:
    res = normalise_length(f"{x}")
    assert res.is_quarantined
    res2 = normalise_length(x)
    assert res2.is_quarantined
    # With an explicit assumption the same bare number is fine.
    res3 = normalise_length(x, assume=UnitAssumption.METRES)
    assert res3.ok
    assert "assumed" in (res3.reason or "")


if HAVE_HYPOTHESIS:  # pragma: no cover

    @given(st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_bare_number_quarantines(x):
        _check_bare_number_quarantines(x)

else:

    @pytest.mark.parametrize("x", _SAMPLE_LENGTHS_M + _SAMPLE_TCC)
    def test_bare_number_quarantines(x):
        _check_bare_number_quarantines(x)


# ---------------------------------------------------------------------------
# Elapsed times
# ---------------------------------------------------------------------------


def _check_time_forms_agree(total_seconds: float) -> None:
    """H:MM:SS and plain-seconds forms of the same duration agree."""
    total_seconds = abs(total_seconds)
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = total_seconds - h * 3600 - m * 60
    colon = f"{h}:{m:02d}:{s:05.2f}"
    plain = f"{total_seconds}"
    a = parse_elapsed_seconds(colon)
    b = parse_elapsed_seconds(plain)
    assert a is not None and b is not None
    assert math.isclose(float(a), float(b), rel_tol=1e-6)


def _check_time_nonnegative(total_seconds: float) -> None:
    res = normalise_elapsed_time(total_seconds)
    if res.ok:
        assert res.value >= 0
        assert res.unit == "s"


if HAVE_HYPOTHESIS:  # pragma: no cover

    @given(st.floats(min_value=0, max_value=1_000_000, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_time_forms_agree(s):
        _check_time_forms_agree(s)

    @given(st.floats(min_value=-100, max_value=1_000_000, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_time_nonnegative(s):
        _check_time_nonnegative(s)

else:

    @pytest.mark.parametrize("s", _SAMPLE_SECONDS)
    def test_time_forms_agree(s):
        _check_time_forms_agree(s)

    @pytest.mark.parametrize("s", _SAMPLE_SECONDS)
    def test_time_nonnegative(s):
        _check_time_nonnegative(s)


def test_negative_elapsed_time_quarantines():
    res = normalise_elapsed_time("-5")
    assert res.is_quarantined


# ---------------------------------------------------------------------------
# Ratings stay in band, round to 3 dp
# ---------------------------------------------------------------------------


def _check_rating_in_band(tcc: float) -> None:
    res = normalise_rating(tcc)
    assert res.ok
    assert 0.700 <= res.value <= 2.000
    # Rounded to 3 dp.
    assert abs(res.value - round(res.value, 3)) < 1e-12


def _check_rating_out_of_band(x: float) -> None:
    res = normalise_rating(x)
    assert res.is_quarantined
    assert res.value is None


if HAVE_HYPOTHESIS:  # pragma: no cover

    @given(st.floats(min_value=0.700, max_value=2.000, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_rating_in_band(tcc):
        _check_rating_in_band(tcc)

    @given(st.floats(min_value=2.001, max_value=1e6, allow_nan=False, allow_infinity=False))
    @settings(max_examples=100)
    def test_rating_out_of_band(x):
        _check_rating_out_of_band(x)

else:

    @pytest.mark.parametrize("tcc", _SAMPLE_TCC)
    def test_rating_in_band(tcc):
        _check_rating_in_band(tcc)

    @pytest.mark.parametrize("x", [0.699, 2.001, 45.0, 100.0])
    def test_rating_out_of_band(x):
        _check_rating_out_of_band(x)


def test_rating_band_boundaries_inclusive():
    assert normalise_rating(0.700).ok
    assert normalise_rating(2.000).ok
    assert normalise_rating(0.6999).is_quarantined
    assert normalise_rating(2.0001).is_quarantined


# ---------------------------------------------------------------------------
# Dimensionless + missing semantics
# ---------------------------------------------------------------------------


def _check_dimensionless(x: float) -> None:
    res = normalise_dimensionless(x)
    assert res.ok
    assert math.isclose(res.value, x, rel_tol=1e-9)


if HAVE_HYPOTHESIS:  # pragma: no cover

    @given(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_dimensionless(x):
        _check_dimensionless(x)

else:

    @pytest.mark.parametrize("x", _SAMPLE_TCC + _SAMPLE_SECONDS)
    def test_dimensionless(x):
        _check_dimensionless(x)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_never_normalised(bad):
    for fn in (normalise_length, normalise_weight, normalise_rating, normalise_dimensionless):
        res = fn(bad)
        assert not res.ok, (fn.__name__, bad, res)
