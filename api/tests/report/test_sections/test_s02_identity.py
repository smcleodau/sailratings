"""End-to-end test for s02_identity."""
import os
import pytest

from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s02_identity import generate
from irc_data.api.services.report.sections._base import SectionResult


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
def test_generate_s02_for_sun_fish():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    assert isinstance(result, SectionResult)
    assert result.section_id == "s02_identity"
    assert result.error is None
    assert len(result.markdown) > 300
    # Should mention SUN FISH + Sunfast 3300 + the year 2019
    md_upper = result.markdown.upper()
    assert "SUN FISH" in md_upper
    assert "SUNFAST" in md_upper or "JEANNEAU" in md_upper
    assert "audit" in result.structured


def test_generate_s02_returns_error_on_unknown_boat():
    eng = get_engine()
    result = generate(eng, boat_id=999_999_999)
    assert isinstance(result, SectionResult)
    assert result.markdown == ""
