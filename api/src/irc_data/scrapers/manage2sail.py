"""Manage2Sail (manage2sail.com) race results scraper — IRC scorings only.

Manage2Sail is a European regatta-management platform (NED/BEL/GER/DEN/...).
Event pages are an AngularJS app, but the data behind them is plain JSON on
an unauthenticated API the site itself calls, so no JS rendering is needed.

Public structure (verified against the live site, Nov 2026)
-----------------------------------------------------------
* ``/en-US/event?page=N``  — server-rendered event list. Without filters it
  shows events overlapping the *current month* only; the filtered listing
  lives under ``/en-US/search``, which robots.txt disallows (``/*/search``),
  so we never use it.
* ``/en-US/Club/Detail/{club_id}?year=YYYY`` — a club's events for a year.
  This is how we reach past months: a set of clubs known to run IRC racing
  (seed list + clubs learnt from earlier IRC results) is walked per year.
* ``/en-US/event/{guid-or-alias}`` — event page. Embeds
  ``window.boostrapedResourceData`` (sic) with one ``Regatta`` per class and
  ``SailingInfo.ConfigEventApiRoot = '/api/event/{guid}/'``.
* ``/api/event/{guid}/regattaresult/{class_id}`` — class results JSON. The
  top level is the class's default scoring; ``ScoringResults[i].Result`` hold
  extra scorings with the same shape (e.g. a combined "IRC/ORC 1" class
  scored as both "IRC 1" and "ORC 1"). Each scoring has ``RatingSystem``
  (``IRC``, ``ORC``, ``One Design``, ``Yardstick``, ...). Per-race entries
  carry ``Hdcp`` (the TCC for IRC), ``RaceTime``, ``CorrectedTime``,
  ``Rank`` and ``RaceStatusCode`` (DNF/OCS/...).

Scope: only scorings whose ``RatingSystem`` is IRC are kept. ORC scorings are
skipped on purpose — their ``Hdcp`` is whichever ORC corrector the scorer
picked (triple-number ToT, GPH, ToD...), so it is not one comparable rating.

One row per boat per race, rating_type ``irc_tcc``. ``race_name`` is prefixed
with the scoring name so a boat entered in two IRC scorings of one class
(``race_results`` dedups on entry + race_name) keeps both rows.

Politeness: policy User-Agent via :func:`get_http_client`, >= 2.5 s + jitter
between requests, robots.txt-allowed paths only (docs/SOURCE-POLICY.md).
"""

from __future__ import annotations

import html as html_lib
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from irc_data.scrapers.base import RateLimiter, fetch_with_retry, get_http_client
from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource

SOURCE = "manage2sail"
M2S_BASE = "https://www.manage2sail.com"
EVENT_LIST_URL = f"{M2S_BASE}/en-US/event"

#: Clubs known to run IRC-scored racing on Manage2Sail. Found by hand from
#: public event pages (Nov 2026). Clubs whose events produce IRC results are
#: also learnt automatically (raw_data->>'organizer_club_ids'), so this list
#: only needs to bootstrap discovery.
SEED_CLUB_IDS: dict[str, str] = {
    "55571942-e6d9-453d-9c03-59c097f3d922": "Stichting Breskens Sailing (NED)",
    "220189ca-f908-48a1-890c-095262c9525f": "Royal North Sea Yacht Club (BEL)",
    "9ef0d06f-eac1-4cf9-8d40-72b1ee0076d4": "Open Belgian Sailing Week co-host (BEL)",
    "d10c827c-6ea4-40a4-9b8f-1205f897c6a5": "Jachtclub Scheveningen (NED)",
    "309c067e-82d1-420a-9c14-6a7c08d6912d": "Nordseewoche (GER)",
    "f3d82944-2087-45ff-97af-80f2b94c0a31": "Kaagweek (NED)",
    # Found for the history backfill (Nov 2026); each organised events with
    # IRC scorings: North Sea Regatta before Jachtclub Scheveningen took it
    # over, Stellendam Regatta (ORC/IRC classes), IRC Europeans 2023 (Cannes).
    "ad64e058-f697-41a6-b197-3e3a2185b18f": "Stichting North Sea Regatta (NED)",
    "1f1848b6-ba79-47c7-b75d-97c1fd898a45": "Stichting Stellendam Regatta (NED)",
    "e0f4f68a-baad-493b-b552-2705cf3331cc": "Yacht Club de Cannes (FRA)",
}

