"""Thin wrapper around the Firecrawl API.

We use Firecrawl for three things:

- `scrape_url(url)` — fetch a single page, return clean markdown
- `map_site(url, limit)` — discover sub-URLs from a seed
- `crawl_site(url, limit)` — crawl a site and return full page contents

Every call is logged to the `firecrawl_calls` table (via the
provider-agnostic ledger in `crawl_telemetry`) so the /justin/firecrawl
dashboard can report burn rate, per-domain success, and recent activity.
Logging is best-effort and never raises — a DB hiccup in telemetry must
not break a scrape.

Every call also passes through the credit-budget gate
(`crawl_telemetry.check_throttle`) *before* hitting the API: at the soft
cap, discovery-class callers are refused; at the hard cap everything but
manual calls is refused with `CrawlBudgetExhausted`. That's the "never
run out of crawl budget silently" guarantee from OPS-01-05.

The wrapper degrades gracefully when `FIRECRAWL_API_KEY` is unset — it
raises `FirecrawlUnavailable` rather than crashing midway through a CLI
run, so the rest of the system can be developed and tested without a
paid key.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from irc_data.discovery.crawl_telemetry import (
    CrawlBudgetExhausted,
    check_throttle,
    domain_of as _domain_of,
    log_call as _log_call,
)

logger = logging.getLogger(__name__)

# Re-exported so callers can catch either failure mode from one import.
__all__ = [
    "CrawlBudgetExhausted",
    "FirecrawlUnavailable",
    "ScrapeResult",
    "scrape_url",
    "map_site",
    "crawl_site",
    "get_credit_usage",
]


class FirecrawlUnavailable(RuntimeError):
    """Raised when FIRECRAWL_API_KEY is missing or the SDK isn't installed."""


def _enforce_budget(mode: str, url: str, caller: str | None) -> None:
    """Soft/hard credit-cap gate. Raises CrawlBudgetExhausted when throttled."""
    decision = check_throttle(mode=mode, url=url, caller=caller)
    if not decision.allowed:
        raise CrawlBudgetExhausted(decision.reason)


@dataclass
class ScrapeResult:
    url: str
    markdown: str
    title: str | None = None
    html: str | None = None


def _client():
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        raise FirecrawlUnavailable(
            "FIRECRAWL_API_KEY not set — add to ~/.env (or 1Password vault) "
            "and re-source before running discovery commands."
        )
    try:
        from firecrawl import Firecrawl  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise FirecrawlUnavailable(
            f"firecrawl-py not installed in this venv: {e}"
        ) from e
    return Firecrawl(api_key=api_key)


def _credits_from_response(resp: Any) -> int | None:
    """Pull credits_used off whichever shape the SDK returned."""
    if resp is None:
        return None
    val = getattr(resp, "credits_used", None)
    if val is None and isinstance(resp, dict):
        val = resp.get("credits_used") or resp.get("creditsUsed")
    if val is None:
        # Try nested metadata
        meta = getattr(resp, "metadata", None) or (resp.get("metadata") if isinstance(resp, dict) else None)
        if meta is not None:
            val = getattr(meta, "credits_used", None)
            if val is None and isinstance(meta, dict):
                val = meta.get("credits_used") or meta.get("creditsUsed")
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def scrape_url(url: str, *, caller: str | None = None) -> ScrapeResult:
    """Scrape a single URL and return cleaned markdown + title."""
    _enforce_budget("scrape", url, caller)
    fc = _client()
    t0 = time.monotonic()
    try:
        resp = fc.scrape(url, formats=["markdown"])
    except Exception as e:
        _log_call(
            mode="scrape", url=url, status="error",
            duration_ms=int((time.monotonic() - t0) * 1000),
            credits=None, response_chars=0,
            error_message=str(e), caller=caller,
        )
        raise

    duration_ms = int((time.monotonic() - t0) * 1000)
    md = getattr(resp, "markdown", None) or (resp.get("markdown") if isinstance(resp, dict) else "") or ""
    meta = getattr(resp, "metadata", None) or (resp.get("metadata", {}) if isinstance(resp, dict) else {})
    title = (meta.get("title") if isinstance(meta, dict) else getattr(meta, "title", None))

    _log_call(
        mode="scrape", url=url,
        status="ok" if md.strip() else "empty",
        duration_ms=duration_ms,
        credits=_credits_from_response(resp),
        response_chars=len(md),
        caller=caller,
    )
    return ScrapeResult(url=url, markdown=md, title=title)


