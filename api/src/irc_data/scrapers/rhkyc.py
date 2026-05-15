"""Royal Hong Kong Yacht Club (RHKYC) race results scraper.

The RHKYC hosts several major IRC events each year including the
Around the Island Race, Autumn Regatta, China Coast Regatta,
Hong Kong Race Week, and various offshore races.

Results are published as PDF files at predictable URLs:
  https://www.rhkyc.org.hk/storage/app/media/Sailing/result/{EVENT}/{YEAR}/{FILE}

The sailing-results page at https://www.rhkyc.org.hk/sailing-results
contains links to all result files.

Data access strategy:
1. Scrape the sailing-results index page to discover all result PDF URLs.
2. Download each PDF and extract tabular data using pdfplumber.
3. Parse the table columns (Place, Division, Sail No., Name, Skipper,
   Start, Finish, Elapsed, RHKATI/IRC, Corrected, Additional) into
   NormalizedResult objects.
4. Only keep results from IRC-relevant divisions (Big Boat IRC, Fast Fleet,
   and overall ATI results that include IRC boats).

PDF table format (Around the Island Race 2024 example):
  Place | Division | Sail No. | Name | Skipper | Start | Finish | Elapsed | RHKATI | Corrected | Additional

The RHKATI rating is equivalent to an IRC TCC multiplier -- corrected
time = elapsed time * RHKATI.
"""

import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from irc_data.scrapers.base import RateLimiter, fetch_with_retry, get_http_client
from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource

RHKYC_BASE = "https://www.rhkyc.org.hk"
RESULTS_PAGE = f"{RHKYC_BASE}/sailing-results"
RESULTS_STORAGE = f"{RHKYC_BASE}/storage/app/media/Sailing/result"

rhkyc_limiter = RateLimiter(min_delay=2.0, jitter=1.0)

# Known events with their URL path slugs and whether they are IRC-relevant.
# We focus on events that are primarily keelboat / IRC racing.
IRC_EVENTS = {
    "AROUND-THE-ISLAND-RACE": {
        "name": "Around the Island Race",
        "type": "coastal",
        "years": list(range(2016, 2026)),
    },
    "AUTUMN-REGATTA": {
        "name": "Autumn Regatta",
        "type": "windward-leeward",
        "years": list(range(2014, 2026)),
    },
    "CHINA-COAST-REGATTA": {
        "name": "China Coast Regatta",
        "type": "windward-leeward",
        "years": list(range(2014, 2026)),
    },
    "HONG-KONG-RACE-WEEK": {
        "name": "Hong Kong Race Week",
        "type": "windward-leeward",
        "years": list(range(2014, 2026)),
    },
    "SPRING-REGATTA": {
        "name": "Spring Regatta",
        "type": "windward-leeward",
        "years": list(range(2016, 2026)),
    },
    "SUNSET-SERIES": {
        "name": "Sunset Series",
        "type": "windward-leeward",
        "years": list(range(2016, 2026)),
    },
    "ROLEX-CHINA-SEA-RACE": {
        "name": "Rolex China Sea Race",
        "type": "offshore",
        "years": list(range(2014, 2026)),
    },
    "NATIONS-CUP": {
        "name": "Nations Cup",
        "type": "windward-leeward",
        "years": list(range(2018, 2026)),
    },
    "TOP-DOG-TROPHY-SERIES": {
        "name": "Top Dog Trophy Series",
        "type": "windward-leeward",
        "years": list(range(2018, 2026)),
    },
}

# File patterns for IRC-relevant result PDFs.
# Big Boat divisions are IRC-rated keelboats.
IRC_FILE_PATTERNS = [
    "BigBoatDivision",
    "BigBigBoat",
    "IRC",
    "ATI_Overall",
    "ATI-Overall",
    "Overall",
    "FastFleet",
    "Fast_Fleet",
]

# Division keywords that indicate IRC-rated boats.
IRC_DIVISION_KEYWORDS = [
    "big boat irc",
    "fast fleet",
    "irc",
    "ati",
]


def _safe_decimal(val: str | None) -> Decimal | None:
    if not val:
        return None
    val = val.strip().replace("\xa0", "").replace(",", "")
    if not val or val == "-":
        return None
    try:
        return Decimal(val)
    except (InvalidOperation, ValueError):
        return None


def _safe_int(val: str | None) -> int | None:
    if not val:
        return None
    val = val.strip()
    if not val or val == "-":
        return None
    try:
        return int(re.sub(r"[^\d]", "", val))
    except (ValueError, TypeError):
        return None


