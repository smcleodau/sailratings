"""Server-side analytics via PostHog.

Captures the high-signal funnel events the frontend can't observe reliably:
order created, order paid, report generated, email sent (or failed).
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("POSTHOG_API_KEY")
    if not api_key:
        return None

    try:
        from posthog import Posthog
    except ImportError:
        logger.warning("posthog package not installed; analytics disabled")
        return None

    host = os.environ.get("POSTHOG_HOST", "https://eu.i.posthog.com")
    _client = Posthog(project_api_key=api_key, host=host)
    return _client


def track(
    event: str,
    distinct_id: str,
    properties: dict[str, Any] | None = None,
) -> None:
    """Fire-and-forget event capture. Never raises."""
    client = _get_client()
    if client is None:
        return
    try:
        client.capture(distinct_id=distinct_id, event=event, properties=properties or {})
    except Exception as e:
        logger.warning(f"posthog capture failed for {event}: {e}")


def get_gemini_client(api_key: str):
    """Return a Gemini client.
    """
    from google import genai
    return genai.Client(api_key=api_key)
