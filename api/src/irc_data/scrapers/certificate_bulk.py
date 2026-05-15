"""Bulk certificate downloader — enumerate ALL certificates via POST API.

The ircrating.org search at /boat-data-for-valid-irc-certificates/ accepts
a simple POST with form field 'pdf_search'. Searching single characters
(A-Z, 0-9) enumerates all ~2,874 current certificates.

No Playwright required — plain httpx POST works.
"""

import asyncio
import re
import string
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

from irc_data.config import CERTIFICATES_DIR, IRC_CERTIFICATE_SEARCH_URL
from irc_data.scrapers.base import RateLimiter, get_http_client

rate_limiter = RateLimiter(min_delay=1.5, jitter=1.0)


def _parse_results_html(html: str) -> list[dict]:
    """Parse the search results HTML to extract PDF info."""
    soup = BeautifulSoup(html, "html.parser")
    results_div = soup.find(id="pdf-results")
    if not results_div:
        return []

    results = []
    for link in results_div.find_all("a", href=True):
        href = link["href"]
        if ".pdf" not in href.lower():
            continue
        filename = unquote(href.rsplit("/", 1)[-1])
        parts = filename.removesuffix(".pdf").split("_", 2)
        info = {
            "url": href,
            "filename": filename,
            "cert_number": parts[0] if len(parts) >= 1 else None,
            "boat_name": parts[1] if len(parts) >= 2 else None,
            "sail_number": parts[2] if len(parts) >= 3 else None,
        }
        results.append(info)
    return results


async def enumerate_all_certificates() -> list[dict]:
    """Enumerate all certificates by searching A-Z, 0-9.

    Returns deduplicated list of certificate info dicts.
    """
    all_certs: dict[str, dict] = {}  # url → info

    # Search characters that cover all boats
    search_chars = list(string.ascii_uppercase) + list(string.digits)

    async with get_http_client() as client:
        for char in search_chars:
            await rate_limiter.wait()
            try:
                resp = await client.post(
                    IRC_CERTIFICATE_SEARCH_URL,
                    data={"pdf_search": char},
                )
                resp.raise_for_status()
                results = _parse_results_html(resp.text)
                new = 0
                for info in results:
                    if info["url"] not in all_certs:
                        all_certs[info["url"]] = info
                        new += 1
                print(f"  '{char}': {len(results)} results, {new} new (total: {len(all_certs)})")
            except Exception as e:
                print(f"  '{char}': ERROR — {e}")

    return list(all_certs.values())


async def download_certificates(
    cert_list: list[dict],
    output_dir: Path | None = None,
    skip_existing: bool = True,
) -> list[Path]:
    """Download certificate PDFs from a list of cert info dicts."""
    output_dir = output_dir or CERTIFICATES_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []

    async with get_http_client() as client:
        for info in cert_list:
            filename = info["filename"]
            dest = output_dir / filename
            if skip_existing and dest.exists():
                downloaded.append(dest)
                continue

            await rate_limiter.wait()
            try:
                resp = await client.get(info["url"])
                resp.raise_for_status()
                if resp.content[:5] == b"%PDF-":
                    dest.write_bytes(resp.content)
                    downloaded.append(dest)
                    print(f"  Downloaded: {filename}")
                else:
                    print(f"  Not a PDF: {filename}")
            except Exception as e:
                print(f"  Failed: {filename} — {e}")

    return downloaded


async def exhaustive_enumerate() -> list[dict]:
    """Strategy 2: Search all 2-letter combinations (AA..ZZ, A0..Z9, etc.).

    The ircrating.org POST search may return certs not in the TCC listing
    (expired but still in search index). 676 queries at 1.5s = ~17 minutes.

    Returns deduplicated list of certificate info dicts.
    """
    all_certs: dict[str, dict] = {}  # url → info
    combos = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    # Also add letter+digit combos
    combos += [a + b for a in string.ascii_uppercase for b in string.digits]

    async with get_http_client() as client:
        for i, combo in enumerate(combos):
            await rate_limiter.wait()
            try:
                resp = await client.post(
                    IRC_CERTIFICATE_SEARCH_URL,
                    data={"pdf_search": combo},
                )
                resp.raise_for_status()
                results = _parse_results_html(resp.text)
                new = 0
                for info in results:
                    if info["url"] not in all_certs:
                        all_certs[info["url"]] = info
                        new += 1
                if new > 0:
                    print(f"  '{combo}': {len(results)} results, {new} new (total: {len(all_certs)})")
                if (i + 1) % 100 == 0:
                    print(f"  Progress: {i + 1}/{len(combos)} combos, {len(all_certs)} total certs")
            except Exception as e:
                print(f"  '{combo}': ERROR — {e}")

    return list(all_certs.values())


async def download_all_certificates(
    output_dir: Path | None = None,
) -> list[Path]:
    """Enumerate and download ALL current IRC certificates."""
    print("Enumerating all certificates via search API...")
    certs = await enumerate_all_certificates()
    print(f"\nFound {len(certs)} unique certificates")

    print(f"\nDownloading...")
    downloaded = await download_certificates(certs, output_dir)
    print(f"\nTotal: {len(downloaded)} certificates")
    return downloaded