def _parse_time(val: str | None) -> str | None:
    """Parse an HH:MM:SS or H:MM:SS time string."""
    if not val:
        return None
    val = val.strip()
    if not val or val == "-":
        return None
    m = re.match(r"(\d+):(\d+):(\d+)", val)
    if m:
        return val.strip()
    return None


def _detect_status(additional: str | None, place_text: str | None = None) -> str:
    """Detect race status from the 'Additional' column or place value."""
    check = ((additional or "") + " " + (place_text or "")).strip().upper()
    if not check:
        return "finished"
    for status in ["DNF", "DNS", "DSQ", "OCS", "RET", "NSC", "DNC", "RAF"]:
        if status in check:
            return status
    return "finished"


def _is_irc_relevant_division(division: str | None) -> bool:
    """Check if a division name indicates IRC-rated boats."""
    if not division:
        return False
    div_lower = division.lower()
    return any(kw in div_lower for kw in IRC_DIVISION_KEYWORDS)


def _is_irc_relevant_filename(filename: str) -> bool:
    """Check if a result filename is likely to contain IRC results."""
    fn_lower = filename.lower()
    return any(pat.lower() in fn_lower for pat in IRC_FILE_PATTERNS)


def _parse_result_line(line: str) -> dict | None:
    """Parse a single text line from an RHKYC result PDF.

    Lines follow a columnar format with fields separated by whitespace.
    The challenge is that division names and boat names can contain spaces.

    Strategy: work from the right side first (times are fixed-format),
    then parse the left side.

    Typical line:
        1 Fast Fleet 3 HKG2548 Rampage 88 Noel Chan 11:00:00 14:34:48 3:34:48 1.377 4:55:47

    Non-finished lines:
        Dragon D38 Phyloong Martin Tsai 8:40:00 - - - DNF
    """
    line = line.strip()
    if not line:
        return None

    # Time pattern: HH:MM:SS
    time_re = r"\d{1,2}:\d{2}:\d{2}"
    # Rating pattern: decimal number like 1.377 or 0.962
    rating_re = r"\d+\.\d+"
    # Sail number pattern: letter/digit combinations like HKG2548, GBR123, NED8809, DHKG50, EHKG1496
    sail_re = r"[A-Z]{1,5}\d{1,6}"

    # Check for non-finished entries (DNF, DNS, DSQ, RET, OCS, NSC, DNC)
    status_match = re.search(r"\b(DNF|DNS|DSQ|OCS|RET|NSC|DNC|RAF)\b", line)

    # Try to find all times from right to left
    times = re.findall(time_re, line)
    rating_matches = re.findall(rating_re, line)

    # Find sail number
    sail_match = None
    for m in re.finditer(sail_re, line):
        candidate = m.group()
        # Sail numbers have at least one letter and one digit
        if re.search(r"[A-Z]", candidate) and re.search(r"\d", candidate):
            # Skip very short matches that could be class names
            if len(candidate) >= 3:
                sail_match = m
                break

    if not sail_match:
        return None

    sail_no = sail_match.group()
    sail_start = sail_match.start()
    sail_end = sail_match.end()

    # Everything before the sail number: place + division
    prefix = line[:sail_start].strip()
    # Everything after the sail number: name + skipper + times + rating + additional
    suffix = line[sail_end:].strip()

    # Parse place from the beginning of prefix
    place = None
    division = prefix
    place_match = re.match(r"(\d+)\s+(.*)", prefix)
    if place_match:
        place = int(place_match.group(1))
        division = place_match.group(2).strip()

    # Parse suffix: Name Skipper Start Finish Elapsed Rating Corrected Additional
    # Find the start time (first HH:MM:SS in suffix)
    start_time_match = re.search(time_re, suffix)
    if start_time_match:
        before_times = suffix[:start_time_match.start()].strip()
        after_start = suffix[start_time_match.start():]
    elif status_match:
        # Non-finished: everything before the status is name+skipper
        before_status = suffix[:suffix.find(status_match.group())].strip()
        # Remove trailing dashes and spaces
        before_status = re.sub(r"[\s-]+$", "", before_status)
        before_times = before_status
        after_start = ""
    else:
        before_times = suffix
        after_start = ""

    # Split before_times into boat name and skipper.
    # This is tricky since both can contain spaces.
    # Heuristic: the name comes first, then the skipper.
    # For now, just store the whole thing as boat_name.
    boat_name = before_times

    # Parse the times section
    start = finish = elapsed = corrected = None
    rating = None
    additional = None

    if after_start:
        # Extract all time and number tokens
        tokens = after_start.split()
        time_tokens = []
        number_tokens = []
        text_tokens = []

        for t in tokens:
            if re.match(r"^\d{1,2}:\d{2}:\d{2}$", t):
                time_tokens.append(t)
            elif re.match(r"^\d+\.\d+$", t):
                number_tokens.append(t)
            elif t == "-":
                time_tokens.append(None)
            else:
                text_tokens.append(t)

        # Assign times: Start, Finish, Elapsed, Corrected
        if len(time_tokens) >= 1:
            start = time_tokens[0]
        if len(time_tokens) >= 2:
            finish = time_tokens[1]
        if len(time_tokens) >= 3:
            elapsed = time_tokens[2]
        if len(time_tokens) >= 4:
            corrected = time_tokens[3]

        # Rating is the decimal number
        if number_tokens:
            rating = number_tokens[0]

        # Additional text (status flags, Lady Helm, U18, etc.)
        if text_tokens:
            additional = " ".join(text_tokens)

    status = "finished"
    if status_match:
        status = status_match.group()
    elif place is None and not elapsed:
        status = "DNF"

    return {
        "place": place,
        "division": division if division else None,
        "sail_no": sail_no,
        "boat_name": boat_name,
        "start": start,
        "finish": finish,
        "elapsed": elapsed,
        "corrected": corrected,
        "rating": rating,
        "additional": additional,
        "status": status,
    }


