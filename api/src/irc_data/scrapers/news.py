"""Sailing-news ingestion (OPS-02-06).

OPS-02-06 moves news acquisition **off Firecrawl** and onto plain-RSS raw
capture. Firecrawl calls to news domains must be **zero** — the acceptance
gate for the whole task.

Two ingestion paths, both Firecrawl-free:

- ``scrape_news_rss(feeds, max_articles)`` — async path used by the
  ``irc-data scrape-news`` CLI. Parses each RSS/Atom feed with the stdlib
  XML parser (no new dependency), fetches each article's HTML directly with
  httpx, converts it to plain text, and runs the Gemini boat-mention
  extractor. Articles are deduplicated by URL in ``boat_news``.
- ``capture_news_feeds(...)`` — the DP-00-04 raw-capture job in
  ``scrapers/raw_capture.py`` (run via ``irc-data scrape raw-capture
  --source sailing-news``). That path archives the raw feed bytes and is the
  scheduled, durable capture. This module's RSS path is the *parse-and-match*
  layer on top.

The legacy Firecrawl path (``scrape_news_source``) is retained for one
release as a deprecated shim that raises unless ``ALLOW_FIRECRAWL_NEWS=1``
is set — a tripwire so the "news domains = 0 Firecrawl calls" budget holds.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import click
from rich.console import Console
from pydantic import BaseModel, Field

from irc_data.db.connection import get_engine
from sqlalchemy import text

console = Console()

# Default RSS/Atom feeds. These mirror ``raw_capture.DEFAULT_NEWS_FEEDS`` —
# sailingscuttlebutt and the other RSS-capable outlets — so the two news
# paths stay in lockstep. Kept as a local constant to avoid a hard import
# cycle with raw_capture (which imports provenance/policy helpers).
DEFAULT_NEWS_FEEDS: tuple[str, ...] = (
    "https://www.sailweb.co.uk/feed",
    "https://www.sail-world.com/rss",
    "https://www.sailingscuttlebutt.com/feed",
)

NEWS_SYSTEM_PROMPT = """You analyse a sailing news article and extract any specific racing yachts mentioned by name and/or sail number.

You will be given the URL and the article's markdown content.

Your job is to return a JSON object containing:
- title: The title of the article
- mentioned_boats: A list of boats mentioned in the text. For each boat, extract:
  - boat_name: The name of the boat (e.g. "Rampage 88", "Comanche")
  - sail_number: The sail number if mentioned (e.g. "GBR8994R", "AUS1"). If not mentioned, return null.
  - snippet: A short (1-2 sentence) quote from the article showing the context in which the boat was mentioned.
  - confidence: Your confidence that this is actually a specific competing racing yacht (1.0 = certain, 0.5 = guessing). E.g. "Rolex" or "TP52" are not specific boats. "Black Jack" or "Sunrise" are.

If no boats are mentioned, return an empty list for mentioned_boats.
"""

class MentionedBoat(BaseModel):
    boat_name: str
    sail_number: str | None = None
    snippet: str
    confidence: float = Field(..., ge=0, le=1)

class ArticleExtraction(BaseModel):
    title: str = Field(default="Untitled")
    mentioned_boats: list[MentionedBoat] = Field(default_factory=list)

def extract_boat_mentions(url: str, markdown: str) -> dict[str, Any]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"_error": "GEMINI_API_KEY not set"}

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)

        user_message = f"URL: {url}\n\nARTICLE MARKDOWN:\n\n{markdown[:30000]}"

        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "mentioned_boats": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "boat_name": {"type": "string"},
                            "sail_number": {"type": "string", "nullable": True},
                            "snippet": {"type": "string"},
                            "confidence": {"type": "number"}
                        },
                        "required": ["boat_name", "snippet", "confidence"]
                    }
                }
            },
            "required": ["title", "mentioned_boats"]
        }

        model = genai.GenerativeModel(
            "gemini-2.5-flash",
            generation_config={"response_mime_type": "application/json", "response_schema": schema},
            system_instruction=NEWS_SYSTEM_PROMPT
        )

        resp = model.generate_content(user_message)

        data = json.loads(resp.text)

        # Validate against our Pydantic schema to ensure structure
        validated_data = ArticleExtraction.model_validate(data).model_dump()
        return validated_data
    except Exception as e:
        return {"_error": f"Gemini call failed: {e}"}

def _domain_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# RSS / Atom parsing (Firecrawl-free)
# ---------------------------------------------------------------------------

# XML namespaces seen in the wild across WordPress / Sail-World / SailWeb.
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"


def parse_feed(xml_bytes: bytes) -> list[dict[str, str | None]]:
    """Parse an RSS 2.0 or Atom feed into a list of article descriptors.

    Returns a list of dicts with keys ``url``, ``title``, ``published`` and
    (when present inline) ``summary``. Uses only the stdlib XML parser so we
    add no third-party dependency. Best-effort: malformed entries are
    skipped rather than aborting the whole feed.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    articles: list[dict[str, str | None]] = []

    # --- RSS 2.0: <channel><item>... -------------------------------------
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        articles.append({
            "url": link,
            "title": (item.findtext("title") or "").strip() or None,
            "published": (
                (item.findtext("pubDate") or "").strip()
                or (item.findtext("date") or "").strip()
                or None
            ),
            "summary": (
                (item.findtext(f"{_CONTENT_NS}encoded") or "").strip()
                or (item.findtext("description") or "").strip()
                or None
            ),
        })

    if articles:
        return articles

    # --- Atom: <entry>... -------------------------------------------------
    for entry in root.iter(f"{_ATOM_NS}entry"):
        link = ""
        for link_el in entry.findall(f"{_ATOM_NS}link"):
            href = link_el.get("href", "")
            rel = link_el.get("rel", "alternate")
            if href and rel in ("alternate", ""):
                link = href
                break
        if not link:
            # Fall back to any href if no explicit alternate link.
            first = entry.find(f"{_ATOM_NS}link")
            if first is not None:
                link = first.get("href", "")
        if not link:
            continue
        articles.append({
            "url": link.strip(),
            "title": (entry.findtext(f"{_ATOM_NS}title") or "").strip() or None,
            "published": (
                (entry.findtext(f"{_ATOM_NS}published") or "").strip()
                or (entry.findtext(f"{_ATOM_NS}updated") or "").strip()
                or None
            ),
            "summary": (
                (entry.findtext(f"{_ATOM_NS}content") or "").strip()
                or (entry.findtext(f"{_ATOM_NS}summary") or "").strip()
                or None
            ),
        })

    return articles


