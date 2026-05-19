"""Thin wrapper around the Firecrawl API.

We use Firecrawl for two things:

- `scrape_url(url)` — fetch a single page, return clean markdown
- `map_site(url, limit)` — discover sub-URLs from a seed

The wrapper degrades gracefully when `FIRECRAWL_API_KEY` is unset — it
raises a clear error rather than crashing midway through a CLI run, so
the rest of the system can be developed and tested without a paid key.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

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


def scrape_url(url: str) -> ScrapeResult:
    """Scrape a single URL and return cleaned markdown + title."""
    fc = _client()
    resp = fc.scrape(url, formats=["markdown"])
    # Firecrawl returns either a dict or a Pydantic model depending on SDK
    # version. Handle both shapes.
    md = getattr(resp, "markdown", None) or (resp.get("markdown") if isinstance(resp, dict) else "") or ""
    meta = getattr(resp, "metadata", None) or (resp.get("metadata", {}) if isinstance(resp, dict) else {})
    title = (meta.get("title") if isinstance(meta, dict) else getattr(meta, "title", None))
    return ScrapeResult(url=url, markdown=md, title=title)


def map_site(seed_url: str, limit: int = 50) -> list[str]:
    """Discover sub-URLs reachable from a seed. Returns a flat list of URLs.

    Use this when handed a calendar / results-index page — Firecrawl walks
    the site and returns every reachable URL we should consider.
    """
    fc = _client()
    resp = fc.map(seed_url, limit=limit)
    # SDK returns dict with `links` (list of strings or dicts)
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
    return links
