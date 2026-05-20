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


RESULTS_SYSTEM_PROMPT = """You extract sailing race results from a single
scraped race page (HTML rendered to markdown, or a PDF rendered to
markdown by Firecrawl). The page is the result table itself — not a
calendar or index.

You will be given the URL and clean markdown. Return:

1. event_name — the regatta / series name (e.g. "Cowes Week 2025",
   "Brisbane to Gladstone 2026"). NOT the class name. NOT just "Race 5".
2. event_date — YYYY-MM-DD for the race, or first day of a multi-day
   series. Null if you genuinely can't see one.
3. race_name — within the event, the specific race (e.g. "Race 5",
   "Coastal Series", "IRC Class 3"). Null if the page is the whole
   series with no per-race identifier.
4. class_name — IRC class as listed on the page (e.g. "IRC Class 3",
   "IRC Zero", "IRC Two-Handed"). This is the *fleet* the boats raced
   in.
5. results — one row per finisher. For each row extract:
   - place (integer): finishing position. NULL if DNS/DNF/DNC and no
     finishing position was assigned.
   - boat_name (string): the boat's name as printed
   - sail_number (string): sail number as printed (e.g. "GBR8994R",
     "NED118", "21" — keep the prefix when present)
   - rating_value (number): IRC TCC if shown (e.g. 0.987). Null if the
     page is not IRC-rated.
   - elapsed_time (string): elapsed time as printed (e.g. "2:14:33").
     Null if not shown.
   - corrected_time (string): corrected time as printed. Null if not
     shown.
   - status (string): one of "finished", "DNF", "DNS", "DNC", "DSQ",
     "RET", "OCS". Default "finished" if a place is present.

CRITICAL:
- Do NOT invent boats. Only return rows you can clearly see in the
  markdown table.
- Do NOT guess sail numbers. If the page omits one, return null.
- Preserve the order the page lists boats — that's the finishing order.
- If the page lists a TCH (time-corrected handicap), that's also TCC.
- If there's only one race row, still return a list with one element.
- Status: a row with no finish time but with "DNF" / "DNS" / "RET" /
  "OCS" / "DNC" / "DSQ" tags → status set accordingly; place null.
- Pages can mix IRC and other handicap classes — only return rows that
  appear under an IRC class header.
"""


def extract_results(url: str, markdown: str) -> dict[str, Any]:
    """Extract a structured list of race results from a results page.

    Returns:
        {
            "event_name": str,
            "event_date": "YYYY-MM-DD" | None,
            "race_name": str | None,
            "class_name": str | None,
            "results": [
                {"place": int|None, "boat_name": str, "sail_number": str|None,
                 "rating_value": float|None, "elapsed_time": str|None,
                 "corrected_time": str|None, "status": str},
                ...
            ],
            "confidence": float,
            "_error": str | None
        }

    Fails soft — returns an empty results list with `_error` set rather
    than raising, so a single bad page doesn't poison a batch.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _failed_results("ANTHROPIC_API_KEY not set", url)

    if not markdown or len(markdown.strip()) < 80:
        return _failed_results("page content too short", url)

    try:
        import anthropic
    except ImportError as e:
        return _failed_results(f"anthropic SDK missing: {e}", url)

    client = anthropic.Anthropic(api_key=api_key)

    tool = {
        "name": "record_results",
        "description": "Record the race results extracted from the page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_name": {"type": "string"},
                "event_date": {"type": ["string", "null"],
                               "description": "YYYY-MM-DD or null"},
                "race_name": {"type": ["string", "null"]},
                "class_name": {"type": ["string", "null"]},
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "place": {"type": ["integer", "null"]},
                            "boat_name": {"type": "string"},
                            "sail_number": {"type": ["string", "null"]},
                            "rating_value": {"type": ["number", "null"]},
                            "elapsed_time": {"type": ["string", "null"]},
                            "corrected_time": {"type": ["string", "null"]},
                            "status": {
                                "type": "string",
                                "enum": ["finished", "DNF", "DNS", "DNC",
                                         "DSQ", "RET", "OCS"],
                            },
                        },
                        "required": ["boat_name", "status"],
                    },
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["event_name", "results", "confidence"],
        },
    }

    # 30k char budget — enough for ~150 boats with all columns. Above that,
    # an event probably has its own per-class pages we should extract
    # separately.
    user_message = (
        f"URL: {url}\n\n"
        f"PAGE MARKDOWN (truncated to 30k chars):\n\n"
        f"{markdown[:30000]}"
    )

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=8000,  # ~150 rows × 50 tokens
            system=RESULTS_SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={"type": "tool", "name": "record_results"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        return _failed_results(f"Anthropic call failed: {e}", url)

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_results":
            data = dict(block.input)
            data.setdefault("event_date", None)
            data.setdefault("race_name", None)
            data.setdefault("class_name", None)
            data.setdefault("results", [])
            data["url"] = url
            return data

    return _failed_results("no tool_use in response", url)


def _failed_results(reason: str, url: str) -> dict[str, Any]:
    return {
        "event_name": "",
        "event_date": None,
        "race_name": None,
        "class_name": None,
        "results": [],
        "confidence": 0.0,
        "url": url,
        "_error": reason,
    }


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
