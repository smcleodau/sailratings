"""SailSys race results scraper.

SailSys (app.sailsys.com.au) is the primary race management platform for
Australian sailing clubs including CYCA (club 3), RPAYC (club 13), RSYS (club 2).

Uses the SailSys REST API (api.sailsys.com.au) exclusively -- no browser needed.

API endpoints (all public, no login):
  GET /api/v1/series/club/{club_id}/grouping/list     - list series for a club
      (only series the club has filed under a season grouping; see the
      series-ID sweep below for series that aren't)
  GET /api/v1/series/{series_id}/display/races         - list races in a series
  GET /api/v1/races/{race_id}/resultsentrants/display  - race results with entrants

Handicap IDs (from handicappings array):
  5  = PHS (SailSys PHS)
  33 = IRC Fleet (Manual)
  25 = IRC Fleet (Manual)  (CYCA's IRC definition, shortName "IRC")
  4  = ORCGP (ORC General Purpose)
  1  = IRC (legacy?)
IRC definitions are also recognised by name (see irc_definition_ids), since
clubs get their own definition ids.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from irc_data.config import RACE_RESULTS_DIR
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
IRC_HANDICAP_IDS = {1, 25, 33}  # IRC legacy, IRC Fleet (Manual) x2
PHS_HANDICAP_ID = 5
ORCGP_HANDICAP_ID = 4


def _is_irc_definition(defn: dict | None) -> bool:
    if not defn:
        return False
    if defn.get("id") in IRC_HANDICAP_IDS:
        return True
    name = (defn.get("shortName") or defn.get("fullName") or "").strip().upper()
    return name.startswith("IRC")


def irc_definition_ids(race_data: dict) -> set[int]:
    """IRC handicap definition ids used in one race.

    A fixed id list missed CYCA, whose IRC definition is id 25 ("IRC Fleet
    (Manual)"): every CYCA IRC race since 2022 was stored with PHS as the
    rating. Read the race's own ``handicappings`` and each entrant's
    handicap definitions and keep any whose name starts with "IRC".
    """
    ids = set(IRC_HANDICAP_IDS)
    defs = list(race_data.get("handicappings") or [])
    for group in race_data.get("competitors") or []:
        for item in group.get("items") or []:
            for hc in ((item or {}).get("handicap") or {}).get("currentHandicaps") or []:
                defs.append((hc or {}).get("definition"))
    for d in defs:
        if _is_irc_definition(d) and d.get("id") is not None:
            ids.add(d["id"])
    return ids

# SailSys race statuses that indicate a race has been sailed and is
# publishing results. 4 = ratified/finalised; 3 = scored but not yet
# fully closed (e.g. Brisbane→Gladstone 2026). Both have entrants and
# elapsed times in the API response.
COMPLETED_RACE_STATUSES = {3, 4}


def _exc_text(e: BaseException) -> str:
    """Render an exception as ``Type: message`` — never an empty string.

    Several exception types str() to '' (e.g. ``RuntimeError()``,
    ``asyncio.CancelledError``), which is how SailSys runs ended up with
    ``completed_with_errors`` + empty ``error_message`` (OPS-02-02).
    """
    text = str(e).strip()
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


@dataclass
class ClubScrapeResult:
    """Structured outcome of scraping one club.

    ``errors`` carries per-series/per-race exception text so callers can
    persist it to ``ingestion_log.error_message`` instead of letting
    failures die in stdout (OPS-02-02).
    """

    club_id: int
    club_name: str
    results: list[RaceResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error_text(self, max_items: int = 10) -> str:
        if not self.errors:
            return ""
        shown = self.errors[:max_items]
        suffix = f" (+{len(self.errors) - max_items} more)" if len(self.errors) > max_items else ""
        return f"{self.club_name}: {len(self.errors)} errors [{'; '.join(shown)}{suffix}]"

# Some clubs (like QCYC) don't expose their series through the public
# grouping/list endpoint, but individual series + race endpoints work
# fine when the IDs are known. Maintain known series here so we still
# scrape them. Discovered by inspecting the official event websites.
EXTRA_SERIES_BY_CLUB: dict[int, list[int]] = {
    37: [5204],  # QCYC — 2026 Brisbane to Gladstone Yacht Race
}

# ---------------------------------------------------------------------------
# Series-ID sweep (ungrouped / current-season series)
# ---------------------------------------------------------------------------
# The public club page (app.sailsys.com.au/club/{id}/results) only lists
# series a club has filed under a "grouping" (season). Clubs file new-season
# series late: on 2026-11-03 CYCA, MHYC and RSYS were all racing 2026-27
# series that were in no grouping at all, so grouping-only discovery found
# nothing after early October. Every other listing endpoint (/races/current,
# /races/past, POST /clubs/{id}/series, /series/club/{id}/groupings) needs a
# login. Series IDs are global and sequential, and the public
# /series/{id}/display/races endpoint names the owning club, so we walk the
# ID space forward from the last visible series and keep a small on-disk
# index of what we found. Hidden and not-yet-created IDs both answer 400
# "not displaying entrants or results"; hidden ones are re-checked daily
# because clubs often switch results on after the first race.

#: Floor for a brand-new index: first series ID of the 2026 winter season.
#: Everything older is reachable through the clubs' grouping lists.
SERIES_SWEEP_START_ID = 5550
#: Stop the forward sweep after this many consecutive 400s (frontier).
SERIES_SWEEP_MISS_LIMIT = 60
#: Hard cap on sweep requests per run (first run from the floor needs ~800).
SERIES_SWEEP_MAX_REQUESTS = 1200
#: Hidden IDs re-checked per run, oldest check first, at most once a day.
SERIES_SWEEP_HIDDEN_RECHECKS = 30
SERIES_SWEEP_HIDDEN_RECHECK_AFTER = timedelta(hours=24)
#: Indexed series are offered to a club scrape if their last known race is
#: no older than ``since`` minus this slack (races get added to a series).
SERIES_INDEX_SINCE_SLACK = timedelta(days=60)
SERIES_INDEX_PATH = RACE_RESULTS_DIR / "sailsys" / "series_index.json"

#: The sweep is a burst of hundreds of requests, so it gets the full 2 s
#: policy delay rather than the per-race limiter's 0.5 s.
sweep_rate_limiter = RateLimiter(min_delay=2.0, jitter=0.5)

HIDDEN = "hidden"


def http_client(**kwargs) -> httpx.AsyncClient:
    """AsyncClient that identifies us with the policy User-Agent."""
    from irc_data.sources.policy import ACTIVE_POLICY

    headers = {"User-Agent": ACTIVE_POLICY.attribution.user_agent, "Accept": "application/json"}
    headers.update(kwargs.pop("headers", {}) or {})
    return httpx.AsyncClient(timeout=kwargs.pop("timeout", 30), headers=headers, **kwargs)


@dataclass
class SeriesIndex:
    """What the series-ID sweep has learnt, persisted between runs.

    ``series``: visible series id -> {club_id, club_name, name, first_race,
    last_race, races, statuses}. ``hidden``: id -> ISO timestamp of the last
    check that answered "not displaying".
    ``high_water``: highest *visible* id seen; the next forward sweep starts
    just above it, so IDs past the frontier are re-probed every run.
    """

    high_water: int = 0
    series: dict[int, dict] = field(default_factory=dict)
    hidden: dict[int, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict) -> "SeriesIndex":
        return cls(
            high_water=int(data.get("high_water") or 0),
            series={int(k): v for k, v in (data.get("series") or {}).items()},
            hidden={int(k): v for k, v in (data.get("hidden") or {}).items()},
        )

    def to_json(self) -> dict:
        return {
            "high_water": self.high_water,
            "series": {str(k): v for k, v in sorted(self.series.items())},
            "hidden": {str(k): v for k, v in sorted(self.hidden.items())},
        }

    @classmethod
    def load(cls, path: Path = SERIES_INDEX_PATH) -> "SeriesIndex":
        try:
            return cls.from_json(json.loads(Path(path).read_text()))
        except FileNotFoundError:
            return cls()

    def save(self, path: Path = SERIES_INDEX_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=1))
        tmp.replace(path)

    def series_for_club(self, club_id: int, since: date | None = None) -> list[dict]:
        """Indexed series for a club, shaped like get_club_series entries."""
        floor = (since - SERIES_INDEX_SINCE_SLACK).isoformat() if since else None
        out = []
        for sid, s in sorted(self.series.items()):
            if s.get("club_id") != club_id:
                continue
            last = s.get("last_race")
            if floor and last and last < floor:
                continue
            out.append({"series_id": sid, "name": s.get("name") or f"Series {sid}", "club_id": club_id})
        return out


@dataclass
class SweepReport:
    probed: int = 0
    new_series: list[int] = field(default_factory=list)
    revealed: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"probed": self.probed, "new_series": len(self.new_series),
                "revealed_hidden": len(self.revealed), "errors": self.errors[:5]}


def _series_entry(data: dict) -> dict:
    club = data.get("club") or {}
    races = data.get("races") or []
    dates = sorted(d for d in (_parse_race_date(r.get("dateTime")) for r in races) if d)
    return {
        "club_id": club.get("id"),
        "club_name": club.get("shortName"),
        "name": data.get("name"),
        "first_race": dates[0].isoformat() if dates else None,
        "last_race": dates[-1].isoformat() if dates else None,
        "races": len(races),
        "statuses": sorted({r.get("status") for r in races if r.get("status") is not None}),
    }


async def probe_series(client: httpx.AsyncClient, series_id: int):
    """Return the series' index entry, ``HIDDEN`` for a 400 "not displaying"
    (hidden or not created yet), or raise for anything else."""
    await sweep_rate_limiter.wait()
    resp = await client.get(f"{SAILSYS_API}/series/{series_id}/display/races")
    if resp.status_code == 400 and "not displaying" in resp.text:
        return HIDDEN
    resp.raise_for_status()
    return _series_entry(resp.json().get("data") or {})


async def sweep_series_index(
    client: httpx.AsyncClient,
    index: SeriesIndex,
    now: datetime | None = None,
    max_requests: int = SERIES_SWEEP_MAX_REQUESTS,
) -> SweepReport:
    """Extend ``index`` in place: walk forward past the frontier, then
    re-check a few stale hidden IDs below it. Errors stop the sweep and are
    reported, never raised — grouping discovery still works without it."""
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    report = SweepReport()

    def record(sid: int, result) -> None:
        if result == HIDDEN:
            index.hidden[sid] = stamp
            return
        if sid not in index.series:
            report.new_series.append(sid)
        index.series[sid] = result
        index.hidden.pop(sid, None)
        index.high_water = max(index.high_water, sid)

    sid = max(index.high_water, SERIES_SWEEP_START_ID - 1) + 1
    misses = 0
    try:
        while misses < SERIES_SWEEP_MISS_LIMIT and report.probed < max_requests:
            result = await probe_series(client, sid)
            report.probed += 1
            record(sid, result)
            misses = misses + 1 if result == HIDDEN else 0
            sid += 1

        cutoff = (now - SERIES_SWEEP_HIDDEN_RECHECK_AFTER).isoformat(timespec="seconds")
        stale = sorted(
            (checked, hid) for hid, checked in index.hidden.items()
            if hid < index.high_water and checked < cutoff
        )
        for _, hid in stale[:SERIES_SWEEP_HIDDEN_RECHECKS]:
            if report.probed >= max_requests:
                break
            result = await probe_series(client, hid)
            report.probed += 1
            if result != HIDDEN:
                report.revealed.append(hid)
            record(hid, result)
    except Exception as e:  # network trouble: keep what we have
        report.errors.append(f"series sweep at id {sid}: {_exc_text(e)}")
    return report


async def refresh_series_index(path: Path = SERIES_INDEX_PATH, **kwargs) -> tuple[SeriesIndex, SweepReport]:
    """Load the on-disk index, sweep, save. Used once per all-clubs run."""
    try:
        index = SeriesIndex.load(path)
    except (ValueError, OSError) as e:  # corrupt/unreadable: rebuild from the floor
        index = SeriesIndex()
        load_error = f"series index unreadable, rebuilt: {_exc_text(e)}"
    else:
        load_error = None
    async with http_client() as client:
        report = await sweep_series_index(client, index, **kwargs)
    if load_error:
        report.errors.insert(0, load_error)
    try:
        index.save(path)
    except OSError as e:
        report.errors.append(f"series index not saved: {_exc_text(e)}")
    return index, report


def _safe_decimal(val) -> Decimal | None:
    if val is None:
        return None
    try:
        # str() first: SailSys sometimes returns handicap values as raw
        # numbers; calling .strip() on a non-str raised AttributeError and
        # silently dropped whole races (OPS-02-02).
        s = str(val).strip()
        if not s:
            return None
        return Decimal(s)
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        return None


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


async def get_club_series(
    client: httpx.AsyncClient,
    club_id: int,
    extra_series: list[dict] | None = None,
) -> list[dict]:
    """Get list of racing series for a club using the SailSys REST API.

    Walks the grouping endpoint for season/group-organised clubs, then
    appends ``extra_series`` (series the ID sweep found for this club, see
    SeriesIndex.series_for_club) and any series declared in
    EXTRA_SERIES_BY_CLUB. Duplicates of grouped series are dropped.
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

    # Series found by the ID sweep that the club hasn't grouped yet.
    for series in extra_series or []:
        sid = series["series_id"]
        if sid in seen:
            continue
        seen.add(sid)
        series_list.append({
            "series_id": sid,
            "name": series.get("name") or f"Series {sid}",
            "club_id": series.get("club_id", club_id),
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
    # Clubs can hide a series; SailSys answers 400 "not displaying entrants
    # or results". Nothing to fetch, not an error.
    if resp.status_code == 400 and "not displaying" in resp.text:
        return []
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

    irc_ids = irc_definition_ids(race_data)

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
                if hc_id in irc_ids:
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

            status = None
            for calc in (item.get("calculations") or []):
                if not calc:
                    continue
                hc_def_id = calc.get("handicapDefinitionId")
                corrected = calc.get("correctedTime")
                placings = calc.get("placings", [])
                place = placings[0].get("place") if placings else None
                # placingText can be explicitly null in the payload; `or ""`
                # guards the .strip() that otherwise AttributeError'd and
                # silently dropped the whole race (OPS-02-02).
                placing_text = (placings[0].get("placingText") or "").strip() if placings else ""

                if placing_text and placing_text in {"DNF", "DNC", "DSQ", "OCS", "RET", "DNS", "RAF", "STP"}:
                    status = placing_text

                if hc_def_id in irc_ids:
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
                status=status,
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


def select_races_to_scrape(races, since, today, completed_statuses=COMPLETED_RACE_STATUSES):
    """Races worth fetching: scored, dated on/before today, and after ``since``.

    Future-dated races are skipped: a club that reuses last season's series
    (SHCC "Women's Keelboat 2025-26", Oct 2026) gets next season's dates on
    races that still carry last season's results.
    """
    picked = []
    for r in races:
        if r.get("status") not in completed_statuses:
            continue
        race_date = _parse_race_date(r.get("date"))
        if race_date and race_date > today:
            continue
        if since and not (race_date and race_date > since):
            continue
        picked.append(r)
    return picked


async def scrape_club_irc_results_detailed(
    club_id: int = 3,
    max_series: int | None = None,
    since: date | None = None,
    extra_series: list[dict] | None = None,
) -> ClubScrapeResult:
    """Scrape all race results for a club, capturing per-series/race errors.

    Same discovery + parse logic as before, but every swallowed exception is
    appended to ``ClubScrapeResult.errors`` (with series/race context) so the
    caller can persist it to ``ingestion_log.error_message`` — this is the
    OPS-02-02 fix for ``completed_with_errors`` rows with empty messages.
    """
    club_name = _club_name_for_id(club_id)
    outcome = ClubScrapeResult(club_id=club_id, club_name=club_name)

    async with http_client() as client:
        print(f"Getting series list for {club_name} (club {club_id})...")
        series_list = await get_club_series(client, club_id, extra_series=extra_series)
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
                msg = f"series {series_id} ({series_name}): {_exc_text(e)}"
                outcome.errors.append(msg)
                print(f"    Error getting races: {e}")
                continue

            completed_races = select_races_to_scrape(races, since=since, today=date.today())

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
                    msg = (
                        f"series {series_id} race {race['race_id']} "
                        f"({race.get('name', '')}): {_exc_text(e)}"
                    )
                    outcome.errors.append(msg)
                    print(f"    Error scraping race {race['race_id']}: {e}")
                    continue

                if results:
                    outcome.results.extend(results)
                    irc_count = sum(1 for r in results if r.raw_data.get("irc_tcc"))
                    print(f"    Race {race['race_id']} ({race['name']}): {len(results)} boats, {irc_count} with IRC")

    return outcome


async def scrape_club_irc_results(
    club_id: int = 3,
    max_series: int | None = None,
    since: date | None = None,
    on_errors: Callable[[str], None] | None = None,
    extra_series: list[dict] | None = None,
) -> list[RaceResult]:
    """Scrape all race results for a club via the SailSys REST API.

    Discovers series and races, then fetches structured results data
    including IRC and PHS handicaps, corrected times, and placings.

    Args:
        club_id: SailSys club ID.
        max_series: Limit number of series to scrape (for testing).
        since: Only scrape races after this date (incremental mode).
        on_errors: Optional callback receiving a summary string when any
            series/race failed during the scrape — lets the run supervisor
            persist per-club error text without changing the list return
            type (OPS-02-02).
        extra_series: Series to scrape on top of the club's grouping list,
            normally ``SeriesIndex.series_for_club(club_id, since)``.
    """
    outcome = await scrape_club_irc_results_detailed(
        club_id, max_series=max_series, since=since, extra_series=extra_series,
    )
    if outcome.errors and on_errors is not None:
        on_errors(outcome.error_text())
    return outcome.results
