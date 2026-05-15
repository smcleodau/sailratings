"""Generic Sailwave HTML results parser.

Sailwave is the most common race results publishing format used by yacht
clubs worldwide.  The software generates static HTML files with a
well-defined table structure that varies slightly between versions but
follows consistent patterns:

  - CSS classes: .summarytable, .summaryrace, .place1/.place2/.place3,
    .even, .odd, .rank1/.rank2/.rank3
  - Column headers in <th> elements within a <tr> inside the results table
  - Data rows alternate between 'even' and 'odd' classes
  - Two main page types:
    1. Series summary: per-race point columns plus Total/Nett
    2. Individual race: Start, Finish, Elapsed, Corrected, Points columns

This module provides:
  - parse_sailwave_html()  — parse a single Sailwave HTML file
  - discover_sailwave_links() — find Sailwave result URLs from an index page
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from irc_data.scrapers.result_base import NormalizedResult


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _safe_decimal(val: str | None) -> Decimal | None:
    """Parse a string to Decimal, returning None on failure."""
    if not val:
        return None
    val = val.strip().replace("\xa0", "").replace(",", "")
    if not val or val in ("&nbsp;", "-"):
        return None
    try:
        return Decimal(val)
    except (InvalidOperation, ValueError):
        return None


def _safe_int(val: str | None) -> int | None:
    """Parse a string to int, stripping ordinal suffixes like '1st', '2nd'."""
    if not val:
        return None
    val = val.strip()
    cleaned = re.sub(r"(st|nd|rd|th)\b", "", val, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"[^\d]", "", cleaned)
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_time(val: str | None) -> str | None:
    """Normalize a time string (HH:MM:SS or D:HH:MM:SS) to HH:MM:SS."""
    if not val:
        return None
    val = val.strip().replace("\xa0", "")
    if not val or val in ("&nbsp;", "-"):
        return None

    # D:HH:MM:SS or DD:HH:MM:SS
    m = re.match(r"(\d+):(\d{1,2}):(\d{2}):(\d{2})", val)
    if m:
        days, hours, mins, secs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        total_hours = days * 24 + hours
        return f"{total_hours}:{mins:02d}:{secs:02d}"

    # HH:MM:SS
    m = re.match(r"(\d+):(\d{2}):(\d{2})", val)
    if m:
        return val.strip()

    # MM:SS (short races)
    m = re.match(r"(\d{1,2}):(\d{2})$", val)
    if m:
        return f"0:{m.group(1).zfill(2)}:{m.group(2)}"

    return None


def _detect_status(text: str | None) -> str:
    """Detect finish status from cell text (DNF, DNS, DSQ, OCS, RET, DNC)."""
    if not text:
        return "finished"
    text = text.strip().upper()
    for code in ("DNC", "DNF", "DNS", "DSQ", "OCS", "RET", "NSC", "ZFP", "UFD", "BFD", "RDG"):
        if code in text:
            return code
    return "finished"


def _is_status_code(text: str) -> bool:
    """Check if a cell value is a status code rather than a numeric result."""
    return bool(re.match(
        r"^(DNC|DNF|DNS|DSQ|OCS|RET|NSC|ZFP|UFD|BFD|RDG|SCP|DPI)$",
        text.strip().upper(),
    ))


# ---------------------------------------------------------------------------
# Column header mapping
# ---------------------------------------------------------------------------

# Map normalized header text to canonical field names
_HEADER_MAP = {
    # Position
    "rank": "rank",
    "pos": "rank",
    "position": "rank",
    "place": "rank",
    # Fleet / class / division
    "fleet": "fleet",
    "class": "class",
    "division": "class",
    "div": "class",
    "group": "class",
    # Boat
    "boat": "boat_name",
    "yacht": "boat_name",
    "boat name": "boat_name",
    "yacht name": "boat_name",
    "name": "boat_name",
    # Sail number
    "sail no.": "sail_number",
    "sail no": "sail_number",
    "sailno": "sail_number",
    "sail number": "sail_number",
    "sail": "sail_number",
    # Type / design
    "type": "boat_type",
    "boat type": "boat_type",
    "design": "boat_type",
    "class/type": "boat_type",
    # Club
    "club": "club",
    # Skipper / owner
    "skipper/owner": "skipper",
    "skipper": "skipper",
    "helmname": "skipper",
    "helm": "skipper",
    "owner": "skipper",
    "helm name": "skipper",
    # Crew
    "crewname": "crew",
    "crew": "crew",
    "crew name": "crew",
    # Rating
    "rating": "rating",
    "rating {at start}": "rating",
    "irc": "rating",
    "tcc": "rating",
    "irc tcc": "rating",
    "irc rating": "rating",
    "py": "rating",
    "handicap": "rating",
    "tcf": "rating",
    "cbh": "rating",
    # Times
    "start": "start_time",
    "finish": "finish_time",
    "elapsed": "elapsed",
    "elapsed time": "elapsed",
    "corrected": "corrected",
    "corrected time": "corrected",
    # Performance
    "bce": "bce",
    "bcr": "bcr",
    "ave speed": "avg_speed",
    "average speed": "avg_speed",
    # Points
    "points": "points",
    "total": "total",
    "nett": "nett",
    "net": "nett",
}


def _map_header(header_text: str) -> str | None:
    """Map a Sailwave header cell text to a canonical field name."""
    h = header_text.strip().lower()
    # Remove content in curly braces like "Rating {At start}"
    h = re.sub(r"\{[^}]*\}", "", h).strip()
    # Direct lookup
    if h in _HEADER_MAP:
        return _HEADER_MAP[h]
    # Partial match for race columns like "Race01CW1", "R1", "R2"
    if re.match(r"^r(ace)?\s*\d", h, re.IGNORECASE):
        return None  # skip per-race point columns in series tables
    return None


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

def _extract_metadata(soup: BeautifulSoup) -> dict:
    """Extract event metadata from the Sailwave HTML page header area."""
    meta = {}

    # Look for title
    title_tag = soup.find("title")
    if title_tag:
        meta["title"] = title_tag.get_text(strip=True)

    # Sailwave pages typically have event info in the first few elements
    # before the main table.  Look for text patterns.
    body = soup.find("body")
    if not body:
        return meta

    # Sailwave version from footer
    footer_text = soup.get_text()
    m = re.search(r"Sailwave Scoring Software\s+([\d.]+)", footer_text)
    if m:
        meta["sailwave_version"] = m.group(1)

    # Scoring system info: "Sailed: N, Discards: N, ..."
    m = re.search(
        r"Sailed:\s*(\d+).*?Discards:\s*(\d+).*?To count:\s*(\d+)",
        footer_text,
    )
    if m:
        meta["sailed"] = int(m.group(1))
        meta["discards"] = int(m.group(2))
        meta["to_count"] = int(m.group(3))

    # Rating system
    m = re.search(r"Rating system:\s*(\w+)", footer_text)
    if m:
        meta["rating_system"] = m.group(1)

    # Entries
    m = re.search(r"Entries:\s*(\d+)", footer_text)
    if m:
        meta["entries"] = int(m.group(1))

    # Date - look for common date patterns in the page
    for pattern in [
        r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})",
        r"(\d{1,2}/\d{1,2}/\d{4})",
        r"(\d{4}-\d{2}-\d{2})",
    ]:
        m = re.search(pattern, footer_text, re.IGNORECASE)
        if m:
            date_str = m.group(1)
            for fmt in ["%d %B %Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y"]:
                try:
                    meta["event_date"] = datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue
            if "event_date" in meta:
                break

    return meta


# ---------------------------------------------------------------------------
# Table detection
# ---------------------------------------------------------------------------

def _find_results_tables(soup: BeautifulSoup) -> list[Tag]:
    """Find result tables in Sailwave HTML.

    Sailwave uses several patterns:
    1. Tables with class 'summarytable'
    2. Tables containing <th> headers with boat/rank columns
    3. Tables with enough rows and recognisable column headers
    """
    tables = []

    # Strategy 1: class="summarytable"
    for t in soup.find_all("table", class_="summarytable"):
        tables.append(t)

    if tables:
        return tables

    # Strategy 2: tables with <th> elements that map to known headers
    for t in soup.find_all("table"):
        ths = t.find_all("th")
        if not ths:
            continue
        header_texts = [th.get_text(strip=True).lower() for th in ths]
        mapped = [h for h in header_texts if _map_header(h) is not None]
        if len(mapped) >= 3 and any(_map_header(h) == "boat_name" for h in header_texts):
            tables.append(t)

    if tables:
        return tables

    # Strategy 3: any table with > 3 rows where first row looks like headers
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if len(rows) < 3:
            continue
        first_row = rows[0]
        cells = first_row.find_all(["th", "td"])
        if not cells:
            continue
        header_texts = [c.get_text(strip=True).lower() for c in cells]
        mapped = [h for h in header_texts if _map_header(h) is not None]
        if len(mapped) >= 3:
            tables.append(t)

    return tables


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_sailwave_html(
    html: str,
    source_url: str = "",
    event_name: str = "",
    organizing_club: str | None = None,
    event_type: str | None = None,
    event_date: date | None = None,
) -> list[NormalizedResult]:
    """Parse a Sailwave-generated HTML file into NormalizedResult objects.

    Handles both series summary tables (with per-race point columns) and
    individual race result tables (with elapsed/corrected times).

    Args:
        html: Raw HTML content of the Sailwave results page.
        source_url: URL the HTML was fetched from.
        event_name: Name to use if not extractable from the page.
        organizing_club: Club name to attach to results.
        event_type: Event type (offshore, coastal, etc.).
        event_date: Date override; extracted from page if not provided.

    Returns:
        List of NormalizedResult objects.
    """
    soup = BeautifulSoup(html, "html.parser")
    meta = _extract_metadata(soup)
    results: list[NormalizedResult] = []

    # Determine event name
    page_title = meta.get("title", "")
    if not event_name and page_title:
        event_name = page_title
    if not event_name:
        event_name = "Unknown Sailwave Event"

    # Determine date
    if not event_date:
        event_date = meta.get("event_date")

    # Determine rating system
    rating_system = meta.get("rating_system", "").upper()
    is_irc = "IRC" in rating_system or "IRC" in event_name.upper()

    tables = _find_results_tables(soup)
    if not tables:
        return results

    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # Parse header row
        header_row = rows[0]
        header_cells = header_row.find_all(["th", "td"])
        raw_headers = [c.get_text(strip=True) for c in header_cells]
        mapped_headers = [_map_header(h) for h in raw_headers]

        # Build column index
        col = {}
        race_columns = []  # indices of per-race point columns (series tables)
        for i, (raw, mapped) in enumerate(zip(raw_headers, mapped_headers)):
            if mapped:
                # If duplicate, keep the first occurrence
                if mapped not in col:
                    col[mapped] = i
            else:
                # Check if this is a per-race column (series summary)
                if re.match(r"^R(ace)?\s*\d", raw, re.IGNORECASE):
                    race_columns.append(i)

        # Must have boat_name to be useful
        if "boat_name" not in col:
            continue

        is_series_table = bool(race_columns) or ("total" in col and "nett" in col)
        is_race_table = "elapsed" in col or "corrected" in col

        # Determine fleet size from data rows
        data_rows = rows[1:]
        fleet_size = len(data_rows)

        # Track class fleet sizes
        class_counts: dict[str, int] = {}

        # First pass: count boats per class
        for row in data_rows:
            cells = row.find_all("td")
            if not cells or len(cells) < 2:
                continue
            fleet_idx = col.get("fleet")
            class_idx = col.get("class")
            class_name = None
            if fleet_idx is not None and fleet_idx < len(cells):
                class_name = cells[fleet_idx].get_text(strip=True)
            if not class_name and class_idx is not None and class_idx < len(cells):
                class_name = cells[class_idx].get_text(strip=True)
            if class_name:
                class_counts[class_name] = class_counts.get(class_name, 0) + 1

        # Track class place counters for series tables (rank may be within class)
        class_place_counters: dict[str, int] = {}

        # Second pass: parse results
        for row in data_rows:
            cells = row.find_all("td")
            if not cells:
                continue
            if len(cells) < 3:
                continue

            def cell_text(key: str) -> str | None:
                idx = col.get(key)
                if idx is not None and idx < len(cells):
                    t = cells[idx].get_text(strip=True)
                    return t if t else None
                return None

            boat_name = cell_text("boat_name")
            if not boat_name:
                continue

            sail_number = cell_text("sail_number")
            class_name = cell_text("fleet") or cell_text("class")
            boat_type = cell_text("boat_type")
            skipper = cell_text("skipper")
            club = cell_text("club")
            rating_str = cell_text("rating")
            rank_str = cell_text("rank")
            elapsed_str = cell_text("elapsed")
            corrected_str = cell_text("corrected")
            points_str = cell_text("points") or cell_text("nett")

            # Parse rating
            rating = _safe_decimal(rating_str)

            # Determine rating type
            rating_type = None
            if rating is not None:
                if is_irc or (rating and Decimal("0.7") <= rating <= Decimal("1.5")):
                    rating_type = "irc_tcc"
                elif rating and rating > Decimal("100"):
                    rating_type = "py"  # Portsmouth Yardstick
                else:
                    rating_type = "other"

            # Parse position
            place = _safe_int(rank_str)

            # Detect status from points column or other indicators
            status = "finished"
            if points_str and _is_status_code(points_str):
                status = points_str.strip().upper()
                points_str = None
            elif corrected_str and _is_status_code(corrected_str):
                status = corrected_str.strip().upper()
                corrected_str = None
            elif elapsed_str and _is_status_code(elapsed_str):
                status = elapsed_str.strip().upper()
                elapsed_str = None

            # For series tables, check nett column for DNC/DNF etc.
            nett_str = cell_text("nett")
            if nett_str and _is_status_code(nett_str):
                status = nett_str.strip().upper()

            # Parse times
            elapsed = _parse_time(elapsed_str)
            corrected = _parse_time(corrected_str)

            # Class fleet info
            class_fleet_size = class_counts.get(class_name) if class_name else None

            # Build raw_data
            raw = {
                "boat_type": boat_type,
                "skipper": skipper,
                "club": club,
                "rating_str": rating_str,
                "headers": raw_headers,
            }
            if points_str:
                raw["points"] = points_str
            if is_series_table:
                raw["table_type"] = "series_summary"
            if is_race_table:
                raw["table_type"] = "individual_race"

            # Add avg speed if present
            avg_speed = cell_text("avg_speed")
            if avg_speed:
                raw["avg_speed"] = avg_speed

            # Sailwave metadata
            if meta.get("sailwave_version"):
                raw["sailwave_version"] = meta["sailwave_version"]

            results.append(NormalizedResult(
                boat_name=boat_name,
                sail_number=sail_number,
                event_name=event_name,
                event_date=event_date,
                organizing_club=organizing_club or club,
                event_type=event_type,
                class_name=class_name,
                place=place,
                fleet_size=fleet_size,
                class_fleet_size=class_fleet_size,
                status=status,
                rating_type=rating_type,
                rating_value=rating,
                elapsed_time=elapsed,
                corrected_time=corrected,
                source_url=source_url,
                raw_data=raw,
            ))

    return results


# ---------------------------------------------------------------------------
# Link discovery
# ---------------------------------------------------------------------------

def discover_sailwave_links(
    html: str,
    base_url: str,
    pattern: str | None = None,
) -> list[dict]:
    """Discover Sailwave result file links from an index page.

    Scans the page for links to .htm/.html files that are likely
    Sailwave results.

    Args:
        html: HTML of the index/listing page.
        base_url: Base URL for resolving relative links.
        pattern: Optional regex to filter link hrefs.

    Returns:
        List of dicts with keys: url, text, filename.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)

        # Resolve relative URLs
        full_url = urljoin(base_url, href)

        # Skip non-result links
        if not re.search(r"\.(html?|php)(\?|$)", full_url, re.IGNORECASE):
            # Also accept links with 'weblink.go' pattern (Joomla redirect)
            if "weblink.go" not in href and "result" not in href.lower():
                continue

        # Apply optional filter
        if pattern and not re.search(pattern, full_url, re.IGNORECASE):
            continue

        # De-duplicate
        if full_url in seen:
            continue
        seen.add(full_url)

        filename = href.split("/")[-1].split("?")[0]
        links.append({
            "url": full_url,
            "text": text,
            "filename": filename,
        })

    return links


def is_sailwave_html(html: str) -> bool:
    """Detect whether an HTML string is Sailwave-generated output.

    Checks for characteristic Sailwave signatures:
    - "Sailwave Scoring Software" in the page
    - CSS classes like .summarytable, .summaryrace
    - Meta generator tag
    """
    if not html:
        return False
    # Fast string checks first
    if "Sailwave Scoring Software" in html:
        return True
    if "sailwave.com" in html.lower():
        return True
    if 'class="summarytable"' in html:
        return True
    if "summaryrace" in html:
        return True
    # Check meta generator
    soup = BeautifulSoup(html[:2000], "html.parser")
    meta = soup.find("meta", attrs={"name": "generator"})
    if meta and "sailwave" in (meta.get("content", "") or "").lower():
        return True
    return False
