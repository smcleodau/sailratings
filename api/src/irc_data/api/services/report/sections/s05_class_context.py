"""Section 5 — Class Context."""
from __future__ import annotations

import json
import logging

from sqlalchemy.engine import Engine

from irc_data.api.services.report.charts import render_class_distribution
from irc_data.api.services.report.claude_client import audit_section_numbers, call_claude
from irc_data.api.services.report.facts_builders import build_class_context
from irc_data.api.services.report.prompts import CLASS_CONTEXT_PROMPT, SYSTEM_PROMPT_V2
from irc_data.api.services.report.sections._base import SectionResult
from irc_data.api.services.report.sections.s03_rating_anatomy import _facts_to_jsonable

logger = logging.getLogger(__name__)
SECTION_ID = "s05_class_context"
SECTION_TITLE = "Class Context"


def generate(engine: Engine, boat_id: int) -> SectionResult:
    facts = build_class_context(engine, boat_id)
    if not facts.design or facts.class_n == 0:
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error="no class context — boat lacks design",
        )
    facts_json = json.dumps(_facts_to_jsonable(facts), indent=2, default=str)
    user_msg = CLASS_CONTEXT_PROMPT.format(facts_json=facts_json)
    try:
        markdown = call_claude(system=SYSTEM_PROMPT_V2, user=user_msg,
                               max_tokens=900, section_id=SECTION_ID)
    except Exception as e:
        logger.error("section %s Claude call failed: %s", SECTION_ID, e)
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error=f"claude call failed: {e}",
        )
    audit = audit_section_numbers(markdown, facts, section_id=SECTION_ID)
    chart_png = render_class_distribution(facts, facts.class_tcc_list)
    return SectionResult(
        section_id=SECTION_ID, title=SECTION_TITLE,
        markdown=markdown,
        chart_pngs={"class_distribution": chart_png},
        structured={"facts": _facts_to_jsonable(facts), "audit": audit},
        error=None,
    )
