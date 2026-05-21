"""End-to-end test for s07_sensitivity."""
import os
import pytest

from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s07_sensitivity import generate
from irc_data.api.services.report.sections._base import SectionResult


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
def test_generate_s07_for_sun_fish():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    assert isinstance(result, SectionResult)
    assert result.section_id == "s07_sensitivity"
    assert result.error is None
    assert len(result.markdown) > 400
    assert "sensitivity_bar" in result.chart_pngs
    assert result.chart_pngs["sensitivity_bar"][:8] == b"\x89PNG\r\n\x1a\n"
    md = result.markdown.upper()
    assert "SUNFAST" in md
    # Should mention R² or coefficient
    assert "R²" in result.markdown or "R²" in result.markdown or "93" in result.markdown
    assert "audit" in result.structured


def test_generate_s07_returns_error_on_unknown_boat():
    eng = get_engine()
    result = generate(eng, boat_id=999_999_999)
    assert isinstance(result, SectionResult)
    assert result.markdown == ""
