"""Section 7 — Measurement Sensitivity (fleet-wide regression view)."""
from __future__ import annotations

import json
import logging

from sqlalchemy.engine import Engine

from irc_data.api.services.report.charts import render_sensitivity_bar
from irc_data.api.services.report.claude_client import audit_section_numbers, call_claude
from irc_data.api.services.report.facts_builders import build_sensitivity
from irc_data.api.services.report.prompts import SENSITIVITY_PROMPT, SYSTEM_PROMPT_V2
from irc_data.api.services.report.sections._base import SectionResult
from irc_data.api.services.report.sections.s03_rating_anatomy import _facts_to_jsonable

logger = logging.getLogger(__name__)
SECTION_ID = "s07_sensitivity"
SECTION_TITLE = "Measurement Sensitivity"


def generate(engine: Engine, boat_id: int) -> SectionResult:
    facts = build_sensitivity(engine, boat_id)
    if not facts.coefficients:
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error="no sensitivity model available",
        )

    facts_json = json.dumps(_facts_to_jsonable(facts), indent=2, default=str)
    user_msg = SENSITIVITY_PROMPT.format(
        design=facts.design,
        r_squared_pct=round(facts.r_squared * 100, 1),
        n_boats=facts.n_boats_in_class,
        model_tier=facts.model_tier,
        facts_json=facts_json,
    )

    try:
        markdown = call_claude(
            system=SYSTEM_PROMPT_V2, user=user_msg,
            max_tokens=900, section_id=SECTION_ID,
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
    chart_png = render_sensitivity_bar(facts)

    return SectionResult(
        section_id=SECTION_ID, title=SECTION_TITLE,
        markdown=markdown,
        chart_pngs={"sensitivity_bar": chart_png},
        structured={"facts": _facts_to_jsonable(facts), "audit": audit},
        error=None,
    )
