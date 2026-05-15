"""Rolex Sydney Hobart Yacht Race results scraper.

Scrapes IRC results from the CYCA Racing platform at:
  https://bwps.cycaracing.com/standings?seriesId=15

The CYCA Racing platform (bwps.cycaracing.com) serves server-rendered HTML
standings for multiple CYCA offshore race series including the Sydney Hobart,
Gold Coast, and Blue Water Pointscore races.

Data available per yacht:
  - Position (overall IRC corrected time ranking)
  - Yacht name and skipper
  - Division (1 or 2)
  - IRC TCC handicap value
  - Corrected time (DD:HH:MM:SS format)
  - Finish status (FINISHED, RETIRED, etc.)

Yacht details (sail number, boat type) are scraped from a separate
yachts listing page for the same race.

Historical data available: 2018-2025 (7 editions; 2020 race was cancelled).
Earlier years (2004-2017) are available on rolexsydneyhobart.com but use
a JavaScript-loaded feed that requires browser rendering.

No JavaScript required -- pure HTTP + BeautifulSoup.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup, NavigableString

from irc_data.scrapers.base import RateLimiter, fetch_with_retry, get_http_client
from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource

# The CYCA Racing platform serves standings for all BWPS races
CYCA_RACING_BASE = "https://bwps.cycaracing.com"
STANDINGS_URL = f"{CYCA_RACING_BASE}/standings"
YACHTS_URL = f"{CYCA_RACING_BASE}/the-yachts/"

# Series IDs on the CYCA Racing platform
SERIES_SYDNEY_HOBART = 15
SERIES_GOLD_COAST = 3
SERIES_FLINDERS_ISLET = 11
SERIES_CABBAGE_TREE = 13
SERIES_BIRD_ISLAND = 14
SERIES_TOLLGATE = 16
SERIES_NEWCASTLE_BASS = 12

# Year -> race ID mapping for the Sydney Hobart
# Discovered from the dropdown options on bwps.cycaracing.com/standings?seriesId=15
SYDNEY_HOBART_RACES = {
    2025: 192,
    2024: 184,
    2023: 171,
    2022: 166,
    2021: 157,
    # 2020: cancelled (COVID)
    2019: 151,
    2018: 143,
}

# Sydney Hobart race date is always 26 December (Boxing Day start)
RACE_MONTH = 12
RACE_DAY = 26

sydneyhobart_limiter = RateLimiter(min_delay=2.0, jitter=1.0)


def _safe_decimal(val: str | None) -> Decimal | None:
    if not val:
        return None
    val = val.strip().replace("\xa0", "")
    if not val:
        return None
    try:
        return Decimal(val)
    except (InvalidOperation, ValueError):
        return None


def _parse_corrected_time(text: str | None) -> str | None:
    """Parse corrected time from CYCA Racing format.

    Format is DD:HH:MM:SS (e.g. '03:04:33:38' = 3 days, 4 hours, 33 min, 38 sec).
    Returns as total hours:minutes:seconds string.
    """
    if not text:
        return None
    text = text.strip()
    m = re.match(r"(\d+):(\d+):(\d+):(\d+)", text)
    if m:
        days, hours, mins, secs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        total_hours = days * 24 + hours
        return f"{total_hours}:{mins:02d}:{secs:02d}"
    return None


def _detect_status(status_text: str | None) -> str:
    """Detect race status from the Position column text."""
    if not status_text:
        return "finished"
    status = status_text.strip().upper()
    if "FINISHED" in status:
        return "finished"
    if "RETIRED" in status:
        return "RET"
    if "DNF" in status:
        return "DNF"
    if "DNS" in status:
        return "DNS"
    if "DSQ" in status:
        return "DSQ"
    if "OCS" in status:
        return "OCS"
    return "RET" if "RETIRE" in status else "finished"


async def scrape_yacht_details(
    client, race_id: int, series_id: int = SERIES_SYDNEY_HOBART,
) -> dict[str, dict]:
    """Scrape yacht details (sail number, boat type) from the yachts listing page.

    Returns a dict mapping yacht_name (lowercase) -> {sail_number, boat_type, category}.
    """
    url = f"{YACHTS_URL}?seriesId={series_id}&raceId={race_id}"
    try:
        resp = await fetch_with_retry(client, url, rate_limiter=sydneyhobart_limiter)
    except Exception:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    yacht_map = {}

    table = soup.find("table", class_="standings")
    if not table:
        return yacht_map

    rows = table.find_all("tr")
    for row in rows[1:]:  # skip header
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        # First cell: yacht name in <a> tag
        link = cells[0].find("a")
        if not link:
            continue
        # Yacht name is the text before the <br> / <span>
        yacht_name = link.get_text(strip=True)
        # Remove the sail number that appears in mobile span
        span = link.find("span", class_="mobile-only")
        if span:
            yacht_name = yacht_name.replace(span.get_text(strip=True), "").strip()

        # Second cell: sail number (desktop-only)
        sail_number = cells[1].get_text(strip=True) if len(cells) > 1 else None

        # Third cell: state/country
        state = cells[2].get_text(strip=True) if len(cells) > 2 else None

        # Fourth cell: boat type
        boat_type = cells[3].get_text(strip=True) if len(cells) > 3 else None

        # Fifth cell (optional): category (IRC / PHS)
        category = cells[4].get_text(strip=True) if len(cells) > 4 else None

        yacht_map[yacht_name.lower()] = {
            "sail_number": sail_number,
            "boat_type": boat_type,
            "state": state,
            "category": category,
        }

    return yacht_map


def parse_standings_html(
    html: str, year: int, race_id: int,
    series_id: int = SERIES_SYDNEY_HOBART,
    yacht_details: dict[str, dict] | None = None,
) -> list[NormalizedResult]:
    """Parse the standings HTML table into NormalizedResult objects."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    table = soup.find("table", class_="standings")
    if not table:
        return results

    tbody = table.find("tbody")
    if not tbody:
        return results

    rows = tbody.find_all("tr")
    fleet_size = len(rows)
    event_date = date(year, RACE_MONTH, RACE_DAY)
    source_url = f"{STANDINGS_URL}?seriesId={series_id}&raceId={race_id}"
    yacht_details = yacht_details or {}

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        # Column 0: Position (class="index")
        place_text = cells[0].get_text(strip=True)
        place = None
        if place_text:
            try:
                place = int(place_text)
            except ValueError:
                pass

        # Column 1: Yacht name + skipper
        name_cell = cells[1]
        link = name_cell.find("a")
        if not link:
            continue

        yacht_name = link.get_text(strip=True)
        # Remove trailing asterisk (protest/redress flag)
        yacht_name = yacht_name.rstrip(" *")

        skipper_div = name_cell.find("div", class_="small")
        skipper = skipper_div.get_text(strip=True) if skipper_div else None

        # Column 2: Division (class="centa")
        division = None
        if len(cells) > 2:
            div_text = cells[2].get_text(strip=True)
            try:
                division = int(div_text)
            except ValueError:
                division = None

        # Column 3: Status (FINISHED, RETIRED, etc.)
        status_text = cells[3].get_text(strip=True) if len(cells) > 3 else None
        status = _detect_status(status_text)

        # Column 4: Handicap/TCC (class="centa")
        tcc = None
        if len(cells) > 4:
            tcc = _safe_decimal(cells[4].get_text(strip=True))

        # Column 5: Corrected time + speed
        corrected_time = None
        if len(cells) > 5:
            time_cell = cells[5]
            # Extract time from direct text nodes only (not <span> or <div> children)
            # to avoid concatenation with speed value
            time_text = ""
            for child in time_cell.children:
                if isinstance(child, NavigableString):
                    t = child.strip()
                    if t:
                        time_text = t
                        break
            # Extract DD:HH:MM:SS pattern
            m = re.search(r"(\d+:\d{2}:\d{2}:\d{2})", time_text)
            if m:
                corrected_time = _parse_corrected_time(m.group(1))

        # Look up yacht details (sail number, boat type)
        details = yacht_details.get(yacht_name.lower(), {})
        sail_number = details.get("sail_number")
        boat_type = details.get("boat_type")
        state = details.get("state")
        category = details.get("category")

        event_name = f"{year} Rolex Sydney Hobart Yacht Race"
        div_label = f"Division {division}" if division else None

        results.append(NormalizedResult(
            boat_name=yacht_name,
            sail_number=sail_number,
            event_name=event_name,
            event_date=event_date,
            event_series="Rolex Sydney Hobart Yacht Race",
            organizing_club="CYCA",
            event_type="offshore",
            race_name=event_name,
            place=place,
            fleet_size=fleet_size,
            class_name=div_label,
            status=status,
            rating_type="irc_tcc" if tcc else None,
            rating_value=tcc,
            corrected_time=corrected_time,
            source_url=source_url,
            raw_data={
                "skipper": skipper,
                "boat_type": boat_type,
                "state": state,
                "category": category,
                "division": division,
                "status_text": status_text,
                "race_id": race_id,
                "series_id": series_id,
                "year": year,
            },
        ))

    return results


