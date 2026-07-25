"""End-to-end test for s05_class_context."""
import os
import pytest

from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s05_class_context import generate
from irc_data.api.services.report.sections._base import SectionResult


@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)
def test_generate_s05_for_sun_fish():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    assert isinstance(result, SectionResult)
    assert result.section_id == "s05_class_context"
    assert result.error is None
    assert len(result.markdown) > 400
    assert "class_distribution" in result.chart_pngs
    assert result.chart_pngs["class_distribution"][:8] == b"\x89PNG\r\n\x1a\n"
    md_upper = result.markdown.upper()
    assert "SUNFAST" in md_upper
    assert "audit" in result.structured


def test_generate_s05_returns_error_on_unknown_boat():
    eng = get_engine()
    result = generate(eng, boat_id=999_999_999)
    assert isinstance(result, SectionResult)
    assert result.markdown == ""
    assert result.chart_pngs == {}