#: Class names that are certainly not rating-scored (one-design / dinghy /
#: board fleets). Skipping them avoids one API call each; anything not
#: matched is fetched and decided by the scoring's RatingSystem.
_ONE_DESIGN_CLASS_RE = re.compile(
    r"\b(opti(mist)?|ilca|laser|420er?|470er?|29er|49er|fx|nacra|finn|europe|"
    r"j[ /-]?70|j[ /-]?80|j[ /-]?24|melges|sb ?20|star|dragon|drachen|"
    r"folkboat|folkeb[aå]d|h[ -]?boot?|pirat|cadet|teeny|zoom ?8|o-?jolle|"
    r"ok[ -]?jolle|contender|505|flying dutchman|fd|hobie|waszp|iqfoil|"
    r"kite|windsurf|rs ?(feva|aero|tera|21|100|200|400|500|800)|topper|"
    r"vaurien|soling|tempest|yngling|lacustre|varianta|conger|"
    r"jollenkreuzer|kielzugvogel|x-?99|platu|longtze|asso|esse 850|"
    r"skiff|catamaran|katamaran|iom|rc ?boat|modell)\b",
    re.IGNORECASE,
)

#: Event names that are training/courses, never IRC racing.
_SKIP_EVENT_RE = re.compile(
    r"(training|trainingslager|ferienprogramm|clinic|\bcamp\b|\bkurs\b|"
    r"\bopti(mist)?\b|\bilca\b|\blaser\b|optisquad)",
    re.IGNORECASE,
)

_GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_STATUS_CODES = {
    "DNF", "DNS", "DSQ", "RET", "OCS", "RAF", "BFD", "UFD", "DNE", "DGM", "NSC",
    "ZFP", "SCP", "RDG", "TLE", "DPI",
}
#: Did not come to the starting area: the boat was not racing, so there is
#: nothing to learn about its rating. Not stored.
_SKIP_STATUS_CODES = {"DNC"}


# ---------------------------------------------------------------------------
# Parsing helpers (pure — unit-tested against saved fixtures)
# ---------------------------------------------------------------------------


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(text or "")).strip()


def _event_key(href: str) -> str:
    """``/en-US/event/<id-or-alias>`` -> lower-cased id/alias."""
    tail = href.split("/event/", 1)[-1]
    return tail.split("?")[0].split("#")[0].strip("/").lower()


def _date_span(year: int, start: tuple[int, int], end: tuple[int, int] | None) -> tuple[date, date] | None:
    try:
        d0 = date(year, start[1], start[0])
        if end is None:
            return d0, d0
        d1 = date(year, end[1], end[0])
    except ValueError:
        return None
    if d1 < d0:  # e.g. 28.11. - 07.03. (winter series crossing new year)
        try:
            d1 = date(year + 1, end[1], end[0])
        except ValueError:
            return None
    return d0, d1


_LIST_ROW_RE = re.compile(
    r"<tr>\s*<td>(\d{4})</td>\s*<td>(\d{1,2})\.(\d{1,2})\.</td>\s*"
    r"<td>(\d{1,2})\.(\d{1,2})\.</td>\s*<td><a href=\"([^\"]+)\">([^<]*)</a>(.*?)</tr>",
    re.S,
)


def parse_event_list(page_html: str) -> list[EventRef]:
    """Parse one page of the public ``/en-US/event`` list."""
    refs = []
    for m in _LIST_ROW_RE.finditer(page_html):
        year, d0, m0, d1, m1, href, name, rest = m.groups()
        span = _date_span(int(year), (int(d0), int(m0)), (int(d1), int(m1)))
        if not span:
            continue
        cells = re.findall(r"<td>([^<]*)</td>", rest)
        refs.append(EventRef(
            source=SOURCE,
            event_name=_clean(name),
            event_url=M2S_BASE + href if href.startswith("/") else href,
            event_date=span[0],
            metadata={
                "end_date": span[1].isoformat(),
                "event_key": _event_key(href),
                "country": _clean(cells[0]) if cells else None,
                "club_ids": re.findall(r"/Club/Detail/(" + _GUID_RE.pattern + ")", rest, re.I),
            },
        ))
    return refs


