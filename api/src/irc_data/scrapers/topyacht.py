"""Strategy 4: TopYacht race result scraper.

TopYacht (topyacht.net.au) generates static HTML race results that clubs host
on their own file servers.  The HTML uses a consistent template with tables
of class ``centre_results_table``, each with a ``<caption>`` identifying the
division and handicap system (e.g. "Division 1  IRC results  Start : 11:35").

Result structure (per club file server):
  {base_url}/{year}/{division}/index.htm          — lists series
  {base_url}/{year}/{division}/{series}/series.htm — lists races in a series
  {base_url}/{year}/{division}/{series}/{nn}RGrp{g}.htm — individual race result

The scraper discovers series and races from the index pages, then parses only
IRC-scored result pages.  PHS/ORC results from the same events are skipped
since we only care about IRC TCC values.

Targets: Southport YC and other Australian clubs that publish TopYacht results
with IRC divisions.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from irc_data.parsers.schemas import RaceResult
from irc_data.scrapers.base import RateLimiter, fetch_with_retry, get_http_client
from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource

rate_limiter = RateLimiter(min_delay=1.5, jitter=1.0)

# ---------------------------------------------------------------------------
# Known clubs that publish TopYacht results with IRC divisions.
#
# Each entry maps a short club key to:
#   base_url    — root of the results file server
#   club_name   — display name
#   divisions   — list of division sub-directories that carry IRC results
#   years       — range of years to scan (sailing-season year = start year)
#
# URL construction:  {base_url}/{year}/{division}/index.htm
#
# Two layout patterns exist on TopYacht:
#
#   1. Club-hosted (Southport YC style):
#      base_url = "http://files.southportyachtclub.com.au/results"
#      divisions = ["od"]
#      → http://files.southportyachtclub.com.au/results/2024/od/index.htm
#      The index.htm lists series like "Summer Short Passage" with links to
#      series.htm pages.  Each series.htm has an IRC column with race links.
#
#   2. Regatta / event (HIRW, ABRW, FoS style):
#      base_url = "https://topyacht.net.au/results"
#      divisions = ["hirw"]          (event code acts as the "division")
#      → https://topyacht.net.au/results/2024/hirw/index.htm
#      The index.htm lists ALL divisions (IRC + PHS + multihull etc.) with
#      series.htm links.  discover_races_from_series filters to IRC column.
#
#   3. Club on topyacht.net.au (ORWA, CYCSA, etc.):
#      base_url = "https://topyacht.net.au/results/orwa"
#      divisions = [""]              (no division sub-directory)
#      → https://topyacht.net.au/results/orwa/2022//index.htm
#      The double-slash is harmless; the server resolves it correctly.
#      The index page lists series with IRC columns.
# ---------------------------------------------------------------------------

TOPYACHT_CLUBS: dict[str, dict] = {
    # -----------------------------------------------------------------------
    # Queensland
    # -----------------------------------------------------------------------
    "SYC": {
        "club_name": "Southport Yacht Club",
        "base_url": "http://files.southportyachtclub.com.au/results",
        "divisions": ["od"],  # offshore division has IRC
        "years": list(range(2018, 2026)),
    },

    # -----------------------------------------------------------------------
    # Major Regattas (topyacht.net.au/results/{year}/{event}/)
    # -----------------------------------------------------------------------
    "HIRW": {
        "club_name": "Hamilton Island Race Week",
        "base_url": "https://topyacht.net.au/results",
        "divisions": ["hirw"],
        # 2018-2025 confirmed. IRC division names vary by year:
        #   2024-2025: rategold, rategoldwhite, rategoldblack, super40
        #   2022-2023: rate1..rate4 (+ ratezero in 2022)
        #   2018-2019: irc1..irc3 (+ irc4 in 2018)
        # discover_series_from_index picks up ALL divisions from the event
        # page; discover_races_from_series then filters to IRC column only.
        "years": list(range(2018, 2026)),
    },
    "ABRW": {
        "club_name": "Airlie Beach Race Week",
        "base_url": "https://topyacht.net.au/results",
        "divisions": ["abrw"],
        # Hosted by Whitsunday Sailing Club.  IRC division names vary:
        #   2017-2019: irc_pass (IRC Passage)
        #   2020: rat (Rating Passage)
        #   2024: pass-rat-1, pass-rat-2 (Rating Passage Div1/2)
        # The series.htm pages have an IRC column that the scraper picks up.
        # 2020/2021 may have been cancelled (COVID) — scraper handles 404s.
        "years": list(range(2017, 2026)),
    },
    "FOS": {
        "club_name": "Festival of Sails",
        "base_url": "https://topyacht.net.au/results",
        "divisions": ["fos"],
        # Royal Geelong YC flagship regatta, held every January.
        # IRC results found in "Racing Series" or "Passage Rating" divisions:
        #   2013: irc-1, irc-2, irc-3 (dedicated IRC divisions)
        #   2014-2019: rc-1, rc-2, rc-3 (or ra-1, ra-2) — Racing/Rating divs
        #     with IRC scoring inside series.htm
        #   2020-2025: pass-ratg-1..pass-ratg-4 — Passage Rating divs
        #     with IRC + ORC AP + AMS scoring inside series.htm
        # All have IRC column in series.htm that the scraper discovers.
        # 2021 may not exist (COVID cancellation).
        "years": list(range(2012, 2026)),
    },
    "SPS": {
        "club_name": "Sail Port Stephens",
        "base_url": "https://topyacht.net.au/results",
        "divisions": ["sps"],
        # IRC divisions vary by year:
        #   2021: IRC-ORC (NSW IRC & ORC combined)
        #   2022: rating1, rating2, rating3 (NSW Yachting Champs IRC & ORC)
        "years": [2021, 2022],
    },
    "IRCCHAMPS": {
        "club_name": "Audi IRC Australian Championships",
        "base_url": "https://topyacht.net.au/results",
        "divisions": ["ircchamps"],
        # 2014 confirmed at Newcastle Cruising Yacht Club — IRC only.
        # 2011 returned 404; may use a different path or not be online.
        "years": [2014],
    },

    # -----------------------------------------------------------------------
    # Victoria — clubs on topyacht.net.au/results/{club}/{year}/
    # -----------------------------------------------------------------------
    "ORCV": {
        "club_name": "Ocean Racing Club of Victoria",
        "base_url": "https://www.orcv.org.au/results",
        "divisions": [""],
        # ORCV hosts TopYacht results on their own domain.  Season pages at
        # orcv.org.au/results/2024-25/ list series.htm links directly.
        # IMPORTANT: ORCV uses season-format URLs (e.g. "2024-25") rather
        # than plain years.  The current scraper builds URLs as
        # {base_url}/{year}//index.htm which won't match.  The scraper's
        # URL builder needs a minor tweak to support season_format, OR
        # use the topyacht.net.au/results/orcv/ mirror instead.
        # topyacht.net.au/results/orcv/ shows the CURRENT season only.
        # Races include Melbourne to Hobart, Cock of the Bay, King Island,
        # Apollo Bay, Port Fairy, Devonport/Rudder Cup, Coastal Sprints.
        # IRC, AMS, ORC AP, and PHS are all scored; scraper filters to IRC.
        # NOTE: Until season-format URL support is added, only the current
        # season at topyacht.net.au/results/orcv/ can be scraped.  For
        # historical seasons, the scraper needs to construct URLs like
        # orcv.org.au/results/2024-25/ (without /index.htm).
        "years": list(range(2008, 2026)),
    },

    # -----------------------------------------------------------------------
    # Western Australia — clubs on topyacht.net.au/results/{club}/{year}/
    # -----------------------------------------------------------------------
    "ORWA": {
        "club_name": "Ocean Racing Club of Western Australia",
        "base_url": "https://topyacht.net.au/results/orwa",
        "divisions": [""],
        # Series include Blue Water, Short Haul, and season aggregates.
        # IRC and PHF handicap columns confirmed in series.htm pages.
        # 2022 confirmed working. 2023 confirmed (BW2324, SH2324). 2024+ may
        # return 403 at directory level but series.htm pages may still work.
        "years": list(range(2022, 2026)),
    },
    "RFBYC": {
        "club_name": "Royal Freshwater Bay Yacht Club",
        "base_url": "https://topyacht.net.au/results/rfbyc",
        "divisions": [""],
        # WA club with IRC offshore divisions (Farrawa Cup etc.).
        # Directory listing returns 403 but year pages may still serve
        # index.htm with series.htm links.
        "years": list(range(2022, 2025)),
    },

    # -----------------------------------------------------------------------
    # South Australia — clubs on topyacht.net.au/results/{club}/{year}/
    # -----------------------------------------------------------------------
    "CYCSA": {
        "club_name": "Cruising Yacht Club of South Australia",
        "base_url": "https://topyacht.net.au/results/cycsa",
        "divisions": [""],
        # SA IRC Championship, Division 1 and 2 IRC, Great Southern Regatta.
        # Directory listing returns 403 but year/index.htm may work.
        "years": list(range(2022, 2026)),
    },
    "LINCOLN_WEEK": {
        "club_name": "Port Lincoln Yacht Club — Lincoln Week",
        "base_url": "https://topyacht.net.au/results/plyc/lincolnweek",
        "divisions": [""],
        # Teakle Classic Adelaide to Port Lincoln + Lincoln Week Regatta.
        # IRC, AMS, PHS, and ORC all scored in series.htm — scraper
        # filters to IRC column.  2023 also hosted Australian Yachting
        # Championships with dedicated IRC divisions (ircchampsd1 etc.).
        "years": list(range(2023, 2026)),
    },

    # -----------------------------------------------------------------------
    # Victoria — more clubs on topyacht.net.au/results/{club}/{year}/
    # -----------------------------------------------------------------------
    "RYCV": {
        "club_name": "Royal Yacht Club of Victoria",
        "base_url": "https://topyacht.net.au/results/rycv",
        "divisions": [""],
        # IRC keelboat divisions.  Part of Port Phillip racing scene.
        # Directory listing returns 403.
        "years": list(range(2024, 2026)),
    },
    "PPNYC": {
        "club_name": "Port Phillip North Yacht Clubs",
        "base_url": "https://topyacht.net.au/results/ppnyc",
        "divisions": [""],
        # Combined racing between RYCV, RMYS, and HBYC.
        # Long Distance IRC divisions confirmed in research.
        # Directory listing returns 403.
        "years": list(range(2024, 2026)),
    },
    "HBYC": {
        "club_name": "Hobsons Bay Yacht Club",
        "base_url": "https://topyacht.net.au/results/hbyc",
        "divisions": [""],
        # Top of the Bay Regatta with PHS/AMS/IRC scoring.
        # Part of PPNYC combined racing.
        # Directory listing returns 403.
        "years": list(range(2022, 2024)),
    },
    "SYC_SANDRINGHAM": {
        "club_name": "Sandringham Yacht Club",
        "base_url": "https://topyacht.net.au/results/syc",
        "divisions": [""],
        # Mainly dinghy/keelboat PHS but some IRC may appear.
        # Directory listing returns 403.  The research notes "mainly
        # dinghy/keelboat PHS" so IRC yield may be low.
        "years": list(range(2022, 2026)),
    },

    # -----------------------------------------------------------------------
    # Tasmania — clubs with own domains
    # NOTE: RYCT (results.ryct.org.au) and DSS (dssinc.org.au) host
    # TopYacht results on their own domains but could not be verified
    # during URL testing (timeouts / redirect issues).  They are listed
    # here for completeness; the scraper will gracefully skip them if
    # the URLs are unreachable.
    # -----------------------------------------------------------------------
    # "RYCT": {
    #     "club_name": "Royal Yacht Club of Tasmania",
    #     "base_url": "https://results.ryct.org.au",
    #     "divisions": [""],
    #     # TopYacht-hosted results.  IRC divisions in Combined Clubs racing,
    #     # Harbour Series, Pennant races.  Years 2016-2022 per research.
    #     # Could not verify URL structure — results.ryct.org.au timed out.
    #     "years": list(range(2016, 2023)),
    # },
    # "DSS": {
    #     "club_name": "Derwent Sailing Squadron",
    #     "base_url": "https://dssinc.org.au/race-results",
    #     # DSS results page redirects; may not be standard TopYacht layout.
    #     # King of the Derwent and L2H both use IRC.  12 seasons of data.
    #     # Needs manual investigation of URL structure.
    #     "divisions": [""],
    #     "years": list(range(2014, 2026)),
    # },
}


def _safe_decimal(val: str) -> Decimal | None:
    if not val:
        return None
    val = val.strip().replace("\xa0", "").replace("&nbsp;", "")
    if not val:
        return None
    try:
        return Decimal(val)
    except (InvalidOperation, ValueError, AttributeError):
        return None


def _safe_int(val: str) -> int | None:
    try:
        return int(val.strip())
    except (ValueError, AttributeError):
        return None


def _parse_race_date(text: str) -> date | None:
    """Extract a date from TopYacht race heading text.

    Common formats:
        "Race 1  (23/11/2025)" or "Race 3 (01/02/2025)"
    """
    m = re.search(r"\((\d{1,2})/(\d{1,2})/(\d{4})\)", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def _extract_race_name(text: str) -> str | None:
    """Extract a race name/number from heading text like 'Race 1  (23/11/2025)'."""
    m = re.match(r"(Race\s+\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return text.strip() if text.strip() else None


def _detect_status(place_text: str) -> tuple[int | None, str]:
    """Parse the Place column — might be a number or a status code (DNC, DNF, etc)."""
    place_text = place_text.strip().upper()
    if not place_text or place_text == "&NBSP":
        return None, "finished"
    status_codes = {"DNC", "DNF", "DNS", "DSQ", "OCS", "RET", "RAF", "NSC", "DNE", "BFD", "UFD"}
    if place_text in status_codes:
        return None, place_text
    try:
        return int(place_text), "finished"
    except ValueError:
        return None, place_text


# ---------------------------------------------------------------------------
# TopYacht HTML parser
# ---------------------------------------------------------------------------

def parse_topyacht_html(
    html: str,
    source_url: str = "",
    organizing_club: str | None = None,
) -> list[RaceResult]:
    """Parse a TopYacht-template HTML race results page.

    TopYacht HTML tables use class ``centre_results_table`` with a ``<caption>``
    containing the division name and handicap system.  Only tables with
    "IRC" in the caption are parsed.

    Headers vary but typically include:
        Place, Sail No, Boat Name, Skipper, Fin Tim, Elapsd, AHC, Cor'd T, BCH, Score, Vis
    """
    soup = BeautifulSoup(html, "html.parser")
    results: list[RaceResult] = []

    # Extract event title from <title> or heading1 class
    event_name = ""
    title_el = soup.find("title")
    if title_el:
        event_name = title_el.get_text(strip=True)

    heading = soup.find(class_="heading1")
    if heading:
        event_name = heading.get_text(strip=True)

    # Extract race date from the paragraph containing the date
    race_date: date | None = None
    race_name: str | None = None
    for p in soup.find_all("p"):
        p_text = p.get_text(strip=True)
        if not race_date:
            parsed = _parse_race_date(p_text)
            if parsed:
                race_date = parsed
        if not race_name and re.match(r"Race\s+\d+", p_text, re.IGNORECASE):
            race_name = _extract_race_name(p_text)

    # Also try boldTextBlue class (used for "Race N" heading)
    if not race_name:
        race_heading = soup.find(class_="boldTextBlue")
        if race_heading:
            race_name = race_heading.get_text(strip=True)

    # Find result tables
    tables = soup.find_all("table", class_="centre_results_table")
    if not tables:
        # Fallback: try all tables (for Sailwave format or non-standard templates)
        tables = soup.find_all("table")

    for table in tables:
        # Check caption for IRC
        caption = table.find("caption")
        caption_text = caption.get_text(strip=True) if caption else ""

        # Only process IRC result tables
        is_irc = bool(re.search(r"\bIRC\b", caption_text, re.IGNORECASE))

        # If no caption, check headers for AHC/IRC column as fallback
        if not caption_text:
            header_row = table.find("tr")
            if header_row:
                header_texts = [h.get_text(strip=True).lower() for h in header_row.find_all(["th", "td"])]
                is_irc = any(h in ("ahc", "irc", "tcc") for h in header_texts)

        if not is_irc:
            continue

        # Extract division name from caption (e.g. "Division 1  IRC results  Start : 11:35")
        division_name = ""
        m = re.match(r"(Division\s+\d+)", caption_text, re.IGNORECASE)
        if m:
            division_name = m.group(1)

        # Get headers from first row
        header_row = table.find("tr")
        if not header_row:
            continue
        headers_els = header_row.find_all(["th", "td"])
        headers = [h.get_text(strip=True).lower() for h in headers_els]

        # Map columns — TopYacht headers are flexible
        col_map: dict[str, int] = {}
        for i, h in enumerate(headers):
            hl = h.strip().lower()
            if hl in ("place", "plc"):
                col_map["place"] = i
            elif hl in ("sail no", "sailno", "sail number", "sail"):
                col_map["sail_number"] = i
            elif hl in ("boat name", "boat", "boatname", "yacht"):
                col_map["boat_name"] = i
            elif hl in ("skipper", "helm", "helmname"):
                col_map["skipper"] = i
            elif hl in ("fin tim", "finish", "fin time", "fintime"):
                col_map["finish_time"] = i
            elif hl in ("elapsd", "elapsed", "elapsed time", "et"):
                col_map["elapsed"] = i
            elif hl in ("ahc", "irc", "tcc", "rating", "tcf", "handicap"):
                col_map["tcc"] = i
            elif hl in ("cor'd t", "corrected", "corrected time", "corr", "ct", "corr time"):
                col_map["corrected"] = i
            elif hl in ("bch",):
                col_map["bch"] = i
            elif hl in ("club",):
                col_map["club"] = i
            elif hl in ("class", "division", "fleet"):
                col_map["division"] = i

        # Require at least boat identification
        has_boat = "boat_name" in col_map or "skipper" in col_map
        if not has_boat:
            continue

        # Parse data rows (skip header)
        data_rows = table.find_all("tr")[1:]
        fleet_size = len(data_rows)

        for row in data_rows:
            cells = row.find_all(["td", "th"])
            cell_texts = [c.get_text(strip=True) for c in cells]

            if len(cell_texts) < 3:
                continue

            # Extract fields
            boat_name = cell_texts[col_map["boat_name"]] if "boat_name" in col_map else None
            skipper = cell_texts[col_map["skipper"]] if "skipper" in col_map else None

            # Sometimes boat_name is actually the sail number repeated — use skipper as name if available
            if not boat_name and skipper:
                boat_name = skipper
                skipper = None

            if not boat_name or boat_name.lower() in ("", "average", "discard"):
                continue

            sail_no = cell_texts[col_map["sail_number"]] if "sail_number" in col_map else None
            tcc = _safe_decimal(cell_texts[col_map["tcc"]]) if "tcc" in col_map else None

            place_text = cell_texts[col_map["place"]] if "place" in col_map else ""
            place, status = _detect_status(place_text)

            elapsed = cell_texts[col_map["elapsed"]] if "elapsed" in col_map else None
            if not elapsed or not elapsed.strip() or elapsed.strip() in ("\xa0",):
                elapsed = None

            corrected = cell_texts[col_map["corrected"]] if "corrected" in col_map else None
            if not corrected or not corrected.strip() or corrected.strip() in ("\xa0",):
                corrected = None

            finish_time = cell_texts[col_map["finish_time"]] if "finish_time" in col_map else None
            if not finish_time or not finish_time.strip() or finish_time.strip() in ("\xa0",):
                finish_time = None

            club_name = cell_texts[col_map["club"]] if "club" in col_map else None

            # Build the event name combining series + race
            full_event_name = event_name
            if race_name and race_name not in event_name:
                full_event_name = f"{event_name} - {race_name}"

            result = RaceResult(
                event_name=full_event_name,
                event_date=race_date,
                source_url=source_url,
                tcc_at_race=tcc,
                place=place,
                division=division_name or None,
                elapsed_time=elapsed,
                corrected_time=corrected,
                raw_data={
                    "boat_name": boat_name,
                    "sail_number": sail_no,
                    "skipper": skipper,
                    "club": club_name or organizing_club,
                    "club_name": organizing_club,
                    "finish_time": finish_time,
                    "division": division_name,
                    "fleet_size": fleet_size,
                    "race_name": race_name,
                    "race_date": str(race_date) if race_date else None,
                    "status": status,
                    "bch": cell_texts[col_map["bch"]] if "bch" in col_map else None,
                },
            )
            results.append(result)

    return results


# Also keep the Sailwave parser for backward compatibility / generic HTML results
parse_sailwave_html = parse_topyacht_html


# ---------------------------------------------------------------------------
# Discovery: crawl a club's file-server to find IRC result pages
# ---------------------------------------------------------------------------

async def _fetch_html(client, url: str) -> str | None:
    """Fetch a URL with rate limiting, returning HTML text or None on error."""
    try:
        resp = await fetch_with_retry(client, url, rate_limiter=rate_limiter, max_retries=1)
        return resp.text
    except Exception:
        return None


async def discover_series_from_index(
    client,
    index_url: str,
) -> list[dict]:
    """Parse an index.htm page and return series references.

    Each series is a dict with keys: name, url (to series.htm).
    """
    html = await _fetch_html(client, index_url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    series_list = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Series pages are linked as e.g. "sumshrtpass/series.htm"
        if href.endswith("/series.htm") or href.endswith("\\series.htm"):
            name = link.get_text(strip=True)
            full_url = urljoin(index_url, href.replace("\\", "/"))
            series_list.append({"name": name, "url": full_url})

    return series_list


async def discover_races_from_series(
    client,
    series_url: str,
) -> list[dict]:
    """Parse a series.htm page and return IRC race result page URLs.

    The series page contains a table with columns like PHS, IRC, ORC, Entrants.
    We look for links in the IRC column (identified by the column header).
    Race rows contain links like "01RGrp11.htm".
    """
    html = await _fetch_html(client, series_url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    # Extract series name
    series_name = ""
    heading = soup.find(class_="heading1")
    if heading:
        series_name = heading.get_text(strip=True)

    races = []
    table = soup.find("table", class_="centre_index_table")
    if not table:
        # Fallback to any table
        tables = soup.find_all("table")
        table = tables[0] if tables else None
    if not table:
        return []

    # Find the IRC column index from the header row
    header_row = table.find("tr")
    if not header_row:
        return []

    headers = [td.get_text(strip=True).upper() for td in header_row.find_all(["td", "th"])]
    irc_col_idx = None
    for i, h in enumerate(headers):
        if h == "IRC":
            irc_col_idx = i
            break

    if irc_col_idx is None:
        return []

    # Parse each race row and extract the IRC result link
    for row in table.find_all("tr")[1:]:  # skip header
        cells = row.find_all(["td", "th"])
        if len(cells) <= irc_col_idx:
            continue

        # Get race name from first column
        race_label = cells[0].get_text(strip=True)
        if not race_label or race_label.lower() == "series scores":
            # Series scores are cumulative, not individual race results — still useful
            pass

        # Check the IRC column cell for a link
        irc_cell = cells[irc_col_idx]
        link = irc_cell.find("a", href=True)
        if not link:
            continue

        href = link["href"]
        full_url = urljoin(series_url, href.replace("\\", "/"))

        races.append({
            "race_label": race_label,
            "url": full_url,
            "series_name": series_name,
            "is_series_scores": "series scores" in race_label.lower() or href.startswith("S"),
        })

    return races


# ---------------------------------------------------------------------------
# Main scraping functions
# ---------------------------------------------------------------------------

async def scrape_topyacht_event(url: str) -> list[RaceResult]:
    """Scrape a single TopYacht event URL."""
    async with get_http_client() as client:
        await rate_limiter.wait()
        resp = await client.get(url)
        resp.raise_for_status()
        return parse_topyacht_html(resp.text, source_url=url)


async def scrape_topyacht_events(urls: list[str]) -> list[RaceResult]:
    """Scrape multiple TopYacht event URLs.

    Args:
        urls: List of TopYacht result page URLs.

    Returns:
        Combined list of all race results with IRC data.
    """
    all_results = []

    async with get_http_client() as client:
        for url in urls:
            await rate_limiter.wait()
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                results = parse_topyacht_html(resp.text, source_url=url)
                irc_count = sum(1 for r in results if r.tcc_at_race)
                print(f"  {url}: {len(results)} results ({irc_count} with IRC)")
                all_results.extend(results)
            except Exception as e:
                print(f"  {url}: ERROR — {e}")

    print(f"\nTotal: {len(all_results)} results")
    irc_total = sum(1 for r in all_results if r.tcc_at_race)
    print(f"  {irc_total} with IRC TCC values")
    return all_results


async def scrape_club(
    club_key: str,
    since: date | None = None,
    max_series: int | None = None,
) -> list[RaceResult]:
    """Scrape all IRC results for a single club.

    Discovers series and races automatically from the club's file server,
    then parses all IRC result pages.

    Args:
        club_key: Key into TOPYACHT_CLUBS (e.g. "SYC").
        since: Only include results after this date.
        max_series: Limit number of series to scrape (for testing).

    Returns:
        List of RaceResult objects with IRC TCC values.
    """
    club = TOPYACHT_CLUBS.get(club_key)
    if not club:
        raise ValueError(f"Unknown TopYacht club: {club_key}. Known: {list(TOPYACHT_CLUBS.keys())}")

    base_url = club["base_url"]
    club_name = club["club_name"]
    divisions = club["divisions"]
    years = club["years"]
    all_results: list[RaceResult] = []

    async with get_http_client() as client:
        total_series = 0

        for year in sorted(years, reverse=True):
            # Quick date filter: skip entire years if they're before our cutoff
            if since and date(year + 1, 6, 30) < since:
                continue

            for division in divisions:
                index_url = f"{base_url}/{year}/{division}/index.htm"
                print(f"\n  Discovering series at {index_url}...")

                series_list = await discover_series_from_index(client, index_url)
                if not series_list:
                    print(f"    No series found")
                    continue

                print(f"    Found {len(series_list)} series")

                for series in series_list:
                    if max_series and total_series >= max_series:
                        break

                    series_name = series["name"]
                    series_url = series["url"]
                    print(f"\n    Series: {series_name}")

                    races = await discover_races_from_series(client, series_url)
                    # Filter out series scores — we want individual race results
                    race_pages = [r for r in races if not r["is_series_scores"]]
                    print(f"      {len(race_pages)} IRC race result pages")

                    for race_info in race_pages:
                        race_url = race_info["url"]
                        try:
                            html = await _fetch_html(client, race_url)
                            if not html:
                                continue

                            results = parse_topyacht_html(
                                html,
                                source_url=race_url,
                                organizing_club=club_name,
                            )

                            # Apply date filter
                            if since:
                                results = [
                                    r for r in results
                                    if r.event_date is None or r.event_date >= since
                                ]

                            if results:
                                irc_count = sum(1 for r in results if r.tcc_at_race)
                                print(f"      {race_info['race_label']}: "
                                      f"{len(results)} results ({irc_count} with IRC TCC)")
                                all_results.extend(results)
                        except Exception as e:
                            print(f"      {race_info['race_label']}: ERROR — {e}")

                    total_series += 1

    print(f"\n  Total for {club_name}: {len(all_results)} results")
    irc_total = sum(1 for r in all_results if r.tcc_at_race)
    print(f"    {irc_total} with IRC TCC values")
    return all_results


async def scrape_all_clubs(
    since: date | None = None,
    max_series: int | None = None,
) -> list[RaceResult]:
    """Scrape IRC results from all known TopYacht clubs.

    Args:
        since: Only include results after this date.
        max_series: Limit series per club (for testing).

    Returns:
        Combined list of all RaceResult objects.
    """
    all_results: list[RaceResult] = []

    for club_key in TOPYACHT_CLUBS:
        club_name = TOPYACHT_CLUBS[club_key]["club_name"]
        print(f"\nScraping TopYacht results for {club_name}...")
        try:
            club_results = await scrape_club(club_key, since=since, max_series=max_series)
            all_results.extend(club_results)
        except Exception as e:
            print(f"  ERROR scraping {club_name}: {e}")

    print(f"\nGrand total: {len(all_results)} results from {len(TOPYACHT_CLUBS)} clubs")
    return all_results


# ---------------------------------------------------------------------------
# RaceResultSource ABC implementation
# ---------------------------------------------------------------------------

class TopYachtSource(RaceResultSource):
    """TopYacht race result source implementing the standard interface."""

    def source_name(self) -> str:
        return "topyacht"

    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        """Discover all IRC race result pages across all known TopYacht clubs."""
        events: list[EventRef] = []

        async with get_http_client() as client:
            for club_key, club in TOPYACHT_CLUBS.items():
                base_url = club["base_url"]
                club_name = club["club_name"]

                for year in sorted(club["years"], reverse=True):
                    if since and date(year + 1, 6, 30) < since:
                        continue

                    for division in club["divisions"]:
                        index_url = f"{base_url}/{year}/{division}/index.htm"
                        series_list = await discover_series_from_index(client, index_url)

                        for series in series_list:
                            races = await discover_races_from_series(client, series["url"])
                            race_pages = [r for r in races if not r["is_series_scores"]]

                            for race_info in race_pages:
                                events.append(EventRef(
                                    source="topyacht",
                                    event_name=f"{series['name']} - {race_info['race_label']}",
                                    event_url=race_info["url"],
                                    organizing_club=club_name,
                                    metadata={
                                        "club_key": club_key,
                                        "year": year,
                                        "division": division,
                                        "series_name": series["name"],
                                    },
                                ))

        return events

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        """Scrape a single race result page and return NormalizedResult objects."""
        async with get_http_client() as client:
            html = await _fetch_html(client, ref.event_url)
            if not html:
                return []

            results = parse_topyacht_html(
                html,
                source_url=ref.event_url,
                organizing_club=ref.organizing_club,
            )

            # Convert RaceResult -> NormalizedResult
            normalized = []
            for r in results:
                raw = r.raw_data or {}
                status = raw.get("status", "finished")
                normalized.append(NormalizedResult(
                    boat_name=raw.get("boat_name", ""),
                    sail_number=raw.get("sail_number"),
                    event_name=r.event_name,
                    event_date=r.event_date,
                    event_series=ref.metadata.get("series_name") if ref.metadata else None,
                    organizing_club=ref.organizing_club,
                    race_name=raw.get("race_name"),
                    place=r.place,
                    fleet_size=raw.get("fleet_size"),
                    class_name=r.division,
                    status=status,
                    rating_type="irc_tcc" if r.tcc_at_race else None,
                    rating_value=r.tcc_at_race,
                    elapsed_time=r.elapsed_time,
                    corrected_time=r.corrected_time,
                    source_url=r.source_url,
                    raw_data=r.raw_data,
                ))

            return normalized
