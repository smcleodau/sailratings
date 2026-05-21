"""End-to-end test for s06_performance."""
import os
import pytest

from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s06_performance import generate
from irc_data.api.services.report.sections._base import SectionResult


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
def test_generate_s06_for_sun_fish():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    assert isinstance(result, SectionResult)
    assert result.section_id == "s06_performance"
    assert result.error is None
    assert len(result.markdown) > 600  # 400-500 words minimum
    assert "results_timeline" in result.chart_pngs
    assert result.chart_pngs["results_timeline"][:8] == b"\x89PNG\r\n\x1a\n"
    md = result.markdown.upper()
    assert "SUN FISH" in md
    # Should mention the real finish/win counts: 31 finishes, 3 wins, 13 podiums
    assert "31" in result.markdown or "thirty-one" in result.markdown.lower()
    assert "audit" in result.structured


def test_generate_s06_returns_error_on_unknown_boat():
    eng = get_engine()
    result = generate(eng, boat_id=999_999_999)
    assert isinstance(result, SectionResult)
    assert result.markdown == ""