def has_next_page(page_html: str) -> bool:
    return bool(re.search(r'href="[^"]*page=\d+"[^>]*>\s*Next\s*<', page_html))


_CLUB_ROW_RE = re.compile(
    r"<tr>\s*<td[^>]*>(\d{4})</td>\s*<td[^>]*>\s*(\d{1,2})/(\d{1,2})"
    r"(?:\s*-\s*(\d{1,2})/(\d{1,2}))?\s*</td>\s*<td><a href=\"([^\"]*/event/[^\"]+)\">([^<]*)</a>",
    re.S,
)


def parse_club_events(page_html: str, club_id: str | None = None) -> list[EventRef]:
    """Parse the events table of a ``/en-US/Club/Detail/{id}`` page."""
    refs = []
    for m in _CLUB_ROW_RE.finditer(page_html):
        year, d0, m0, d1, m1, href, name = m.groups()
        end = (int(d1), int(m1)) if d1 else None
        span = _date_span(int(year), (int(d0), int(m0)), end)
        if not span:
            continue
        refs.append(EventRef(
            source=SOURCE,
            event_name=_clean(name),
            event_url=M2S_BASE + href if href.startswith("/") else href,
            event_date=span[0],
            metadata={
                "end_date": span[1].isoformat(),
                "event_key": _event_key(href),
                "club_ids": [club_id] if club_id else [],
            },
        ))
    return refs


def parse_event_page(page_html: str) -> dict[str, Any]:
    """Extract the event GUID, title, organiser clubs and classes."""
    guid_m = re.search(r"ConfigEventApiRoot\s*=\s*'/api/event/(" + _GUID_RE.pattern + ")/'", page_html, re.I)
    title_m = re.search(r"<h1>([^<]*)</h1>", page_html)
    regattas: list[dict] = []
    boot_m = re.search(r"window\.boostrapedResourceData\s*=\s*(\{.*?\});\s*$", page_html, re.M)
    if boot_m:
        try:
            boot = json.loads(boot_m.group(1))
            regattas = [r for r in boot.get("Regatta") or [] if isinstance(r, dict)]
        except json.JSONDecodeError:
            regattas = []
    # Organiser club(s): the event header links them; ignore Club/List nav.
    club_ids = list(dict.fromkeys(
        c.lower() for c in re.findall(r"/Club/Detail/(" + _GUID_RE.pattern + ")", page_html, re.I)
    ))
    club_names = [
        _clean(n) for n in re.findall(r'href="/en-US/Club/Detail/[^"]+"[^>]*>([^<]+)</a>', page_html)
    ]
    return {
        "guid": guid_m.group(1).lower() if guid_m else None,
        "name": _clean(title_m.group(1)) if title_m else None,
        "club_ids": club_ids,
        "club_name": next((n for n in club_names if n), None),
        "classes": [
            {
                "id": r.get("Id"),
                "name": _clean(r.get("Name")),
                "has_results": bool(r.get("HasResults")),
                "cancelled": bool(r.get("IsCancelled")),
            }
            for r in regattas
            if r.get("Id")
        ],
    }


def is_one_design_class(name: str) -> bool:
    """True when a class name is clearly a one-design fleet (skip fetching)."""
    if re.search(r"\b(irc|orc|yardstick|ys|handicap|hcp)\b", name or "", re.I):
        return False
    return bool(_ONE_DESIGN_CLASS_RE.search(name or ""))


def _parse_interval(value: Any) -> timedelta | None:
    """``"2:34:01"`` / ``"1.02:03:04"`` -> timedelta; zero/blank -> None."""
    if not value or not isinstance(value, str):
        return None
    m = re.fullmatch(r"(?:(\d+)[.d ]\s*)?(\d+):(\d{1,2}):(\d{1,2})", value.strip())
    if not m:
        return None
    days, h, mi, s = (int(x) if x else 0 for x in m.groups())
    td = timedelta(days=days, hours=h, minutes=mi, seconds=s)
    return td or None


