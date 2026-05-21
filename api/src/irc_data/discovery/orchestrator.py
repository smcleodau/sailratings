"""Seed-crawl orchestrator: map → scrape → extract → ingest.

Given a seed URL (e.g. https://sailracehq.com/ or
https://www.isora.org/index.php/notice-board/results2), this module:

1. Uses Firecrawl's `map` to discover linked sub-URLs (capped by
   ``max_pages``).
2. For each URL, scrapes the page via Firecrawl (rendered HTML / PDF
   normalised to markdown).
3. Passes the markdown to ``extract_results`` (Claude tool_use) which
   returns a structured ``RaceResult`` list.
4. Imports the results via ``import_scraper_results`` with the supplied
   ``source`` and ``transport`` tag — typically ``transport='firecrawl'``
   during the parallel-run window.

Designed to be cron-driven and fail-soft per URL — a single bad page
doesn't poison the batch.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _to_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _payload_to_race_results(url: str, payload: dict[str, Any]) -> list:
    """Translate extractor payload → list[RaceResult] (parsers/schemas.py)."""
    from datetime import datetime as _dt

    from irc_data.parsers.schemas import RaceResult

    rows = payload.get("results", [])
    if not rows:
        return []

    event_name = payload.get("event_name") or "Unknown Event"
    event_date = None
    if payload.get("event_date"):
        try:
            event_date = _dt.fromisoformat(payload["event_date"]).date()
        except Exception:
            event_date = None
    race_name = payload.get("race_name")
    class_name = payload.get("class_name")
    confidence = payload.get("confidence")
    fleet_size = len(rows)

    race_results: list = []
    for r in rows:
        rating = _to_decimal(r.get("rating_value"))
        raw = {
            "boat_name": r.get("boat_name"),
            "sail_number": r.get("sail_number"),
            "fleet_size": fleet_size,
            "division": class_name,
            "race_name": race_name,
            "rating_type": "irc_tcc" if rating else None,
            "rating_value": float(rating) if rating else None,
            "status": r.get("status"),
            "elapsed_time": r.get("elapsed_time"),
            "corrected_time": r.get("corrected_time"),
            "confidence": confidence,
        }
        race_results.append(
            RaceResult(
                event_name=event_name,
                event_date=event_date,
                source_url=url,
                tcc_at_race=rating,
                place=r.get("place"),
                division=class_name,
                elapsed_time=r.get("elapsed_time"),
                corrected_time=r.get("corrected_time"),
                raw_data=raw,
            )
        )
    return race_results


def seed_crawl_and_ingest(
    engine: Engine,
    seed_url: str,
    source: str,
    *,
    max_pages: int = 20,
    transport_tag: str = "firecrawl",
    year: int | None = None,
    mode: str = "map-site",
) -> dict[str, int]:
    """Crawl ``seed_url``, extract race results from each sub-URL, import.

    ``mode`` controls how the URL list is built:
    - ``"map-site"`` (default): Firecrawl maps the seed URL and discovers
      sub-URLs automatically (slow; can over-discover).
    - ``"per-source-expand"``: calls the registered expander for ``source``
      (see ``url_expanders.py``), producing a fixed list of leaf URLs. Faster
      and more reliable for sources with a known per-class URL pattern.

    Returns a per-batch stats dict::

        {"urls_mapped": int,
         "urls_with_results": int,
         "urls_failed": int,
         "rows_imported": int,
         "rows_matched": int}
    """
    from irc_data.discovery.extractor import extract_results
    from irc_data.discovery.firecrawl_client import (
        FirecrawlUnavailable, map_site, scrape_url,
    )
    from irc_data.discovery.url_expanders import expand_for_source
    from irc_data.scrapers.result_import import import_scraper_results

    stats = {
        "urls_mapped": 0,
        "urls_with_results": 0,
        "urls_failed": 0,
        "rows_imported": 0,
        "rows_matched": 0,
    }

    if mode == "per-source-expand":
        urls = expand_for_source(source, seed_url, year)
    else:
        try:
            urls = map_site(seed_url, limit=max_pages, caller="discover-and-ingest")
        except FirecrawlUnavailable as e:
            logger.error("firecrawl unavailable: %s", e)
            return stats
        # Always include the seed itself (some sites publish the index page
        # results directly).
        if seed_url not in urls:
            urls = [seed_url, *urls]

    stats["urls_mapped"] = len(urls)

    for url in urls[:max_pages]:
        try:
            page = scrape_url(url, caller="discover-and-ingest")
        except Exception as e:
            logger.warning("scrape failed for %s: %s", url, e)
            stats["urls_failed"] += 1
            continue
        if not page.markdown or not page.markdown.strip():
            continue

        payload = extract_results(url=url, markdown=page.markdown)
        if payload.get("_error"):
            logger.info("extractor _error %s → %s", url, payload["_error"])
            stats["urls_failed"] += 1
            continue

        race_results = _payload_to_race_results(url, payload)
        if not race_results:
            continue

        result = import_scraper_results(
            engine, race_results, source=source, transport=transport_tag
        )
        stats["urls_with_results"] += 1
        stats["rows_imported"] += result.get("imported", 0)
        stats["rows_matched"] += result.get("matched", 0)

    return stats