def map_site(seed_url: str, limit: int = 50, *, search: str | None = None, caller: str | None = None) -> list[str]:
    """Discover sub-URLs reachable from a seed. Returns a flat list of URLs.

    Use this when handed a calendar / results-index page — Firecrawl walks
    the site and returns every reachable URL we should consider.
    """
    _enforce_budget("map", seed_url, caller)
    fc = _client()
    t0 = time.monotonic()
    try:
        kwargs = {}
        if search:
            kwargs["search"] = search
        else:
            kwargs["limit"] = limit
        resp = fc.map(seed_url, **kwargs)
    except Exception as e:
        _log_call(
            mode="map", url=seed_url, status="error",
            duration_ms=int((time.monotonic() - t0) * 1000),
            credits=None, links_found=0,
            error_message=str(e), caller=caller,
        )
        raise

    duration_ms = int((time.monotonic() - t0) * 1000)
    links_raw = (
        resp.get("links") if isinstance(resp, dict)
        else getattr(resp, "links", None)
    ) or []
    links: list[str] = []
    for item in links_raw:
        if isinstance(item, str):
            links.append(item)
        elif isinstance(item, dict) and "url" in item:
            links.append(item["url"])
        elif hasattr(item, "url"):
            links.append(item.url)

    _log_call(
        mode="map", url=seed_url,
        status="ok" if links else "empty",
        duration_ms=duration_ms,
        credits=_credits_from_response(resp),
        response_chars=0, links_found=len(links),
        caller=caller,
    )
    return links


def crawl_site(seed_url: str, limit: int = 10, *, caller: str | None = None) -> dict[str, Any]:
    """Crawl a site from a seed, scraping pages as it goes.

    Unlike `map_site` (URL discovery only), Firecrawl's crawl endpoint
    returns full page content per URL — the right tool when we know we want
    every page under a results index. Logged as mode='crawl' in the ledger
    with one row for the whole job (pages scraped → ``links_found``).
    """
    _enforce_budget("crawl", seed_url, caller)
    fc = _client()
    t0 = time.monotonic()
    try:
        resp = fc.crawl(seed_url, limit=limit, scrape_options={"formats": ["markdown"]})
    except Exception as e:
        _log_call(
            mode="crawl", url=seed_url, status="error",
            duration_ms=int((time.monotonic() - t0) * 1000),
            credits=None, links_found=0,
            error_message=str(e), caller=caller,
        )
        raise

    duration_ms = int((time.monotonic() - t0) * 1000)
    # SDK returns either a dict with 'data' or an object with .data; each
    # page entry carries markdown + metadata like scrape() results.
    pages = (
        resp.get("data") if isinstance(resp, dict)
        else getattr(resp, "data", None)
    ) or []
    out: list[dict[str, Any]] = []
    for page in pages:
        if isinstance(page, dict):
            md = page.get("markdown") or ""
            meta = page.get("metadata") or {}
            page_url = (meta.get("sourceURL") or meta.get("url")
                        or page.get("url") or seed_url)
            title = meta.get("title")
        else:
            md = getattr(page, "markdown", "") or ""
            meta = getattr(page, "metadata", None)
            page_url = (
                getattr(meta, "sourceURL", None) or getattr(meta, "url", None)
                or getattr(page, "url", None) or seed_url
            )
            title = getattr(meta, "title", None) if meta is not None else None
        out.append({"url": page_url, "markdown": md, "title": title})

    _log_call(
        mode="crawl", url=seed_url,
        status="ok" if out else "empty",
        duration_ms=duration_ms,
        credits=_credits_from_response(resp),
        response_chars=sum(len(p["markdown"]) for p in out),
        links_found=len(out),
        caller=caller,
    )
    return {"seed_url": seed_url, "pages": out, "page_count": len(out)}


def get_credit_usage() -> dict[str, Any] | None:
    """Ask Firecrawl for current credit-usage state on this account.

    Returns {"remaining_credits": int, "plan_credits": int | None} or None
    if the SDK call fails. Used by the dashboard to show "X of Y credits
    remaining this billing period" — the authoritative number that our
    own per-call tally is a lossy approximation of.
    """
    try:
        fc = _client()
    except FirecrawlUnavailable:
        return None
    try:
        resp = fc.get_credit_usage()
    except Exception as e:
        logger.warning("get_credit_usage failed: %s", e)
        return None

    def _g(name: str) -> Any:
        v = getattr(resp, name, None)
        if v is None and isinstance(resp, dict):
            v = resp.get(name)
        return v

    out = {
        "remaining_credits": _g("remaining_credits"),
        "plan_credits": _g("plan_credits"),
    }
    if out["remaining_credits"] is None:
        return None
    return out
