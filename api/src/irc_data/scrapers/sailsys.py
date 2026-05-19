"""SailSys race results scraper.

SailSys (app.sailsys.com.au) is the primary race management platform for
Australian sailing clubs including CYCA (club 3), RPAYC (club 13), RSYS (club 2).

Uses the SailSys REST API (api.sailsys.com.au) exclusively -- no browser needed.

API endpoints:
  GET /api/v1/series/club/{club_id}/grouping/list     - list series for a club
  GET /api/v1/series/{series_id}/display/races         - list races in a series
  GET /api/v1/races/{race_id}/resultsentrants/display  - race results with entrants

Handicap IDs (from handicappings array):
  5  = PHS (SailSys PHS)
  33 = IRC Fleet (Manual)
  4  = ORCGP (ORC General Purpose)
  1  = IRC (legacy?)
"""

import asyncio
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from irc_data.parsers.schemas import RaceResult
from irc_data.scrapers.base import RateLimiter

rate_limiter = RateLimiter(min_delay=0.5, jitter=0.5)

SAILSYS_BASE = "https://app.sailsys.com.au"
SAILSYS_API = "https://api.sailsys.com.au/api/v1"

# Known club IDs (from https://app.sailsys.com.au/club/{id}/profile)
CLUBS = {
    "RPEYC": 1,    # Royal Prince Edward Yacht Club
    "RSYS": 2,     # Royal Sydney Yacht Squadron
    "CYCA": 3,     # Cruising Yacht Club of Australia
    "RANSA": 4,    # Royal Australian Naval Sailing Association
    "MHYC": 5,     # Middle Harbour Yacht Club
    "SASC": 6,     # Sydney Amateur Sailing Club
    "ASC": 17,     # Avalon Sailing Club
    "RMYCBB": 12,  # Royal Motor Yacht Club Broken Bay
    "RPAYC": 13,   # Royal Prince Alfred Yacht Club
    "GFS": 14,     # Greenwich Flying Squadron
    "CSC": 18,     # Cronulla Sailing Club
    "NCYC": 22,    # Newcastle Cruising Yacht Club
    "MYC": 23,     # Manly Yacht Club
    "RFBYC": 24,   # Royal Freshwater Bay Yacht Club
    "RQYS": 25,    # Royal Queensland Yacht Squadron
    "SHCC": 27,    # Sydney Harbour Combined Clubs (IRC Div 1&2)
    "LMYC": 28,    # Lake Macquarie Yacht Club
    "QCYC": 37,    # Queensland Cruising Yacht Club (Brisbane→Gladstone)
    "TYC": 41,     # Townsville Yacht Club
    "VYC": 50,     # Vaucluse Yacht Club
    "MH16FT": 54,  # Middle Harbour 16ft Skiff Club
    "GSC": 59,     # Gosford Sailing Club
    "RSAYS": 60,   # Royal South Australian Yacht Squadron
    "KYC": 71,     # Kettering Yacht Club
    "WMSC": 79,    # Wynnum Manly Sailing Club
    "ABRW": 104,   # Airlie Beach Race Week (IRC)
    "CCYC": 109,   # Capricornia Cruising Yacht Club
    "ICC": 111,    # InterClub Challenge
    "MC38": 121,   # MC38 Class Association (one-design, no IRC — kept for completeness)
    "JBSC": 142,   # Jervis Bay Sailing Club
    "TP52": 147,   # TP52 Association (one-design)
    "SSORC": 167,  # Nautilus Marine Sydney Short Ocean Racing Championship
    "MHYCCR": 173, # MHYC — Championships & Regattas
    "SPS": 179,    # Sail Port Stephens (NSW IRC Championship)
    "HHYC": 201,   # Hebe Haven Yacht Club (HK)
    "SHSS": 208,   # Sydney Harbour Sprint Series
}

# Handicap definition IDs we care about (IRC-related)
IRC_HANDICAP_IDS = {1, 33}  # IRC legacy and IRC Fleet (Manual)
PHS_HANDICAP_ID = 5
ORCGP_HANDICAP_ID = 4

# SailSys race statuses that indicate a race has been sailed and is
# publishing results. 4 = ratified/finalised; 3 = scored but not yet
# fully closed (e.g. Brisbane→Gladstone 2026). Both have entrants and
# elapsed times in the API response.
COMPLETED_RACE_STATUSES = {3, 4}

# Some clubs (like QCYC) don't expose their series through the public
# grouping/list endpoint, but individual series + race endpoints work
# fine when the IDs are known. Maintain known series here so we still
# scrape them. Discovered by inspecting the official event websites.
EXTRA_SERIES_BY_CLUB: dict[int, list[int]] = {
    37: [5204],  # QCYC — 2026 Brisbane to Gladstone Yacht Race
}


