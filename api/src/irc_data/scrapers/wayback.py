"""Wayback Machine historical IRC certificate PDF finder & downloader."""

import asyncio
import re
from pathlib import Path

from irc_data.config import CERTIFICATES_DIR, WAYBACK_BASE_URL, WAYBACK_CDX_URL
from irc_data.scrapers.base import RateLimiter, fetch_with_retry, get_http_client

rate_limiter = RateLimiter(min_delay=2.0, jitter=1.0)

# Patterns we ask Wayback's CDX index about when looking for historical
# TCC (IRC rating) listings. The wildcards match year-segmented directories
# and the rotating "tcc-listing-YYYY-MM-DD.csv" / "ClubListing-YYYY-MM-DD.csv"
# filenames that ircrating.org has used over the years.
IRC_TCC_PATTERNS = [
    "https://ircrating.org/wp-content/uploads/*/ClubListing*.csv",
    "https://ircrating.org/wp-content/uploads/*/tcc-listing*.csv",
    "https://ircrating.org/irc-racing/online-tcc-listings/",
]


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


async def harvest_tcc_archives(
    start_year: int,
    end_year: int,
    out_dir: Path,
    patterns: list[str] | None = None,
    max_per_pattern: int | None = None,
) -> list[dict]:
    """Query Wayback CDX for archived IRC TCC listings between two years.

    For each URL pattern in ``IRC_TCC_PATTERNS`` (or the override) ask the
    CDX index for distinct snapshots, then download each unique snapshot
    via the ``id_/`` raw passthrough and persist it to
    ``out_dir/tcc_{year}_{timestamp}.csv``.

    Args:
        start_year: Inclusive earliest year (e.g. 2010).
        end_year:   Inclusive latest year (e.g. 2025).
        out_dir:    Destination directory; created if missing.
        patterns:   Optional override of the patterns to query. Default is
                    :data:`IRC_TCC_PATTERNS`.
        max_per_pattern: Optional cap on snapshots per pattern (for smoke
                    testing). ``None`` means unlimited.

    Returns:
        List of ``{year, timestamp, original_url, path}`` dicts, one entry
        per file actually written to disk this call (already-cached files
        are skipped).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    patterns = patterns or IRC_TCC_PATTERNS
    results: list[dict] = []

    async with get_http_client() as client:
        for pattern in patterns:
            params = {
                "url": pattern,
                "from": f"{start_year}0101",
                "to": f"{end_year}1231",
                "output": "json",
                "fl": "timestamp,original",
                # Collapse to monthly granularity so we don't pull dozens of
                # identical snapshots from the same week.
                "collapse": "timestamp:6",
            }
            await rate_limiter.wait()
            try:
                r = await client.get(WAYBACK_CDX_URL, params=params)
            except Exception as exc:
                print(f"  CDX query failed for {pattern}: {exc}")
                continue
            if r.status_code != 200:
                print(f"  CDX query {pattern} -> HTTP {r.status_code}")
                continue
            try:
                payload = r.json()
            except ValueError:
                print(f"  CDX returned non-JSON for {pattern}")
                continue
            # First row is the header (matches our `fl` columns).
            rows = payload[1:] if payload else []
            if max_per_pattern is not None:
                rows = rows[:max_per_pattern]
            for row in rows:
                # row is [timestamp, original]; tolerate extra fields.
                if len(row) < 2:
                    continue
                ts, original = row[0], row[1]
                if not ts or len(ts) < 4:
                    continue
                try:
                    year = int(ts[:4])
                except ValueError:
                    continue
                snap_url = f"{WAYBACK_BASE_URL}/{ts}id_/{original}"
                target = out_dir / f"tcc_{year}_{ts}.csv"
                if target.exists():
                    continue
                await rate_limiter.wait()
                try:
                    snap = await client.get(snap_url)
                except Exception as exc:
                    print(f"  Snapshot fetch failed {snap_url}: {exc}")
                    continue
                if snap.status_code != 200 or not snap.content:
                    print(
                        f"  Snapshot {snap_url} -> HTTP {snap.status_code}, "
                        f"{len(snap.content)} bytes; skipping"
                    )
                    continue
                target.write_bytes(snap.content)
                results.append(
                    {
                        "year": year,
                        "timestamp": ts,
                        "original_url": original,
                        "path": target,
                    }
                )
    return results


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
