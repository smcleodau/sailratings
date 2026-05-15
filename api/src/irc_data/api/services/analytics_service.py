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


def get_anthropic_client(api_key: str):
    """Return an Anthropic client, instrumented with PostHog LLM tracking when available.

    Falls back to the vanilla `anthropic.Anthropic` if PostHog isn't configured or its
    AI helper isn't installed. The wrapper is a transparent drop-in — same `messages.create`,
    `messages.stream` API surface — so callers don't change.
    """
    posthog_client = _get_client()
    if posthog_client is not None:
        try:
            from posthog.ai.anthropic import Anthropic as PostHogAnthropic
            return PostHogAnthropic(api_key=api_key, posthog_client=posthog_client)
        except ImportError:
            logger.debug("posthog.ai.anthropic not available; using vanilla client")

    import anthropic
    return anthropic.Anthropic(api_key=api_key)
