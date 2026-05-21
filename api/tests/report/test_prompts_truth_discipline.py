"""Verify the truth-discipline guard catches hallucinated numbers.

The premium report's biggest failure mode under long-form generation
is invented statistics — Claude will fill in plausible-sounding numbers
that aren't in the source data. The truth-discipline pattern: every
section passes a Facts dataclass to Claude with an explicit allowlist
of numeric values, and after generation we scan the markdown for
numeric tokens that don't appear in the Facts allowlist. Suspicious
tokens get logged + the section is marked degraded.
"""
import re
from decimal import Decimal

from irc_data.api.services.report.claude_client import (
    extract_numeric_tokens,
    facts_numeric_allowlist,
)
from irc_data.api.services.report.facts import (
    RatingAnatomyFacts, MeasurementContribution,
)


def test_extracts_numeric_tokens_from_prose():
    md = "Her TCC sits at 1.0250, against a class median of 1.0025. " \
         "She's 285 kg lighter than the median Sunfast 3300."
    tokens = extract_numeric_tokens(md)
    assert "1.0250" in tokens
    assert "1.0025" in tokens
    assert "285" in tokens


def test_facts_allowlist_includes_decomposition_values():
    facts = RatingAnatomyFacts(
        boat_name="SUN FISH",
        tcc_now=Decimal("1.0250"),
        class_mean_tcc=1.0025,
        class_median_tcc=1.0020,
        decomposition=[
            MeasurementContribution(
                field="displacement", this_boat=3696.0, class_mean=3981.0,
                delta=-285.0, contrib_tcc=0.0096, unit="per 100kg",
                beta=-0.003373,
            ),
        ],
        explained_variance_pct=93.4,
        model_tier="A",
        n_boats_in_class=82,
    )
    allowlist = facts_numeric_allowlist(facts)
    # Numbers from the Facts object should appear in the allowlist,
    # rounded to 1 decimal (so prose can use 285 OR 285.0 OR 0.0096).
    assert "1.025" in allowlist
    assert "1.0025" in allowlist
    assert "285" in allowlist
    assert "3696" in allowlist
    assert "3981" in allowlist
    assert "0.0096" in allowlist
    assert "93" in allowlist or "93.4" in allowlist
    assert "82" in allowlist


def test_allowlist_normalises_decimal_representations():
    """1.025, 1.0250, 1.02 → all forms should be valid for the same value."""
    from irc_data.api.services.report.claude_client import _normalise_number
    assert _normalise_number("1.0250") == _normalise_number("1.025")
    assert _normalise_number("285.0") == _normalise_number("285")