def _tcc(value: Any) -> Decimal | None:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    # A plausible IRC TCC; filters 0.0 placeholders and non-TCC numbers.
    return d if Decimal("0.5") <= d <= Decimal("2.5") else None


def _iter_scorings(result: dict) -> Iterable[dict]:
    """Default scoring followed by each additional ScoringResults[i].Result."""
    yield result
    for s in result.get("ScoringResults") or []:
        inner = (s or {}).get("Result")
        if isinstance(inner, dict):
            inner.setdefault("ScoringName", s.get("Name"))
            yield inner


def is_irc_scoring(scoring: dict) -> bool:
    return "IRC" in str(scoring.get("RatingSystem") or "").upper()


def extract_irc_results(
    result: dict,
    *,
    event_name: str,
    event_date: date | None,
    event_guid: str,
    class_id: str,
    class_name: str,
    organizing_club: str | None = None,
    organizer_club_ids: list[str] | None = None,
) -> list[NormalizedResult]:
    """Turn one class's regattaresult JSON into per-race IRC rows."""
    out: list[NormalizedResult] = []
    source_url = f"{M2S_BASE}/en-US/event/{event_guid}#!results?classId={class_id}"
    seen_scorings: set[str] = set()
    for scoring in _iter_scorings(result):
        if not is_irc_scoring(scoring):
            continue
        sid = str(scoring.get("Id") or "")
        if sid in seen_scorings:  # default scoring can repeat as ScoringResults[0]
            continue
        seen_scorings.add(sid)
        scoring_name = _clean(scoring.get("ScoringName"))
        label = scoring_name if scoring_name and scoring_name.lower() != "default" else (class_name or "IRC")
        race_names = {r.get("RaceIndex"): _clean(r.get("Name")) for r in scoring.get("RaceNames") or []}
        race_dates: dict[str, date] = {}
        for info in scoring.get("RaceInfos") or []:
            try:
                race_dates[info["RaceId"]] = datetime.fromisoformat(info["StartDate"]).date()
            except (KeyError, TypeError, ValueError):
                continue
        entries = scoring.get("EntryResults") or []
        # Per-race fleet: boats that came to the start (DNC excluded).
        race_fleet: dict[Any, int] = {}
        for entry in entries:
            for rr in entry.get("EntryRaceResults") or []:
                if str(rr.get("RaceStatusCode") or "").strip().upper() not in _SKIP_STATUS_CODES:
                    race_fleet[rr.get("RaceId")] = race_fleet.get(rr.get("RaceId"), 0) + 1
        for entry in entries:
            boat = _clean(entry.get("BoatName")) or _clean(entry.get("TeamName"))
            if not boat:
                continue
            sail = _clean(entry.get("SailNumber")) or None
            skipper = entry.get("Skipper") or {}
            entry_tcc = _tcc(entry.get("Hcp"))
            for rr in entry.get("EntryRaceResults") or []:
                code = str(rr.get("RaceStatusCode") or "").strip().upper()
                if code in _SKIP_STATUS_CODES:
                    continue
                rank = rr.get("Rank")
                elapsed = _parse_interval(rr.get("RaceTime"))
                if not code and rank is None and elapsed is None:
                    continue  # race not sailed / not scored for this boat
                idx = rr.get("OverallRaceIndex")
                rname = race_names.get(idx) or (f"R{idx}" if idx else "Race")
                tcc = _tcc(rr.get("Hdcp")) or entry_tcc
                status = code if code in _STATUS_CODES else ("finished" if not code else code)
                race_date = race_dates.get(rr.get("RaceId"))
                out.append(NormalizedResult(
                    boat_name=boat,
                    sail_number=sail,
                    event_name=event_name,
                    event_date=event_date,
                    organizing_club=organizing_club,
                    race_name=f"{label} - {rname}",
                    race_number=idx if isinstance(idx, int) else None,
                    place=int(rank) if isinstance(rank, int) and not code else None,
                    fleet_size=race_fleet.get(rr.get("RaceId")) or len(entries),
                    class_name=class_name,
                    status=status,
                    rating_type="irc_tcc" if tcc else None,
                    rating_value=tcc,
                    elapsed_time=rr.get("RaceTime") or None,
                    corrected_time=rr.get("CorrectedTime") if _parse_interval(rr.get("CorrectedTime")) else None,
                    time_behind_winner=rr.get("Delta") or None,
                    source_url=source_url,
                    raw_data={
                        "boat_name": boat,
                        "sail_number": sail,
                        "boat_type": _clean(entry.get("BoatType")) or None,
                        "skipper": " ".join(
                            p for p in (skipper.get("FirstName"), skipper.get("LastName")) if p
                        ) or _clean(entry.get("TeamName")) or None,
                        "boat_club": _clean(entry.get("ClubName")) or None,
                        "country": entry.get("Country"),
                        "status": code or None,
                        "points": rr.get("Points"),
                        "points_discarded": bool(rr.get("PointsDiscarded")),
                        "race_date": race_date.isoformat() if race_date else None,
                        "series_rank": entry.get("Rank"),
                        "series_net_points": entry.get("NetPoints"),
                        "scoring_name": scoring_name or None,
                        "rating_system": scoring.get("RatingSystem"),
                        "m2s_event_id": event_guid,
                        "m2s_class_id": class_id,
                        "m2s_scoring_id": sid or None,
                        "m2s_entry_id": entry.get("Id"),
                        "m2s_race_id": rr.get("RaceId"),
                        "organizer_club_ids": list(organizer_club_ids or []),
                    },
                ))
    return _one_row_per_boat_race(out)


