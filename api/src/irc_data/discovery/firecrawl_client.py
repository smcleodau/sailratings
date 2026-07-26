"""Thin wrapper around the Firecrawl API.

We use Firecrawl for two things:

- `scrape_url(url)` — fetch a single page, return clean markdown
- `map_site(url, limit)` — discover sub-URLs from a seed

Every call is logged to the `firecrawl_calls` table so the
/justin/firecrawl dashboard can report burn rate, per-domain success,
and recent activity. Logging is best-effort and never raises — a DB
hiccup in telemetry must not break a scrape.

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
from urllib.parse import urlparse

from sqlalchemy import text

logger = logging.getLogger(__name__)


class FirecrawlUnavailable(RuntimeError):
    """Raised when FIRECRAWL_API_KEY is missing or the SDK isn't installed."""


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


def _domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.lower().lstrip("www.")
    except Exception:
        return ""


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


def _log_call(
    *,
    mode: str,
    url: str,
    status: str,
    duration_ms: int,
    credits: int | None,
    response_chars: int | None = None,
    links_found: int | None = None,
    error_message: str | None = None,
    caller: str | None = None,
) -> None:
    """Best-effort write to firecrawl_calls. Never raises."""
    try:
        from irc_data.db.connection import get_engine
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO firecrawl_calls
                  (mode, url, domain, status, credits, duration_ms,
                   response_chars, links_found, error_message, caller)
                VALUES
                  (:mode, :url, :domain, :status, :credits, :duration_ms,
                   :response_chars, :links_found, :error_message, :caller)
            """), {
                "mode": mode,
                "url": url,
                "domain": _domain_of(url),
                "status": status,
                "credits": credits if credits is not None else 1,
                "duration_ms": duration_ms,
                "response_chars": response_chars,
                "links_found": links_found,
                "error_message": (error_message or "")[:500] or None,
                "caller": caller or os.environ.get("FIRECRAWL_CALLER", "discovery"),
            })
    except Exception:  # noqa: BLE001 — telemetry must never break a scrape
        logger.exception("firecrawl_calls insert failed (non-fatal)")


def scrape_url(url: str, *, caller: str | None = None) -> ScrapeResult:
    """Scrape a single URL and return cleaned markdown + title."""
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
