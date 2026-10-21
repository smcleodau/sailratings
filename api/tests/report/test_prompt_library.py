"""Prompt library (AI-01-02): snapshots + structural invariants.

Each persona's built system prompt is stored under ``tests/report/snapshots``.
Any change to irc_data/prompts/*.md or the knowledge pack changes the built prompt and
fails the matching snapshot until a human reviews the diff and regenerates:

    UPDATE_PROMPT_SNAPSHOTS=1 pytest tests/report/test_prompt_library.py -q
    git diff tests/report/snapshots     # review every changed line
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from irc_data.api.services.report import prompt_library as pl
from irc_data.api.services.report.knowledge import load_knowledge
from irc_data.api.services.report.prompt_library import PERSONAS, build_system_prompt

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


@pytest.mark.parametrize("persona", PERSONAS)
def test_persona_snapshot(persona):
    built = build_system_prompt(persona)
    snap = SNAPSHOT_DIR / f"{persona}.md"
    if os.environ.get("UPDATE_PROMPT_SNAPSHOTS") == "1":
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        snap.write_text(built, encoding="utf-8")
    assert snap.is_file(), (
        f"No snapshot for persona {persona!r}. Review the prompt, then run "
        "UPDATE_PROMPT_SNAPSHOTS=1 pytest tests/report/test_prompt_library.py"
    )
    assert built == snap.read_text(encoding="utf-8"), (
        f"Built prompt for {persona!r} differs from {snap.name}. If the change is "
        "intended, review the diff and regenerate with UPDATE_PROMPT_SNAPSHOTS=1."
    )


def test_guardrails_present_in_every_persona():
    # seven persona overlays on disk, one per registered persona
    on_disk = sorted(p.stem for p in (pl.prompts_dir() / "personas").glob("*.md"))
    assert on_disk == sorted(PERSONAS) and len(on_disk) == 7
    guardrails = pl.guardrails_text()
    assert "Never an official rating" in guardrails
    for persona in PERSONAS:
        assert guardrails in build_system_prompt(persona), persona
    # the teaser variant shares the same guardrails
    assert guardrails in build_system_prompt("analyst", variant="teaser")


def test_base_referenced_once_per_prompt():
    base = pl.base_text()
    for persona in PERSONAS:
        prompt = build_system_prompt(persona)
        assert prompt.count(base) == 1, persona
        assert prompt.count("# SailRatings — base prompt") == 1, persona
        assert prompt.index(base) < prompt.index(pl.guardrails_text()), persona
    assert "KNOWLEDGE PACK" in base


def test_knowledge_section_injected_and_header_sha_stable():
    prompt = build_system_prompt("analyst")
    assert load_knowledge() in prompt
    assert prompt.count("# KNOWLEDGE PACK") == 1
    # restricted persona gets only its sections
    race = build_system_prompt("race_prep")
    assert load_knowledge([1, 4, 5, 6, 7, 8]) in race
    assert load_knowledge([3]) not in race
    # header comment carries a sha of the body, stable across rebuilds
    header = prompt.splitlines()[0]
    assert header.startswith("<!-- prompt-library persona=analyst sha=") and header.endswith("-->")
    pl.clear_cache()
    assert build_system_prompt("analyst") == prompt
    assert build_system_prompt("analyst") is build_system_prompt("analyst")  # cached
    # teaser variant is a distinct overlay, not a persona
    teaser = build_system_prompt("analyst", variant="teaser")
    assert teaser != prompt and "exactly four paragraphs" in teaser
    with pytest.raises(KeyError):
        build_system_prompt("nobody")
    with pytest.raises(KeyError):
        build_system_prompt("watch", variant="teaser")


def test_alias_system_prompt_v2_is_report_writer():
    from irc_data.api.services.report.prompts import SYSTEM_PROMPT_V2

    assert SYSTEM_PROMPT_V2 == build_system_prompt("report_writer")
    assert "NEVER cite a number that is not in the provided FACTS payload" in SYSTEM_PROMPT_V2


def test_alias_system_prompt_premium_is_analyst():
    from irc_data.api.services import insights_service as ins

    assert ins.SYSTEM_PROMPT_PREMIUM == build_system_prompt("analyst")
    assert ins.SYSTEM_PROMPT_TEASER == build_system_prompt("analyst", variant="teaser")
    assert ins.SYSTEM_PROMPT is ins.SYSTEM_PROMPT_TEASER
