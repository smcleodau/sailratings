"""Cowes Week race results scraper.

Cowes Week is the world's largest annual IRC regatta, held each August in
the Solent waters off Cowes, Isle of Wight, UK.  ~500 boats across IRC
Classes 0-7 race over 7 days.

Results are served by a PHP-based system at:
  https://www.cowesweek.co.uk/web/code/php/main_c.php

Data access strategy:
1. The archive page lists years from 2001-2025 with links to both
   overall-points pages (``page=pointsYYYY``) and daily-results pages
   (``page=resultsYYYY``).
2. Overall-points pages accept a ``resultrequest`` GET parameter that
   selects a series/class.  The server renders an HTML table with
   positions, boat names, and per-day scores.  Each boat name is a
   link to a **boat details** page (``page=boatdetailsYYYY&boatref=N``)
   which yields sail number, IRC TCC, design type, etc.
3. We focus on IRC Classes 0-7 (series IDs 5, 10, 20, 30, 40, 50, 60,
   70) and scrape both the points tables and every boat's detail page
   to extract TCC rating, sail number, and design.

No JavaScript rendering is required -- all useful data is in the
server-rendered HTML.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from irc_data.scrapers.base import RateLimiter, fetch_with_retry, get_http_client
from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource

CW_BASE = "https://www.cowesweek.co.uk"
CW_PHP = f"{CW_BASE}/web/code/php/main_c.php"

cw_limiter = RateLimiter(min_delay=2.0, jitter=1.0)

# IRC class series IDs used in the ``resultrequest`` parameter.
IRC_CLASS_IDS = {
    "IRC Class 0": 5,
    "IRC Class 1": 10,
    "IRC Class 2": 20,
    "IRC Class 3": 30,
    "IRC Class 4": 40,
    "IRC Class 5": 50,
    "IRC Class 6": 60,
    "IRC Class 7": 70,
}

# Years for which overall-points pages exist in the archive.
# 2012 and 2020 are missing (2020 = COVID, 2012 = not in archive).
CW_YEARS = [
    2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010, 2011,
    2013, 2014, 2015, 2016, 2017, 2018, 2019,
    2021, 2022, 2023, 2024, 2025,
]

# Typical Cowes Week dates (first Saturday in August).
# Used to approximate event_date for each year.
CW_APPROXIMATE_DATES = {y: date(y, 8, 1) for y in CW_YEARS}


def _safe_decimal(val: str | None) -> Decimal | None:
    if not val:
        return None
    val = val.strip().replace("\xa0", "").replace("&nbsp;", "")
    if not val:
        return None
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


def _detect_status(score_text: str | None) -> str:
    """Detect race status from Cowes Week score cell content."""
    if not score_text:
        return "finished"
    txt = score_text.strip().upper()
    if "DNF" in txt:
        return "DNF"
    if "DNS" in txt:
        return "DNS"
    if "DSQ" in txt:
        return "DSQ"
    if "OCS" in txt:
        return "OCS"
    if "RET" in txt:
        return "RET"
    if "NOD" in txt:
        return "NOD"  # Not on data / Did not start this race
    if "NER" in txt:
        return "NER"  # Not entered for this race
    return "finished"


def _build_points_url(year: int, series_id: int) -> str:
    """Build URL for an overall points page for a given year + class."""
    # Archive years use section=regatta, current year uses section=racing
    section = "racing" if year == CW_YEARS[-1] else "regatta"
    return (
        f"{CW_PHP}?map=cw26&ui=cw4&style=std&override="
        f"&section={section}&page=points{year}&resultrequest={series_id}"
    )


def _build_boat_details_url(year: int, boatref: int) -> str:
    """Build URL for a boat's detail page."""
    section = "racing" if year == CW_YEARS[-1] else "regatta"
    return (
        f"{CW_PHP}?map=cw26&ui=cw4&style=std&override="
        f"&section={section}&page=boatdetails{year}&boatref={boatref}"
    )


def _build_archive_url() -> str:
    """Build URL for the results archive page."""
    return (
        f"{CW_PHP}?map=cw26&ui=cw4&style=std&override="
        "&section=regatta&page=archive"
    )


async def _fetch_available_classes(client, year: int) -> dict[str, int]:
    """Fetch the series dropdown for a given year and return IRC class options.

    Returns dict mapping class name -> series ID.
    """
    section = "racing" if year == CW_YEARS[-1] else "regatta"
    url = (
        f"{CW_PHP}?map=cw26&ui=cw4&style=std&override="
        f"&section={section}&page=points{year}"
    )
    await cw_limiter.wait()
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return {}
    except Exception:
        return {}

    classes = {}
    for m in re.finditer(
        r'option\s+value="(\d+)"\s*(?:selected)?\s*>([^<]*)',
        resp.text,
    ):
        series_id = int(m.group(1))
        name = m.group(2).strip()
        if name.startswith("IRC Class"):
            classes[name] = series_id

    return classes