def _one_row_per_boat_race(rows: list[NormalizedResult]) -> list[NormalizedResult]:
    """Keep one row per boat per sailed race across a class's IRC scorings.

    Classes often carry several IRC scorings over the same races (e.g. North
    Sea Regatta: "IRC Overall" plus "IRC2/3 Inshores"). Storing each would
    count one physical race twice, so keep the scoring with the largest fleet
    (the overall placing).
    """
    best: dict[tuple, NormalizedResult] = {}
    order: list[tuple] = []
    for r in rows:
        raw = r.raw_data or {}
        key = (r.boat_name.lower(), (r.sail_number or "").replace(" ", "").upper(),
               raw.get("m2s_race_id") or r.race_name)
        if key not in best:
            order.append(key)
            best[key] = r
        elif (r.fleet_size or 0) > (best[key].fleet_size or 0):
            best[key] = r
    return [best[k] for k in order]


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------


class Manage2SailScraper(RaceResultSource):
    """Discover Manage2Sail events and extract their IRC results.

    ``club_ids`` extends :data:`SEED_CLUB_IDS` (e.g. clubs learnt from earlier
    IRC results). ``today`` is injectable for tests.
    """

    def __init__(
        self,
        *,
        client=None,
        rate_limiter: RateLimiter | None = None,
        club_ids: Iterable[str] | None = None,
        max_listing_pages: int = 15,
        max_events: int = 400,
        today: date | None = None,
    ):
        self._client = client
        self._limiter = rate_limiter or RateLimiter(min_delay=2.5, jitter=1.5)
        self.club_ids = list(dict.fromkeys([*SEED_CLUB_IDS, *(c.lower() for c in club_ids or [] if c)]))
        self.max_listing_pages = max_listing_pages
        self.max_events = max_events
        self.today = today or date.today()
        self.stats: dict[str, int] = {
            "requests": 0, "fetch_errors": 0, "events_scanned": 0,
            "classes_fetched": 0, "classes_skipped_one_design": 0, "irc_scorings": 0,
        }

    def source_name(self) -> str:
        return SOURCE

    async def _get(self, url: str):
        if self._client is None:
            self._client = get_http_client()
        self.stats["requests"] += 1
        return await fetch_with_retry(self._client, url, rate_limiter=self._limiter, max_retries=2)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        """Events that started on/before today and ended on/after ``since``.

        ``since=None`` means 1 January of the current year (a bounded
        backfill — Manage2Sail goes back to 2013 and pre-2026 is out of scope
        for a nightly source).
        """
        since = since or date(self.today.year, 1, 1)
        found: dict[str, EventRef] = {}

        def keep(ref: EventRef) -> None:
            end = date.fromisoformat(ref.metadata["end_date"])
            if ref.event_date and ref.event_date > self.today:
                return
            if end < since:
                return
            if _SKIP_EVENT_RE.search(ref.event_name):
                return
            key = ref.metadata["event_key"]
            if key in found:
                found[key].metadata["club_ids"] = list(dict.fromkeys(
                    found[key].metadata["club_ids"] + ref.metadata["club_ids"]
                ))
            else:
                found[key] = ref

        # 1. Current-month public list (robots-allowed; filters are not).
        for page in range(1, self.max_listing_pages + 1):
            url = EVENT_LIST_URL if page == 1 else f"{EVENT_LIST_URL}?page={page}"
            try:
                resp = await self._get(url)
            except Exception:
                self.stats["fetch_errors"] += 1
                break
            for ref in parse_event_list(resp.text):
                keep(ref)
            if not has_next_page(resp.text):
                break

        # 2. Known IRC clubs, one page per club per year in the window.
        for club_id in self.club_ids:
            for year in range(since.year, self.today.year + 1):
                url = f"{M2S_BASE}/en-US/Club/Detail/{club_id}"
                if year != self.today.year:
                    url += f"?year={year}"
                try:
                    resp = await self._get(url)
                except Exception:
                    self.stats["fetch_errors"] += 1
                    continue
                for ref in parse_club_events(resp.text, club_id):
                    keep(ref)

        refs = sorted(found.values(), key=lambda r: (r.event_date or date.min, r.event_name))
        return refs[: self.max_events]

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        try:
            page = await self._get(ref.event_url)
        except Exception:
            self.stats["fetch_errors"] += 1
            return []
        info = parse_event_page(page.text)
        guid = info["guid"]
        if not guid:
            return []
        self.stats["events_scanned"] += 1
        ref.metadata = {**(ref.metadata or {}), "guid": guid}
        organizer_ids = list(dict.fromkeys((ref.metadata.get("club_ids") or []) + info["club_ids"]))
        results: list[NormalizedResult] = []
        for cls in info["classes"]:
            if not cls["has_results"] or cls["cancelled"]:
                continue
            if is_one_design_class(cls["name"]):
                self.stats["classes_skipped_one_design"] += 1
                continue
            url = f"{M2S_BASE}/api/event/{guid}/regattaresult/{cls['id']}"
            try:
                resp = await self._get(url)
                data = resp.json()
            except Exception:
                self.stats["fetch_errors"] += 1
                continue
            self.stats["classes_fetched"] += 1
            if not isinstance(data, dict):
                continue
            self.stats["irc_scorings"] += sum(1 for s in _iter_scorings(data) if is_irc_scoring(s))
            results.extend(extract_irc_results(
                data,
                event_name=info["name"] or ref.event_name,
                event_date=ref.event_date,
                event_guid=guid,
                class_id=cls["id"],
                class_name=cls["name"],
                organizing_club=info["club_name"],
                organizer_club_ids=organizer_ids,
            ))
        return results


