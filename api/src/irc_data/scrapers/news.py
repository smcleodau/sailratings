import asyncio
import os
import re
import json
from typing import Any
from urllib.parse import urlparse

import click
from rich.console import Console
from pydantic import BaseModel, Field

from irc_data.db.connection import get_engine
from irc_data.discovery.firecrawl_client import scrape_url, map_site
from sqlalchemy import text

console = Console()

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
        host = urlparse(url).hostname or ""
        return host.lower().lstrip("www.")
    except:
        return ""

async def scrape_news_source(seed_url: str, max_articles: int = 5):
    """Crawl a news source and process recent articles."""
    engine = get_engine()
    console.print(f"Mapping {seed_url} for articles...")
    
    try:
        # We use a larger limit to ensure we hit actual articles before filtering
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
            
        from irc_data.scrapers.result_import import _find_boat_by_name
        from irc_data.db.operations import find_boat_by_sail_number
        from irc_data.matching.identity import normalize_sail
        
        # Save to DB
        with engine.begin() as conn:
            # 1. Insert Article
            row = conn.execute(text("""
                INSERT INTO boat_news (source_domain, url, title, raw_markdown)
                VALUES (:domain, :url, :title, :md)
                RETURNING id
            """), {
                "domain": _domain_of(link),
                "url": link,
                "title": title[:500],
                "md": scraped.markdown
            }).fetchone()
            
            news_id = row[0]
            
            # 2. Match and insert mentions
            matched = 0
            for m in mentions:
                if m["confidence"] < 0.6:
                    continue
                    
                boat_id = None
                if m.get("sail_number"):
                    boat_id = find_boat_by_sail_number(engine, normalize_sail(m["sail_number"]))
                if not boat_id and m.get("boat_name"):
                    boat_id = _find_boat_by_name(engine, m["boat_name"], None)
                    
                if boat_id:
                    # Insert mention
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
                        console.print(f"    -> Matched '{m['boat_name']}' to boat {boat_id}")
                    except Exception as e:
                        console.print(f"    [yellow]Error saving mention: {e}[/yellow]")
                else:
                    console.print(f"    -> Could not match '{m['boat_name']}'")
                    
        processed += 1
