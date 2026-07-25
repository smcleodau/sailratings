"""End-to-end test for s03_rating_anatomy.

This is the proof of the section pattern: build Facts from DB → call
Claude with the prompt template → run the truth-discipline audit →
render the chart → return a SectionResult.

Marked as `requires_anthropic` so CI without an API key can skip.
"""
import os
import pytest

from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s03_rating_anatomy import generate
from irc_data.api.services.report.sections._base import SectionResult


@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)
def test_generate_s03_for_sun_fish():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    assert isinstance(result, SectionResult)
    assert result.section_id == "s03_rating_anatomy"
    assert result.error is None
    assert len(result.markdown) > 500  # substantive paragraph(s)
    assert "anatomy_bar" in result.chart_pngs
    assert result.chart_pngs["anatomy_bar"][:8] == b"\x89PNG\r\n\x1a\n"
    # Must mention SUN FISH at least once (boat name was in Facts).
    assert "SUN FISH" in result.markdown.upper()
    # Truth-discipline audit ran (logged suspicious tokens if any).
    assert "audit" in result.structured
    assert isinstance(result.structured["audit"]["suspicious"], list)


def test_generate_s03_returns_error_on_unknown_boat():
    eng = get_engine()
    result = generate(eng, boat_id=999_999_999)
    assert isinstance(result, SectionResult)
    # Empty Facts → empty section, no Claude call.
    assert result.markdown == "" or "no data" in result.markdown.lower()
    assert result.chart_pngs == {}