# ---------------------------------------------------------------------------
# Persistence + runner (used by the Temporal legacy adapter and the CLI)
# ---------------------------------------------------------------------------


def learned_club_ids(engine) -> list[str]:
    """Organiser clubs of events that already produced Manage2Sail IRC rows."""
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT jsonb_array_elements_text(raw_data->'organizer_club_ids') "
            "FROM race_results WHERE source = :s AND jsonb_typeof(raw_data->'organizer_club_ids') = 'array'"
        ), {"s": SOURCE}).fetchall()
    return [r[0] for r in rows if r[0]]


def store_results(engine, results: list[NormalizedResult]) -> dict[str, int]:
    """Persist via :func:`upsert_race_result`; count genuinely new rows.

    ``upsert_race_result`` returns the existing id for an already-stored
    entry+race, so "rows written" is measured as the change in this source's
    row count rather than the number of upsert calls.
    """
    from sqlalchemy import text

    from irc_data.db.operations import (
        find_boat_by_sail_number,
        log_ingestion_end,
        log_ingestion_start,
        upsert_race_result,
    )
    from irc_data.matching.identity import normalize_sail
    from irc_data.scrapers.result_import import _find_boat_by_name

    count_sql = text("SELECT count(*) FROM race_results WHERE source = :s")
    with engine.connect() as conn:
        before = int(conn.execute(count_sql, {"s": SOURCE}).scalar() or 0)

    log_id = log_ingestion_start(engine, SOURCE, metadata={"adapter": "manage2sail"})
    boat_cache: dict[tuple, int | None] = {}
    matched = errors = 0
    error_details: list[str] = []
    for r in results:
        key = (r.sail_number, r.boat_name)
        if key not in boat_cache:
            boat_id = None
            if r.sail_number:
                boat_id = find_boat_by_sail_number(engine, normalize_sail(r.sail_number))
            if not boat_id:
                boat_id = _find_boat_by_name(engine, r.boat_name, r.rating_value)
            boat_cache[key] = boat_id
        boat_id = boat_cache[key]
        if boat_id:
            matched += 1
        race_date = (r.raw_data or {}).get("race_date")
        try:
            upsert_race_result(
                engine,
                boat_id=boat_id,
                event_name=r.event_name,
                event_date=r.event_date,
                race_name=r.race_name,
                race_number=r.race_number,
                race_date_specific=date.fromisoformat(race_date) if race_date else None,
                organizing_club=r.organizing_club,
                source=SOURCE,
                source_url=r.source_url,
                rating_type=r.rating_type,
                rating_value=r.rating_value,
                tcc_at_race=r.rating_value,
                place=r.place,
                fleet_size=r.fleet_size,
                class_name=r.class_name,
                division=(r.raw_data or {}).get("scoring_name"),
                status=r.status,
                elapsed_time=_parse_interval(r.elapsed_time),
                corrected_time=_parse_interval(r.corrected_time),
                time_behind_winner=_parse_interval(r.time_behind_winner),
                transport="legacy",
                raw_data=r.raw_data,
            )
        except Exception as e:  # keep going; surface the reason in the log
            errors += 1
            if len(error_details) < 20:
                error_details.append(f"{r.boat_name}: {type(e).__name__}: {e}")

    with engine.connect() as conn:
        after = int(conn.execute(count_sql, {"s": SOURCE}).scalar() or 0)
    log_ingestion_end(
        engine, log_id,
        status="completed" if not errors else "completed_with_errors",
        records_found=len(results),
        records_new=after - before,
        error_message=("; ".join(error_details)[:1000] if errors else None),
    )
    return {"written": after - before, "matched": matched, "errors": errors}


