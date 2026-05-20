"""Section 3 — Rating Anatomy: why this boat rates what it does."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict

from sqlalchemy.engine import Engine

from irc_data.api.services.report.charts import render_anatomy_bar
from irc_data.api.services.report.claude_client import audit_section_numbers, call_claude
from irc_data.api.services.report.facts_builders import build_rating_anatomy
from irc_data.api.services.report.prompts import RATING_ANATOMY_PROMPT, SYSTEM_PROMPT_V2
from irc_data.api.services.report.sections._base import SectionResult

logger = logging.getLogger(__name__)
SECTION_ID = "s03_rating_anatomy"
SECTION_TITLE = "Rating Anatomy"


def generate(engine: Engine, boat_id: int) -> SectionResult:
    facts = build_rating_anatomy(engine, boat_id)

    # Empty Facts → skip Claude; emit a "no data" placeholder.
    if not facts.decomposition or facts.tcc_now == 0:
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="",
            chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error="no decomposition available — boat lacks TCC or design class",
        )

    facts_json = json.dumps(_facts_to_jsonable(facts), indent=2, default=str)
    user_msg = RATING_ANATOMY_PROMPT.format(
        tcc_now=facts.tcc_now, design=facts.boat_name,
        class_median_tcc=facts.class_median_tcc,
        delta=round(float(facts.tcc_now) - (facts.class_median_tcc or 0), 4),
        facts_json=facts_json,
    )

    try:
        markdown = call_claude(
            system=SYSTEM_PROMPT_V2, user=user_msg,
            max_tokens=2500, section_id=SECTION_ID,
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
    chart_png = render_anatomy_bar(facts)

    return SectionResult(
        section_id=SECTION_ID, title=SECTION_TITLE,
        markdown=markdown,
        chart_pngs={"anatomy_bar": chart_png},
        structured={"facts": _facts_to_jsonable(facts), "audit": audit},
        error=None,
    )


def _facts_to_jsonable(facts) -> dict:
    """Convert dataclass tree to JSON-serialisable form (handles Decimal)."""
    def _conv(v):
        from decimal import Decimal
        if isinstance(v, Decimal):
            return float(v)
        if hasattr(v, "__dataclass_fields__"):
            return {k: _conv(getattr(v, k)) for k in v.__dataclass_fields__}
        if isinstance(v, list):
            return [_conv(x) for x in v]
        if isinstance(v, dict):
            return {k: _conv(x) for k, x in v.items()}
        return v
    return _conv(facts)
