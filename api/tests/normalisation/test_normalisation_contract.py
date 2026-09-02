"""Contract tests for the NormalisedValueV1 handoff / output contract
and the rating-system version rules (DP-03-03).

Covers:

* The transparency contract — every result carries the original
  representation, the rule id and the rule-set version.
* The never-guess contract — ``on_ambiguous="raise"`` surfaces
  :class:`AmbiguousNormalisationError`; the default quarantines.
* Missing-value semantics across every rule.
* Rating-system version year-range boundaries.
* Serialisation (``to_dict`` / ``to_json``) is JSON-safe.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from irc_data.normalisation import (
    RULES_VERSION,
    AmbiguousNormalisationError,
    NormalisationKind,
    NormalisedValueV1,
    Rule,
    normalise_country_code,
    normalise_date,
    normalise_datetime,
    normalise_length,
    normalise_name,
    normalise_rating_system_version,
    normalise_sail_number,
)


# ---------------------------------------------------------------------------
# Transparency: original + rule version always retained
# ---------------------------------------------------------------------------


def test_result_retains_original_and_rule_version():
    raw = "  WILD\u00a0OATS\u200b XI "
    res = normalise_name(raw)
    assert res.original == raw            # untouched original
    assert res.rules_version == RULES_VERSION
    assert res.rule == Rule.NAME.value
    assert res.kind is NormalisationKind.NORMALISED


def test_quarantine_retains_original_and_reason():
    res = normalise_length("10.5")  # bare number, no assumption
    assert res.is_quarantined
    assert res.original == "10.5"
    assert res.reason  # human-readable why
    assert res.value is None
    assert res.rules_version == RULES_VERSION


def test_missing_retains_original():
    res = normalise_name("DNF")
    assert res.kind is NormalisationKind.MISSING
    assert res.original == "DNF"
    assert res.value is None


# ---------------------------------------------------------------------------
# Never guess: on_ambiguous="raise" surfaces the exception
# ---------------------------------------------------------------------------


def test_raise_policy_raises_on_ambiguity():
    with pytest.raises(AmbiguousNormalisationError):
        normalise_date("03/04/2024", on_ambiguous="raise")


def test_raise_policy_raises_on_unknown_country():
    with pytest.raises(AmbiguousNormalisationError):
        normalise_country_code("XX", on_ambiguous="raise")


def test_raise_policy_still_normalises_clean_input():
    res = normalise_date("2024-07-21", on_ambiguous="raise")
    assert res.ok
    assert res.value == date(2024, 7, 21)


def test_invalid_policy_rejected():
    with pytest.raises(ValueError):
        normalise_length("10.5", on_ambiguous="guess")


# ---------------------------------------------------------------------------
# Missing-value semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "-", "N/A", "dnf", "tbd"])
def test_missing_tokens(raw):
    res = normalise_sail_number(raw)
    assert res.kind is NormalisationKind.MISSING


def test_missing_is_not_quarantine():
    res = normalise_datetime(None)
    assert res.is_missing
    assert not res.is_quarantined


# ---------------------------------------------------------------------------
# Rating-system version boundaries
# ---------------------------------------------------------------------------


def test_rating_system_in_range_year():
    res = normalise_rating_system_version("IRC 2024")
    assert res.ok and res.value == "IRC 2024"


def test_rating_system_two_digit_in_range():
    res = normalise_rating_system_version("IRC 24")
    assert res.ok and res.value == "IRC 2024"
    assert "24" in (res.reason or "")


def test_rating_system_two_digit_out_of_range_quarantines():
    assert normalise_rating_system_version("IRC 97").is_quarantined


def test_rating_system_four_digit_out_of_range_quarantines():
    assert normalise_rating_system_version("IRC 1899").is_quarantined


def test_rating_system_yearless_quarantines():
    assert normalise_rating_system_version("IRC").is_quarantined


def test_rating_system_unknown_quarantines():
    assert normalise_rating_system_version("NOPE 2024").is_quarantined


# ---------------------------------------------------------------------------
# Serialisation is JSON-safe
# ---------------------------------------------------------------------------


def test_to_dict_is_json_safe_for_datetime():
    res = normalise_datetime("2024-07-21T10:30:00Z")
    d = res.to_dict()
    assert isinstance(d["value"], str)
    assert d["value"] == datetime(2024, 7, 21, 10, 30, tzinfo=timezone.utc).isoformat()
    assert d["original"] == "2024-07-21T10:30:00Z"
    assert d["rules_version"] == RULES_VERSION


def test_to_json_roundtrips_through_json():
    import json

    res = normalise_country_code("gb")
    blob = json.loads(res.to_json())
    assert blob["value"] == "GBR"
    assert blob["kind"] == "normalised"


def test_normalised_value_is_frozen():
    import dataclasses

    res = normalise_country_code("gb")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(res, "value", "USA")
