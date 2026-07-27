"""Tests for the Sailing AI domain-expert prompts and knowledge base.

Covers the acceptance criteria in ``docs/specs/SPEC-08-Sailing-AI.md``:

* the domain knowledge base exists and thoroughly covers Handicapping &
  Measurement, Weather/Routing & Polars, and Race Tactics & Maneuvers;
* :data:`SYSTEM_PROMPT_EXPERT` adopts the "Technical Expert & Tactician"
  persona;
* the prompt enforces the "Empirical Deduction" rule (IRC is a secret
  black box; never claim to know the formula);
* the prompt reiterates the "Facts Contract" (only cite numbers in the input;
  never invent statistics).

These are pure string/file tests — no database or API key required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from irc_data.api.services.report import prompts as report_prompts
from irc_data.api.services.report.prompts import (
    DEFAULT_KNOWLEDGE_REL_PATH,
    DOMAIN_KNOWLEDGE_FILENAME,
    SYSTEM_PROMPT_EXPERT,
    build_expert_system_prompt,
    load_domain_knowledge,
)

# Repository root: this module lives at <repo>/api/tests/test_sailing_prompts.py
REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_PATH = REPO_ROOT / Path(*DEFAULT_KNOWLEDGE_REL_PATH)


# ---------------------------------------------------------------------------
# Knowledge base file
# ---------------------------------------------------------------------------

def test_knowledge_base_file_exists_at_specified_path():
    """SPEC-08 §2 requires docs/domain/sailing-knowledge.md to exist."""
    assert KNOWLEDGE_PATH.is_file(), f"Expected knowledge base at {KNOWLEDGE_PATH}"
    assert KNOWLEDGE_PATH.name == DOMAIN_KNOWLEDGE_FILENAME


def test_knowledge_base_is_substantial():
    """The knowledge base must be a substantial, thorough document."""
    content = KNOWLEDGE_PATH.read_text(encoding="utf-8")
    # A rigorous domain KB is many thousands of characters.
    assert len(content) > 5000, "Knowledge base is too short to be thorough"
    assert content.count("\n") > 60, "Knowledge base should have real structure"


# ---------------------------------------------------------------------------
# Knowledge base coverage — SPEC-08 §2.1 Handicapping & Measurement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "term",
    [
        "IRC",          # secret, empirical rule
        "ORC",          # open, VPP-based rule
        "TCC",          # Time Correction Coefficient
        "GPH",          # General Purpose Handicap
        "DLR",          # Displacement-Length Ratio
        "SA/D",         # Sail Area to Displacement Ratio
        "HLU",          # Headsail Luff
        "HLP",          # Headsail Perpendicular
        "SPA",          # Spinnaker Area
        "\nP",          # mainsail hoist (match on its own token-ish)
        "E",            # mainsail foot
        "J",            # foretriangle base
        "FL",           # foretriangle height
    ],
)
def test_knowledge_covers_handicapping_and_measurement_terms(term):
    content = KNOWLEDGE_PATH.read_text(encoding="utf-8")
    assert term in content, f"Knowledge base missing required term: {term!r}"


def test_knowledge_explains_irc_vs_orc_philosophies():
    """IRC must be described as secret/empirical; ORC as open/VPP-based."""
    content = KNOWLEDGE_PATH.read_text(encoding="utf-8")
    lower = content.lower()
    assert "secret" in lower and "empirical" in lower, (
        "IRC secrecy/empirical nature must be explained"
    )
    assert "vpp" in lower, "ORC VPP basis must be explained"
    assert "black box" in lower, "IRC must be framed as a secret black box"


def test_knowledge_explains_how_displacement_and_draft_affect_rating():
    """SPEC-08 §2.1: explain how changes in displacement/draft affect a rating."""
    content = KNOWLEDGE_PATH.read_text(encoding="utf-8").lower()
    assert "displacement" in content
    assert "draft" in content
    # Both directions of effect should be discussed.
    assert ("favourable" in content or "favorable" in content) and (
        "cost" in content or "costs" in content or "penal" in content
    ), "Knowledge base must explain rating effects of displacement & draft"


# ---------------------------------------------------------------------------
# Knowledge base coverage — SPEC-08 §2.2 Weather, Routing & Polars
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "term",
    ["VPP", "polar", "TWS", "TWA", "AWS", "AWA", "VMG"],
)
def test_knowledge_covers_weather_routing_and_polars(term):
    content = KNOWLEDGE_PATH.read_text(encoding="utf-8")
    assert term in content, f"Knowledge base missing required term: {term!r}"


def test_knowledge_explains_true_vs_apparent_wind():
    content = KNOWLEDGE_PATH.read_text(encoding="utf-8").lower()
    assert "true wind speed" in content and "apparent wind speed" in content
    assert "true wind angle" in content and "apparent wind angle" in content


def test_knowledge_covers_target_boat_speeds():
    content = KNOWLEDGE_PATH.read_text(encoding="utf-8").lower()
    assert "target" in content, "Target boat speeds must be covered"


# ---------------------------------------------------------------------------
# Knowledge base coverage — SPEC-08 §2.3 Race Tactics & Maneuvers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "term",
    ["layline", "mark round", "cover", "header", "lift"],
)
def test_knowledge_covers_core_tactics(term):
    content = KNOWLEDGE_PATH.read_text(encoding="utf-8").lower()
    assert term in content, f"Knowledge base missing tactic: {term!r}"


def test_knowledge_covers_sail_selection_nuances():
    """A0 vs A2 spinnaker, Code Zero vs Jib."""
    content = KNOWLEDGE_PATH.read_text(encoding="utf-8")
    assert "A0" in content and "A2" in content, "A0 vs A2 must be covered"
    assert "Code Zero" in content, "Code Zero must be covered"
    assert "jib" in content.lower(), "Jib must be covered"


# ---------------------------------------------------------------------------
# System prompt persona — SPEC-08 §3
# ---------------------------------------------------------------------------

def test_system_prompt_adopts_technical_expert_tactician_persona():
    """SPEC-08 §3: persona is the 'Technical Expert & Tactician'."""
    assert "Technical Expert" in SYSTEM_PROMPT_EXPERT
    assert "Tactician" in SYSTEM_PROMPT_EXPERT


def test_system_prompt_uses_proper_terminology():
    """Persona must use proper terminology (wetted surface, form stability, VPP).

    Terms may wrap across line breaks in the multi-line prompt, so collapse
    whitespace before matching.
    """
    import re

    collapsed = re.sub(r"\s+", " ", SYSTEM_PROMPT_EXPERT).lower()
    assert "wetted surface" in collapsed
    assert "form stability" in collapsed
    assert "vpp" in collapsed


def test_system_prompt_is_authoritative_and_accessible():
    """Tone: authoritative/analytical but final prose accessible & actionable."""
    lower = SYSTEM_PROMPT_EXPERT.lower()
    assert "authoritative" in lower
    assert "accessible" in lower
    assert "actionable" in lower


# ---------------------------------------------------------------------------
# Empirical Deduction rule — SPEC-08 §3 (CRITICAL)
# ---------------------------------------------------------------------------

def test_system_prompt_enforces_empirical_deduction_rule():
    """The IRC formula is a secret black box; never claim to know it."""
    lower = SYSTEM_PROMPT_EXPERT.lower()
    assert "empirical deduction" in lower
    assert "secret" in lower and "black box" in lower
    # Must forbid claiming to know the formula/coefficients.
    assert "never claim to know" in lower
    assert "coefficient" in lower


def test_system_prompt_forbids_formula_mechanics_language():
    """Must ban 'the IRC formula penalises X' style claims."""
    assert "the IRC formula penalises" in SYSTEM_PROMPT_EXPERT
    lower = SYSTEM_PROMPT_EXPERT.lower()
    assert "never say" in lower or "never" in lower
    # And direct the model to empirical framing.
    assert "statistical analysis of the fleet" in SYSTEM_PROMPT_EXPERT


# ---------------------------------------------------------------------------
# Facts Contract — SPEC-08 §3
# ---------------------------------------------------------------------------

def test_system_prompt_reiterates_facts_contract():
    """Only cite numbers provided in the input; never invent statistics."""
    lower = SYSTEM_PROMPT_EXPERT.lower()
    assert "facts contract" in lower
    assert "only cite numbers" in lower
    assert "never invent statistics" in lower


def test_system_prompt_forbids_fabrication():
    lower = SYSTEM_PROMPT_EXPERT.lower()
    assert "fabricat" in lower or "invent" in lower
    assert "never" in lower


def test_system_prompt_sets_precision_conventions():
    """TCC four decimals, GPH two — matches the data contract."""
    content = SYSTEM_PROMPT_EXPERT
    assert "four decimals" in content.lower() or "four decimal" in content.lower()
    assert "two decimals" in content.lower() or "two decimal" in content.lower()


# ---------------------------------------------------------------------------
# Knowledge loader & prompt assembly
# ---------------------------------------------------------------------------

def test_load_domain_knowledge_returns_content():
    content = load_domain_knowledge()
    assert isinstance(content, str)
    assert "sailing" in content.lower()
    assert len(content) > 1000


def test_load_domain_knowledge_is_cached():
    """The loader is memoised so repeated calls don't re-read the file."""
    # Clear any cache from earlier tests/import.
    load_domain_knowledge.cache_clear()
    first = load_domain_knowledge()
    second = load_domain_knowledge()
    assert first is second  # cached -> identical object
    info = load_domain_knowledge.cache_info()
    assert info.hits >= 1


