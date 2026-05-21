"""End-to-end test for s11_appendix.

Deterministic — no API key required."""
from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s11_appendix import generate
from irc_data.api.services.report.sections._base import SectionResult


def test_generate_s11_methodology_present():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    assert isinstance(result, SectionResult)
    assert result.section_id == "s11_appendix"
    assert result.error is None
    md = result.markdown
    assert "## Methodology" in md
    assert "## Data sources" in md
    assert "## Glossary" in md


def test_generate_s11_lists_all_six_sources():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    # All 6 data sources present
    for source_token in ["IRC TCC", "IRC certificate", "ORC", "SailSys", "TopYacht", "RHKYC"]:
        assert source_token in result.markdown


def test_generate_s11_lists_twelve_glossary_terms():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    # Count **TERM** boundaries — 12 expected
    bolded = result.markdown.count("**TCC**") + result.markdown.count("**IRC**") + \
             result.markdown.count("**ORC**") + result.markdown.count("**RAI**") + \
             result.markdown.count("**Tier A / B / C**") + result.markdown.count("**R²**") + \
             result.markdown.count("**β (beta)**") + result.markdown.count("**Standardised β**") + \
             result.markdown.count("**Class median**") + result.markdown.count("**Percentile rank**") + \
             result.markdown.count("**Head-to-head**") + result.markdown.count("**Drift**")
    assert bolded == 12
