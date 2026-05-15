"""ISORA (Irish Sea Offshore Racing Association) race results scraper.

Scrapes IRC results from ISORA's website at:
  https://www.isora.org/index.php/notice-board/results2

Results are organized by year (2010-2026) with links to Sailwave-generated
HTML result files accessed via Joomla weblink redirects.

Each year's page contains links to:
  - Overall series results table (all races, with per-race points)
  - Class-specific results tables
  - Coastal series results (Ireland, Wales)
  - Individual race results (with elapsed/corrected times)
  - Special events (VDLR, D2D, etc.)

The actual result pages are Sailwave HTML files served as temporary
downloads through Joomla's weblink system.  The scraper follows these
redirects to fetch the underlying HTML.

No JavaScript required — pure HTTP + BeautifulSoup + Sailwave parser.
"""

import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from irc_data.scrapers.base import RateLimiter, fetch_with_retry, get_http_client
from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource
from irc_data.scrapers.sailwave import is_sailwave_html, parse_sailwave_html

ISORA_BASE = "https://www.isora.org"
RESULTS_INDEX = f"{ISORA_BASE}/index.php/notice-board/results2"

isora_limiter = RateLimiter(min_delay=2.0, jitter=1.0)

# Year URL patterns on the ISORA site
# Some years have 'x' suffix, some don't
YEAR_URL_PATTERNS = {
    2026: "results-2026",
    2025: "results-2025x",
    2024: "results-2024x",
    2023: "results-2023",
    2022: "results-2022",
    2021: "results-2021",
    2020: "results-2020",
    2019: "results-2019",
    2018: "results-2018",
    2017: "results-2017",
    2016: "results-2016",
    2015: "results-2015",
    2014: "results-2014",
    2013: "results-2013",
    2012: "results-2012",
    2011: "results-2011",
    2010: "results-2010",
}

# Special pages (non-year-specific)
SPECIAL_PAGES = [
    ("VDLR Offshore 2017", "2017-results-vdlr"),
    ("Coastal Series", "results-coastal-series"),
]


def _classify_result_link(text: str) -> dict:
    """Classify a result link by its text to determine type and priority.

    Returns dict with:
      - type: 'series_overall', 'series_class', 'coastal', 'race', 'special'
      - is_class_breakdown: bool
      - race_number: int or None
    """
    text_upper = text.upper()
    info: dict = {
        "type": "unknown",
        "is_class_breakdown": False,
        "race_number": None,
    }

    # Check for class breakdown
    if "BY CLASS" in text_upper or "CLASS SERIES" in text_upper:
        info["is_class_breakdown"] = True

    # Individual race results (Race NN)
    m = re.search(r"RACE\s*(\d+)", text_upper)
    if m:
        info["type"] = "race"
        info["race_number"] = int(m.group(1))
        return info

    # Series overall
    if "OVERALL" in text_upper and "SERIES" in text_upper:
        info["type"] = "series_overall"
        return info

    if "OVERALL RESULTS" in text_upper or "RESULTS TABLE" in text_upper:
        if info["is_class_breakdown"]:
            info["type"] = "series_class"
        else:
            info["type"] = "series_overall"
        return info

    # Coastal series
    if "COASTAL" in text_upper:
        info["type"] = "coastal"
        return info

    # Championship / special events
    if any(kw in text_upper for kw in ("CHAMPIONSHIP", "VDLR", "D2D", "DUBLIN")):
        info["type"] = "special"
        return info

    # Default: if it has "result" it's probably useful
    if "RESULT" in text_upper:
        info["type"] = "series_overall"
        return info

    return info


async def _discover_year_links(client, year_url: str, year: int) -> list[dict]:
    """Discover result links from a single year's index page.

    Returns list of dicts with: url, text, year, classification.
    """
    links = []
    try:
        resp = await fetch_with_retry(client, year_url, rate_limiter=isora_limiter)
        if resp.status_code != 200:
            return links

        soup = BeautifulSoup(resp.text, "html.parser")

        # Find all weblink.go links (Joomla redirect pattern)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if not text:
                continue

            # ISORA uses weblink.go for result redirects
            if "weblink.go" in href:
                full_url = urljoin(ISORA_BASE, href)
                classification = _classify_result_link(text)
                links.append({
                    "url": full_url,
                    "text": text,
                    "year": year,
                    **classification,
                })
            # Also check for direct .htm/.html links
            elif re.search(r"\.(html?|pdf)$", href, re.IGNORECASE):
                full_url = urljoin(ISORA_BASE, href)
                classification = _classify_result_link(text)
                if classification["type"] != "unknown":
                    links.append({
                        "url": full_url,
                        "text": text,
                        "year": year,
                        **classification,
                    })

        # Check for pagination (page 2 etc.)
        for page_link in soup.find_all("a", href=re.compile(r"start=\d+")):
            page_href = page_link["href"]
            page_url = urljoin(ISORA_BASE, page_href)
            if page_url != year_url:
                try:
                    page_resp = await fetch_with_retry(client, page_url, rate_limiter=isora_limiter)
                    if page_resp.status_code == 200:
                        page_soup = BeautifulSoup(page_resp.text, "html.parser")
                        for a in page_soup.find_all("a", href=True):
                            href2 = a["href"]
                            text2 = a.get_text(strip=True)
                            if text2 and "weblink.go" in href2:
                                full_url2 = urljoin(ISORA_BASE, href2)
                                # Skip if already found
                                if any(l["url"] == full_url2 for l in links):
                                    continue
                                classification2 = _classify_result_link(text2)
                                links.append({
                                    "url": full_url2,
                                    "text": text2,
                                    "year": year,
                                    **classification2,
                                })
                except Exception:
                    pass

    except Exception as e:
        print(f"  ISORA {year}: error discovering links - {e}")

    return links


