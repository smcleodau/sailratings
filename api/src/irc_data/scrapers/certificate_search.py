"""Certificate PDF scraper using Playwright to interact with ircrating.org.

The search page at /boat-data-for-valid-irc-certificates/ uses a form POST
with field name 'pdf_search'. Results appear in <div id="pdf-results">
with download links pointing to /pdfdirectory/{certno}_{boatname}_{sailno}.pdf.
"""

import asyncio
import re
from pathlib import Path

from irc_data.config import CERTIFICATES_DIR, IRC_CERTIFICATE_SEARCH_URL
from irc_data.scrapers.base import RateLimiter, get_http_client

rate_limiter = RateLimiter(min_delay=2.0, jitter=1.5)


def _parse_pdf_filename(filename: str) -> dict:
    """Parse cert number, boat name, sail number from PDF filename.

    Format: {certno}_{boatname}_{sailno}.pdf
    e.g. '14163_KOA_AUS52152.pdf' → cert=14163, name=KOA, sail=AUS52152
    """
    name = filename.removesuffix(".pdf")
    parts = name.split("_", 2)
    if len(parts) >= 3:
        return {"cert_number": parts[0], "boat_name": parts[1], "sail_number": parts[2]}
    return {"cert_number": None, "boat_name": None, "sail_number": name}


async def search_certificates(
    search_term: str,
    page,
) -> list[dict]:
    """Perform a single search and return PDF info.

    Returns list of dicts with keys: url, filename, cert_number, boat_name, sail_number.
    """
    await rate_limiter.wait()

    # Fill the search input and submit via button click
    search_input = page.locator("#pdf-search")
    await search_input.fill(search_term)
    await page.locator("form:has(#pdf-search) input[type='submit']").click()
    await page.wait_for_load_state("networkidle", timeout=20000)

    # Parse results from <div id="pdf-results">
    results_div = page.locator("#pdf-results")
    if await results_div.count() == 0:
        return []

    pdf_links = await results_div.locator("a[href*='.pdf']").all()
    results = []
    seen = set()

    for link in pdf_links:
        href = await link.get_attribute("href")
        if not href or href in seen:
            continue
        seen.add(href)
        filename = href.rsplit("/", 1)[-1]
        info = _parse_pdf_filename(filename)
        info["url"] = href
        info["filename"] = filename
        results.append(info)

    return results


async def search_and_download_certificates(
    search_terms: list[str],
    output_dir: Path | None = None,
) -> list[Path]:
    """Search ircrating.org for certificates and download PDFs.

    search_terms: list of search strings (sail number prefixes, boat names, etc.)
        - Use country prefixes like "AUS", "GBR" to get all boats from that country
        - Use sail numbers like "AUS3300" for specific boats
    """
    from playwright.async_api import async_playwright

    output_dir = output_dir or CERTIFICATES_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    all_pdf_info: dict[str, dict] = {}  # url → info, deduped

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        print(f"Loading {IRC_CERTIFICATE_SEARCH_URL}...")
        await page.goto(IRC_CERTIFICATE_SEARCH_URL, wait_until="networkidle")

        for term in search_terms:
            print(f"Searching for '{term}'...")
            results = await search_certificates(term, page)
            print(f"  Found {len(results)} certificates")
            for info in results:
                all_pdf_info[info["url"]] = info

        await browser.close()

    print(f"\nTotal unique certificates: {len(all_pdf_info)}")

    # Download all PDFs
    async with get_http_client() as client:
        for url, info in all_pdf_info.items():
            dest = output_dir / info["filename"]
            if dest.exists():
                print(f"  Already have: {info['filename']}")
                downloaded.append(dest)
                continue

            await rate_limiter.wait()
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                if resp.headers.get("content-type", "").startswith("application/pdf") or (
                    resp.content[:5] == b"%PDF-"
                ):
                    dest.write_bytes(resp.content)
                    downloaded.append(dest)
                    print(f"  Downloaded: {info['filename']}")
                else:
                    print(f"  Not a PDF: {info['filename']}")
            except Exception as e:
                print(f"  Failed: {info['filename']} — {e}")

    return downloaded


async def download_all_target_certificates(
    output_dir: Path | None = None,
) -> list[Path]:
    """Download certificates for all Australian boats + known Sunfast 3300 sail numbers.

    Strategy: search by country prefix to get all boats from each country,
    plus specific sail numbers for boats without standard prefixes.
    """
    search_terms = [
        # Country prefix searches
        "AUS",
        # Known Sunfast 3300 sail numbers that won't match country prefix searches
        "3322",     # TOUCAN
        "33001",    # BORDERLINE
        # Search for GBR Sunfast 3300s specifically
        "GBR5176",  # GAME ON
        "GBR8438",  # ORBIT
        "GBR5964",  # SANITY
        "GBR2095",  # SURF
        "GBR1663",  # CHILLI PEPPER
        "GBR9473",  # ROCKIT
        # Other countries
        "FRA47015",  # ALQUIMIA
        "FIN33001",  # STIMMY
        "D3300",     # KRAKEN
        "JPN4410",   # DECISION 5
        "NZL10082",  # RAGNAR
    ]
    return await search_and_download_certificates(search_terms, output_dir)
