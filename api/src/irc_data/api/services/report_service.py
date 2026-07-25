"""Report content generation service.

Generates the full premium report content (markdown + structured analytics)
for a paid order. This is the content engine — no formatting or presentation.
"""

import logging
import os
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def generate_report_content(engine: Engine, order_id: int) -> None:
    """Generate report content for a paid order.

    1. Build premium context → send to Claude → store as report_markdown
    2. Fetch structured analytics → store as report_analytics JSONB
    3. Update order status to 'generated'
    """
    # V2 is the default; set REPORT_V2=false to fall back to the legacy single-Claude-call path.
    if os.environ.get("REPORT_V2", "true").lower() != "false":
        _generate_report_v2(engine, order_id)
        return

    with engine.connect() as conn:
        order = conn.execute(
            text("SELECT * FROM orders WHERE id = :id"), {"id": order_id}
        ).first()

    if not order:
        logger.error(f"Order {order_id} not found")
        return

    boat_id = order.boat_id

    # --- Step 1: Generate AI analysis ---
    report_markdown = _generate_ai_analysis(engine, boat_id)

    # --- Step 2: Fetch structured analytics ---
    report_analytics = _fetch_structured_analytics(engine, boat_id)

    # --- Step 3: Update order ---
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE orders
                SET report_markdown = :markdown,
                    report_analytics = CAST(:analytics AS jsonb),
                    status = 'generated',
                    report_generated_at = :now
                WHERE id = :id
            """),
            {
                "markdown": report_markdown,
                "analytics": _json_dumps(report_analytics),
                "now": datetime.now(timezone.utc),
                "id": order_id,
            },
        )

    logger.info(f"Report generated for order {order_id}, boat {boat_id}")


def _generate_ai_analysis(engine: Engine, boat_id: int) -> str:
    """Call Gemini with premium context to generate the full report text."""
    from google import genai
    from google.genai import types

    from irc_data.api.services.insights_service import (
        SYSTEM_PROMPT_PREMIUM,
        build_boat_context,
    )

    context = build_boat_context(engine, boat_id, detail_level="premium")
    if not context:
        return "Error: boat context could not be assembled."

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY not configured")
        return "Error: AI service not configured."

    try:
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=context,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT_PREMIUM,
                max_output_tokens=2500,
            )
        )

        from irc_data.api.services.analytics_service import track
        track("$ai_generation", str(boat_id), {
            "$ai_provider": "gemini",
            "$ai_model": "gemini-2.5-pro",
            "$ai_input": [{"role": "user", "content": context}],
            "$ai_output_choices": [
                {"role": "assistant", "content": response.text}
            ],
            "$ai_input_tokens": getattr(response.usage_metadata, "prompt_token_count", None),
            "$ai_output_tokens": getattr(response.usage_metadata, "candidates_token_count", None),
            "endpoint": "report_service.generate_report_content",
            "boat_id": boat_id,
        })

        return response.text or ""
    except Exception as e:
        logger.error(f"Gemini report generation failed: {e}")
        return f"Error generating report: {e}"


def _fetch_structured_analytics(engine: Engine, boat_id: int) -> dict:
    """Fetch structured data from the analytics engines."""
    result = {}

    # Optimisation recommendations (Engine 4)
    try:
        from irc_data.analysis.optimizer import generate_optimisation_report

        report = generate_optimisation_report(engine, boat_id)
        if report and report.recommendations:
            result["optimize"] = [r.to_dict() for r in report.recommendations]
    except Exception as e:
        logger.debug(f"Optimizer failed for boat {boat_id}: {e}")

    # RAI (Engine 3a)
    try:
        from irc_data.analysis.performance import compute_rai

        rai = compute_rai(engine, boat_id)
        if rai:
            result["rai"] = rai.to_dict()
    except Exception as e:
        logger.debug(f"RAI failed for boat {boat_id}: {e}")

    # Rivals (Engine 3b)
    try:
        from irc_data.analysis.performance import compute_head_to_head

        rivals = compute_head_to_head(engine, boat_id)
        if rivals:
            result["rivals"] = [r.to_dict() for r in rivals[:10]]
    except Exception as e:
        logger.debug(f"Rivals failed for boat {boat_id}: {e}")

    return result


def _json_dumps(obj: dict) -> str:
    """JSON serialize with Decimal + date/datetime handling."""
    import json
    from datetime import date, datetime
    from decimal import Decimal

    class DecimalEncoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, Decimal):
                return float(o)
            if isinstance(o, (date, datetime)):
                return o.isoformat()
            return super().default(o)

    return json.dumps(obj, cls=DecimalEncoder)


def _generate_report_v2(engine: Engine, order_id: int) -> None:
    """V2 path: run the 11-section orchestrator, store the aggregated
    payload in report_analytics, and a concatenated markdown for the
    legacy /v1/reports/{token} HTML view."""
    from irc_data.api.services.report.orchestrator import build_report

    with engine.connect() as conn:
        order = conn.execute(
            text("SELECT * FROM orders WHERE id = :id"), {"id": order_id}
        ).first()
    if not order:
        logger.error(f"Order {order_id} not found")
        return

    payload = build_report(engine, order.boat_id)

    markdown_concat = "\n\n".join(
        f"## {s['title']}\n\n{s['markdown']}" for s in payload["sections"]
        if s["markdown"]
    )
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE orders
            SET report_markdown = :md,
                report_analytics = CAST(:analytics AS jsonb),
                status = 'generated',
                report_generated_at = :now
            WHERE id = :id
        """), {
            "md": markdown_concat,
            "analytics": _json_dumps(payload),
            "now": datetime.now(timezone.utc),
            "id": order_id,
        })
    logger.info(f"Report V2 generated for order {order_id}, boat {order.boat_id}")
