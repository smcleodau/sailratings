"""Wayback Machine historical IRC certificate PDF finder & downloader."""

import asyncio
import re
from pathlib import Path

from irc_data.config import CERTIFICATES_DIR, WAYBACK_BASE_URL, WAYBACK_CDX_URL
from irc_data.scrapers.base import RateLimiter, fetch_with_retry, get_http_client

rate_limiter = RateLimiter(min_delay=2.0, jitter=1.0)


async def search_wayback_pdfs(
    domain: str = "ircrating.org",
) -> list[dict]:
    """Query the Wayback Machine CDX API for archived IRC PDFs.

    Returns a list of dicts with keys: timestamp, url, mimetype, statuscode.
    """
    params = {
        "url": f"{domain}/*",
        "output": "json",
        "filter": "mimetype:application/pdf",
        "fl": "timestamp,original,mimetype,statuscode",
        "collapse": "digest",  # Deduplicate identical content
    }

    async with get_http_client() as client:
        resp = await fetch_with_retry(
            client, WAYBACK_CDX_URL, rate_limiter=rate_limiter, params=params
        )
        data = resp.json()

    if not data or len(data) < 2:
        return []

    # First row is headers
    headers = data[0]
    results = []
    for row in data[1:]:
        entry = dict(zip(headers, row))
        if entry.get("statuscode") == "200":
            results.append(entry)

    return results


async def download_wayback_pdf(
    timestamp: str,
    original_url: str,
    output_dir: Path | None = None,
) -> Path | None:
    """Download a PDF from the Wayback Machine.

    Args:
        timestamp: Wayback timestamp (e.g. '20230615120000')
        original_url: Original URL of the PDF
        output_dir: Where to save the PDF
    """
    output_dir = output_dir or CERTIFICATES_DIR / "historical"
    output_dir.mkdir(parents=True, exist_ok=True)

    wayback_url = f"{WAYBACK_BASE_URL}/{timestamp}id_/{original_url}"
    # id_ suffix tells Wayback to serve original content without banner

    safe_name = re.sub(r"[^\w\-.]", "_", original_url.split("/")[-1])
    if not safe_name.endswith(".pdf"):
        safe_name += ".pdf"
    dest = output_dir / f"{timestamp}_{safe_name}"

    if dest.exists():
        return dest

    async with get_http_client() as client:
        try:
            await rate_limiter.wait()
            resp = await client.get(wayback_url)
            resp.raise_for_status()
            if b"%PDF" in resp.content[:10]:
                dest.write_bytes(resp.content)
                return dest
            else:
                print(f"  Not a PDF: {wayback_url}")
                return None
        except Exception as e:
            print(f"  Failed: {wayback_url} — {e}")
            return None


async def find_and_download_all(
    domains: list[str] | None = None,
    output_dir: Path | None = None,
) -> list[Path]:
    """Search Wayback Machine for all IRC PDFs and download them."""
    domains = domains or ["ircrating.org", "rorcrating.com"]
    downloaded = []

    for domain in domains:
        print(f"Searching Wayback Machine for PDFs on {domain}...")
        results = await search_wayback_pdfs(domain)
        print(f"  Found {len(results)} archived PDFs")

        for entry in results:
            path = await download_wayback_pdf(
                entry["timestamp"],
                entry["original"],
                output_dir=output_dir,
            )
            if path:
                downloaded.append(path)
                print(f"  Saved: {path.name}")

    return downloaded
