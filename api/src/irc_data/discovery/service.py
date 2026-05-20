"""Discovery service — crawl, extract, persist.

Three entry points:

- `discover_url(engine, url)` — handle a single URL. Scrape with Firecrawl,
  extract with Claude, upsert into `event_discovery`.
- `discover_seed(engine, seed_url, limit)` — discover sub-URLs via Firecrawl
  map, then handle each.
- `ingest_confirmed(engine, discovery_id)` — route a confirmed entry to the
  existing per-platform scraper.

All persistence is idempotent on `source_url`: re-running the discovery for
the same URL refreshes the extraction rather than creating duplicates.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.discovery.firecrawl_client import (
    FirecrawlUnavailable, scrape_url, map_site,
)
from irc_data.discovery.extractor import extract_event

logger = logging.getLogger(__name__)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def discover_url(engine: Engine, url: str, *, seed_url: str | None = None,
                 source_type: str = "manual") -> dict[str, Any]:
    """Scrape, extract, persist. Returns the persisted row as a dict."""
    try:
        scraped = scrape_url(url)
    except FirecrawlUnavailable as e:
        return _persist(engine, url, seed_url, source_type, {
            "scoring_platform": "unknown",
            "platform_ids": {},
            "confidence": 0.0,
            "reasoning": f"firecrawl unavailable: {e}",
            "_error": str(e),
        })

    extraction = extract_event(url, scraped.markdown)
    if not extraction.get("title"):
        extraction["title"] = scraped.title

    return _persist(engine, url, seed_url, source_type, extraction)


def discover_seed(engine: Engine, seed_url: str, *, limit: int = 30) -> list[dict[str, Any]]:
    """Map a seed URL via Firecrawl, then extract from each sub-URL."""
    try:
        urls = map_site(seed_url, limit=limit)
    except FirecrawlUnavailable as e:
        raise RuntimeError(f"cannot map seed: {e}") from e

    # Always include the seed itself
    if seed_url not in urls:
        urls.insert(0, seed_url)

    out: list[dict[str, Any]] = []
    for u in urls[:limit]:
        try:
            row = discover_url(engine, u, seed_url=seed_url, source_type="seed_crawl")
            out.append(row)
        except Exception as e:
            logger.exception(f"discover_url failed for {u}: {e}")
    return out


def _persist(engine: Engine, url: str, seed_url: str | None,
             source_type: str, extraction: dict[str, Any]) -> dict[str, Any]:
    """Upsert one extraction into event_discovery. Idempotent on source_url."""
    platform = extraction.get("scoring_platform", "unknown") or "unknown"
    platform_ids = extraction.get("platform_ids") or {}
    # Claude sometimes nests platform_ids under the platform name
    # (e.g. {"sailsys": {"club_id": …}}). Flatten when we see that.
    if (
        platform != "unknown"
        and isinstance(platform_ids, dict)
        and len(platform_ids) == 1
        and platform in platform_ids
        and isinstance(platform_ids[platform], dict)
    ):
        platform_ids = platform_ids[platform]
    title = extraction.get("title")
    event_date = _parse_date(extraction.get("event_date"))
    location = extraction.get("event_location")
    conf = extraction.get("confidence")
    if conf is not None:
        conf = round(float(conf), 2)
    error = extraction.get("_error")

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id, status FROM event_discovery WHERE source_url = :url"),
            {"url": url},
        ).first()
        if existing:
            # Don't downgrade a confirmed/ingested entry; only refresh pending ones.
            if existing.status in ("pending", "failed", "rejected"):
                conn.execute(text("""
                    UPDATE event_discovery
                    SET scoring_platform = :platform,
                        platform_ids = CAST(:ids AS jsonb),
                        title = COALESCE(:title, title),
                        event_date = COALESCE(:event_date, event_date),
                        event_location = COALESCE(:location, event_location),
                        confidence = :conf,
                        raw_extraction = CAST(:raw AS jsonb),
                        error_message = :err,
                        status = CASE WHEN :err IS NOT NULL THEN 'failed'
                                      ELSE 'pending' END
                    WHERE id = :id
                """), {
                    "id": existing.id, "platform": platform,
                    "ids": _to_json(platform_ids),
                    "title": title, "event_date": event_date,
                    "location": location, "conf": conf,
                    "raw": _to_json(extraction), "err": error,
                })
            row_id = existing.id
        else:
            row_id = conn.execute(text("""
                INSERT INTO event_discovery
                  (source_url, source_type, seed_url, scoring_platform, platform_ids,
                   title, event_date, event_location, confidence, raw_extraction,
                   status, error_message)
                VALUES
                  (:url, :stype, :seed, :platform, CAST(:ids AS jsonb),
                   :title, :event_date, :location, :conf, CAST(:raw AS jsonb),
                   CASE WHEN CAST(:err AS text) IS NOT NULL THEN 'failed' ELSE 'pending' END,
                   :err)
                RETURNING id
            """), {
                "url": url, "stype": source_type, "seed": seed_url,
                "platform": platform, "ids": _to_json(platform_ids),
                "title": title, "event_date": event_date, "location": location,
                "conf": conf, "raw": _to_json(extraction), "err": error,
            }).scalar()

        row = conn.execute(
            text("SELECT * FROM event_discovery WHERE id = :id"), {"id": row_id}
        ).first()
        return _row_to_dict(row)


def _to_json(obj) -> str:
    import json as _json
    return _json.dumps(obj, default=str)


def _row_to_dict(row) -> dict[str, Any]:
    if row is None:
        return {}
    d = dict(row._mapping)
    for k, v in list(d.items()):
        if isinstance(v, (datetime, date)):
            d[k] = v.isoformat()
    return d


def ingest_confirmed(engine: Engine, discovery_id: int) -> dict[str, Any]:
    """Run the appropriate per-platform scraper for a confirmed entry.

    Currently routes:
    - sailsys → adds (club_id, series_id) and calls scrape_race_results
      for the specific race(s) declared in platform_ids
    - topyacht → reuses scrape_club for the discovered club + year

    Other platforms (sailwave, yachtscoring, pdf) raise NotImplementedError
    until per-platform scrapers exist. The row stays at status='confirmed'
    so it remains visible.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM event_discovery WHERE id = :id"), {"id": discovery_id}
        ).first()
    if not row:
        raise ValueError(f"discovery {discovery_id} not found")

    platform = row.scoring_platform
    ids = row.platform_ids or {}

    if platform == "sailsys":
        return _ingest_sailsys(engine, discovery_id, ids)
    if platform == "topyacht":
        return _ingest_topyacht(engine, discovery_id, ids)

    _mark_failed(engine, discovery_id,
                 f"automatic ingestion for {platform!r} not implemented yet")
    return {"status": "failed", "platform": platform}


