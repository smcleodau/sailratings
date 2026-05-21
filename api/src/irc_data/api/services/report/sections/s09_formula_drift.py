"""Section 9 — Formula Drift (no chart for v1)."""
from __future__ import annotations

import json
import logging

from sqlalchemy.engine import Engine

from irc_data.api.services.report.claude_client import audit_section_numbers, call_claude
from irc_data.api.services.report.facts_builders import build_formula_drift
from irc_data.api.services.report.prompts import FORMULA_DRIFT_PROMPT, SYSTEM_PROMPT_V2
from irc_data.api.services.report.sections._base import SectionResult
from irc_data.api.services.report.sections.s03_rating_anatomy import _facts_to_jsonable

logger = logging.getLogger(__name__)
SECTION_ID = "s09_formula_drift"
SECTION_TITLE = "Formula Drift"


def generate(engine: Engine, boat_id: int) -> SectionResult:
    facts = build_formula_drift(engine, boat_id)
    if not facts.design:
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error="no drift analysis — boat lacks design",
        )

    facts_json = json.dumps(_facts_to_jsonable(facts), indent=2, default=str)
    user_msg = FORMULA_DRIFT_PROMPT.format(facts_json=facts_json)

    try:
        markdown = call_claude(
            system=SYSTEM_PROMPT_V2, user=user_msg,
            max_tokens=800, section_id=SECTION_ID,
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
    return SectionResult(
        section_id=SECTION_ID, title=SECTION_TITLE,
        markdown=markdown, chart_pngs={},
        structured={"facts": _facts_to_jsonable(facts), "audit": audit},
        error=None,
    )
