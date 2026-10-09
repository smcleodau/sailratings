"""AI-01-01 — sailing knowledge pack: quiz + structural checks.

The quiz answers 20 factual questions by string lookup in the pack (no LLM,
no network, no database). If a reviewer edits a fact in the pack, the matching
question fails and the edit has to be a conscious one.

24 tests: 20 quiz + 4 structural (every file has a Sources section, glossary
parses with the right shape, glossary has >= 60 terms, loader returns every
section).
"""
from __future__ import annotations

import re

import pytest

from irc_data.api.services.report import knowledge

EXPECTED_SECTIONS = 8
MIN_GLOSSARY_TERMS = 60


def _plain(text: str) -> str:
    """Strip markdown emphasis/code ticks and collapse whitespace; lower-case."""
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text).lower()


# (section number, question, substring that must appear in that section)
QUIZ = [
    (1, "What does TCC stand for?", "Time Correction Coefficient"),
    (1, "Which French body co-manages IRC with the RORC?", "UNCL"),
    (1, "What unit is a PHRF rating in?", "seconds per mile"),
    (3, "What does GPH stand for?", "General Purpose Handicap"),
    (3, "What unit is GPH measured in?", "seconds per nautical mile"),
    (3, "What does CDL stand for?", "Class Division Length"),
    (3, "How many coefficients make an ORC Triple Number?", "three time-on-time coefficients"),
    (3, "Which two standard ORC course types are tabulated in the allowances?", "windward-leeward"),
    (2, "What is HLP on an IRC certificate?", "luff perpendicular"),
    (2, "What is the P measurement?", "Mainsail luff length"),
    (2, "What does the non-spinnaker TCC apply to?", "without a spinnaker"),
    (2, "What does AVS stand for?", "Angle of vanishing stability"),
    (4, "When does a new IRC Rule year take effect?", "1 January"),
    (4, "By how much must a regression coefficient change for a dimension to count as affected by drift?", "more than 0.2"),
    (5, "How big is one IRC rating point?", "0.001 TCC"),
    (5, "How many seconds per hour is one rating point worth?", "3.6 seconds per hour"),
    (5, "How many seconds per hour separate TCC 1.025 and 1.020?", "18 seconds per hour"),
    (5, "Who wins on corrected time?", "lowest corrected time wins"),
    (5, "What is the IRC corrected-time formula?", "elapsed × TCC"),
    (7, "Which Racing Rules of Sailing rule governs changes to class rules?", "Rule 87"),
]


assert len(QUIZ) == 20, "the quiz is a contract of exactly 20 Q/A pairs"


@pytest.mark.parametrize(
    "section,question,answer",
    QUIZ,
    ids=[q[1][:48] for q in QUIZ],
)
def test_quiz(section: int, question: str, answer: str):
    pack = _plain(knowledge.load_knowledge([section]))
    assert _plain(answer) in pack, f"Q: {question!r} — {answer!r} not found in section {section:02d}"


def test_every_file_has_sources_section():
    files = knowledge.section_files()
    assert len(files) == EXPECTED_SECTIONS
    for path in files:
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^## Sources\s*$(.*)", text, flags=re.M | re.S)
        assert match, f"{path.name} has no '## Sources' section"
        bullets = [ln for ln in match.group(1).splitlines() if ln.lstrip().startswith("- ")]
        assert bullets, f"{path.name} Sources section lists nothing"


def test_glossary_parses_with_required_fields():
    data = knowledge.load_glossary()
    assert isinstance(data, dict) and isinstance(data["terms"], list)
    for entry in data["terms"]:
        assert set(entry) == {"term", "definition", "unit", "aliases", "source"}, entry.get("term")
        assert entry["term"] and entry["definition"] and entry["source"], entry["term"]
        assert isinstance(entry["aliases"], list), entry["term"]
    names = [e["term"].lower() for e in data["terms"]]
    assert len(names) == len(set(names)), "duplicate glossary terms"


def test_glossary_has_at_least_60_terms():
    assert len(knowledge.load_glossary()["terms"]) >= MIN_GLOSSARY_TERMS


def test_loader_returns_every_section():
    full = knowledge.load_knowledge()
    assert full is knowledge.load_knowledge()  # cached
    files = knowledge.section_files()
    assert len(files) == EXPECTED_SECTIONS
    for path in files:
        number = int(path.name[:2])
        body = knowledge.load_knowledge([number])
        assert body.startswith("# "), path.name
        assert body.strip() in full, path.name
    # selecting by full stem works and request order does not change output order
    assert knowledge.load_knowledge(["02-irc-certificate-anatomy", 1]) == knowledge.load_knowledge([1, 2])
    with pytest.raises(KeyError):
        knowledge.load_knowledge([99])
