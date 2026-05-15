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
    """Call Claude with premium context to generate the full report text."""
    import anthropic

    from irc_data.api.services.insights_service import (
        SYSTEM_PROMPT_PREMIUM,
        build_boat_context,
    )

    context = build_boat_context(engine, boat_id, detail_level="premium")
    if not context:
        return "Error: boat context could not be assembled."

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not configured")
        return "Error: AI service not configured."

    from irc_data.api.services.analytics_service import get_anthropic_client
    client = get_anthropic_client(api_key)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2500,
        system=SYSTEM_PROMPT_PREMIUM,
        messages=[{"role": "user", "content": context}],
        posthog_distinct_id=str(boat_id),
        posthog_properties={
            "endpoint": "report_service.generate_report_content",
            "boat_id": boat_id,
        },
    )

    return response.content[0].text


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
    """JSON serialize with Decimal handling."""
    import json
    from decimal import Decimal

    class DecimalEncoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, Decimal):
                return float(o)
            return super().default(o)

    return json.dumps(obj, cls=DecimalEncoder)
