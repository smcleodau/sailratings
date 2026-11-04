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
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from irc_data.parsers.schemas import RaceResult
from irc_data.scrapers.base import RateLimiter, fetch_with_retry, get_http_client
from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource

rate_limiter = RateLimiter(min_delay=1.5, jitter=1.0)

# Highest sailing-season year to scan. Resolved at import time so a long-lived
# process still picks up the new season after midnight on 1 Jan. TopYacht
# organises by sailing-season-start year, which is the current calendar year
# for the SH season (Sep–Aug) — so we extend through current_year + 1.
_NOW_YEAR = datetime.utcnow().year
_LATEST_SEASON = _NOW_YEAR + 1

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
        "years": list(range(2018, _LATEST_SEASON + 1)),
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
        "years": list(range(2018, _LATEST_SEASON + 1)),
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
        "years": list(range(2017, _LATEST_SEASON + 1)),
    },
    "MIRW": {
        "club_name": "Magnetic Island Race Week",
        "base_url": "https://topyacht.net.au/results",
        "divisions": ["mirw"],
        # Townsville YC, late Aug/early Sep. IRC division confirmed 2026
        # (not listed in TopYacht's events directory, so only reachable here).
        "years": list(range(2024, _LATEST_SEASON + 1)),
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
        "years": list(range(2012, _LATEST_SEASON + 1)),
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
        "years": list(range(2008, _LATEST_SEASON + 1)),
    },

    # -----------------------------------------------------------------------
    # Western Australia — clubs on topyacht.net.au/results/{club}/{year}/
    # -----------------------------------------------------------------------
    "FSC": {
        "club_name": "Fremantle Sailing Club",
        "base_url": "https://topyacht.net.au/results/fsc",
        # Keelboat tree is named for the season's end year: 2025/kb26/.
        # IRC in Success Cup, Coastal Cup, West Coaster etc. The club site
        # sits behind a JavaScript bot check, so it can't be crawled.
        "divisions": ["kb{end_yy}"],
        "years": list(range(2025, _LATEST_SEASON + 1)),
    },
    "ORWA": {
        "club_name": "Ocean Racing Club of Western Australia",
        "base_url": "https://topyacht.net.au/results/orwa",
        "divisions": [""],
        # Series include Blue Water, Short Haul, and season aggregates.
        # IRC and PHF handicap columns confirmed in series.htm pages.
        # 2022 confirmed working. 2023 confirmed (BW2324, SH2324). 2024+ may
        # return 403 at directory level but series.htm pages may still work.
        "years": list(range(2022, _LATEST_SEASON + 1)),
    },
    "RFBYC": {
        "club_name": "Royal Freshwater Bay Yacht Club",
        "base_url": "https://topyacht.net.au/results/rfbyc",
        "divisions": [""],
        # WA club with IRC offshore divisions (Farrawa Cup etc.).
        # Directory listing returns 403 but year pages may still serve
        # index.htm with series.htm links.
        "years": list(range(2022, _LATEST_SEASON + 1)),
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
        "years": list(range(2022, _LATEST_SEASON + 1)),
    },
    "LINCOLN_WEEK": {
        "club_name": "Port Lincoln Yacht Club — Lincoln Week",
        "base_url": "https://topyacht.net.au/results/plyc/lincolnweek",
        "divisions": [""],
        # Teakle Classic Adelaide to Port Lincoln + Lincoln Week Regatta.
        # IRC, AMS, PHS, and ORC all scored in series.htm — scraper
        # filters to IRC column.  2023 also hosted Australian Yachting
        # Championships with dedicated IRC divisions (ircchampsd1 etc.).
        "years": list(range(2023, _LATEST_SEASON + 1)),
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
        "years": list(range(2024, _LATEST_SEASON + 1)),
    },
    "PPNYC": {
        "club_name": "Port Phillip North Yacht Clubs",
        "base_url": "https://topyacht.net.au/results/ppnyc",
        "divisions": [""],
        # Combined racing between RYCV, RMYS, and HBYC.
        # Long Distance IRC divisions confirmed in research.
        # Directory listing returns 403.
        "years": list(range(2024, _LATEST_SEASON + 1)),
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
        "years": list(range(2022, _LATEST_SEASON + 1)),
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
    #     "years": list(range(2014, _LATEST_SEASON + 1)),
    # },
}