def _try_table_extraction(pdf) -> list[dict]:
    """Try pdfplumber table extraction with 'text' strategy.

    This correctly separates Name from Skipper by using the column
    positions in the PDF.
    """
    parsed = []

    for page in pdf.pages:
        try:
            tables = page.extract_tables({
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
            })
        except Exception:
            continue

        for table in tables:
            if not table or len(table) < 2:
                continue

            # Find header row
            col_map = None
            for row_idx, row in enumerate(table):
                if not row:
                    continue
                row_text = " ".join((c or "").strip().lower() for c in row)
                if "place" in row_text and ("sail" in row_text or "name" in row_text):
                    col_map = _build_col_map(row)
                    continue

                if col_map and row:
                    entry = _parse_table_row(row, col_map)
                    if entry:
                        parsed.append(entry)

    return parsed


def _try_text_extraction(pdf) -> list[dict]:
    """Fallback: parse text lines from the PDF using regex."""
    parsed = []
    header_found = False

    for page in pdf.pages:
        text = page.extract_text()
        if not text:
            continue

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            line_lower = line.lower()
            if "place" in line_lower and "sail" in line_lower and "name" in line_lower:
                header_found = True
                continue

            if not header_found:
                continue

            if line.startswith("Division:") or line.startswith("Around ") or line.startswith("Page "):
                continue

            entry = _parse_result_line(line)
            if entry:
                parsed.append(entry)

    return parsed


def _build_col_map(header_row: list) -> dict:
    """Build column index from a header row."""
    col_map = {}
    for i, h in enumerate(header_row):
        h_clean = (h or "").strip().lower().replace("\n", " ")
        if not h_clean:
            continue
        if "place" in h_clean:
            col_map["place"] = i
        elif "division" in h_clean or "class" in h_clean:
            col_map["division"] = i
        elif "sail" in h_clean:
            col_map["sail_no"] = i
        elif h_clean == "name" or "boat" in h_clean:
            col_map["name"] = i
        elif "kipper" in h_clean or "helm" in h_clean:
            # "Skipper" sometimes splits across columns as "S" + "kipper"
            col_map["skipper"] = i
        elif h_clean == "s" and i + 1 < len(header_row):
            # Lone "S" before "kipper" -- the next col is the real skipper
            next_h = (header_row[i + 1] or "").strip().lower()
            if "kipper" in next_h:
                col_map["skipper"] = i
        elif "start" in h_clean:
            col_map["start"] = i
        elif "finish" in h_clean:
            col_map["finish"] = i
        elif "elapsed" in h_clean:
            col_map["elapsed"] = i
        elif "rhkati" in h_clean or "rating" in h_clean or h_clean == "irc":
            col_map["rating"] = i
        elif "corrected" in h_clean:
            col_map["corrected"] = i
        elif "additional" in h_clean or "remark" in h_clean or "note" in h_clean:
            col_map["additional"] = i
    return col_map