class SydneyHobartSource(RaceResultSource):
    """Rolex Sydney Hobart Yacht Race results source.

    Scrapes from bwps.cycaracing.com which serves server-rendered HTML
    standings for all CYCA Blue Water Pointscore races including the
    Sydney Hobart.
    """

    def source_name(self) -> str:
        return "sydneyhobart"

    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        """Discover available Sydney Hobart race years.

        Fetches the standings page to find all available year/raceId options,
        then returns EventRef objects for each year.
        """
        events = []

        async with get_http_client() as client:
            # Fetch the standings page to discover available years
            url = f"{STANDINGS_URL}?seriesId={SERIES_SYDNEY_HOBART}"
            try:
                resp = await fetch_with_retry(client, url, rate_limiter=sydneyhobart_limiter)
            except Exception as e:
                print(f"Error fetching standings page: {e}")
                # Fall back to hardcoded mapping
                for year, race_id in sorted(SYDNEY_HOBART_RACES.items()):
                    if since and date(year, RACE_MONTH, RACE_DAY) < since:
                        continue
                    events.append(EventRef(
                        source="sydneyhobart",
                        event_name=f"{year} Rolex Sydney Hobart Yacht Race",
                        event_url=f"{STANDINGS_URL}?seriesId={SERIES_SYDNEY_HOBART}&raceId={race_id}",
                        event_date=date(year, RACE_MONTH, RACE_DAY),
                        organizing_club="CYCA",
                        event_type="offshore",
                        metadata={"year": year, "race_id": race_id, "series_id": SERIES_SYDNEY_HOBART},
                    ))
                return events

            # Parse the year dropdown to find all available race IDs
            soup = BeautifulSoup(resp.text, "html.parser")
            year_options = soup.find_all("option", value=re.compile(r"seriesId=15"))

            discovered = {}
            for opt in year_options:
                opt_text = opt.get_text(strip=True)
                href = opt.get("value", "")

                # Extract year from option text (e.g. "2024", "2023")
                year_match = re.match(r"^(20\d{2})$", opt_text)
                if not year_match:
                    continue
                year = int(year_match.group(1))

                # Extract raceId from the value URL
                race_id_match = re.search(r"raceId=(\d+)", href)
                if not race_id_match:
                    continue
                race_id = int(race_id_match.group(1))

                discovered[year] = race_id

            # Merge discovered with hardcoded (hardcoded as fallback)
            all_races = {**SYDNEY_HOBART_RACES, **discovered}

            for year, race_id in sorted(all_races.items()):
                if since and date(year, RACE_MONTH, RACE_DAY) < since:
                    continue
                events.append(EventRef(
                    source="sydneyhobart",
                    event_name=f"{year} Rolex Sydney Hobart Yacht Race",
                    event_url=f"{STANDINGS_URL}?seriesId={SERIES_SYDNEY_HOBART}&raceId={race_id}",
                    event_date=date(year, RACE_MONTH, RACE_DAY),
                    organizing_club="CYCA",
                    event_type="offshore",
                    metadata={"year": year, "race_id": race_id, "series_id": SERIES_SYDNEY_HOBART},
                ))

        print(f"  Discovered {len(events)} Sydney Hobart editions")
        return events

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        """Scrape results for a single Sydney Hobart race year.

        Fetches both the standings page (results) and the yachts page
        (sail numbers, boat types) and merges them.
        """
        meta = ref.metadata or {}
        year = meta.get("year", ref.event_date.year if ref.event_date else 2024)
        race_id = meta.get("race_id")
        series_id = meta.get("series_id", SERIES_SYDNEY_HOBART)

        if not race_id:
            race_id = SYDNEY_HOBART_RACES.get(year)
        if not race_id:
            print(f"  No race ID for {year}")
            return []

        async with get_http_client() as client:
            # Fetch yacht details first (sail numbers, boat types)
            yacht_details = await scrape_yacht_details(client, race_id, series_id)
            if yacht_details:
                print(f"  {year}: Found {len(yacht_details)} yacht entries")

            # Fetch standings page
            url = f"{STANDINGS_URL}?seriesId={series_id}&raceId={race_id}"
            try:
                resp = await fetch_with_retry(client, url, rate_limiter=sydneyhobart_limiter)
            except Exception as e:
                print(f"  {year}: Error fetching standings: {e}")
                return []

            results = parse_standings_html(
                resp.text, year, race_id,
                series_id=series_id,
                yacht_details=yacht_details,
            )

            irc_count = sum(1 for r in results if r.rating_value)
            finished_count = sum(1 for r in results if r.status == "finished")
            print(f"  {year}: {len(results)} yachts ({finished_count} finished, {irc_count} with TCC)")

        return results


async def scrape_all_years(
    since: date | None = None,
    year: int | None = None,
) -> list[NormalizedResult]:
    """Scrape Sydney Hobart results for all available years.

    Convenience function for direct use outside the RaceResultSource pattern.

    Args:
        since: Only scrape races after this date.
        year: If set, only scrape this specific year.
    """
    source = SydneyHobartSource()
    events = await source.discover_events(since=since)

    if year:
        events = [e for e in events if e.metadata and e.metadata.get("year") == year]

    all_results = []
    for event in events:
        results = await source.scrape_event(event)
        all_results.extend(results)

    return all_results
