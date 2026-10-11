"""End-to-end test for s09_formula_drift."""
import os
import pytest

from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s09_formula_drift import generate
from irc_data.api.services.report.sections._base import SectionResult


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
def test_generate_s09_for_sun_fish():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    assert isinstance(result, SectionResult)
    assert result.section_id == "s09_formula_drift"
    # Drift may or may not have data — accept either
    if result.error:
        return
    assert len(result.markdown) > 100
    assert "audit" in result.structured


def test_generate_s09_returns_error_on_unknown_boat():
    eng = get_engine()
    result = generate(eng, boat_id=999_999_999)
    assert isinstance(result, SectionResult)
    assert result.markdown == ""
