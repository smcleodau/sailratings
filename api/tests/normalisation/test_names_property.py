"""Property-style tests for name / sail-number / country rules (DP-03-03).

Properties pinned:

* **Idempotence**: ``clean_name(clean_name(s)) == clean_name(s)`` and
  normalising an already-normalised name is a fixed point.
* **No information is invented**: normalised names never contain
  leading/trailing whitespace, double spaces, or control characters.
* **Unicode preserved**: letters with diacritics survive cleanup
  (NFKC folds compatibility forms but does not strip accents).
* **Sail numbers uppercase & split deterministically**; a recognised
  national prefix round-trips through :func:`split_sail_number`.
* **Country codes are alpha-3 or quarantined** — never guessed.
"""

from __future__ import annotations

import unicodedata

import pytest

from irc_data.normalisation import (
    clean_name,
    normalise_country_code,
    normalise_name,
    normalise_sail_number,
    split_sail_number,
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


_SAMPLE_NAMES = [
    "  WILD\u00a0OATS\u200b XI ",
    "São   Paulo",
    "O'Brien",
    "MCDONALD",
    "van den Berg",
    "Foo  ,  Bar",
    "black jack",
    "Œuvre  de  Mer",
    "Ⅻ Metre",          # unicode roman numeral → NFKC folds
    "ＦＵＬＬＷＩＤＴＨ",   # full-width latin
]


# ---------------------------------------------------------------------------
# clean_name properties
# ---------------------------------------------------------------------------


def _check_clean_idempotent(s: str) -> None:
    once = clean_name(s)
    assert clean_name(once) == once


def _check_clean_never_invents(s: str) -> None:
    out = clean_name(s)
    assert out == out.strip()
    assert "  " not in out
    for ch in out:
        assert unicodedata.category(ch) not in ("Cc", "Cf")


if HAVE_HYPOTHESIS:  # pragma: no cover

    @given(st.text())
    @settings(max_examples=300)
    def test_clean_idempotent(s):
        _check_clean_idempotent(s)

    @given(st.text())
    @settings(max_examples=300)
    def test_clean_never_invents(s):
        _check_clean_never_invents(s)

else:

    @pytest.mark.parametrize("s", _SAMPLE_NAMES)
    def test_clean_idempotent(s):
        _check_clean_idempotent(s)

    @pytest.mark.parametrize("s", _SAMPLE_NAMES)
    def test_clean_never_invents(s):
        _check_clean_never_invents(s)


# ---------------------------------------------------------------------------
# normalise_name properties
# ---------------------------------------------------------------------------


def _check_normalise_name_fixed_point(s: str) -> None:
    res = normalise_name(s)
    if res.ok:
        again = normalise_name(res.value)
        assert again.ok
        # Re-normalising a canonical name changes nothing.
        assert again.value == res.value


if HAVE_HYPOTHESIS:  # pragma: no cover

    @given(st.text(min_size=1))
    @settings(max_examples=300)
    def test_normalise_name_fixed_point(s):
        _check_normalise_name_fixed_point(s)

else:

    @pytest.mark.parametrize("s", _SAMPLE_NAMES)
    def test_normalise_name_fixed_point(s):
        _check_normalise_name_fixed_point(s)


def test_diacritics_preserved():
    res = normalise_name("São Paulo")
    assert res.ok
    assert "ã" in res.value  # accent kept, not stripped


def test_ligature_folds():
    res = normalise_name("FINISH\uFB01RE")  # ﬁ ligature
    assert res.ok
    assert "fi" in res.value.lower()


# ---------------------------------------------------------------------------
# Sail number properties
# ---------------------------------------------------------------------------


_SAMPLE_SAILS = ["gbr8310", "GBR 8310", "8310", "KISMET", "USA 123", "aus-5295"]


def _check_sail_upper(s: str) -> None:
    res = normalise_sail_number(s)
    if res.ok:
        assert res.value == res.value.upper()


def _check_sail_split_roundtrip(s: str) -> None:
    country, sail = split_sail_number(s)
    res = normalise_sail_number(s)
    if res.ok:
        if country is not None:
            assert res.value == f"{country} {sail}"
        else:
            assert res.value == sail


if HAVE_HYPOTHESIS:  # pragma: no cover

    @given(st.text(min_size=1))
    @settings(max_examples=300)
    def test_sail_upper(s):
        _check_sail_upper(s)

    @given(st.text(min_size=1))
    @settings(max_examples=300)
    def test_sail_split_roundtrip(s):
        _check_sail_split_roundtrip(s)

else:

    @pytest.mark.parametrize("s", _SAMPLE_SAILS)
    def test_sail_upper(s):
        _check_sail_upper(s)

    @pytest.mark.parametrize("s", _SAMPLE_SAILS)
    def test_sail_split_roundtrip(s):
        _check_sail_split_roundtrip(s)


def test_letters_only_sail_not_split_as_country():
    # "BELLADONNA" starts with "BEL" but is a name, not BEL + "LADONNA".
    country, sail = split_sail_number("BELLADONNA")
    assert country is None
    assert sail == "BELLADONNA"


# ---------------------------------------------------------------------------
# Country code properties
# ---------------------------------------------------------------------------


_SAMPLE_COUNTRIES = ["GBR", "gb", "uk", "Australia", "SUI", "usa"]


def _check_country_alpha3_or_quarantine(s: str) -> None:
    res = normalise_country_code(s)
    if res.ok:
        assert len(res.value) == 3
        assert res.value.isalpha() and res.value.isupper()
    else:
        assert res.is_quarantined or res.is_missing


if HAVE_HYPOTHESIS:  # pragma: no cover

    @given(st.text(min_size=1))
    @settings(max_examples=300)
    def test_country_alpha3_or_quarantine(s):
        _check_country_alpha3_or_quarantine(s)

else:

    @pytest.mark.parametrize("s", _SAMPLE_COUNTRIES)
    def test_country_alpha3_or_quarantine(s):
        _check_country_alpha3_or_quarantine(s)


def test_country_never_guesses_unknown():
    res = normalise_country_code("XX")
    assert res.is_quarantined
    assert res.value is None
