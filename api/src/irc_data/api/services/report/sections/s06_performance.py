"""Section 6 — Racing Performance."""
from __future__ import annotations

import json
import logging

from sqlalchemy.engine import Engine

from irc_data.api.services.report.charts import render_results_timeline
from irc_data.api.services.report.claude_client import audit_section_numbers, call_claude
from irc_data.api.services.report.facts_builders import build_performance
from irc_data.api.services.report.prompts import PERFORMANCE_PROMPT, SYSTEM_PROMPT_V2
from irc_data.api.services.report.sections._base import SectionResult
from irc_data.api.services.report.sections.s03_rating_anatomy import _facts_to_jsonable

logger = logging.getLogger(__name__)
SECTION_ID = "s06_performance"
SECTION_TITLE = "Racing Performance"


def generate(engine: Engine, boat_id: int) -> SectionResult:
    facts = build_performance(engine, boat_id)
    if facts.finishes == 0:
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error="no race results on file",
        )

    facts_json = json.dumps(_facts_to_jsonable(facts), indent=2, default=str)
    user_msg = PERFORMANCE_PROMPT.format(facts_json=facts_json)

    try:
        markdown = call_claude(
            system=SYSTEM_PROMPT_V2, user=user_msg,
            max_tokens=1500, section_id=SECTION_ID,
        )
    except Exception as e:
        logger.error("section %s Claude call failed: %s", SECTION_ID, e)
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error=f"claude call failed: {e}",
        )

    audit = audit_section_numbers(markdown, facts, section_id=SECTION_ID)
    chart_png = render_results_timeline(facts)

    return SectionResult(
        section_id=SECTION_ID, title=SECTION_TITLE,
        markdown=markdown,
        chart_pngs={"results_timeline": chart_png},
        structured={"facts": _facts_to_jsonable(facts), "audit": audit},
        error=None,
    )