class ISORASource(RaceResultSource):
    """ISORA race results source — IRC offshore and coastal races."""

    def source_name(self) -> str:
        return "isora"

    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        """Discover ISORA result links by scanning year index pages.

        Args:
            since: Only return events after this date. If None, return all
                   years from 2010 onwards.

        Returns:
            List of EventRef objects, one per result page.
        """
        events = []
        min_year = since.year if since else 2010

        async with get_http_client() as client:
            for year, slug in sorted(YEAR_URL_PATTERNS.items()):
                if year < min_year:
                    continue

                year_url = f"{RESULTS_INDEX}/{slug}"
                print(f"  Scanning ISORA {year}...")

                year_links = await _discover_year_links(client, year_url, year)

                for link in year_links:
                    event_type = "offshore"
                    if link["type"] == "coastal":
                        event_type = "coastal"

                    events.append(EventRef(
                        source="isora",
                        event_name=f"{year} ISORA - {link['text']}",
                        event_url=link["url"],
                        event_date=date(year, 1, 1),  # Approximate; refined on scrape
                        organizing_club="ISORA",
                        event_type=event_type,
                        metadata={
                            "year": year,
                            "link_text": link["text"],
                            "result_type": link["type"],
                            "is_class_breakdown": link["is_class_breakdown"],
                            "race_number": link.get("race_number"),
                        },
                    ))

                print(f"  {year}: {len(year_links)} result links")

            # Special pages
            for name, slug in SPECIAL_PAGES:
                special_url = f"{ISORA_BASE}/index.php/{slug}"
                special_links = await _discover_year_links(client, special_url, 0)
                for link in special_links:
                    events.append(EventRef(
                        source="isora",
                        event_name=f"ISORA - {name} - {link['text']}",
                        event_url=link["url"],
                        organizing_club="ISORA",
                        event_type="offshore",
                        metadata={
                            "year": 0,
                            "link_text": link["text"],
                            "result_type": link["type"],
                            "special_page": name,
                        },
                    ))

        return events

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        """Fetch and parse a single ISORA result page.

        ISORA result links go through Joomla's weblink redirect system,
        which serves the Sailwave HTML as a temporary file.  The scraper
        follows the redirect and parses the resulting HTML.

        Args:
            ref: EventRef from discover_events().

        Returns:
            List of NormalizedResult objects.
        """
        async with get_http_client() as client:
            try:
                resp = await fetch_with_retry(
                    client, ref.event_url,
                    rate_limiter=isora_limiter,
                    max_retries=2,
                )
            except Exception as e:
                print(f"  Failed to fetch {ref.event_name}: {e}")
                return []

            if resp.status_code != 200:
                return []

            html = resp.text

            # Check if this is actually Sailwave HTML
            if not is_sailwave_html(html):
                # The response might be a Joomla page wrapping the results.
                # Try to find an iframe or embedded content.
                soup = BeautifulSoup(html, "html.parser")

                # Check for iframe
                iframe = soup.find("iframe", src=True)
                if iframe:
                    iframe_url = urljoin(ref.event_url, iframe["src"])
                    try:
                        iframe_resp = await fetch_with_retry(
                            client, iframe_url,
                            rate_limiter=isora_limiter,
                        )
                        if iframe_resp.status_code == 200 and is_sailwave_html(iframe_resp.text):
                            html = iframe_resp.text
                    except Exception:
                        pass

                # Check for redirect links in the body
                if not is_sailwave_html(html):
                    # Look for links to .htm files in the page content
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        if re.search(r"\.(html?)$", href, re.IGNORECASE):
                            file_url = urljoin(ref.event_url, href)
                            try:
                                file_resp = await fetch_with_retry(
                                    client, file_url,
                                    rate_limiter=isora_limiter,
                                )
                                if file_resp.status_code == 200 and is_sailwave_html(file_resp.text):
                                    html = file_resp.text
                                    break
                            except Exception:
                                continue

            # Even if not strictly Sailwave, try parsing with the Sailwave
            # parser — it handles generic HTML tables too.
            year = ref.metadata.get("year", 0) if ref.metadata else 0
            event_date = ref.event_date

            results = parse_sailwave_html(
                html,
                source_url=ref.event_url,
                event_name=ref.event_name,
                organizing_club="ISORA",
                event_type=ref.event_type,
                event_date=event_date,
            )

            # Tag results with ISORA-specific metadata
            for r in results:
                if r.raw_data is None:
                    r.raw_data = {}
                r.raw_data["isora_year"] = year
                if ref.metadata:
                    r.raw_data["result_type"] = ref.metadata.get("result_type")
                    r.raw_data["race_number"] = ref.metadata.get("race_number")

            return results
