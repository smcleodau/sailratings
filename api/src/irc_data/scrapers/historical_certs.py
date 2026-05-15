"""Strategy 1: Download historical IRC certificates from CSV-derived URLs.

The URL pattern https://ircrating.org/pdfdirectory/{cert_no}_{BOAT_NAME}_{SAIL_NO}.pdf
works for old certificates that are no longer in search results but still on disk.

We have ~4,173 cert-number/name/sail combinations from historical CSVs that we
don't already have PDFs for.
"""

import asyncio
import re
from pathlib import Path
from urllib.parse import quote

from irc_data.config import (
    CERTIFICATES_DIR,
    HISTORICAL_CERTS_DIR,
    IRC_PDF_BASE_URL,
    TCC_LISTINGS_DIR,
)
from irc_data.parsers.tcc_csv import load_all_known_certs
from irc_data.scrapers.base import RateLimiter, get_http_client

# Lighter rate limiting for HEAD requests
head_limiter = RateLimiter(min_delay=1.0, jitter=0.5)
download_limiter = RateLimiter(min_delay=1.5, jitter=1.0)


def _normalise_boat_name(name: str) -> str:
    """Normalise boat name for URL construction.

    - Strip trailing spaces
    - Strip (SH) suffix from 2009 short-handed entries
    - Uppercase
    """
    name = name.strip()
    name = re.sub(r"\s*\(SH\)\s*$", "", name, flags=re.IGNORECASE)
    return name.upper()


def _normalise_sail_number(sail_no: str) -> str:
    """Normalise sail number — strip spaces, uppercase."""
    return sail_no.strip().upper()


def build_cert_url(cert_no: str, boat_name: str, sail_no: str) -> str:
    """Construct the /pdfdirectory/ URL for a certificate.

    Pattern: {cert_no}_{BOAT_NAME}_{SAIL_NO}.pdf — case-sensitive, uppercase.
    """
    name = _normalise_boat_name(boat_name)
    sail = _normalise_sail_number(sail_no)
    filename = f"{cert_no}_{name}_{sail}.pdf"
    return f"{IRC_PDF_BASE_URL}/{filename}"


def build_cert_url_variants(cert_no: str, boat_name: str, sail_no: str) -> list[str]:
    """Build URL variants handling special characters.

    Returns list of URLs to try, in priority order.
    """
    name = _normalise_boat_name(boat_name)
    sail = _normalise_sail_number(sail_no)
    urls = set()

    # Primary: literal URL
    filename = f"{cert_no}_{name}_{sail}.pdf"
    urls.add(f"{IRC_PDF_BASE_URL}/{filename}")

    # Variant: URL-encode special chars in boat name
    if any(c in name for c in "'&.+"):
        encoded_name = quote(name, safe="")
        encoded_filename = f"{cert_no}_{encoded_name}_{sail}.pdf"
        urls.add(f"{IRC_PDF_BASE_URL}/{encoded_filename}")

    # Variant: trailing space in boat name (seen in some existing certs)
    filename_space = f"{cert_no}_{name} _{sail}.pdf"
    urls.add(f"{IRC_PDF_BASE_URL}/{filename_space}")

    return list(urls)


def _existing_cert_numbers(cert_dir: Path) -> set[str]:
    """Get set of cert numbers we already have PDFs for."""
    existing = set()
    if cert_dir.exists():
        for pdf in cert_dir.glob("*.pdf"):
            parts = pdf.stem.split("_", 1)
            if parts[0].isdigit():
                existing.add(parts[0])
    return existing


async def probe_cert(client, url: str) -> bool:
    """HTTP HEAD request to check if a cert PDF exists (fast, no download)."""
    try:
        resp = await client.head(url, follow_redirects=True)
        if resp.status_code == 200:
            ct = resp.headers.get("content-type", "")
            cl = int(resp.headers.get("content-length", "0"))
            return "pdf" in ct.lower() or cl > 1000
        return False
    except Exception:
        return False


