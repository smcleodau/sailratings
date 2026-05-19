"""Claude-based extractor for sailing event pages.

Given a URL and its scraped markdown, return a structured prediction:

    {
        "is_sailing_event": bool,
        "scoring_platform": "sailsys" | "topyacht" | "sailwave"
                           | "yachtscoring" | "pdf" | "none" | "unknown",
        "platform_ids": dict,
        "title": str | None,
        "event_date": "YYYY-MM-DD" | None,
        "event_location": str | None,
        "confidence": float (0..1),
        "reasoning": str,
    }

Uses the Anthropic SDK with structured output via tool_use. Designed to
fail soft — returns a "no extraction" result rather than raising on any
LLM error, so a single bad page doesn't poison a batch.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


EXTRACTION_SYSTEM_PROMPT = """You analyse a single web page that *might* be a
sailing race or regatta page, and identify which scoring platform publishes
its results.

You will be given the URL and the page's clean markdown content. Your job:

1. Decide if this page is about a specific sailing race or regatta (or a
   page that links straight to one). Calendar/index pages are NOT events
   themselves — return is_sailing_event=false and scoring_platform="none"
   for those.

2. If it IS an event, find the link to the scoring system. Look for:

   - **SailSys**: URLs containing `sailsys.com.au`, `app.sailsys.com.au`,
     or `api.sailsys.com.au`. Extract club_id, series_id, race_id from
     URLs like /club/{club_id}/results/series/{series_id}/races/{race_id}.
   - **TopYacht**: URLs containing `topyacht.net.au` OR a club's own file
     server using the topyacht layout (e.g.
     `files.southportyachtclub.com.au/results/{year}/{division}/index.htm`).
     Extract year and division key.
   - **Sailwave**: URLs containing `sailwave.com` or filenames ending in
     `.htm` published by a club with Sailwave's signature layout.
   - **YachtScoring**: URLs containing `yachtscoring.com`. Extract eid.
   - **PDF**: a direct link to a results PDF. Capture the URL.

3. Extract the human-facing title, event date (single day or first day of
   a multi-day regatta), and location.

4. Confidence is your honest sense of how certain you are. 1.0 = absolutely
   certain, 0.5 = leaning, 0.2 = guessing.

CRITICAL:
- NEVER invent platform IDs. Only return them if you can see the matching
  URL in the page content.
- If the page links to MULTIPLE platforms (e.g. both SailSys and a PDF),
  pick SailSys first (it's structured), then TopYacht, then YachtScoring,
  then Sailwave, then PDF.
- If the page is a sailing CALENDAR or news article rather than a single
  event, return scoring_platform="none".
"""


def extract_event(url: str, markdown: str) -> dict[str, Any]:
    """Run extraction. Returns a dict matching the docstring schema.

    On any error, returns a best-effort fallback so the caller can still
    persist a row marked as failed.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _failed("ANTHROPIC_API_KEY not set", url)

    try:
        import anthropic
    except ImportError as e:
        return _failed(f"anthropic SDK missing: {e}", url)

    if not markdown or len(markdown.strip()) < 80:
        return _failed("page content too short to extract", url)

    client = anthropic.Anthropic(api_key=api_key)

    tool = {
        "name": "record_event",
        "description": "Record what was found on the sailing-event page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "is_sailing_event": {"type": "boolean"},
                "scoring_platform": {
                    "type": "string",
                    "enum": ["sailsys", "topyacht", "sailwave",
                             "yachtscoring", "pdf", "none", "unknown"],
                },
                "platform_ids": {
                    "type": "object",
                    "description": "Platform-specific identifiers extracted from URLs.",
                    "additionalProperties": True,
                },
                "title": {"type": ["string", "null"]},
                "event_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD or null",
                },
                "event_location": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
            },
            "required": ["is_sailing_event", "scoring_platform",
                         "platform_ids", "confidence", "reasoning"],
        },
    }

    user_message = (
        f"URL: {url}\n\n"
        f"PAGE MARKDOWN (truncated to 12k chars):\n\n"
        f"{markdown[:12000]}"
    )

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1500,
            system=EXTRACTION_SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={"type": "tool", "name": "record_event"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        return _failed(f"Anthropic call failed: {e}", url)

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_event":
            data = dict(block.input)
            data.setdefault("title", None)
            data.setdefault("event_date", None)
            data.setdefault("event_location", None)
            data["url"] = url
            return data

    return _failed("no tool_use in response", url)


def _failed(reason: str, url: str) -> dict[str, Any]:
    return {
        "is_sailing_event": False,
        "scoring_platform": "unknown",
        "platform_ids": {},
        "title": None,
        "event_date": None,
        "event_location": None,
        "confidence": 0.0,
        "reasoning": f"extraction failed: {reason}",
        "url": url,
        "_error": reason,
    }
