"""Section 11 — Appendix.

Deterministic markdown rendered straight from AppendixFacts — no
LLM call, no chart, no truth-discipline scan needed."""
from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from irc_data.api.services.report.facts_builders import build_appendix
from irc_data.api.services.report.sections._base import SectionResult
from irc_data.api.services.report.sections.s03_rating_anatomy import _facts_to_jsonable

logger = logging.getLogger(__name__)
SECTION_ID = "s11_appendix"
SECTION_TITLE = "Appendix"


def generate(engine: Engine, boat_id: int) -> SectionResult:
    facts = build_appendix(engine, boat_id)
    md_parts: list[str] = []
    md_parts.append("## Methodology")
    md_parts.append("")
    md_parts.append(facts.methodology_blurb)
    md_parts.append("")
    md_parts.append("## Data sources")
    md_parts.append("")
    for src in facts.data_sources:
        md_parts.append(f"- {src}")
    md_parts.append("")
    md_parts.append("## Glossary")
    md_parts.append("")
    for term, definition in facts.glossary:
        md_parts.append(f"**{term}** — {definition}")
        md_parts.append("")
    markdown = "\n".join(md_parts)
    return SectionResult(
        section_id=SECTION_ID, title=SECTION_TITLE,
        markdown=markdown, chart_pngs={},
        structured={"facts": _facts_to_jsonable(facts)},
        error=None,
    )
