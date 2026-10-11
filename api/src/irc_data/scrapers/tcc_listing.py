"""TCC listing CSV scraper.

Downloads the current IRC TCC listing CSV.

Primary source (since 2026): ircrating.org's "Valid Rating Listings" page now
embeds TopYacht's RORC listing app, whose "download complete listing" link is
a plain CSV at DIRECT_CSV_URL (same columns as the old ClubListing files).

Fallback: the ircrating.org page's own ClubListing_YYYYMMDD.csv link, which is
how the listing was published until about May 2026.
"""

import re
from datetime import date
from pathlib import Path

from irc_data.config import TCC_LISTINGS_DIR
from irc_data.scrapers.base import RateLimiter, get_http_client

rate_limiter = RateLimiter(min_delay=3.0, jitter=2.0)

DIRECT_CSV_URL = "https://www.topyacht.com.au/rorc/data/ClubListing.csv"
LISTING_HEADER_PREFIX = b"Boat Name,Sail No"

# Page that contains the link to the latest ClubListing_YYYYMMDD.csv
LISTING_PAGE_URL = "https://ircrating.org/irc-racing/online-tcc-listings/"

# CSV URL pattern published on the listing page. We pick the most recent one
# by sorting on the YYYYMMDD in the filename.
CSV_URL_RE = re.compile(
    r"https://ircrating\.org/wp-content/uploads/\d{4}/\d{2}/ClubListing_(\d{8})\.csv",
    re.IGNORECASE,
)


def _looks_like_listing(body: bytes) -> bool:
    return len(body) >= 1024 and body.lstrip(b"\xef\xbb\xbf").startswith(LISTING_HEADER_PREFIX)


async def _get_csv(client, url: str) -> bytes | None:
    try:
        await rate_limiter.wait()
        resp = await client.get(url)
        resp.raise_for_status()
    except Exception as e:
        print(f"  Failed to fetch {url}: {e}")
        return None
    if not _looks_like_listing(resp.content):
        print(f"  {url} did not return a TCC listing "
              f"(size={len(resp.content)}, content-type={resp.headers.get('content-type')!r})")
        return None
    return resp.content


async def _ircrating_page_csv_url(client) -> str | None:
    try:
        await rate_limiter.wait()
        page = await client.get(LISTING_PAGE_URL)
        page.raise_for_status()
    except Exception as e:
        print(f"  Failed to fetch listing page: {e}")
        return None
    matches = list(CSV_URL_RE.finditer(page.text))
    if not matches:
        print("  No ClubListing CSV URL found on the ircrating.org page.")
        return None
    return max(matches, key=lambda m: m.group(1)).group(0)


async def download_tcc_listing(output_dir: Path | None = None) -> Path | None:
    """Download the latest TCC listing CSV. Returns the saved path, or None on failure."""
    output_dir = output_dir or TCC_LISTINGS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"tcc_listing_{date.today().isoformat()}.csv"

    if dest.exists() and dest.stat().st_size > 0:
        print(f"  Already have today's listing: {dest}")
        return dest

    async with get_http_client() as client:
        print(f"Fetching {DIRECT_CSV_URL}...")
        body = await _get_csv(client, DIRECT_CSV_URL)
        if body is None:
            print(f"Falling back to {LISTING_PAGE_URL}...")
            url = await _ircrating_page_csv_url(client)
            body = await _get_csv(client, url) if url else None
        if body is None:
            return None

    dest.write_bytes(body)
    print(f"  Saved: {dest} ({len(body):,} bytes)")
    return dest