async def download_cert(client, url: str, output_dir: Path) -> Path | None:
    """Download a certificate PDF if it's valid."""
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        if resp.content[:5] == b"%PDF-":
            filename = url.rsplit("/", 1)[-1]
            dest = output_dir / filename
            dest.write_bytes(resp.content)
            return dest
    except Exception:
        pass
    return None


async def download_all_historical(
    output_dir: Path | None = None,
    dry_run: bool = False,
    include_offset: bool = True,
) -> dict:
    """Orchestrate: load known certs from CSVs, probe all, download hits.

    Args:
        output_dir: Where to save PDFs (default: HISTORICAL_CERTS_DIR)
        dry_run: If True, just show what would be tried
        include_offset: If True, also try cert_no ± 10 variants

    Returns:
        dict with stats: probed, found, downloaded, errors
    """
    output_dir = output_dir or HISTORICAL_CERTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load all known cert/name/sail combos from CSVs
    all_certs = load_all_known_certs()
    print(f"Loaded {len(all_certs)} cert/name/sail combinations from CSVs")

    # Filter out certs we already have
    existing = _existing_cert_numbers(CERTIFICATES_DIR)
    existing |= _existing_cert_numbers(output_dir)
    print(f"Already have {len(existing)} certificate PDFs")

    to_try = []
    for cert in all_certs:
        cert_no = cert["cert_number"]
        if cert_no in existing:
            continue
        to_try.append(cert)

        # Also try ±10 offset
        if include_offset:
            try:
                cert_int = int(cert_no)
                for offset in [-10, 10]:
                    offset_no = str(cert_int + offset)
                    if offset_no not in existing and offset_no != cert_no:
                        to_try.append({
                            **cert,
                            "cert_number": offset_no,
                            "source": f"offset({offset}) from {cert_no}",
                        })
            except ValueError:
                pass

    # Deduplicate by cert_number
    seen = set()
    unique_to_try = []
    for cert in to_try:
        if cert["cert_number"] not in seen:
            seen.add(cert["cert_number"])
            unique_to_try.append(cert)
    to_try = unique_to_try

    print(f"Will probe {len(to_try)} URLs")

    if dry_run:
        for cert in to_try[:20]:
            url = build_cert_url(cert["cert_number"], cert["boat_name"], cert["sail_number"])
            print(f"  {url}")
        if len(to_try) > 20:
            print(f"  ... and {len(to_try) - 20} more")
        return {"total": len(to_try), "probed": 0, "found": 0, "downloaded": 0}

    stats = {"total": len(to_try), "probed": 0, "found": 0, "downloaded": 0, "errors": 0}

    # Phase 1: Probe all URLs with HEAD requests
    found_urls = []
    async with get_http_client() as client:
        for i, cert in enumerate(to_try):
            urls = build_cert_url_variants(
                cert["cert_number"], cert["boat_name"], cert["sail_number"]
            )
            hit = False
            for url in urls:
                await head_limiter.wait()
                stats["probed"] += 1
                if await probe_cert(client, url):
                    found_urls.append((url, cert))
                    stats["found"] += 1
                    hit = True
                    print(f"  [{stats['found']}] FOUND: {url}")
                    break
            if not hit and i % 500 == 0:
                print(
                    f"  Progress: {i}/{len(to_try)} probed, "
                    f"{stats['found']} found"
                )

    print(f"\nProbe complete: {stats['found']} hits from {stats['probed']} probes")

    # Phase 2: Download all hits
    if found_urls:
        print(f"\nDownloading {len(found_urls)} certificates...")
        async with get_http_client() as client:
            for url, cert in found_urls:
                await download_limiter.wait()
                path = await download_cert(client, url, output_dir)
                if path:
                    stats["downloaded"] += 1
                    print(f"  Downloaded: {path.name}")
                else:
                    stats["errors"] += 1

    print(
        f"\nDone: {stats['downloaded']} downloaded, "
        f"{stats['errors']} errors"
    )
    return stats
