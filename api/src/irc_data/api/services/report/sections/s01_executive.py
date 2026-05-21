"""Section 1 — Executive Summary."""
from __future__ import annotations

import json
import logging

from sqlalchemy.engine import Engine

from irc_data.api.services.report.claude_client import audit_section_numbers, call_claude
from irc_data.api.services.report.facts_builders import build_executive_summary
from irc_data.api.services.report.prompts import EXECUTIVE_SUMMARY_PROMPT, SYSTEM_PROMPT_V2
from irc_data.api.services.report.sections._base import SectionResult
from irc_data.api.services.report.sections.s03_rating_anatomy import _facts_to_jsonable

logger = logging.getLogger(__name__)
SECTION_ID = "s01_executive"
SECTION_TITLE = "Executive Summary"


def generate(engine: Engine, boat_id: int) -> SectionResult:
    facts = build_executive_summary(engine, boat_id)
    if not facts.headline_finding_1:
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error="no findings available — boat data sparse",
        )
    facts_json = json.dumps(_facts_to_jsonable(facts), indent=2, default=str)
    user_msg = EXECUTIVE_SUMMARY_PROMPT.format(facts_json=facts_json)
    try:
        markdown = call_claude(system=SYSTEM_PROMPT_V2, user=user_msg,
                               max_tokens=600, section_id=SECTION_ID)
    except Exception as e:
        logger.error("section %s Claude call failed: %s", SECTION_ID, e)
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error=f"claude call failed: {e}",
        )
    audit = audit_section_numbers(markdown, facts, section_id=SECTION_ID)
    return SectionResult(
        section_id=SECTION_ID, title=SECTION_TITLE,
        markdown=markdown, chart_pngs={},
        structured={"facts": _facts_to_jsonable(facts), "audit": audit},
        error=None,
    )
