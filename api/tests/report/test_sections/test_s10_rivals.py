"""End-to-end test for s10_rivals."""
import os
import pytest

from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s10_rivals import generate
from irc_data.api.services.report.sections._base import SectionResult


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
def test_generate_s10_for_sun_fish():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    assert isinstance(result, SectionResult)
    assert result.section_id == "s10_rivals"
    # Accept either rivals-prose or no-rivals-error
    if result.error:
        return
    assert len(result.markdown) > 300
    assert "audit" in result.structured


def test_generate_s10_returns_error_on_unknown_boat():
    eng = get_engine()
    result = generate(eng, boat_id=999_999_999)
    assert isinstance(result, SectionResult)
    assert result.markdown == ""
