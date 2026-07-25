"""End-to-end test for s01_executive."""
import os
import pytest

from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s01_executive import generate
from irc_data.api.services.report.sections._base import SectionResult


@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)
def test_generate_s01_for_sun_fish():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    assert isinstance(result, SectionResult)
    assert result.section_id == "s01_executive"
    assert result.error is None
    assert len(result.markdown) > 200  # at least the 150-200 word summary
    # Should mention boat name AND her TCC + design.
    md_upper = result.markdown.upper()
    assert "SUN FISH" in md_upper
    assert "1.025" in result.markdown or "1.0250" in result.markdown
    assert "SUNFAST" in md_upper
    assert "audit" in result.structured


def test_generate_s01_returns_error_on_unknown_boat():
    eng = get_engine()
    result = generate(eng, boat_id=999_999_999)
    assert isinstance(result, SectionResult)
    assert result.markdown == ""
