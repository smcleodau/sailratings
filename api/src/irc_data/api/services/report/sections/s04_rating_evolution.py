"""Section 4 — Rating Evolution."""
from __future__ import annotations

import json
import logging

from sqlalchemy.engine import Engine

from irc_data.api.services.report.charts import render_tcc_timeseries
from irc_data.api.services.report.claude_client import audit_section_numbers, call_claude
from irc_data.api.services.report.facts_builders import build_rating_evolution
from irc_data.api.services.report.prompts import RATING_EVOLUTION_PROMPT, SYSTEM_PROMPT_V2
from irc_data.api.services.report.sections._base import SectionResult
from irc_data.api.services.report.sections.s03_rating_anatomy import _facts_to_jsonable

logger = logging.getLogger(__name__)
SECTION_ID = "s04_rating_evolution"
SECTION_TITLE = "Rating Evolution"


def generate(engine: Engine, boat_id: int) -> SectionResult:
    facts = build_rating_evolution(engine, boat_id)
    if not facts.snapshots:
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error="no rating history on file",
        )
    facts_json = json.dumps(_facts_to_jsonable(facts), indent=2, default=str)
    user_msg = RATING_EVOLUTION_PROMPT.format(
        first_tcc=facts.first_snapshot_tcc,
        latest_tcc=facts.latest_snapshot_tcc,
        total_movement=round(facts.total_movement, 4),
        n_snapshots=len(facts.snapshots),
        facts_json=facts_json,
    )
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
    chart_png = render_tcc_timeseries(facts)
    return SectionResult(
        section_id=SECTION_ID, title=SECTION_TITLE,
        markdown=markdown,
        chart_pngs={"tcc_timeseries": chart_png},
        structured={"facts": _facts_to_jsonable(facts), "audit": audit},
        error=None,
    )