def _parse_table_row(row: list, col_map: dict) -> dict | None:
    """Parse a single table row from pdfplumber extraction."""
    def cell(key):
        idx = col_map.get(key)
        if idx is not None and idx < len(row):
            val = row[idx]
            return (val or "").strip() if val else None
        return None

    # Merge empty columns that may contain parts of multi-word values
    # For division, merge consecutive non-empty cells between place and sail_no
    boat_name = cell("name")
    if not boat_name:
        return None

    # Handle division which might span multiple columns
    division = cell("division")
    if not division:
        # Try to reconstruct division from columns between place and sail_no
        place_idx = col_map.get("place", 0)
        sail_idx = col_map.get("sail_no", len(row))
        parts = []
        for i in range(place_idx + 1, min(sail_idx, len(row))):
            if i not in col_map.values():
                val = (row[i] or "").strip()
                if val:
                    parts.append(val)
            elif i == col_map.get("division"):
                val = (row[i] or "").strip()
                if val:
                    parts.append(val)
        if parts:
            division = " ".join(parts)

    sail_no = cell("sail_no")
    skipper = cell("skipper")
    elapsed = _parse_time(cell("elapsed"))
    corrected = _parse_time(cell("corrected"))
    rating_str = cell("rating")
    additional = cell("additional")
    place_text = cell("place")
    place = _safe_int(place_text)
    status = _detect_status(additional, place_text)

    if place is None and status == "finished" and not elapsed:
        status = "DNF"

    return {
        "place": place,
        "division": division,
        "sail_no": sail_no,
        "boat_name": boat_name,
        "skipper": skipper,
        "elapsed": elapsed,
        "corrected": corrected,
        "rating": rating_str,
        "additional": additional,
        "status": status,
    }


def parse_pdf_results(
    pdf_bytes: bytes,
    event_name: str,
    event_date: date | None,
    source_url: str,
    organizing_club: str = "RHKYC",
    event_type: str | None = None,
) -> list[NormalizedResult]:
    """Parse a RHKYC result PDF into NormalizedResult objects.

    First tries pdfplumber table extraction with 'text' strategy, which
    correctly separates Name from Skipper columns.  Falls back to
    line-by-line text parsing if table extraction fails.

    Typical column layout:
        Place | Division | Sail No. | Name | Skipper | Start | Finish |
        Elapsed | RHKATI | Corrected | Additional
    """
    try:
        import pdfplumber
    except ImportError:
        print("  pdfplumber not installed -- pip install pdfplumber")
        return []

    try:
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception as e:
        print(f"  Error opening PDF: {e}")
        return []

    # Strategy 1: Line-by-line text parsing (most robust)
    parsed_entries = _try_text_extraction(pdf)

    # Strategy 2: Fallback to table extraction with text strategy
    if not parsed_entries:
        parsed_entries = _try_table_extraction(pdf)

    pdf.close()

    if not parsed_entries:
        return []

    fleet_size = len(parsed_entries)
    results = []

    for entry in parsed_entries:
        rating = _safe_decimal(entry.get("rating"))

        results.append(NormalizedResult(
            boat_name=entry["boat_name"],
            sail_number=entry.get("sail_no"),
            event_name=event_name,
            event_date=event_date,
            event_series=event_name,
            organizing_club=organizing_club,
            event_type=event_type,
            race_name=entry.get("division"),
            place=entry.get("place"),
            fleet_size=fleet_size,
            class_name=entry.get("division"),
            class_place=entry.get("place"),
            class_fleet_size=fleet_size,
            status=entry.get("status", "finished"),
            rating_type="irc_tcc" if rating else None,
            rating_value=rating,
            elapsed_time=entry.get("elapsed"),
            corrected_time=entry.get("corrected"),
            source_url=source_url,
            raw_data={
                "skipper": entry.get("skipper"),
                "division": entry.get("division"),
                "additional": entry.get("additional"),
                "source_file": source_url.split("/")[-1] if source_url else None,
            },
        ))

    return results


