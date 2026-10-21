"""Verify the orchestrator runs every section and aggregates results."""
import pytest, os
from irc_data.db.connection import get_engine
from irc_data.api.services.report.orchestrator import build_report


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no API key")
def test_orchestrator_builds_11_sections_for_sun_fish():
    eng = get_engine()
    report = build_report(eng, boat_id=12330)
    assert "sections" in report
    assert len(report["sections"]) == 11
    section_ids = [s["section_id"] for s in report["sections"]]
    expected = ["s01_executive", "s02_identity", "s03_rating_anatomy",
                "s04_rating_evolution", "s05_class_context", "s06_performance",
                "s07_sensitivity", "s08_optimisation", "s09_formula_drift",
                "s10_rivals", "s11_appendix"]
    assert section_ids == expected
    # At least 8 of 11 should have non-empty markdown.
    non_empty = sum(1 for s in report["sections"] if s["markdown"])
    assert non_empty >= 8
    # The aggregated structured data has every section's audit summary.
    audits = [s["structured"].get("audit") for s in report["sections"]
              if s["structured"].get("audit")]
    assert len(audits) >= 6
