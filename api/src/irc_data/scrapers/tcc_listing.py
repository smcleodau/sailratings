"""TCC listing CSV scraper.

Downloads the current IRC TCC listing CSV from ircrating.org.

The site's "Valid Rating Listings" page (online-tcc-listings/) historically
served the data via an iframe-embedded listing app with a "Club Listing"
download link. That hosted listing has been broken for some time, and the
site now publishes the latest CSV directly on the page as a fallback link of
the form:

    https://ircrating.org/wp-content/uploads/YYYY/MM/ClubListing_YYYYMMDD.csv

This scraper finds that link in the page HTML and downloads the CSV. No
browser automation required.
"""

import re
from datetime import date
from pathlib import Path

from irc_data.config import TCC_LISTINGS_DIR
from irc_data.scrapers.base import RateLimiter, get_http_client

rate_limiter = RateLimiter(min_delay=3.0, jitter=2.0)

# Page that contains the link to the latest ClubListing_YYYYMMDD.csv
LISTING_PAGE_URL = "https://ircrating.org/irc-racing/online-tcc-listings/"

# CSV URL pattern published on the listing page. We pick the most recent one
# by sorting on the YYYYMMDD in the filename.
CSV_URL_RE = re.compile(
    r"https://ircrating\.org/wp-content/uploads/\d{4}/\d{2}/ClubListing_(\d{8})\.csv",
    re.IGNORECASE,
)


async def download_tcc_listing(output_dir: Path | None = None) -> Path | None:
    """Download the latest TCC listing CSV from ircrating.org.

    Returns the path to the saved CSV, or None if the download failed.
    """
    output_dir = output_dir or TCC_LISTINGS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    dest = output_dir / f"tcc_listing_{today}.csv"

    if dest.exists() and dest.stat().st_size > 0:
        print(f"  Already have today's listing: {dest}")
        return dest

    async with get_http_client() as client:
        # 1. Fetch the listing page to discover the latest CSV URL.
        print(f"Fetching {LISTING_PAGE_URL}...")
        try:
            await rate_limiter.wait()
            page_resp = await client.get(LISTING_PAGE_URL)
            page_resp.raise_for_status()
        except Exception as e:
            print(f"  Failed to fetch listing page: {e}")
            return None

        matches = CSV_URL_RE.findall(page_resp.text)
        if not matches:
            print(
                "  No ClubListing CSV URL found on page. "
                "The site layout may have changed again."
            )
            return None

        # Pick the most recent date by lexical sort (YYYYMMDD sorts correctly).
        latest_date = max(matches)
        # Rebuild the full URL (we re-find it to get the YYYY/MM prefix).
        full_urls = [
            m.group(0)
            for m in CSV_URL_RE.finditer(page_resp.text)
            if m.group(1) == latest_date
        ]
        if not full_urls:
            print("  Could not resolve full CSV URL.")
            return None
        csv_url = full_urls[0]
        print(f"  Latest listing: {csv_url}")

        # 2. Download the CSV.
        try:
            await rate_limiter.wait()
            csv_resp = await client.get(csv_url)
            csv_resp.raise_for_status()
        except Exception as e:
            print(f"  Failed to download CSV: {e}")
            return None

        # Basic sanity check: should be a CSV starting with a header line.
        body = csv_resp.content
        if len(body) < 1024 or b"," not in body[:200]:
            print(
                f"  Downloaded body looks wrong "
                f"(size={len(body)}, content-type={csv_resp.headers.get('content-type')!r})"
            )
            return None

        dest.write_bytes(body)
        print(f"  Saved: {dest} ({len(body):,} bytes)")
        return dest