async def discover_result_files(
    client,
    event_slug: str,
    event_info: dict,
) -> list[tuple[str, int, str]]:
    """Try to discover available result files for an event across years.

    Returns list of (url, year, filename) tuples.
    """
    found = []
    for year in event_info["years"]:
        # Try common IRC-relevant file patterns
        for pattern in IRC_FILE_PATTERNS:
            for suffix in [".pdf", ".htm", ".html"]:
                # Try numbered divisions: BigBoatDivision0, BigBoatDivision1, etc.
                if "Division" in pattern:
                    for div_num in range(4):
                        filename = f"{pattern}{div_num}{suffix}"
                        url = f"{RESULTS_STORAGE}/{event_slug}/{year}/{filename}"
                        found.append((url, year, filename))
                else:
                    filename = f"{pattern}{suffix}"
                    url = f"{RESULTS_STORAGE}/{event_slug}/{year}/{filename}"
                    found.append((url, year, filename))

        # Also try event-specific patterns for Around the Island Race
        if "AROUND-THE-ISLAND" in event_slug:
            prefix = f"ATIR{year}"
            for suffix_name in ["ATI_Overall", "ATI-Overall", "IRC_Overall", "ATI"]:
                for ext in [".pdf"]:
                    filename = f"{prefix}-{suffix_name}{ext}"
                    url = f"{RESULTS_STORAGE}/{event_slug}/{year}/{filename}"
                    found.append((url, year, filename))

    return found


class RHKYCSource(RaceResultSource):
    """RHKYC race results source.

    Discovers result PDFs from the sailing-results page and parses them
    with pdfplumber to extract IRC race results.
    """

    def source_name(self) -> str:
        return "rhkyc"

    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        """Discover RHKYC result files by scraping the sailing-results page.

        Returns one EventRef per result file found on the index page.
        Only includes files whose names suggest IRC-relevant content.
        """
        events = []
        min_year = since.year if since else 2008

        async with get_http_client() as client:
            # Fetch the main sailing-results page to get all links
            await rhkyc_limiter.wait()
            try:
                resp = await client.get(RESULTS_PAGE)
                if resp.status_code != 200:
                    print(f"  Failed to fetch results page: {resp.status_code}")
                    return events
            except Exception as e:
                print(f"  Error fetching results page: {e}")
                return events

            # Extract all PDF/HTML links from the page
            found_urls = set()
            for m in re.finditer(
                r'href="([^"]*storage/app/media/Sailing/result/[^"]*\.(?:pdf|htm|html))"',
                resp.text,
                re.IGNORECASE,
            ):
                url = m.group(1)
                if not url.startswith("http"):
                    url = urljoin(RHKYC_BASE, url)
                found_urls.add(url)

            print(f"  Found {len(found_urls)} total result files on index page")

            # Filter to IRC-relevant files and parse event/year info
            for url in sorted(found_urls):
                # Parse the URL structure: .../result/{EVENT}/{YEAR}/{FILE}
                m = re.search(
                    r"/result/([^/]+)/(\d{4})/([^/]+)$",
                    url,
                )
                if not m:
                    continue

                event_slug = m.group(1)
                year = int(m.group(2))
                filename = m.group(3)

                if year < min_year:
                    continue

                # Only include IRC-relevant files
                if not _is_irc_relevant_filename(filename):
                    continue

                # Look up event info
                event_info = IRC_EVENTS.get(event_slug, {})
                event_name_pretty = event_info.get("name", event_slug.replace("-", " ").title())
                event_type = event_info.get("type")

                events.append(EventRef(
                    source="rhkyc",
                    event_name=f"{year} RHKYC {event_name_pretty} - {filename}",
                    event_url=url,
                    event_date=date(year, 6, 1),  # Approximate
                    organizing_club="RHKYC",
                    event_type=event_type,
                    metadata={
                        "year": year,
                        "event_slug": event_slug,
                        "event_name": event_name_pretty,
                        "filename": filename,
                    },
                ))

            # Also probe known event/year combinations not found on the index page
            for event_slug, event_info in IRC_EVENTS.items():
                for year in event_info["years"]:
                    if year < min_year:
                        continue

                    # Try BigBoatDivision0.pdf which is the most common IRC file
                    for div_num in range(4):
                        probe_url = f"{RESULTS_STORAGE}/{event_slug}/{year}/BigBoatDivision{div_num}.pdf"
                        if probe_url not in found_urls:
                            # We'll add it as a candidate -- scrape_event will check if it exists
                            event_name_pretty = event_info["name"]
                            filename = f"BigBoatDivision{div_num}.pdf"
                            events.append(EventRef(
                                source="rhkyc",
                                event_name=f"{year} RHKYC {event_name_pretty} - {filename}",
                                event_url=probe_url,
                                event_date=date(year, 6, 1),
                                organizing_club="RHKYC",
                                event_type=event_info.get("type"),
                                metadata={
                                    "year": year,
                                    "event_slug": event_slug,
                                    "event_name": event_name_pretty,
                                    "filename": filename,
                                    "probed": True,
                                },
                            ))

            # Deduplicate by URL
            seen = set()
            deduped = []
            for e in events:
                if e.event_url not in seen:
                    seen.add(e.event_url)
                    deduped.append(e)
            events = deduped

            print(f"  {len(events)} IRC-relevant result files to scrape")

        return events

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        """Download a result PDF and parse it into NormalizedResult objects."""
        year = ref.metadata["year"]
        event_name = ref.metadata["event_name"]
        filename = ref.metadata["filename"]

        async with get_http_client() as client:
            await rhkyc_limiter.wait()
            try:
                resp = await client.get(ref.event_url)
                if resp.status_code == 404:
                    # Probed URL that doesn't exist
                    return []
                if resp.status_code != 200:
                    return []
            except Exception:
                return []

            content_type = resp.headers.get("content-type", "")

            # Check we actually got a PDF (not a redirect to homepage)
            if filename.lower().endswith(".pdf"):
                if "pdf" not in content_type.lower() and len(resp.content) < 1000:
                    # Got an HTML page (probably redirect to homepage)
                    return []

                return parse_pdf_results(
                    pdf_bytes=resp.content,
                    event_name=f"{year} RHKYC {event_name}",
                    event_date=date(year, 6, 1),
                    source_url=ref.event_url,
                    organizing_club="RHKYC",
                    event_type=ref.event_type,
                )

            elif filename.lower().endswith((".htm", ".html")):
                # HTML result files -- parse as HTML table
                return _parse_html_results(
                    html=resp.text,
                    event_name=f"{year} RHKYC {event_name}",
                    event_date=date(year, 6, 1),
                    source_url=ref.event_url,
                    event_type=ref.event_type,
                )

        return []


