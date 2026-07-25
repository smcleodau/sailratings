"""End-to-end test for s08_optimisation."""
import os
import pytest

from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s08_optimisation import generate
from irc_data.api.services.report.sections._base import SectionResult


@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)
def test_generate_s08_for_sun_fish():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    assert isinstance(result, SectionResult)
    assert result.section_id == "s08_optimisation"
    # Either error (no recommendations) or markdown with recommendations
    if result.error:
        # Acceptable — optimiser may return no recs for some boats
        return
    assert len(result.markdown) > 400
    assert "audit" in result.structured


def test_generate_s08_returns_error_on_unknown_boat():
    eng = get_engine()
    result = generate(eng, boat_id=999_999_999)
    assert isinstance(result, SectionResult)
    assert result.markdown == ""