def parse_points_table(
    html: str,
    year: int,
    class_name: str,
) -> list[dict]:
    """Parse a Cowes Week overall-points HTML table.

    Returns a list of dicts with keys:
        boat_name, skipper, boatref, place, day_scores, total, best_n
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return []

    results = []
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # Parse header to identify columns
        header = rows[0]
        header_cells = header.find_all("td")
        headers = [c.get_text(strip=True).replace("\xa0", "").lower() for c in header_cells]

        if "pos" not in headers and "pos " not in " ".join(headers):
            continue

        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            # Position is always first column
            pos_text = cells[0].get_text(strip=True)
            place = _safe_int(pos_text)

            # Boat name cell has the name in an <a> tag + skipper in <span class="entrant">
            name_cell = cells[1]
            boat_link = name_cell.find("a")
            entrant_span = name_cell.find("span", class_="entrant")

            boat_name = boat_link.get_text(strip=True) if boat_link else name_cell.get_text(strip=True)
            skipper = entrant_span.get_text(strip=True) if entrant_span else None

            # Extract boatref from the link
            boatref = None
            if boat_link:
                href = boat_link.get("href", "")
                m = re.search(r"boatref=(\d+)", href)
                if m:
                    boatref = int(m.group(1))

            # Day scores are in subsequent columns
            day_scores = []
            for cell in cells[2:]:
                text = cell.get_text(strip=True).replace("\xa0", "")
                status_span = cell.find("span", class_="status")
                status_text = status_span.get_text(strip=True) if status_span else ""
                if text:
                    day_scores.append({"score": text, "status": status_text})

            results.append({
                "boat_name": boat_name,
                "skipper": skipper,
                "boatref": boatref,
                "place": place,
                "fleet_size": len(rows) - 1,
                "day_scores": day_scores,
            })

    return results


async def fetch_boat_details(
    client,
    year: int,
    boatref: int,
) -> dict:
    """Fetch boat details (sail number, TCC, design type, etc.)."""
    url = _build_boat_details_url(year, boatref)
    await cw_limiter.wait()

    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return {}
    except Exception:
        return {}

    details = {}
    # Parse the key-value table: colEven = field name, colOdd = value
    for m in re.finditer(
        r"colEven['\"]?>([^<]+)</td>\s*<td\s+class=['\"]colOdd['\"]?>([^<]*)",
        resp.text,
    ):
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        if "sail" in key and "number" in key:
            details["sail_number"] = val
        elif "handicap" in key:
            details["tcc"] = _safe_decimal(val)
        elif "design" in key and "type" in key:
            details["design_type"] = val
        elif "designer" in key:
            details["designer"] = val
        elif "year" in key and "built" in key:
            details["year_built"] = val
        elif "skipper" in key:
            details["skipper"] = val
        elif "crew" in key:
            details["crew"] = val

    return details


class CowesWeekSource(RaceResultSource):
    """Cowes Week race results source.

    Scrapes IRC class overall-points tables and per-boat detail pages
    to produce NormalizedResult objects with boat name, sail number,
    TCC rating, class position, and fleet size.

    Each year produces one NormalizedResult per boat per IRC class,
    representing their overall result in that class for the week.
    """

    def source_name(self) -> str:
        return "cowesweek"

    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        """Discover Cowes Week editions by year.

        Returns one EventRef per IRC class per year, e.g.
        ``2024 Cowes Week - IRC Class 0``.
        """
        events = []
        min_year = since.year if since else 2001

        async with get_http_client() as client:
            for year in CW_YEARS:
                if year < min_year:
                    continue

                # Fetch available IRC classes for this year
                classes = await _fetch_available_classes(client, year)
                if not classes:
                    # Fallback to the standard IRC class IDs
                    classes = dict(IRC_CLASS_IDS)

                for class_name, series_id in sorted(classes.items()):
                    url = _build_points_url(year, series_id)
                    events.append(EventRef(
                        source="cowesweek",
                        event_name=f"{year} Cowes Week - {class_name}",
                        event_url=url,
                        event_date=CW_APPROXIMATE_DATES.get(year),
                        organizing_club="Cowes Week",
                        event_type="windward-leeward",
                        metadata={
                            "year": year,
                            "class_name": class_name,
                            "series_id": series_id,
                        },
                    ))

                print(f"  {year}: {len(classes)} IRC classes found")

        return events

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        """Scrape a single IRC class for a single year.

        For each boat in the points table, fetches the boat detail page
        to get sail number, TCC rating, and design type.
        """
        year = ref.metadata["year"]
        class_name = ref.metadata["class_name"]
        series_id = ref.metadata["series_id"]

        results = []
        async with get_http_client() as client:
            # Fetch the overall points page
            url = _build_points_url(year, series_id)
            await cw_limiter.wait()
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return []
            except Exception:
                return []

            entries = parse_points_table(resp.text, year, class_name)
            if not entries:
                return []

            fleet_size = len(entries)

            # Fetch boat details for each entry
            for entry in entries:
                boatref = entry.get("boatref")
                details = {}
                if boatref is not None:
                    details = await fetch_boat_details(client, year, boatref)

                sail_number = details.get("sail_number")
                tcc = details.get("tcc")

                results.append(NormalizedResult(
                    boat_name=entry["boat_name"],
                    sail_number=sail_number,
                    event_name=f"{year} Cowes Week",
                    event_date=CW_APPROXIMATE_DATES.get(year),
                    event_series=f"{year} Cowes Week",
                    organizing_club="Cowes Week",
                    event_type="windward-leeward",
                    race_name=class_name,
                    place=entry.get("place"),
                    class_name=class_name,
                    class_place=entry.get("place"),
                    class_fleet_size=fleet_size,
                    fleet_size=fleet_size,
                    status="finished",
                    rating_type="irc_tcc" if tcc else None,
                    rating_value=tcc,
                    source_url=ref.event_url,
                    raw_data={
                        "skipper": entry.get("skipper"),
                        "boatref": boatref,
                        "design_type": details.get("design_type"),
                        "designer": details.get("designer"),
                        "year_built": details.get("year_built"),
                        "day_scores": entry.get("day_scores"),
                        "series_id": series_id,
                        "year": year,
                    },
                ))

        return results