def test_load_domain_knowledge_raises_helpful_error_when_missing(tmp_path, monkeypatch):
    """When the file can't be found anywhere, raise with the tried paths."""
    load_domain_knowledge.cache_clear()
    # Force every probe location to be a non-existent path under tmp_path.
    bogus = tmp_path / "does-not-exist.md"
    monkeypatch.setenv("SAILRATINGS_DOMAIN_KNOWLEDGE_PATH", str(bogus))
    # Make CWD-based probe also miss.
    monkeypatch.chdir(tmp_path)
    # Monkeypatch the path candidate function so ONLY bogus locations are tried,
    # guaranteeing a miss regardless of where the repo actually lives.
    monkeypatch.setattr(
        report_prompts,
        "_candidate_knowledge_paths",
        lambda: [bogus, tmp_path / "also-missing.md"],
    )
    with pytest.raises(FileNotFoundError) as exc:
        load_domain_knowledge()
    assert "sailing-knowledge.md" in str(exc.value)
    assert "does-not-exist.md" in str(exc.value)
    load_domain_knowledge.cache_clear()


def test_build_expert_system_prompt_includes_persona_and_knowledge():
    """The full prompt concatenates persona + knowledge base."""
    full = build_expert_system_prompt()
    assert full.startswith(SYSTEM_PROMPT_EXPERT)
    knowledge = load_domain_knowledge()
    assert knowledge in full
    assert "Foundational domain knowledge base" in full


def test_build_expert_system_prompt_persona_only():
    """include_knowledge=False returns just the persona rules."""
    persona_only = build_expert_system_prompt(include_knowledge=False)
    assert persona_only == SYSTEM_PROMPT_EXPERT
    knowledge = load_domain_knowledge()
    assert knowledge not in persona_only


def test_env_override_resolves_alternative_knowledge_file(tmp_path, monkeypatch):
    """An env-var override path is honoured ahead of the repo default."""
    alt = tmp_path / "alt-knowledge.md"
    alt.write_text("# ALT KNOWLEDGE MARKER\n\nCustom fleet notes.\n", encoding="utf-8")
    monkeypatch.setenv("SAILRATINGS_DOMAIN_KNOWLEDGE_PATH", str(alt))
    load_domain_knowledge.cache_clear()
    try:
        content = load_domain_knowledge()
        assert "ALT KNOWLEDGE MARKER" in content
    finally:
        load_domain_knowledge.cache_clear()
