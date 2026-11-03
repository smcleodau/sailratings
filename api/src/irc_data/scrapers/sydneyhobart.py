"""Rolex Sydney Hobart Yacht Race results scraper.

Reads IRC results from the CYCA Racing feeds API (``feeds.cycaracing.com/v2``)
that backs the Blue Water Pointscore site (bwps.cycaracing.com).

History: until 2026 the BWPS site served server-rendered standings at
``/standings?seriesId=15&raceId=N``. The site was rebuilt (Astro + Alpine)
and that URL now redirects to ``/race/<year>/results``, a client-rendered
page with no results table, so the old HTML parser silently returned 0 rows
for every edition. The page itself reads the JSON feeds used here:

* ``/v2/series/15/races`` — every edition (race id, year, start time, status)
* ``/v2/races/{raceId}`` — the edition's categories (IRC, PHS, Double Handed
  IRC, ...)
* ``/v2/races/{raceId}/categories/{categoryId}/results/at/final`` — the
  final standings for a category: name, sail number, IRC TCC (``tcf``),
  status, overall/division place and corrected time.

Only the overall ``IRC`` category is read (the Double Handed / Corinthian
IRC categories are subsets of it). Pure HTTP + JSON, no JavaScript.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from irc_data.scrapers.base import RateLimiter, fetch_with_retry, get_http_client
from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource

CYCA_RACING_BASE = "https://bwps.cycaracing.com"
FEEDS_BASE = "https://feeds.cycaracing.com/v2"

# Series IDs on the CYCA Racing platform
SERIES_SYDNEY_HOBART = 15
SERIES_GOLD_COAST = 3
SERIES_FLINDERS_ISLET = 11
SERIES_CABBAGE_TREE = 13
SERIES_BIRD_ISLAND = 14
SERIES_TOLLGATE = 16
SERIES_NEWCASTLE_BASS = 12

RACE_SLUG = "rolex-sydney-hobart-yacht-race"
EVENT_SERIES = "Rolex Sydney Hobart Yacht Race"

# Year -> race ID, used only if the series feed is unreachable.
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

#: catPlace the feed gives boats without a place (retired, DNS, ...).
NO_PLACE = 999

sydneyhobart_limiter = RateLimiter(min_delay=2.0, jitter=1.0)


def _safe_decimal(val) -> Decimal | None:
    if val in (None, "", 0):
        return None
    try:
        d = Decimal(str(val).strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def _feed_datetime(val: str | None) -> datetime | None:
    """Parse a feed timestamp; the feed's "no value" sentinel is year 1."""
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val)
    except ValueError:
        return None
    return None if dt.year <= 1 else dt


def _corrected_time(corrected: str | None, start: str | None) -> str | None:
    """Corrected time as ``H:MM:SS`` total hours (same format as the old parser).

    The feed gives the corrected *finish* timestamp; corrected time is that
    minus the race start.
    """
    end, begin = _feed_datetime(corrected), _feed_datetime(start)
    if not end or not begin or end <= begin:
        return None
    secs = int((end - begin).total_seconds())
    return f"{secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _detect_status(status_text: str | None) -> str:
    """Map the feed's status text (``Finished``, ``Retired - at Port``, ...)."""
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


_DH_SUFFIX = re.compile(r"\s*\(DH\)\s*$", re.IGNORECASE)


def results_url(year: int) -> str:
    """Public results page for an edition (what a reader would open)."""
    return f"{CYCA_RACING_BASE}/race/{year}/results/{RACE_SLUG}"


def editions_from_series(races: list[dict]) -> dict[int, dict]:
    """``/v2/series/15/races`` → {year: race} (all editions, any status)."""
    out = {}
    for race in races or []:
        try:
            year, race_id = int(race["year"]), int(race["raceId"])
        except (KeyError, TypeError, ValueError):
            continue
        out[year] = {"race_id": race_id, "status": race.get("status"),
                     "start": race.get("startDateTime")}
    return out


def irc_category(race_detail: dict) -> dict | None:
    """The overall IRC category of an edition (not the DH/Corinthian subsets)."""
    cats = race_detail.get("categories") or []
    for cat in cats:
        if (cat.get("name") or "").strip().upper() == "IRC":
            return cat
    return None


