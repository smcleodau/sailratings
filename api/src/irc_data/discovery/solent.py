"""OPS-02-14 — UK / Solent coverage: discovery + ingestion.

Goal
----
Results for the boats that pay: **Solent**, not just Sydney.  The acceptance
criterion is that the **Sun Fast 3300** and **J/109** Solent fleets each have
at least one season of results in ``race_results``.

What this module does
---------------------
1.  **Solent source registry.**  The concrete UK / Solent result platforms
    (JOG, Warsash Spring Series, Hamble Winter Series / HRSC, plus the
    HalSail platform host) are registered as first-class ``data_sources``
    rows (see ``irc_data.sources.seed_data`` ``_SOLENT``) so the Temporal
    schedule registry, watchdog and policy gate all treat them uniformly.

2.  **Policy checks.**  Every collection / discovery action passes through
    the SOURCE-POLICY enforcement gate
    (:func:`irc_data.sources.registry.resolve_and_assert_approved` for
    content collection, ``can_discover`` for discovery-only metadata) before
    a single byte is fetched.

3.  **Discovery.**  ``discover_solent_sources()`` maps the registered seed
    URLs (JOG results index, Warsash Spring Series results index, HRSC
    results page, Hamble Winter Series site) and queues each reachable result
    page into ``event_discovery`` for review / ingestion.  JOG additionally
    yields concrete per-race ``/raceresults/<uuid>?year=YYYY`` pages which
    are imported directly (server-rendered HTML — no Firecrawl credits).

4.  **Ingestion.**  ``ingest_solent_results()`` fetches the pages, extracts
    structured results (JOG: local HTML row parser; Sailwave files: the
    existing ``sailwave`` parser; everything else: the Gemini
    ``extract_results`` pipeline) and imports them via
    ``import_scraper_results`` into ``race_results``.

Everything is fail-soft per URL and every external call is logged through
``irc_data.discovery.crawl_telemetry`` (OPS-01-05) where a paid provider is
used; plain-HTTP fetches go through the same politeness ``RateLimiter`` used
by the raw-capture adapters.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urljoin, urlparse

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.scrapers.result_base import EventRef, NormalizedResult, RaceResultSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Solent source registry (OPS-02-14)
# ---------------------------------------------------------------------------

#: The Solent / UK results sources OPS-02-14 is responsible for.  These are
#: the ``data_sources`` slugs the register must carry; the seed set lives in
#: ``irc_data.sources.seed_data._SOLENT``.
SOLENT_SOURCE_SLUGS: tuple[str, ...] = (
    "jog",
    "warsash-spring-series",
    "hamble-winter-series",
)

#: Slug written to ``race_results.source`` for rows produced by this pipeline.
#: JOG rows land under ``jog``; Warsash rows under ``warsash-spring-series``;
#: Hamble rows under ``hamble-winter-series``.
SOURCE_JOG = "jog"
SOURCE_WARSASH = "warsash-spring-series"
SOURCE_HAMBLE = "hamble-winter-series"

#: Known organising clubs / venues for the coverage query.
SOLENT_CLUB = "Solent"

#: JOG public results index (per-year).  Server-rendered: returns the full
#: season's per-race links as ``/raceresults/<uuid>``.
JOG_RESULTS_INDEX = "https://myjog.jog.org.uk/results"

#: JOG per-race URL pattern.
_JOG_RACE_RE = re.compile(r"/raceresults/([0-9a-fA-F-]{36})\b")

#: Rate limiter shared by the plain-HTTP fetches (policy §3: 1 req / 2 s + jitter).
from irc_data.scrapers.base import RateLimiter, get_http_client  # noqa: E402

_solent_limiter = RateLimiter(min_delay=2.0, jitter=1.0)


# ---------------------------------------------------------------------------
# Policy gate helpers
# ---------------------------------------------------------------------------


def _assert_collectable(engine: Engine | None, slug: str) -> None:
    """Raise unless *slug* is approved + enabled for content collection.

    This is the SPEC-012 §2.3 / §3.1 enforcement invariant applied to every
    Solent source before any content bytes are fetched.  Discovery-only
    callers use :func:`_assert_discoverable` instead.
    """
    from irc_data.sources.registry import resolve_and_assert_approved

    if engine is None:
        # No DB → fall back to the in-memory seed registry gate.
        from irc_data.sources.registry import get_in_memory_source, assert_approved

        record = get_in_memory_source(slug)
        if record is None:
            from irc_data.sources.registry import SourceNotApprovedError

            raise SourceNotApprovedError(slug, "not registered")
        assert_approved(record)
        return
    resolve_and_assert_approved(engine, slug)


def _assert_discoverable(slug: str, engine: Engine | None = None) -> None:
    """Raise unless *slug* may be *discovered* (metadata only).

    Discovery is permitted for approved / hold / unknown sources, but not
    blocked or disabled ones (SOURCE-POLICY.md §2).  Resolves the record from
    the ``data_sources`` register when an engine is available, falling back to
    the in-memory seed registry otherwise.
    """
    from irc_data.sources.registry import (
        SourceNotApprovedError,
        can_discover,
        get_in_memory_source,
    )

    record = None
    if engine is not None:
        from irc_data.sources.registry import DataSource

        from sqlalchemy import select
        from sqlalchemy.orm import Session

        with Session(engine) as session:
            row = session.execute(
                select(DataSource).where(DataSource.slug == slug)
            ).scalar_one_or_none()
        if row is not None:
            record = row  # ORM row exposes enabled / legal_status
    if record is None:
        record = get_in_memory_source(slug)
    if record is None:
        raise SourceNotApprovedError(slug, "not registered")
    if not can_discover(record):
        raise SourceNotApprovedError(slug, "discovery not permitted")


# ---------------------------------------------------------------------------
# JOG per-race HTML parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JogRaceRow:
    """One parsed JOG result row."""

    boat_name: str
    sail_number: str | None
    tcc: Decimal | None
    place: int | None
    elapsed_time: str | None
    corrected_time: str | None
    class_name: str | None


_JOG_BOAT_BLOCK_RE = re.compile(
    r'title="View boat details">(?P<name>[^<]+)</a></div>\s*'
    r'<div class="col-12 col-crew-members-bow-number-and-flag"><span class="bow-number">'
    r"(?P<sail>[^<]+)</span>",
    re.S,
)
_JOG_TCC_RE = re.compile(r'TCC\s*</span>\s*([0-9]+\.[0-9]+)', re.S)
_JOG_CLASS_RE = re.compile(
    r'<span class="btn-text">(?P<class>[^<]*(?:IRC|Class|Division)[^<]*)</span>',
    re.I,
)


def _dec(val: str | None) -> Decimal | None:
    if not val:
        return None
    try:
        return Decimal(val.strip())
    except (InvalidOperation, ValueError):
        return None


def parse_jog_race_html(
    html: str,
    *,
    source_url: str,
    event_name: str,
    event_date: date | None = None,
    organizing_club: str = "JOG",
) -> list[NormalizedResult]:
    """Parse a JOG per-race results page into :class:`NormalizedResult` rows.

    JOG pages are server-rendered (no JS needed).  Each boat block exposes
    the boat name, sail number and (on IRC pages) TCC, elapsed and corrected
    times.  Finishing order on the page is the result order, so the place is
    the row index within the class block.
    """
    class_m = _JOG_CLASS_RE.search(html)
    class_name = (class_m.group("class").strip() if class_m else None) or "IRC"

    # Split the page at boat blocks so each boat's TCC / times are scoped.
    blocks = list(_JOG_BOAT_BLOCK_RE.finditer(html))
    results: list[NormalizedResult] = []
    for idx, m in enumerate(blocks, start=1):
        name = m.group("name").strip()
        sail = (m.group("sail") or "").strip() or None
        # Scope the trailing segment (until the next boat block) for fields.
        end = blocks[idx].start() if idx < len(blocks) else len(html)
        seg = html[m.end():end]
        tcc_m = _JOG_TCC_RE.search(seg)
        tcc = _dec(tcc_m.group(1)) if tcc_m else None
        results.append(
            NormalizedResult(
                boat_name=name,
                sail_number=sail,
                event_name=event_name,
                event_date=event_date,
                organizing_club=organizing_club,
                place=idx,
                fleet_size=len(blocks),
                class_name=class_name,
                rating_type="irc_tcc" if tcc else None,
                rating_value=tcc,
                source_url=source_url,
                raw_data={"boat_name": name, "sail_number": sail, "class_name": class_name},
            )
        )
    return results


# ---------------------------------------------------------------------------
# Solent source adapters
# ---------------------------------------------------------------------------


class JOGSource(RaceResultSource):
    """JOG race-results source (myjog.jog.org.uk).

    Discovery walks the public per-year results index and yields one
    :class:`EventRef` per ``/raceresults/<uuid>`` race page.  Scraping parses
    the server-rendered HTML directly — no browser rendering or Firecrawl
    credits needed.
    """

    def __init__(self, years: Sequence[int] | None = None, *, engine: Engine | None = None):
        self._years = list(years) if years else [datetime.now().year, datetime.now().year - 1]
        self._engine = engine

    def source_name(self) -> str:
        return SOURCE_JOG

    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        """Discover JOG race pages from the public per-year index."""
        _assert_discoverable(SOURCE_JOG, self._engine)
        events: list[EventRef] = []
        async with get_http_client() as client:
            for year in self._years:
                url = f"{JOG_RESULTS_INDEX}?year={year}"
                await _solent_limiter.wait()
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                except Exception:
                    continue
                # Pair each /raceresults/<uuid> with the race name / date.
                for m in _JOG_RACE_RE.finditer(resp.text):
                    race_id = m.group(1)
                    race_url = f"https://myjog.jog.org.uk/raceresults/{race_id}"
                    # Best-effort race name + date from the surrounding row.
                    ctx = resp.text[max(0, m.start() - 1500):m.start()]
                    name_m = re.findall(
                        r'style="font-weight: 400;">([^<]{3,80})</span>', ctx
                    )
                    event_name = name_m[-1].strip() if name_m else f"JOG race {race_id[:8]}"
                    # Date: the row shows e.g. "APR 20 2025" (month / day / year
                    # stacked in the date tile).  Fall back to Jan 1 of the year.
                    ev_date = date(year, 1, 1)
                    dm = re.findall(
                        r'<div class="col-12 opacity-75">([A-Z]{3})</div>\s*'
                        r'<div class="col-12 font-weight-bold"[^>]*>(\d{1,2})</div>',
                        ctx,
                    )
                    if dm:
                        mon_abbr, day = dm[-1]
                        months = {m_: i for i, m_ in enumerate(
                            ("JAN","FEB","MAR","APR","MAY","JUN",
                             "JUL","AUG","SEP","OCT","NOV","DEC"), start=1)}
                        mm = months.get(mon_abbr.upper())
                        if mm:
                            try:
                                ev_date = date(year, mm, int(day))
                            except ValueError:
                                pass
                    events.append(
                        EventRef(
                            source=SOURCE_JOG,
                            event_name=event_name,
                            event_url=race_url,
                            event_date=ev_date,
                            organizing_club="JOG",
                            metadata={"race_id": race_id, "year": year},
                        )
                    )
        # De-duplicate by race_id while preserving order.
        seen: set[str] = set()
        out: list[EventRef] = []
        for ev in events:
            rid = ev.metadata.get("race_id") if ev.metadata else None
            if rid and rid not in seen:
                seen.add(rid)
                out.append(ev)
        if since:
            out = [e for e in out if not e.event_date or e.event_date >= since]
        return out

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        """Scrape one JOG race page into normalised results."""
        if self._engine is not None:
            _assert_collectable(self._engine, SOURCE_JOG)
        async with get_http_client() as client:
            await _solent_limiter.wait()
            resp = await client.get(ref.event_url)
            if resp.status_code != 200:
                return []
        return parse_jog_race_html(
            resp.text,
            source_url=ref.event_url,
            event_name=ref.event_name,
            event_date=ref.event_date,
            organizing_club=ref.organizing_club or "JOG",
        )


class HalSailResultsSource(RaceResultSource):
    """HalSail-hosted club results (HRSC / Hamble Winter Series).

    HalSail pages are JS-rendered, so collection goes through the Firecrawl
    + Gemini ``extract_results`` pipeline (``seed_crawl_and_ingest``) rather
    than a bespoke parser.  This adapter exists so the register has a
    concrete ``adapter_class`` and so the schedule registry treats Hamble as
    a first-class source.
    """

    def __init__(self, seed_url: str = "https://www.hamblewinterseries.com"):
        self._seed_url = seed_url

    def source_name(self) -> str:
        return SOURCE_HAMBLE

    async def discover_events(self, since: date | None = None) -> list[EventRef]:  # noqa: ARG002
        """HalSail discovery is provider-backed — see ``discover_solent_sources``."""
        return [
            EventRef(
                source=SOURCE_HAMBLE,
                event_name="Hamble Winter Series",
                event_url=self._seed_url,
                organizing_club="HRSC",
            )
        ]

    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:  # noqa: ARG002
        """HalSail pages need JS rendering; use the discovery pipeline."""
        return []


# ---------------------------------------------------------------------------
# Discovery — queue Solent result pages into event_discovery
# ---------------------------------------------------------------------------


def discover_solent_sources(
    engine: Engine,
    *,
    limit: int = 40,
    include_halsail: bool = True,
) -> dict[str, Any]:
    """Run the discovery pipeline over the registered Solent sources.

    For each registered Solent source that passes the policy gate
    (``can_discover``), map its seed URL and queue every reachable result
    page into ``event_discovery`` with ``source_type='solent-coverage'``.

    Returns a per-source summary dict.  Fails soft per source so one bad
    seed doesn't poison the batch.
    """
    from irc_data.discovery.service import discover_seed, discover_url

    summary: dict[str, Any] = {"sources": {}, "queued": 0, "errors": []}

    seeds: list[tuple[str, str]] = [
        (SOURCE_JOG, JOG_RESULTS_INDEX),
        (SOURCE_WARSASH, "https://warsashsc.org.uk/springseries/black-group-results/"),
        (SOURCE_WARSASH, "https://warsashsc.org.uk/springseries/white-group-results/"),
        (SOURCE_HAMBLE, "https://www.hrsc.org.uk/page/hrsc-results"),
        (SOURCE_HAMBLE, "https://www.hamblewinterseries.com"),
    ]
    if include_halsail:
        # HalSail is the platform host for HRSC / Hamble.  It stays
        # legal_status='unknown' → discovery metadata only (never content).
        seeds.append(("halsail", "https://halsail.com/Result/Club/3560"))

    for slug, seed_url in seeds:
        try:
            _assert_discoverable(slug, engine)
        except Exception as e:
            summary["errors"].append(f"{slug}: {e}")
            continue
        try:
            rows = discover_seed(engine, seed_url, limit=limit)
            summary["sources"][seed_url] = len(rows)
            summary["queued"] += len(rows)
        except Exception as e:
            logger.warning("discover_seed failed for %s: %s", seed_url, e)
            summary["errors"].append(f"{seed_url}: {e}")

    return summary


# ---------------------------------------------------------------------------
# Ingestion — fetch + extract + import into race_results
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3NF event / entry helpers (race_results.event_entry_id is NOT NULL)
# ---------------------------------------------------------------------------


def _ensure_event(
    conn,
    *,
    name: str,
    event_date: date | None,
    organiser: str | None,
    venue: str | None = "Solent",
) -> int:
    """Return the ``events.id`` for (name, start_date), creating it if needed."""
    row = conn.execute(
        text(
            "SELECT id FROM events WHERE name = :n AND "
            "(start_date = :d OR (start_date IS NULL AND :d IS NULL)) LIMIT 1"
        ),
        {"n": name, "d": event_date},
    ).first()
    if row:
        return int(row[0])
    return int(
        conn.execute(
            text(
                "INSERT INTO events (name, start_date, end_date, venue, organiser) "
                "VALUES (:n, :d, :d, :v, :o) RETURNING id"
            ),
            {"n": name, "d": event_date, "v": venue, "o": organiser},
        ).scalar_one()
    )


def _ensure_event_entry(
    conn,
    *,
    event_id: int,
    boat_id: int | None,
    boat_name: str | None,
    sail_number: str | None,
    tcc,
) -> int:
    """Return the ``event_entries.id`` for this boat in this event."""
    row = conn.execute(
        text(
            "SELECT id FROM event_entries WHERE event_id = :e AND "
            "boat_name IS NOT DISTINCT FROM :bn AND "
            "sail_number IS NOT DISTINCT FROM :sn LIMIT 1"
        ),
        {"e": event_id, "bn": boat_name, "sn": sail_number},
    ).first()
    if row:
        return int(row[0])
    return int(
        conn.execute(
            text(
                "INSERT INTO event_entries (event_id, boat_id, sail_number, "
                "boat_name, tcc) VALUES (:e, :b, :sn, :bn, :tcc) RETURNING id"
            ),
            {
                "e": event_id, "b": boat_id, "sn": sail_number, "bn": boat_name,
                "tcc": float(tcc) if isinstance(tcc, Decimal) else tcc,
            },
        ).scalar_one()
    )


def _insert_result(
    conn,
    *,
    entry_id: int,
    r: NormalizedResult,
    source: str,
    boat_id: int | None = None,
) -> None:
    """Insert one race_results row under an event entry.

    Dedup on (event_entry_id, race_name) — the ORM-level unique constraint.
    Existing rows are left untouched (results are stable once published).
    ``boat_id`` is written directly so the per-fleet coverage query (which
    joins ``race_results.boat_id`` → ``boats``) sees the match immediately.
    """
    import json

    # SQLite (used by the in-memory tests) can't bind Decimal; coerce to
    # float for the parameter values.  Postgres accepts both.
    def _f(v):
        return float(v) if isinstance(v, Decimal) else v

    existing = conn.execute(
        text(
            "SELECT id FROM race_results WHERE event_entry_id = :e AND "
            "race_name IS NOT DISTINCT FROM :rn LIMIT 1"
        ),
        {"e": entry_id, "rn": r.race_name},
    ).first()
    if existing:
        return
    conn.execute(
        text(
            """
            INSERT INTO race_results (
                event_entry_id, boat_id, event_name, event_date, race_name,
                event_series, organizing_club, event_type, source, source_url,
                rating_type, rating_value, tcc_at_race, place, fleet_size,
                class_name, class_place, class_fleet_size, status, raw_data,
                transport
            ) VALUES (
                :entry, :boat, :event, :edate, :race, :series, :club, :etype,
                :source, :url, :rtype, :rval, :tcc, :place, :fleet, :cls,
                :cplace, :cfleet, :status, CAST(:raw AS jsonb), 'legacy'
            )
            """
        ),
        {
            "entry": entry_id,
            "boat": boat_id,
            "event": r.event_name,
            "edate": r.event_date,
            "race": r.race_name,
            "series": r.event_series,
            "club": r.organizing_club,
            "etype": r.event_type,
            "source": source,
            "url": r.source_url,
            "rtype": r.rating_type,
            "rval": _f(r.rating_value),
            "tcc": _f(r.rating_value),
            "place": r.place,
            "fleet": r.fleet_size,
            "cls": r.class_name,
            "cplace": r.class_place,
            "cfleet": r.class_fleet_size,
            "status": r.status,
            "raw": json.dumps(r.raw_data or {}, default=str),
        },
    )


def _import_normalized(
    engine: Engine,
    results: list[NormalizedResult],
    source: str,
) -> dict[str, int]:
    """Import NormalizedResult rows into race_results (3NF path).

    Ensures the ``events`` + ``event_entries`` rows exist (``race_results
    .event_entry_id`` is NOT NULL with an FK), matches each row to a boat,
    and inserts the result.  Returns ``{"imported", "matched"}``.
    """
    from irc_data.db.operations import (
        find_boat_by_sail_number,
        log_ingestion_end,
        log_ingestion_start,
    )
    from irc_data.matching.identity import normalize_sail
    from irc_data.scrapers.result_import import _find_boat_by_name

    log_id = log_ingestion_start(engine, source)
    imported = matched = 0
    with engine.begin() as conn:
        for r in results:
            boat_id = None
            if r.sail_number:
                boat_id = find_boat_by_sail_number(engine, normalize_sail(r.sail_number))
            if not boat_id and r.boat_name:
                boat_id = _find_boat_by_name(engine, r.boat_name, r.rating_value)
            if boat_id:
                matched += 1
            rd = dict(r.raw_data or {})
            rd.setdefault("boat_name", r.boat_name)
            if r.sail_number:
                rd.setdefault("sail_number", r.sail_number)
            r.raw_data = rd
            try:
                event_id = _ensure_event(
                    conn,
                    name=r.event_name,
                    event_date=r.event_date,
                    organiser=r.organizing_club,
                )
                entry_id = _ensure_event_entry(
                    conn,
                    event_id=event_id,
                    boat_id=boat_id,
                    boat_name=r.boat_name,
                    sail_number=r.sail_number,
                    tcc=r.rating_value,
                )
                # Link the entry to the matched boat when we found one.
                if boat_id:
                    conn.execute(
                        text(
                            "UPDATE event_entries SET boat_id = :b "
                            "WHERE id = :i AND boat_id IS NULL"
                        ),
                        {"b": boat_id, "i": entry_id},
                    )
                # Insert the result row under this entry, then point it at the
                # matched boat (so the coverage query, which joins on
                # race_results.boat_id, sees it).
                _insert_result(conn, entry_id=entry_id, r=r, source=source, boat_id=boat_id)
                imported += 1
            except Exception as e:  # noqa: BLE001
                logger.debug("import row failed (%s): %s", r.boat_name, e)

    log_ingestion_end(
        engine, log_id, records_found=len(results), records_new=imported
    )
    return {"imported": imported, "matched": matched}


def ingest_jog_season(
    engine: Engine,
    *,
    years: Sequence[int] | None = None,
    max_races: int | None = None,
) -> dict[str, Any]:
    """Discover + ingest one or more JOG seasons into ``race_results``.

    JOG pages are server-rendered so this path uses plain HTTP (no Firecrawl
    credits).  The source must pass the policy gate first.
    """
    import asyncio

    _assert_collectable(engine, SOURCE_JOG)

    src = JOGSource(years=years, engine=engine)
    events = asyncio.run(src.discover_events())
    if max_races:
        events = events[:max_races]

    total = {"events": 0, "imported": 0, "matched": 0}
    for ev in events:
        results = asyncio.run(src.scrape_event(ev))
        if not results:
            continue
        stats = _import_normalized(engine, results, SOURCE_JOG)
        total["events"] += 1
        total["imported"] += stats["imported"]
        total["matched"] += stats["matched"]
    return total


def ingest_warsash_sailwave(
    engine: Engine,
    *,
    seed_url: str = "https://warsashsc.org.uk/springseries/black-group-results/",
    extra_seed_urls: Sequence[str] = (),
    max_files: int | None = None,
) -> dict[str, Any]:
    """Ingest Warsash Spring Series results via their public Sailwave files.

    The Warsash results pages link to static Sailwave HTML files on
    ``sailwave.com/results/warsashsc``.  We fetch each with plain HTTP and
    parse it with the existing Sailwave parser (``sailwave`` is an approved
    source), writing rows under the ``warsash-spring-series`` source so the
    Solent coverage is attributable.
    """
    _assert_collectable(engine, SOURCE_WARSASH)

    import httpx

    from irc_data.scrapers.sailwave import parse_sailwave_html

    sailwave_urls: list[str] = []
    for seed in (seed_url, *extra_seed_urls):
        try:
            resp = httpx.get(
                seed,
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "SailRatings/1.0 (+https://sailratings.com)"},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("warsash seed fetch failed (%s): %s", seed, e)
            continue
        if resp.status_code != 200:
            continue
        for href in re.findall(r'href="(https://sailwave\.com/[^"]+\.htm)"', resp.text):
            if href not in sailwave_urls:
                sailwave_urls.append(href)

    if max_files:
        sailwave_urls = sailwave_urls[:max_files]

    total = {"files": 0, "imported": 0, "matched": 0}
    for url in sailwave_urls:
        try:
            resp = httpx.get(
                url,
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "SailRatings/1.0 (+https://sailratings.com)"},
            )
            if resp.status_code != 200:
                continue
        except Exception as e:  # noqa: BLE001
            logger.warning("sailwave fetch failed (%s): %s", url, e)
            continue
        results = parse_sailwave_html(
            resp.text, source_url=url, organizing_club="Warsash Sailing Club"
        )
        if not results:
            continue
        # Sailwave series pages don't carry a per-race date; the Warsash
        # season is named for its year ("… Warsash Sailing Club 2026").
        # Backfill a season date so the coverage query can count the season.
        year_m = re.search(r"(20\d\d)", url) or re.search(
            r"(20\d\d)", results[0].event_name or ""
        )
        season_year = int(year_m.group(1)) if year_m else None
        for r in results:
            if r.event_date is None and season_year:
                r.event_date = date(season_year, 3, 1)  # spring series ~ March
        stats = _import_normalized(engine, results, SOURCE_WARSASH)
        total["files"] += 1
        total["imported"] += stats["imported"]
        total["matched"] += stats["matched"]
    return total


__all__ = [
    "SOLENT_SOURCE_SLUGS",
    "SOURCE_JOG",
    "SOURCE_WARSASH",
    "SOURCE_HAMBLE",
    "JOG_RESULTS_INDEX",
    "JogRaceRow",
    "JOGSource",
    "HalSailResultsSource",
    "parse_jog_race_html",
    "discover_solent_sources",
    "ingest_jog_season",
    "ingest_warsash_sailwave",
]