def _safe_decimal(val) -> Decimal | None:
    if val is None:
        return None
    try:
        return Decimal(str(val).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


async def get_club_series(client: httpx.AsyncClient, club_id: int) -> list[dict]:
    """Get list of racing series for a club using the SailSys REST API.

    Walks the grouping endpoint for season/group-organised clubs, then
    appends any series declared in EXTRA_SERIES_BY_CLUB — used for clubs
    whose grouping list is empty even though they have public races.
    """
    url = f"{SAILSYS_API}/series/club/{club_id}/grouping/list"
    await rate_limiter.wait()
    resp = await client.get(url)
    resp.raise_for_status()
    data = resp.json()

    series_list = []
    seen: set[int] = set()
    for group in data.get("data", []):
        for series in group.get("series", []):
            sid = series["id"]
            if sid in seen:
                continue
            seen.add(sid)
            series_list.append({
                "series_id": sid,
                "name": series.get("name", f"Series {sid}"),
                "club_id": series.get("clubId", club_id),
            })

    # Append explicitly-known extra series for clubs with empty grouping lists.
    for sid in EXTRA_SERIES_BY_CLUB.get(club_id, []):
        if sid in seen:
            continue
        # Fetch the series name from the races endpoint.
        try:
            await rate_limiter.wait()
            r = await client.get(f"{SAILSYS_API}/series/{sid}/display/races")
            r.raise_for_status()
            d = r.json().get("data", {})
            name = d.get("name") or f"Series {sid}"
        except Exception:
            name = f"Series {sid}"
        series_list.append({"series_id": sid, "name": name, "club_id": club_id})
        seen.add(sid)

    return series_list


async def get_series_races(client: httpx.AsyncClient, series_id: int) -> list[dict]:
    """Get list of races in a series using the SailSys REST API."""
    url = f"{SAILSYS_API}/series/{series_id}/display/races"
    await rate_limiter.wait()
    resp = await client.get(url)
    resp.raise_for_status()
    data = resp.json()

    races = []
    series_data = data.get("data", {})
    for race in series_data.get("races", []):
        rid = race["id"]
        races.append({
            "race_id": rid,
            "name": race.get("name", f"Race {rid}"),
            "status": race.get("status"),
            "date": race.get("dateTime"),
        })

    return races


def _parse_race_date(date_str: str | None) -> date | None:
    """Parse a race date from SailSys API format (e.g. '2024-12-14T00:00:00.000')."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


async def scrape_race_results(
    client: httpx.AsyncClient,
    club_id: int,
    series_id: int,
    race_id: int,
    series_name: str = "",
    club_name: str = "",
) -> list[RaceResult]:
    """Scrape results from a single race via the API.

    Extracts boat info, IRC/PHS handicaps, corrected times, and placings
    from the structured JSON response.
    """
    url = f"{SAILSYS_API}/races/{race_id}/resultsentrants/display"
    await rate_limiter.wait()
    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        return []

    data = resp.json()
    race_data = data.get("data", {})
    race_name = race_data.get("name", f"Race {race_id}")
    race_date = race_data.get("dateTime")
    event_date = _parse_race_date(race_date)

    results = []
    source_url = f"{SAILSYS_BASE}/club/{club_id}/results/series/{series_id}/races/{race_id}?handicap=1"

    # Count fleet size across all competitor groups
    total_competitors = 0
    for competitor_group in (race_data.get("competitors") or []):
        total_competitors += len(competitor_group.get("items") or [])

    for competitor_group in (race_data.get("competitors") or []):
        parent = competitor_group.get("parent") or {}
        division_name = parent.get("name", "")
        division_items = competitor_group.get("items") or []
        division_fleet_size = len(division_items)

        for item in division_items:
            boat = item.get("boat") or {}
            skipper = item.get("skipper") or {}
            skipper_profile = skipper.get("profile") or {} if skipper else {}

            boat_name = boat.get("name")
            if not boat_name:
                continue

            sail_number = boat.get("sailNumber")
            boat_club = boat.get("club")
            boat_make = boat.get("make", "")
            boat_model = boat.get("model", "")

            # Extract handicap values
            irc_tcc = None
            phs_value = None
            orc_value = None
            handicap_data = item.get("handicap") or {}
            handicaps = handicap_data.get("currentHandicaps") or []
            for hc in handicaps:
                if not hc:
                    continue
                hc_def = hc.get("definition", {})
                hc_id = hc_def.get("id")
                hc_value = hc.get("value")
                if hc_id in IRC_HANDICAP_IDS:
                    irc_tcc = _safe_decimal(hc_value)
                elif hc_id == PHS_HANDICAP_ID:
                    phs_value = _safe_decimal(hc_value)
                elif hc_id == ORCGP_HANDICAP_ID:
                    orc_value = _safe_decimal(hc_value)

            # Extract calculations (corrected times and placings per handicap)
            irc_corrected = None
            irc_place = None
            phs_corrected = None
            phs_place = None
            scratch_place = None

            for calc in (item.get("calculations") or []):
                if not calc:
                    continue
                hc_def_id = calc.get("handicapDefinitionId")
                corrected = calc.get("correctedTime")
                placings = calc.get("placings", [])
                place = placings[0].get("place") if placings else None
                placing_text = placings[0].get("placingText", "") if placings else ""

                if hc_def_id in IRC_HANDICAP_IDS:
                    irc_corrected = corrected
                    irc_place = _safe_int(place)
                elif hc_def_id == PHS_HANDICAP_ID:
                    phs_corrected = corrected
                    phs_place = _safe_int(place)
                elif hc_def_id is None:
                    # Scratch/line honours
                    scratch_place = _safe_int(place)

            elapsed_time = item.get("elapsedTime")
            finish_time = item.get("finishTimeLocal")

            # Use IRC data if available, fall back to PHS
            tcc = irc_tcc if irc_tcc else phs_value
            place = irc_place if irc_place is not None else phs_place
            corrected_time = irc_corrected if irc_corrected else phs_corrected

            result = RaceResult(
                event_name=f"{series_name} - {race_name}",
                event_date=event_date,
                source_url=source_url,
                tcc_at_race=tcc,
                place=place,
                elapsed_time=elapsed_time,
                corrected_time=corrected_time,
                raw_data={
                    "boat_name": boat_name,
                    "sail_number": sail_number,
                    "boat_club": boat_club,
                    "boat_make": boat_make,
                    "boat_model": boat_model,
                    "skipper": skipper_profile.get("fullName"),
                    "division": division_name,
                    "fleet_size": total_competitors,
                    "division_fleet_size": division_fleet_size,
                    "irc_tcc": str(irc_tcc) if irc_tcc else None,
                    "phs": str(phs_value) if phs_value else None,
                    "orc_gp": str(orc_value) if orc_value else None,
                    "irc_place": irc_place,
                    "phs_place": phs_place,
                    "scratch_place": scratch_place,
                    "irc_corrected": irc_corrected,
                    "phs_corrected": phs_corrected,
                    "finish_time": finish_time,
                    "race_date": race_date,
                    "race_name": race_name,
                    "club_id": club_id,
                    "club_name": club_name,
                    "series_id": series_id,
                    "race_id": race_id,
                },
            )
            results.append(result)

    return results


def _club_name_for_id(club_id: int) -> str:
    """Reverse lookup: club_id -> club abbreviation."""
    for name, cid in CLUBS.items():
        if cid == club_id:
            return name
    return f"Club_{club_id}"


async def scrape_club_irc_results(
    club_id: int = 3,
    max_series: int | None = None,
    since: date | None = None,
) -> list[RaceResult]:
    """Scrape all race results for a club via the SailSys REST API.

    Discovers series and races, then fetches structured results data
    including IRC and PHS handicaps, corrected times, and placings.

    Args:
        club_id: SailSys club ID.
        max_series: Limit number of series to scrape (for testing).
        since: Only scrape races after this date (incremental mode).
    """
    all_results = []
    club_name = _club_name_for_id(club_id)

    async with httpx.AsyncClient(timeout=30) as client:
        print(f"Getting series list for {club_name} (club {club_id})...")
        series_list = await get_club_series(client, club_id)
        print(f"  Found {len(series_list)} series")

        if max_series:
            series_list = series_list[:max_series]

        for series in series_list:
            series_name = series["name"]
            series_id = series["series_id"]
            print(f"\n  Series: {series_name} (ID {series_id})")

            try:
                races = await get_series_races(client, series_id)
            except Exception as e:
                print(f"    Error getting races: {e}")
                continue

            # Only process races that have been sailed and scored.
            # See COMPLETED_RACE_STATUSES at top of module.
            completed_races = [r for r in races if r.get("status") in COMPLETED_RACE_STATUSES]

            # Incremental mode: skip races before the cutoff date
            if since:
                filtered = []
                for r in completed_races:
                    race_date = _parse_race_date(r.get("date"))
                    if race_date and race_date > since:
                        filtered.append(r)
                skipped = len(completed_races) - len(filtered)
                completed_races = filtered
                if skipped:
                    print(f"    Skipped {skipped} races before {since}")

            print(f"    {len(completed_races)} completed races to process (of {len(races)} total)")

            for race in completed_races:
                try:
                    results = await scrape_race_results(
                        client,
                        club_id,
                        series_id,
                        race["race_id"],
                        series_name=series_name,
                        club_name=club_name,
                    )
                except Exception as e:
                    print(f"    Error scraping race {race['race_id']}: {e}")
                    continue

                if results:
                    all_results.extend(results)
                    irc_count = sum(1 for r in results if r.raw_data.get("irc_tcc"))
                    print(f"    Race {race['race_id']} ({race['name']}): {len(results)} boats, {irc_count} with IRC")

    return all_results
