"""Race results scrapers for multiple sources."""

import asyncio
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bs4 import BeautifulSoup

from irc_data.config import (
    CYCA_RESULTS_URL,
    RACE_RESULTS_DIR,
    SAILRACEHQ_URL,
    SAILWAVE_URL,
    TOPYACHT_URL,
)
from irc_data.parsers.schemas import RaceResult
from irc_data.scrapers.base import RateLimiter, fetch_with_retry, get_http_client

rate_limiter = RateLimiter(min_delay=2.0, jitter=1.0)


def _safe_decimal(val: str) -> Decimal | None:
    try:
        return Decimal(val.strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def _safe_int(val: str) -> int | None:
    try:
        return int(val.strip())
    except (ValueError, AttributeError):
        return None


def parse_cyca_result_html(html: str, source_url: str) -> list[RaceResult]:
    """Parse a CYCA static HTML results page.

    These pages typically have tables with columns like:
    Place, Sail No, Boat Name, AHC (= IRC rating), Elapsed, Corrected, etc.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Try to find the event name from the page title or heading
    title_tag = soup.find("title") or soup.find("h1") or soup.find("h2")
    event_name = title_tag.get_text(strip=True) if title_tag else "Unknown CYCA Event"

    # Find all tables that look like result tables
    for table in soup.find_all("table"):
        headers = []
        for th in table.find_all("th"):
            headers.append(th.get_text(strip=True).lower())

        if not headers:
            # Try first row as headers
            first_row = table.find("tr")
            if first_row:
                for td in first_row.find_all("td"):
                    headers.append(td.get_text(strip=True).lower())

        # Look for IRC-related columns
        has_rating = any(
            h in ("ahc", "irc", "tcc", "rating", "irc rating", "irc tcc")
            for h in headers
        )
        has_sail = any(h in ("sail no", "sail", "sail number", "sailno") for h in headers)

        if not (has_rating and has_sail):
            continue

        # Map column indices
        col_map = {}
        for i, h in enumerate(headers):
            if h in ("sail no", "sail", "sail number", "sailno"):
                col_map["sail_number"] = i
            elif h in ("boat name", "boat", "yacht", "name"):
                col_map["boat_name"] = i
            elif h in ("ahc", "irc", "tcc", "rating", "irc rating", "irc tcc"):
                col_map["tcc"] = i
            elif h in ("place", "pos", "position"):
                col_map["place"] = i
            elif h in ("elapsed", "elapsed time"):
                col_map["elapsed"] = i
            elif h in ("corrected", "corrected time"):
                col_map["corrected"] = i
            elif h in ("division", "div", "class"):
                col_map["division"] = i

        # Parse data rows
        for row in table.find_all("tr")[1:]:  # Skip header row
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < len(headers):
                continue

            sail_no = cells[col_map["sail_number"]] if "sail_number" in col_map else None
            if not sail_no:
                continue

            result = RaceResult(
                event_name=event_name,
                source_url=source_url,
                tcc_at_race=_safe_decimal(cells[col_map["tcc"]]) if "tcc" in col_map else None,
                place=_safe_int(cells[col_map["place"]]) if "place" in col_map else None,
                division=cells[col_map.get("division", -1)] if "division" in col_map else None,
                elapsed_time=cells[col_map.get("elapsed", -1)] if "elapsed" in col_map else None,
                corrected_time=cells[col_map.get("corrected", -1)] if "corrected" in col_map else None,
                raw_data={
                    "sail_number": sail_no,
                    "boat_name": cells[col_map.get("boat_name", -1)] if "boat_name" in col_map else None,
                    "headers": headers,
                    "cells": cells,
                },
            )
            results.append(result)

    return results


async def scrape_cyca_results(
    years: list[int] | None = None,
) -> list[RaceResult]:
    """Scrape CYCA race results pages.

    CYCA results are static HTML at known URL patterns.
    We need to discover the exact paths as the index may be 403.
    """
    from playwright.async_api import async_playwright

    years = years or list(range(2019, date.today().year + 1))
    all_results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        for year in years:
            url = f"{CYCA_RESULTS_URL}/{year}/"
            await rate_limiter.wait()

            try:
                resp = await page.goto(url, wait_until="networkidle", timeout=15000)
                if resp and resp.ok:
                    # Find links to IRC result files
                    links = await page.locator('a[href*="irc"], a[href*="IRC"]').all()
                    for link in links:
                        href = await link.get_attribute("href")
                        if href and href.endswith(".htm"):
                            await rate_limiter.wait()
                            try:
                                result_resp = await page.goto(href, timeout=15000)
                                if result_resp and result_resp.ok:
                                    html = await page.content()
                                    results = parse_cyca_result_html(html, href)
                                    all_results.extend(results)
                                    print(f"  {href}: {len(results)} results")
                            except Exception as e:
                                print(f"  Failed: {href} — {e}")
            except Exception as e:
                print(f"  Failed: {url} — {e}")

        await browser.close()

    return all_results


async def scrape_sailwave_results() -> list[RaceResult]:
    """Scrape Sailwave static HTML results."""
    all_results = []
    async with get_http_client() as client:
        # Sailwave results are static — discover IRC result pages
        await rate_limiter.wait()
        try:
            resp = await fetch_with_retry(client, SAILWAVE_URL, rate_limiter=rate_limiter)
            soup = BeautifulSoup(resp.text, "html.parser")
            # Find links containing 'irc' or 'IRC'
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if re.search(r"irc", href, re.IGNORECASE) and href.endswith(".htm"):
                    full_url = href if href.startswith("http") else f"{SAILWAVE_URL}/{href}"
                    await rate_limiter.wait()
                    try:
                        page_resp = await fetch_with_retry(
                            client, full_url, rate_limiter=rate_limiter
                        )
                        results = parse_cyca_result_html(page_resp.text, full_url)
                        all_results.extend(results)
                    except Exception as e:
                        print(f"  Failed: {full_url} — {e}")
        except Exception as e:
            print(f"  Failed to fetch Sailwave index: {e}")

    return all_results