def _ingest_sailsys(engine: Engine, discovery_id: int, ids: dict) -> dict[str, Any]:
    """Scrape one SailSys race using the existing scraper internals."""
    import asyncio
    import httpx

    from irc_data.scrapers import sailsys as ss
    from irc_data.scrapers.result_import import import_scraper_results

    series_id = ids.get("series_id")
    race_id = ids.get("race_id")
    club_id = ids.get("club_id")
    if not (series_id and club_id):
        _mark_failed(engine, discovery_id, "missing series_id/club_id in platform_ids")
        return {"status": "failed"}

    async def _go() -> list:
        results: list = []
        async with httpx.AsyncClient(timeout=30) as client:
            races = await ss.get_series_races(client, int(series_id))
            races_to_do = (
                [r for r in races if r["race_id"] == int(race_id)]
                if race_id else races
            )
            for r in races_to_do:
                if r.get("status") not in ss.COMPLETED_RACE_STATUSES:
                    continue
                rr = await ss.scrape_race_results(
                    client, int(club_id), int(series_id), int(r["race_id"]),
                    series_name=str(ids.get("series_name", "")),
                    club_name=str(ids.get("club_name", "")),
                )
                results.extend(rr)
        return results

    results = asyncio.run(_go())
    if not results:
        _mark_failed(engine, discovery_id, "scraper returned 0 rows")
        return {"status": "failed", "rows": 0}

    stats = import_scraper_results(
        engine, results, source="sailsys",
        organizing_club=str(ids.get("club_name") or ""),
    )
    _mark_ingested(engine, discovery_id, stats.get("imported", 0))
    return {"status": "ingested", "platform": "sailsys",
            "imported": stats.get("imported", 0),
            "matched": stats.get("matched", 0)}


def _ingest_topyacht(engine: Engine, discovery_id: int, ids: dict) -> dict[str, Any]:
    """For now, just queue a club/year scrape — TopYacht's discovery model
    expects a club key + year, not a single race id."""
    club_key = ids.get("club_key")
    if not club_key:
        _mark_failed(engine, discovery_id, "topyacht discoveries need a club_key in platform_ids")
        return {"status": "failed"}
    # We don't run topyacht inline (it's slow) — leave the row confirmed,
    # the daily cron will pick it up if the club is in TOPYACHT_CLUBS.
    _mark_failed(engine, discovery_id,
                 f"TopYacht club_key={club_key!r} — confirm it's in TOPYACHT_CLUBS, then daily cron will pick it up")
    return {"status": "pending-cron", "platform": "topyacht"}


def _mark_ingested(engine: Engine, discovery_id: int, rows: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE event_discovery
            SET status = 'ingested',
                ingested_at = :now,
                notes = COALESCE(notes, '') || 'imported ' || :rows || ' rows'
            WHERE id = :id
        """), {"id": discovery_id, "now": datetime.now(timezone.utc), "rows": rows})


def _mark_failed(engine: Engine, discovery_id: int, msg: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE event_discovery
            SET status = 'failed', error_message = :msg
            WHERE id = :id
        """), {"id": discovery_id, "msg": msg})
