"""Unit tests for sail-number normalisation in ``irc_data.matching.identity``.

The matching module exposes ``normalize_sail_tokens`` — a function that
returns the set of equivalent token strings for a given sail number. Two
boats whose token sets intersect are considered sail-identifier matches.

The ORC-side matcher (``match_orc_to_irc``) historically used the naive
``normalize_sail`` helper, which doesn't handle the common case of an
incoming ORC cert tagged ``AUS1213`` matching an IRC boat stored as bare
``1213`` (or vice versa). These tests pin down that behaviour: stripping
country prefixes, stripping class-letter prefixes, and absorbing
whitespace/punctuation.
"""

import pytest

from irc_data.matching.identity import normalize_sail_tokens


@pytest.mark.parametrize(
    "input_sail, expected",
    [
        ("EAUS1213", "1213"),
        ("AUS1213", "1213"),
        ("1213", "1213"),
        ("E-AUS-1213", "1213"),
        ("AUS 1213", "1213"),
    ],
)
def test_normalize_sail_tokens_strips_country_prefix(input_sail, expected):
    """Country / class prefix is removed so bare-number variants match."""
    tokens = normalize_sail_tokens(input_sail)
    assert expected in tokens, f"{expected!r} not in {tokens!r} (input={input_sail!r})"


def test_normalize_sail_tokens_returns_set_for_orc_matching():
    """The class-prefix expansion covers EAUS → AUS → bare-number."""
    tokens = normalize_sail_tokens("EAUS1213")
    assert "EAUS1213" in tokens
    assert "AUS1213" in tokens
    assert "1213" in tokens


def test_normalize_sail_tokens_bare_country_yields_number():
    """``AUS1213`` (no class letter) must still expose the bare ``1213``."""
    tokens = normalize_sail_tokens("AUS1213")
    assert "1213" in tokens
    assert "AUS1213" in tokens


def test_normalize_sail_tokens_empty_input_returns_empty_set():
    assert normalize_sail_tokens(None) == set()
    assert normalize_sail_tokens("") == set()
    assert normalize_sail_tokens("   ") == set()