def parse_feed_results(
    rows: list[dict], year: int, race_id: int,
    start: str | None = None,
    series_id: int = SERIES_SYDNEY_HOBART,
    category: str = "IRC",
) -> list[NormalizedResult]:
    """Final IRC category standings JSON → NormalizedResult rows."""
    results = []
    rows = [r for r in rows or [] if (r.get("nameRace") or "").strip()]
    fleet_size = len(rows)
    event_name = f"{year} {EVENT_SERIES}"
    event_date = date(year, RACE_MONTH, RACE_DAY)
    for r in rows:
        raw_name = r["nameRace"].strip()
        name = _DH_SUFFIX.sub("", raw_name).rstrip(" *")
        place = r.get("catPlace")
        place = int(place) if isinstance(place, int) and 0 < place < NO_PLACE else None
        division = (r.get("divisionName") or "").strip() or None
        tcc = _safe_decimal(r.get("tcf"))
        status_text = r.get("status")
        results.append(NormalizedResult(
            boat_name=name,
            sail_number=(r.get("sailNumber") or "").strip() or None,
            event_name=event_name,
            event_date=event_date,
            event_series=EVENT_SERIES,
            organizing_club="CYCA",
            event_type="offshore",
            race_name=event_name,
            place=place,
            fleet_size=fleet_size,
            class_name=f"Division {division}" if division else None,
            class_place=r.get("divPlace") if place else None,
            status=_detect_status(status_text),
            rating_type="irc_tcc" if tcc else None,
            rating_value=tcc,
            corrected_time=_corrected_time(r.get("correctedTime"), start),
            source_url=results_url(year),
            raw_data={
                "race_entry_id": r.get("raceEntryId"),
                "name_race": raw_name,
                "double_handed": raw_name != name and "(DH)" in raw_name.upper(),
                "owner": (r.get("owner") or "").strip() or None,
                "skipper": (r.get("skipper") or "").strip() or None,
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
    """Rolex Sydney Hobart Yacht Race results source (CYCA feeds API)."""

    def source_name(self) -> str:
        return "sydneyhobart"

    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        """One EventRef per edition whose 26 December start is on/after ``since``."""
        async with get_http_client() as client:
            try:
                resp = await fetch_with_retry(
                    client, f"{FEEDS_BASE}/series/{SERIES_SYDNEY_HOBART}/races",
                    rate_limiter=sydneyhobart_limiter,
                )
                editions = editions_from_series(resp.json())
            except Exception as e:
                print(f"  Sydney Hobart series feed failed ({e}); using known race ids")
                editions = {}
        for year, race_id in SYDNEY_HOBART_RACES.items():
            editions.setdefault(year, {"race_id": race_id, "status": None, "start": None})

        events = []
        for year, ed in sorted(editions.items()):
            if since and date(year, RACE_MONTH, RACE_DAY) < since:
                continue
            events.append(EventRef(
                source="sydneyhobart",
                event_name=f"{year} {EVENT_SERIES}",
                event_url=results_url(year),
                event_date=date(year, RACE_MONTH, RACE_DAY),
                organizing_club="CYCA",
                event_type="offshore",
                metadata={"year": year, "race_id": ed["race_id"], "series_id": SERIES_SYDNEY_HOBART,
                          "status": ed["status"], "start": ed["start"]},
            ))
        print(f"  Discovered {len(events)} Sydney Hobart editions")
        return events

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        """Final IRC standings for one edition (empty before results exist)."""
        meta = ref.metadata or {}
        year = meta.get("year") or (ref.event_date.year if ref.event_date else None)
        race_id = meta.get("race_id") or SYDNEY_HOBART_RACES.get(year)
        if not race_id:
            print(f"  No race ID for {year}")
            return []

        async with get_http_client() as client:
            resp = await fetch_with_retry(client, f"{FEEDS_BASE}/races/{race_id}",
                                          rate_limiter=sydneyhobart_limiter)
            detail = resp.json()
            cat = irc_category(detail)
            if not cat:
                print(f"  {year}: no IRC category")
                return []
            await sydneyhobart_limiter.wait()
            resp = await client.get(
                f"{FEEDS_BASE}/races/{race_id}/categories/{cat['id']}/results/at/final"
            )
            # 404 = no final snapshot yet (pre-race / racing): not a failure.
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            results = parse_feed_results(
                resp.json(), year, race_id,
                start=detail.get("startDateTime") or meta.get("start"),
                series_id=meta.get("series_id", SERIES_SYDNEY_HOBART),
            )
        irc_count = sum(1 for r in results if r.rating_value)
        finished = sum(1 for r in results if r.status == "finished")
        print(f"  {year}: {len(results)} yachts ({finished} finished, {irc_count} with TCC)")
        return results


async def scrape_all_years(
    since: date | None = None,
    year: int | None = None,
) -> list[NormalizedResult]:
    """Scrape Sydney Hobart results for all available years."""
    source = SydneyHobartSource()
    events = await source.discover_events(since=since)
    if year:
        events = [e for e in events if e.metadata and e.metadata.get("year") == year]
    all_results = []
    for event in events:
        all_results.extend(await source.scrape_event(event))
    return all_results
