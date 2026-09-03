"""ReportFactsV1 contract tests — pure, no database required."""
from __future__ import annotations

import json

from irc_data.api.services.report.facts import (
    ExecutiveSummaryFacts,
    RatingAnatomyFacts,
)
from irc_data.api.services.report.facts_bundle import (
    SCHEMA_VERSION,
    _normalise,
    bundle_to_json,
    validate_report_facts_bundle,
)


def _minimal_bundle() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "boat": {"id": 1, "name": "X", "sail_number": "X1", "design": "D"},
        "sections": {s: {} for s in (
            "s01_executive", "s02_identity", "s03_rating_anatomy",
            "s04_rating_evolution", "s05_class_context", "s06_performance",
            "s07_sensitivity", "s08_optimisation", "s09_formula_drift",
            "s10_rivals", "s11_appendix",
        )},
        "engines": {},
        "facts_sha256": "a" * 64,
    }


def test_schema_version_constant():
    # The narrative generator (AI-01-06) pins this string; bumping it is a
    # breaking change and must be a deliberate act.
    assert SCHEMA_VERSION == "ReportFactsV1"


def test_validate_accepts_minimal_bundle():
    assert validate_report_facts_bundle(_minimal_bundle()) == []


def test_validate_flags_bad_version_and_missing_sections():
    bundle = _minimal_bundle()
    bundle["schema_version"] = "ReportFactsV0"
    del bundle["sections"]["s03_rating_anatomy"]
    bundle["sections"]["s06_performance"] = "not-a-dict"
    bundle["facts_sha256"] = "short"
    violations = validate_report_facts_bundle(bundle)
    assert any("schema_version" in v for v in violations)
    assert any("s03_rating_anatomy" in v for v in violations)
    assert any("s06_performance" in v for v in violations)
    assert any("facts_sha256" in v for v in violations)


def test_normalise_rounds_floats_and_handles_decimals_dates():
    from datetime import date
    from decimal import Decimal

    facts = ExecutiveSummaryFacts(
        boat_name="TEST", sail_number="T1", design="D", country=None,
        tcc_now=Decimal("1.0260"), class_median_tcc=1.0123456789,
        this_boat_percentile=62.5, finishes=44, wins=3, podiums=4,
        headline_finding_1="a", headline_finding_2="b", headline_finding_3="c",
        top_recommendation=None,
    )
    data = _normalise(facts)
    assert data["tcc_now"] == 1.026
    assert data["class_median_tcc"] == round(1.0123456789, 6)
    assert data["finishes"] == 44

    # dates become ISO strings inside nested dataclasses
    nested = _normalise(RatingAnatomyFacts(
        boat_name="T", tcc_now=Decimal("1.0"), class_mean_tcc=None,
        class_median_tcc=None,
    ))
    assert nested["decomposition"] == []
    assert _normalise(date(2026, 6, 1)) == "2026-06-01"


def test_bundle_json_is_canonical():
    b1 = _minimal_bundle()
    b2 = json.loads(bundle_to_json(b1))
    assert bundle_to_json(b1) == bundle_to_json(b2)