def _parse_html_results(
    html: str,
    event_name: str,
    event_date: date | None,
    source_url: str,
    event_type: str | None = None,
) -> list[NormalizedResult]:
    """Parse RHKYC HTML result files (older results use .htm format).

    These typically use Sailwave HTML output with standard table structure.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []

    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # Parse header
        header_cells = rows[0].find_all(["td", "th"])
        headers = [
            c.get_text(strip=True).lower().replace("\n", " ")
            for c in header_cells
        ]

        # Build column map
        col_map = {}
        for i, h in enumerate(headers):
            if "place" in h or "pos" in h or "rank" in h:
                col_map["place"] = i
            elif "division" in h or "class" in h:
                col_map["division"] = i
            elif "sail" in h:
                col_map["sail_no"] = i
            elif h in ("name", "boat", "boat name", "yacht"):
                col_map["name"] = i
            elif "skipper" in h or "helm" in h:
                col_map["skipper"] = i
            elif "elapsed" in h:
                col_map["elapsed"] = i
            elif "corrected" in h:
                col_map["corrected"] = i
            elif "rating" in h or "tcc" in h or "rhkati" in h or "irc" in h:
                col_map["rating"] = i
            elif "additional" in h or "remark" in h or "note" in h:
                col_map["additional"] = i

        if "name" not in col_map:
            continue

        fleet_size = len(rows) - 1
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            def cell(key):
                idx = col_map.get(key)
                if idx is not None and idx < len(cells):
                    return cells[idx].get_text(strip=True)
                return None

            boat_name = cell("name")
            if not boat_name:
                continue

            place = _safe_int(cell("place"))
            division = cell("division")
            sail_no = cell("sail_no")
            elapsed = _parse_time(cell("elapsed"))
            corrected = _parse_time(cell("corrected"))
            rating = _safe_decimal(cell("rating"))
            additional = cell("additional")
            status = _detect_status(additional, cell("place"))

            results.append(NormalizedResult(
                boat_name=boat_name,
                sail_number=sail_no,
                event_name=event_name,
                event_date=event_date,
                event_series=event_name,
                organizing_club="RHKYC",
                event_type=event_type,
                race_name=division,
                place=place,
                fleet_size=fleet_size,
                class_name=division,
                status=status,
                rating_type="irc_tcc" if rating else None,
                rating_value=rating,
                elapsed_time=elapsed,
                corrected_time=corrected,
                source_url=source_url,
                raw_data={
                    "skipper": cell("skipper"),
                    "division": division,
                    "additional": additional,
                },
            ))

    return results