async def scrape(since: date | None, club_ids: Iterable[str] = (), **kwargs) -> tuple[list[EventRef], list[NormalizedResult], dict]:
    scraper = Manage2SailScraper(club_ids=club_ids, **kwargs)
    try:
        events = await scraper.discover_events(since=since)
        results: list[NormalizedResult] = []
        events_with_irc = 0
        for ref in events:
            rows = await scraper.scrape_event(ref)
            if rows:
                events_with_irc += 1
                results.extend(rows)
        stats = {**scraper.stats, "events_with_irc": events_with_irc}
        return events, results, stats
    finally:
        await scraper.aclose()


def run(record: dict | None = None, *, since: date | None = None) -> dict[str, Any]:
    """Scrape + persist. Returns the legacy-adapter stats mapping.

    ``since`` (or ``record['since']`` as ISO date) overrides the incremental
    cutoff the caller computed from the run ledger.
    """
    import asyncio

    from irc_data.db.connection import get_engine

    record = record or {}
    if since is None and record.get("since"):
        since = date.fromisoformat(str(record["since"]))
    engine = get_engine()
    clubs = learned_club_ids(engine)
    events, results, stats = asyncio.run(scrape(since, club_ids=clubs))
    stored = store_results(engine, results) if results else {"written": 0, "matched": 0, "errors": 0}
    return {
        "records_written": stored["written"],
        "records_found": len(results),
        "events": len(events),
        "events_with_irc": stats["events_with_irc"],
        "boats_matched_rows": stored["matched"],
        "store_errors": stored["errors"],
        "clubs_walked": len(set(SEED_CLUB_IDS) | {c.lower() for c in clubs}),
        "since": since.isoformat() if since else None,
        "http": {k: v for k, v in stats.items() if k != "events_with_irc"},
    }
