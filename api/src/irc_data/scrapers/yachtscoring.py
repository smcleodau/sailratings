"""YachtScoring race results scraper.

YachtScoring (yachtscoring.com) is a React SPA with a REST API at
api.yachtscoring.com/v1. The public API provides event metadata and
boat entries, but actual race results require browser rendering.

Strategy:
1. Use the public API to discover events and get boat registrations
2. Use Playwright to render results pages and extract table data
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from irc_data.scrapers.base import RateLimiter, get_http_client
from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource

YACHTSCORING_API = "https://api.yachtscoring.com/v1"
YACHTSCORING_WEB = "https://www.yachtscoring.com"

ys_limiter = RateLimiter(min_delay=2.0, jitter=1.0)


def _safe_decimal(val: str | None) -> Decimal | None:
    if not val:
        return None
    val = val.strip()
    try:
        return Decimal(val)
    except (InvalidOperation, ValueError):
        return None


def _safe_int(val: str | None) -> int | None:
    if not val:
        return None
    try:
        return int(re.sub(r"[^\d]", "", val.strip()))
    except (ValueError, TypeError):
        return None


async def discover_recent_events(
    since: date | None = None,
    region: str | None = None,
) -> list[EventRef]:
    """Discover events from YachtScoring's public event listing.

    Uses the Playwright approach to browse the event selection page since
    the public API doesn't provide an event listing endpoint.
    """
    # YachtScoring doesn't have a public event listing API.
    # Discovery would require either:
    # 1. Scanning known event IDs (sequential)
    # 2. Browsing the event selection page with Playwright
    # 3. Maintaining a list of known event IDs
    #
    # For now, we support scraping by known event ID.
    return []


async def get_event_info(event_id: int) -> dict | None:
    """Get event metadata from the public API."""
    async with get_http_client() as client:
        await ys_limiter.wait()
        url = f"{YACHTSCORING_API}/public/event/{event_id}"
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None


async def get_event_boats(event_id: int) -> list[dict]:
    """Get registered boats for an event from the public API."""
    boats = []
    page = 0
    async with get_http_client() as client:
        while True:
            await ys_limiter.wait()
            url = f"{YACHTSCORING_API}/public/event/{event_id}/boats?size=100&page={page}"
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    break
                data = resp.json()
                content = data.get("content", data) if isinstance(data, dict) else data
                if isinstance(content, list):
                    boats.extend(content)
                    if len(content) < 100:
                        break
                else:
                    break
                page += 1
            except Exception:
                break
    return boats


async def scrape_event_results_playwright(event_id: int) -> list[NormalizedResult]:
    """Scrape race results using Playwright to render the React SPA.

    Navigates to the cumulative results page and extracts the rendered table.
    """
    from playwright.async_api import async_playwright

    results = []
    url = f"{YACHTSCORING_WEB}/event_results_cumulative/{event_id}"

    # Get event info first
    event_info = await get_event_info(event_id)
    event_name = event_info.get("name", f"YachtScoring Event {event_id}") if event_info else f"Event {event_id}"
    event_date_str = event_info.get("startDate") if event_info else None
    event_date = None
    if event_date_str:
        try:
            event_date = datetime.fromisoformat(event_date_str.replace("Z", "+00:00")).date()
        except (ValueError, TypeError):
            pass

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            # Wait for table to render
            await page.wait_for_selector("table", timeout=10000)

            # Get all tables
            tables = await page.query_selector_all("table")
            for table in tables:
                rows = await table.query_selector_all("tr")
                if len(rows) < 2:
                    continue

                # Parse headers
                header_row = rows[0]
                header_cells = await header_row.query_selector_all("th, td")
                headers = []
                for cell in header_cells:
                    text = (await cell.text_content() or "").strip().lower()
                    headers.append(text)

                # Map columns
                col_map = {}
                for i, h in enumerate(headers):
                    if h in ("boat", "boat name", "yacht", "name"):
                        col_map["boat_name"] = i
                    elif "sail" in h:
                        col_map["sail_number"] = i
                    elif h in ("rating", "tcc", "irc", "phrf", "tcf"):
                        col_map["rating"] = i
                    elif h in ("total", "points", "total points"):
                        col_map["points"] = i
                    elif h in ("place", "pos", "rank"):
                        col_map["place"] = i
                    elif "class" in h or "division" in h:
                        col_map["class"] = i
                    elif "owner" in h or "skipper" in h:
                        col_map["owner"] = i

                if "boat_name" not in col_map:
                    continue

                # Parse data rows
                for row in rows[1:]:
                    cells = await row.query_selector_all("td")
                    cell_texts = []
                    for cell in cells:
                        cell_texts.append((await cell.text_content() or "").strip())

                    if len(cell_texts) < len(headers):
                        continue

                    def get_cell(key):
                        idx = col_map.get(key)
                        if idx is not None and idx < len(cell_texts):
                            return cell_texts[idx]
                        return None

                    boat_name = get_cell("boat_name")
                    if not boat_name:
                        continue

                    results.append(NormalizedResult(
                        boat_name=boat_name,
                        sail_number=get_cell("sail_number"),
                        event_name=event_name,
                        event_date=event_date,
                        organizing_club=None,
                        place=_safe_int(get_cell("place")),
                        fleet_size=len(rows) - 1,
                        class_name=get_cell("class"),
                        rating_type="irc_tcc" if get_cell("rating") else None,
                        rating_value=_safe_decimal(get_cell("rating")),
                        source_url=url,
                        raw_data={
                            "event_id": event_id,
                            "owner": get_cell("owner"),
                            "points": get_cell("points"),
                            "headers": headers,
                            "cells": cell_texts,
                        },
                    ))

        except Exception as e:
            print(f"  YachtScoring scrape error for event {event_id}: {e}")
        finally:
            await browser.close()

    return results


class YachtScoringSource(RaceResultSource):
    """YachtScoring race results source."""

    def __init__(self, event_ids: list[int] | None = None):
        self._event_ids = event_ids or []

    def source_name(self) -> str:
        return "yachtscoring"

    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        """Discover events — requires known event IDs."""
        events = []
        for event_id in self._event_ids:
            info = await get_event_info(event_id)
            if info:
                event_date = None
                if info.get("startDate"):
                    try:
                        event_date = datetime.fromisoformat(
                            info["startDate"].replace("Z", "+00:00")
                        ).date()
                    except (ValueError, TypeError):
                        pass

                if since and event_date and event_date < since:
                    continue

                events.append(EventRef(
                    source="yachtscoring",
                    event_name=info.get("name", f"Event {event_id}"),
                    event_url=f"{YACHTSCORING_WEB}/event_results_cumulative/{event_id}",
                    event_date=event_date,
                    metadata={"event_id": event_id},
                ))
        return events

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        """Scrape results for a single event using Playwright."""
        event_id = ref.metadata.get("event_id") if ref.metadata else None
        if not event_id:
            return []
        return await scrape_event_results_playwright(event_id)