# "IRC", "IRC  Div1", "IRC TCC", "IRC1", "IRC_NS", "IRCNS" (non-spinnaker) —
# but not words that merely contain the letters (e.g. "Circuit").
_IRC_HEADER_RE = re.compile(r"(?<![A-Za-z])IRC(?:NS)?(?![A-Za-z])", re.I)


def club_index_url(base_url: str, year: int, division: str) -> str:
    """``{base}/{year}/{division}/index.htm``; ``{end_yy}`` in a division
    expands to the season's two-digit end year (FSC: 2025/kb26)."""
    division = division.format(end_yy=f"{(year + 1) % 100:02d}")
    return f"{base_url}/{year}/{division}/index.htm"


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


def _plausible(d: date) -> date | None:
    """Drop dates no race can have (club typos such as 2106 for 2016)."""
    return d if 1990 <= d.year <= date.today().year + 1 else None


def _parse_race_date(text: str) -> date | None:
    """Extract a date from TopYacht race heading text.

    Common formats:
        "Race 1  (23/11/2025)" or "Race 3 (01/02/2025)"
    """
    m = re.search(r"\((\d{1,2})/(\d{1,2})/(\d{4})\)", text)
    if m:
        try:
            return _plausible(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
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
        is_irc = bool(_IRC_HEADER_RE.search(caption_text))

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
                    "caption": " ".join(caption_text.split()) or None,
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


def irc_column_indexes(headers: list[str]) -> list[int]:
    """Indexes of IRC-scored columns in a series.htm header row.

    Clubs label them "IRC", "IRC  Div1", "IRC TCC", "IRC Overall", "IRCNS"…
    — an exact "IRC" match silently dropped Festival of Sails and similar
    multi-division regattas.
    """
    return [i for i, h in enumerate(headers) if _IRC_HEADER_RE.search(h or "")]


def _label_date(label: str) -> date | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", label or "")
    if not m:
        return None
    try:
        return _plausible(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
    except ValueError:
        return None


async def discover_races_from_series(
    client,
    series_url: str,
    since: date | None = None,
) -> list[dict]:
    """Parse a series.htm page and return IRC race result page URLs.

    The series page contains a table with columns like PHS, IRC, ORC, Entrants.
    We look for links in every IRC column (see ``irc_column_indexes``).
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

    headers = [td.get_text(" ", strip=True) for td in header_row.find_all(["td", "th"])]
    irc_cols = irc_column_indexes(headers)
    if not irc_cols:
        return []

    seen_urls: set[str] = set()
    for row in table.find_all("tr")[1:]:  # skip header
        cells = row.find_all(["td", "th"])
        race_label = cells[0].get_text(" ", strip=True) if cells else ""

        # Rows are labelled "dd/mm/yyyy ..." or "Race 4 - 26/01/2026" — skip
        # races before the cutoff without fetching them (the daily crawl
        # would otherwise reopen every race of every season).
        label_date = _label_date(race_label)
        if since and label_date and label_date < since:
            continue

        for col in irc_cols:
            if len(cells) <= col:
                continue
            link = cells[col].find("a", href=True)
            if not link:
                continue
            href = link["href"]
            full_url = urljoin(series_url, href.replace("\\", "/"))
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            races.append({
                "race_label": race_label,
                "url": full_url,
                "series_name": series_name,
                "irc_column": headers[col],
                "is_series_scores": "series scores" in race_label.lower() or href.startswith("S"),
            })

    return races


# ---------------------------------------------------------------------------
# Events directory (topyacht.com.au/web/events/)
# ---------------------------------------------------------------------------
#
# Clubs no longer keep a predictable {base}/{year}/{division}/ layout on
# topyacht.net.au: many publish on their own sites, and the rest use paths
# like hbyc/2025/club_racing/ or rycv/2025/summer/BBst26/. TopYacht's public
# events directory lists every event with an "Enter" link (the logged-in
# entry system, not needed here) and a "Results" link, which is the reliable
# starting point.

EVENTS_DIRECTORY_URL = "https://topyacht.com.au/web/events/"
# Classic TopYacht pages: index/series pages, the topyacht.net.au results tree
# (now served from tes.topyacht.net.au), and club-hosted TopYacht trees like
# orcv.org.au/results/2025-26/, files.southportyachtclub.com.au/results/2025/
# or dssinc-results.org/results/2026/.
_TOPYACHT_PAGE_RE = re.compile(
    r"(?:/index\.htm|/series\.htm)(?:[?#].*)?$|topyacht\.net\.au/results/|/results/20\d\d(?:-\d\d)?/",
    re.I,
)
# TopYacht's newer cloud platform: a JavaScript app at topyacht.net whose
# public results pages read unauthenticated JSON from app.topyacht.net.
# Southport YC moved its offshore (IRC) racing here for 2026/27.
_PUBLICRESULTS_RE = re.compile(r"topyacht\.net/publicresults/(?:IndexResults|SeriesIndex)\?", re.I)
PUBLICRESULTS_API = "https://app.topyacht.net/api/PublishResults/clubs/"
PUBLICRESULTS_PAGE = "https://topyacht.net/publicresults/SeriesIndex"
_SKIP_EXT_RE = re.compile(r"\.(?:pdf|jpe?g|png|gif|svg|docx?|xlsx?|csv|zip|mp4)$", re.I)
_SKIP_PATH_RE = re.compile(r"/(?:news|tag|category|author|feed|wp-content|wp-json|wp-admin)/", re.I)
_YEAR_RE = re.compile(r"(?<!\d)(20\d\d)(?!\d)")


def parse_events_directory(html: str, years: set[int]) -> list[dict]:
    """Events whose name mentions one of ``years`` and that have a Results link."""
    soup = BeautifulSoup(html, "html.parser")
    year_re = re.compile(r"\b(" + "|".join(str(y) for y in sorted(years)) + r")\b")
    events = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        name = " ".join(cells[0].get_text(" ", strip=True).split())
        if not year_re.search(name):
            continue
        link = next((a for a in row.find_all("a", href=True)
                     if "result" in a.get_text(strip=True).lower() and a["href"].strip()), None)
        if not link:
            continue
        club = cells[2].get_text(" ", strip=True) if len(cells) > 2 else ""
        events.append({"name": name, "club": club, "results_url": link["href"].strip()})
    return events


def season_url_variants(url: str, event_name: str) -> list[str]:
    """Newer-season guesses for a stale directory Results link.

    Clubs clone last season's event in TopYacht's directory without updating
    the Results link, so "ORCV Season 2026 - 2027" still points at
    orcv.org.au/results/2024-25/ and "ORWA Season 2025-26" at orwa/2024/.
    Rewrites the URL's season token ("2024-25", "2024-2025" or a "/2024/"
    path segment) to each season the event name mentions (and the season
    starting the year before, for single-year names like "FOS 2027").
    """
    years = sorted({int(y) for y in _YEAR_RE.findall(event_name or "")})
    if not years:
        return []
    candidates = sorted({years[0] - 1, *years})
    patterns = [
        (re.compile(r"(?<!\d)(20\d\d)-(20\d\d)(?!\d)"), lambda y: f"{y}-{y + 1}"),
        (re.compile(r"(?<!\d)(20\d\d)-(\d\d)(?!\d)"), lambda y: f"{y}-{(y + 1) % 100:02d}"),
        (re.compile(r"/(20\d\d)/"), lambda y: f"/{y}/"),
    ]
    for pat, fmt in patterns:
        m = pat.search(url)
        if not m:
            continue
        current = int(m.group(1))
        out = []
        for y in candidates:
            if y > current:
                v = url[:m.start()] + fmt(y) + url[m.end():]
                if v not in out:
                    out.append(v)
        return out
    return []


async def _fetch(client, url: str):
    try:
        return await fetch_with_retry(client, url, rate_limiter=rate_limiter, max_retries=1)
    except Exception:
        return None


def _modified_before(resp, since: date | None) -> bool:
    lm = resp.headers.get("last-modified") if since else None
    if not lm:
        return False
    try:
        return parsedate_to_datetime(lm).date() < since
    except (TypeError, ValueError):
        return False


async def _fetch_club_page(client, url: str):
    """Like ``_fetch`` but keeps a 404 page's body.

    Directory Results links often point at a club page that has since moved
    (byc.org.au/current-race-results/, ryct.org.au/sailing/results/); the
    club's 404 page still carries its site navigation, whose "Results" links
    lead to the current pages.
    """
    try:
        return await fetch_with_retry(client, url, rate_limiter=rate_limiter, max_retries=1)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404 and "html" in e.response.headers.get("content-type", ""):
            return e.response
        return None
    except Exception:
        return None


def _stale_url(url: str, since: date | None) -> bool:
    """URL names only seasons that ended before ``since`` (e.g. .../2019/...)."""
    if not since:
        return False
    years = [int(y) for y in _YEAR_RE.findall(url)]
    return bool(years) and max(years) < since.year - 1


async def resolve_series_urls(client, url: str, since: date | None = None,
                              max_hops: int = 2, max_pages: int = 15) -> list[str]:
    """Series pages reachable from an event's Results link.

    Returns classic ``series.htm`` URLs plus any topyacht.net
    ``/publicresults/`` links (scraped through its JSON API). Handles a
    TopYacht index, a series page itself, or a club web page that links to
    TopYacht pages, following up to ``max_hops`` links: from the start page,
    TopYacht-looking links and same-site "results" links; beyond that, only
    TopYacht-looking links. Pages whose Last-Modified predates ``since`` are
    skipped: nothing in them can be new.
    """
    out: list[str] = []
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(url, 0)]
    fetched = 0
    while queue and fetched < max_pages:
        page_url, hop = queue.pop(0)
        if page_url in seen:
            continue
        seen.add(page_url)
        if _PUBLICRESULTS_RE.search(page_url):
            if page_url not in out:
                out.append(page_url)
            continue
        resp = await (_fetch_club_page(client, page_url) if hop == 0 else _fetch(client, page_url))
        fetched += 1
        if resp is None:
            continue
        final = str(resp.url)
        if _modified_before(resp, since):
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        if final.lower().split("?")[0].endswith("series.htm") or soup.find("table", class_="centre_index_table"):
            if final not in out:
                out.append(final)
            continue
        host = urlparse(final).netloc.lower()
        for a in soup.find_all("a", href=True):
            raw = a["href"].strip().replace("\\", "/")
            if not raw or raw.startswith(("mailto:", "javascript:", "tel:", "#")):
                continue
            href = urljoin(final, raw).split("#")[0]
            path = href.lower().split("?")[0]
            if path.endswith("/series.htm") or _PUBLICRESULTS_RE.search(href):
                if href not in out and not _stale_url(href, since):
                    out.append(href)
                continue
            if hop >= max_hops or href in seen or _SKIP_EXT_RE.search(path) or _stale_url(href, since):
                continue
            if _TOPYACHT_PAGE_RE.search(href):
                queue.append((href, hop + 1))
            elif (hop == 0 and urlparse(href).netloc.lower() == host and not _SKIP_PATH_RE.search(path)
                  and ("result" in path or "result" in a.get_text(" ", strip=True).lower())):
                queue.append((href, hop + 1))
    return out


async def scrape_series(client, series_url: str, since: date | None = None,
                        organizing_club: str | None = None) -> list[RaceResult]:
    results: list[RaceResult] = []
    races = await discover_races_from_series(client, series_url, since=since)
    for race in (r for r in races if not r["is_series_scores"]):
        html = await _fetch_html(client, race["url"])
        if not html:
            continue
        try:
            parsed = parse_topyacht_html(html, source_url=race["url"], organizing_club=organizing_club)
        except Exception as e:
            print(f"      {race['race_label']}: ERROR — {e}")
            continue
        if since:
            # Undated rows can't be shown to be recent; a club page linking an
            # old regatta (e.g. rmys/2018/ayc from the Range Series page) would
            # otherwise slip through a since-bounded run.
            parsed = [r for r in parsed if r.event_date is not None and r.event_date >= since]
        results.extend(parsed)
    return results


# ---------------------------------------------------------------------------
# topyacht.net public results (JSON behind the JavaScript results app)
# ---------------------------------------------------------------------------

def _blank_time(val) -> str | None:
    val = (val or "").strip()
    return None if val in ("", "00:00:00", "0:00:00") else val


def _race_label(label: str | None) -> str | None:
    label = (label or "").strip()
    m = re.fullmatch(r"R(?:ace)?\s*(\d+)", label, re.I)
    return f"Race {int(m.group(1))}" if m else (label or None)


def parse_publicresults_race(
    data: dict,
    *,
    series_name: str,
    fleet_label: str | None,
    race_label: str | None,
    source_url: str,
    organizing_club: str | None = None,
) -> list[RaceResult]:
    """IRC rows from an app.topyacht.net ``raceResults`` payload (``data``)."""
    results: list[RaceResult] = []
    race_name = _race_label(race_label)
    event_name = f"{series_name} - {race_name}" if race_name and race_name not in series_name else series_name
    for group in data.get("resultGroupeds") or []:
        option = (group.get("resultOption") or "").strip()
        if not _IRC_HEADER_RE.search(option):
            continue
        rows = [r for r in group.get("results") or [] if (r.get("boatName") or "").strip()]
        for r in rows:
            place, status = _detect_status(str(r.get("showPlace") or ""))
            race_date = None
            try:
                race_date = date.fromisoformat(str(r.get("raceDate") or "")[:10])
            except ValueError:
                pass
            results.append(RaceResult(
                event_name=event_name,
                event_date=race_date,
                source_url=source_url,
                tcc_at_race=_safe_decimal(str(r.get("ahc") or "")),
                place=place,
                division=fleet_label or None,
                elapsed_time=_blank_time(r.get("elapsedTime")),
                corrected_time=_blank_time(r.get("correctedTime")),
                raw_data={
                    "boat_name": r["boatName"].strip(),
                    "sail_number": (r.get("sailnum") or "").strip() or None,
                    "skipper": (r.get("skipper") or "").strip() or None,
                    "club": organizing_club,
                    "club_name": organizing_club,
                    "finish_time": _blank_time(r.get("finishTime")),
                    "division": fleet_label,
                    "fleet_size": len(rows),
                    "race_name": race_name,
                    "race_date": str(race_date) if race_date else None,
                    "status": status,
                    "bch": r.get("bch"),
                    "result_option": option,
                },
            ))
    return results


async def _fetch_json_data(client, url: str):
    resp = await _fetch(client, url)
    if resp is None:
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or not body.get("success"):
        return None
    return body.get("data")


async def publicresults_series(client, url: str) -> list[dict]:
    """Series referenced by a topyacht.net /publicresults/ link."""
    q = parse_qs(urlparse(url).query)
    if q.get("seriesId"):
        return [{"series_id": q["seriesId"][0], "label": None, "club": None}]
    if q.get("clubId") and q.get("eventId"):
        data = await _fetch_json_data(
            client, f"{PUBLICRESULTS_API}list?clubId={q['clubId'][0]}&eventId={q['eventId'][0]}")
        return [{"series_id": str(d["seriesId"]), "label": d.get("label"), "club": d.get("clubName")}
                for d in data or [] if d.get("seriesId") is not None]
    return []


async def scrape_publicresults_series(client, series_id: str, since: date | None = None,
                                      organizing_club: str | None = None,
                                      label: str | None = None) -> list[RaceResult]:
    """IRC results for one topyacht.net series, newest race first.

    Each raceResults call returns every scoring option for a fleet's race, so
    a fleet whose first published race has no IRC group is skipped; within a
    fleet, two consecutive races entirely before ``since`` end the walk.
    """
    data = await _fetch_json_data(
        client, f"{PUBLICRESULTS_API}raceSessionsAndFleetsBySeriesId?seriesId={series_id}")
    if not data:
        return []
    sessions = data.get("raceSessions") or []
    results: list[RaceResult] = []
    for fleet in data.get("distinctFleets") or []:
        fid = fleet.get("seriesFleetClassId")
        series_name = fleet.get("seriesName") or label or f"TopYacht series {series_id}"
        club = fleet.get("clubName") or organizing_club
        fleet_sessions = sorted((s for s in sessions if s.get("seriesFleetClassId") == fid),
                                key=lambda s: s.get("sessionId") or 0, reverse=True)
        old_streak = 0
        for s in fleet_sessions:
            sid = s.get("sessionId")
            race = await _fetch_json_data(
                client, f"{PUBLICRESULTS_API}raceResults?seriesId={series_id}&fleetId={fid}&raceSessionId={sid}")
            groups = (race or {}).get("resultGroupeds") or []
            if not groups:
                continue
            if not any(_IRC_HEADER_RE.search(g.get("resultOption") or "") for g in groups):
                break  # fleet not IRC-scored
            parsed = parse_publicresults_race(
                race, series_name=series_name, fleet_label=fleet.get("label"), race_label=s.get("label"),
                source_url=f"{PUBLICRESULTS_PAGE}?seriesId={series_id}&fleetId={fid}&raceSessionId={sid}",
                organizing_club=club,
            )
            if since:
                dated = [r for r in parsed if r.event_date]
                if dated and all(r.event_date < since for r in dated):
                    old_streak += 1
                    if old_streak >= 2:
                        break
                    continue
                old_streak = 0
                parsed = [r for r in parsed if r.event_date is None or r.event_date >= since]
            results.extend(parsed)
    return results


async def scrape_results_url(client, url: str, since: date | None = None,
                             organizing_club: str | None = None,
                             seen: set[str] | None = None) -> list[RaceResult]:
    """IRC results behind one Results link (classic pages and topyacht.net)."""
    seen = seen if seen is not None else set()
    results: list[RaceResult] = []
    for s_url in await resolve_series_urls(client, url, since=since):
        key = s_url.split("://", 1)[-1]  # http/https copies of the same page
        if key in seen:
            continue
        seen.add(key)
        if _PUBLICRESULTS_RE.search(s_url):
            for ser in await publicresults_series(client, s_url):
                key = f"topyacht.net-series:{ser['series_id']}"
                if key in seen:
                    continue
                seen.add(key)
                found = await scrape_publicresults_series(
                    client, ser["series_id"], since=since,
                    organizing_club=ser["club"] or organizing_club, label=ser["label"])
                if found:
                    print(f"    {ser['label'] or ser['series_id']}: {len(found)} results (topyacht.net)")
                results.extend(found)
        else:
            found = await scrape_series(client, s_url, since=since, organizing_club=organizing_club)
            if found:
                print(f"    {len(found)} results ({s_url})")
            results.extend(found)
    return results


async def scrape_events_directory(since: date | None = None, years: set[int] | None = None,
                                  skip_series: set[str] | None = None) -> list[RaceResult]:
    """Scrape IRC results for every recent event in TopYacht's events directory."""
    today = date.today()
    if not years:
        first = min(since.year, today.year - 1) if since else today.year - 1
        years = set(range(first, today.year + 2))
    seen = {u.split("://", 1)[-1] for u in skip_series or ()}
    roots_done: set[str] = set()
    all_results: list[RaceResult] = []
    async with get_http_client() as client:
        resp = await _fetch(client, EVENTS_DIRECTORY_URL)
        if resp is None:
            raise RuntimeError(f"TopYacht events directory unavailable: {EVENTS_DIRECTORY_URL}")
        events = parse_events_directory(resp.text, years)
        print(f"TopYacht directory: {len(events)} recent events with results links")
        for ev in events:
            for root in [ev["results_url"], *season_url_variants(ev["results_url"], ev["name"])]:
                if root in roots_done:
                    continue
                roots_done.add(root)
                found = await scrape_results_url(client, root, since=since,
                                                 organizing_club=ev["club"] or None, seen=seen)
                if found:
                    print(f"  {ev['name'][:60]}: {len(found)} results ({root})")
                all_results.extend(found)
    print(f"TopYacht directory total: {len(all_results)} results")
    return all_results


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
                index_url = club_index_url(base_url, year, division)
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

                    races = await discover_races_from_series(client, series_url, since=since)
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
                        index_url = club_index_url(base_url, year, division)
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