def html_to_text(html: str) -> str:
    """Convert article HTML to clean plain text for the extractor.

    Strips script/style/noscript blocks, then all tags. Collapses runs of
    whitespace. This is the Firecrawl-free substitute for Firecrawl's
    markdown rendering — good enough for prose news articles.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "form"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


async def _fetch_text(client: Any, url: str) -> str:
    """Fetch a URL and return its body as text. Raises on transport error."""
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _already_processed(engine, url: str) -> bool:
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT id FROM boat_news WHERE url = :url"), {"url": url}
        ).first()
    return exists is not None


async def scrape_news_rss(
    feeds: list[str] | tuple[str, ...] | None = None,
    max_articles: int = 5,
) -> dict[str, int]:
    """Ingest sailing news via RSS feeds — the Firecrawl-free path.

    Parses each feed, fetches recent article pages directly, extracts boat
    mentions with Gemini, and persists to ``boat_news`` / ``boat_news_mentions``.

    Returns a stats dict: ``{"feeds", "articles_seen", "articles_new",
    "articles_processed", "mentions_matched"}``.
    """
    import httpx

    engine = get_engine()
    feed_urls = list(feeds) if feeds else list(DEFAULT_NEWS_FEEDS)

    stats = {
        "feeds": len(feed_urls),
        "articles_seen": 0,
        "articles_new": 0,
        "articles_processed": 0,
        "mentions_matched": 0,
    }

    headers = {
        "User-Agent": (
            "SailRatings-NewsBot/1.0 (+https://sailratings.com; "
            "news ingestion; contact admin@sailratings.com)"
        )
    }

    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        for feed_url in feed_urls:
            if stats["articles_processed"] >= max_articles:
                break
            console.print(f"[cyan]Fetching feed[/cyan] {feed_url}")
            try:
                feed_xml = await _fetch_text(client, feed_url)
            except Exception as e:
                console.print(f"  [red]Feed fetch failed: {e}[/red]")
                continue

            articles = parse_feed(feed_xml.encode("utf-8", "ignore"))
            stats["articles_seen"] += len(articles)
            console.print(f"  {len(articles)} entries in feed")

            for art in articles:
                if stats["articles_processed"] >= max_articles:
                    break
                link = art["url"]
                if not link:
                    continue
                if _already_processed(engine, link):
                    continue

                stats["articles_new"] += 1
                console.print(f"\n[cyan]Article[/cyan] {link}")
                try:
                    html = await _fetch_text(client, link)
                    body = html_to_text(html)
                    await asyncio.sleep(2)  # be polite to the news site
                except Exception as e:
                    console.print(f"  [red]Article fetch failed: {e}[/red]")
                    continue

                if not body.strip():
                    continue

                extraction = extract_boat_mentions(link, body)
                if extraction.get("_error"):
                    console.print(
                        f"  [red]Extraction failed: {extraction['_error']}[/red]"
                    )
                    continue

                title = extraction.get("title") or art["title"] or "Untitled"
                mentions = extraction.get("mentioned_boats", [])
                console.print(f"  Title: {title}")
                console.print(f"  Found {len(mentions)} boat mentions")

                matched = _persist_article(engine, link, title, body, mentions)
                stats["mentions_matched"] += matched
                stats["articles_processed"] += 1

    return stats


def _persist_article(engine, url: str, title: str, body: str,
                     mentions: list[dict[str, Any]]) -> int:
    """Insert one article + its matched boat mentions. Returns match count.

    Kept separate from the fetch/extract loop so it can be unit-tested and
    reused by the raw-capture parse layer.
    """
    if not mentions:
        # Still record the article so we don't reprocess it, but there is
        # nothing to match.
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO boat_news (source_domain, url, title, raw_markdown)
                VALUES (:domain, :url, :title, :md)
                ON CONFLICT (url) DO NOTHING
            """), {
                "domain": _domain_of(url),
                "url": url,
                "title": title[:500],
                "md": body,
            })
        return 0

    from irc_data.scrapers.result_import import _find_boat_by_name
    from irc_data.db.operations import find_boat_by_sail_number
    from irc_data.matching.identity import normalize_sail

    matched = 0
    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO boat_news (source_domain, url, title, raw_markdown)
            VALUES (:domain, :url, :title, :md)
            ON CONFLICT (url) DO UPDATE SET title = EXCLUDED.title
            RETURNING id
        """), {
            "domain": _domain_of(url),
            "url": url,
            "title": title[:500],
            "md": body,
        }).fetchone()

        news_id = row[0]

        for m in mentions:
            if m["confidence"] < 0.6:
                continue

            boat_id = None
            if m.get("sail_number"):
                boat_id = find_boat_by_sail_number(
                    engine, normalize_sail(m["sail_number"])
                )
            if not boat_id and m.get("boat_name"):
                boat_id = _find_boat_by_name(engine, m["boat_name"], None)

            if boat_id:
                try:
                    conn.execute(text("""
                        INSERT INTO boat_news_mentions (news_id, boat_id, confidence)
                        VALUES (:nid, :bid, :conf)
                        ON CONFLICT DO NOTHING
                    """), {
                        "nid": news_id,
                        "bid": boat_id,
                        "conf": m["confidence"]
                    })
                    matched += 1
                    console.print(
                        f"    -> Matched '{m['boat_name']}' to boat {boat_id}"
                    )
                except Exception as e:
                    console.print(f"    [yellow]Error saving mention: {e}[/yellow]")
            else:
                console.print(f"    -> Could not match '{m['boat_name']}'")

    return matched


# ---------------------------------------------------------------------------
# Deprecated Firecrawl path — tripwire only
# ---------------------------------------------------------------------------

async def scrape_news_source(seed_url: str, max_articles: int = 5):
    """DEPRECATED Firecrawl-based news scrape.

    OPS-02-06 moved news to plain-RSS raw capture; Firecrawl calls to news
    domains must be zero. This shim is a tripwire: it raises unless the
    operator explicitly sets ``ALLOW_FIRECRAWL_NEWS=1`` (a deliberate,
    auditable override). Otherwise use :func:`scrape_news_rss`.
    """
    if os.environ.get("ALLOW_FIRECRAWL_NEWS") != "1":
        raise RuntimeError(
            "scrape_news_source() is deprecated (OPS-02-06): news ingestion "
            "uses RSS raw-capture and must not spend Firecrawl credits on "
            "news domains. Use scrape_news_rss() / `irc-data scrape-news`. "
            "Set ALLOW_FIRECRAWL_NEWS=1 to force the legacy path (audited)."
        )

    # Lazy import so the deprecated path alone doesn't drag the Firecrawl
    # client (and its budget gate) into the RSS pipeline.
    from irc_data.discovery.firecrawl_client import scrape_url, map_site

    console.print(
        "[yellow]WARNING: legacy Firecrawl news path enabled via "
        "ALLOW_FIRECRAWL_NEWS=1 — this spends crawl credits on news.[/yellow]"
    )
    engine = get_engine()
    console.print(f"Mapping {seed_url} for articles...")

    try:
        links = map_site(seed_url, limit=100, search="race", caller="news.scraper")
    except Exception as e:
        console.print(f"[red]Failed to map {seed_url}: {e}[/red]")
        return

    console.print(f"Found {len(links)} links. Filtering for articles...")

    processed = 0
    for link in links:
        if processed >= max_articles:
            break

        # Very basic heuristic to skip categories/tags and hit articles
        if "/category/" in link or "/tag/" in link or "/author/" in link:
            continue

        # Check if we already processed this URL
        with engine.connect() as conn:
            exists = conn.execute(text("SELECT id FROM boat_news WHERE url = :url"), {"url": link}).first()
            if exists:
                continue

        console.print(f"\nScraping {link}")
        try:
            scraped = scrape_url(link, caller="news.scraper")
            await asyncio.sleep(2) # Avoid rate limits
        except Exception as e:
            console.print(f"  [red]Scrape failed: {e}[/red]")
            if "Rate limit" in str(e):
                await asyncio.sleep(5)
            continue

        if not scraped.markdown.strip():
            continue

        extraction = extract_boat_mentions(link, scraped.markdown)
        if extraction.get("_error"):
            console.print(f"  [red]Extraction failed: {extraction['_error']}[/red]")
            continue

        title = extraction.get("title") or scraped.title or "Untitled"
        mentions = extraction.get("mentioned_boats", [])

        console.print(f"  Title: {title}")
        console.print(f"  Found {len(mentions)} boat mentions")

        if not mentions:
            continue

        _persist_article(engine, link, title, scraped.markdown, mentions)
        processed += 1
