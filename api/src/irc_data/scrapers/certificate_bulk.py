"""Bulk certificate downloader for ircrating.org boat-data PDFs.

The search at /boat-data-for-valid-irc-certificates/ accepts a plain POST
with form field ``pdf_search`` (two characters minimum — single-character
searches return nothing) and lists matching PDFs inside ``#pdf-results``.

Since mid-2026 each result links to a *signed, short-lived* URL::

    https://ircrating.org/?irc_dl=14163_KOA_AUS52152.pdf&tk=<ts>.<hmac>

instead of the old ``/pdfdirectory/<file>.pdf``.  The old downloader
enumerated all 936 search terms first and downloaded afterwards, so every
token had expired (HTTP 403) by the time it was used, and it derived the
filename from the URL path (``?irc_dl=...&tk=...``).  Weekly runs therefore
wrote nothing after late July 2026.  This module downloads straight after
each search, re-searches a cert number once to refresh an expired token, and
names files from the ``irc_dl`` parameter.

Cert numbers are stable across annual reissues, so a 2026 certificate has the
same filename as the 2025 one.  "File exists, skip" therefore never picked up
reissues; :func:`refresh_cert_numbers` decides which known cert numbers to
re-download, and :func:`save_pdf` archives a replaced copy under
``superseded/`` instead of overwriting it.

No browser automation; policy User-Agent from ``get_http_client``; at least
2 s between requests with jitter.
"""

from __future__ import annotations

import asyncio
import hashlib
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from irc_data.config import CERTIFICATES_DIR, IRC_CERTIFICATE_SEARCH_URL
from irc_data.scrapers.base import RateLimiter, get_http_client

rate_limiter = RateLimiter(min_delay=2.0, jitter=1.0)

SUPERSEDED_DIRNAME = "superseded"


def _filename_from_href(href: str) -> str | None:
    """PDF filename for a result link (signed ``irc_dl`` or legacy path)."""
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    if "irc_dl" in qs and qs["irc_dl"]:
        name = qs["irc_dl"][0]
    else:
        name = parsed.path.rsplit("/", 1)[-1]
    name = unquote(name).strip()
    if not name.lower().endswith(".pdf") or "/" in name or name.startswith("."):
        return None
    return name


def _parse_results_html(html: str) -> list[dict]:
    """Parse the search results HTML to extract PDF info."""
    soup = BeautifulSoup(html, "html.parser")
    results_div = soup.find(id="pdf-results")
    if not results_div:
        return []

    results = []
    seen = set()
    for link in results_div.find_all("a", href=True):
        href = link["href"]
        filename = _filename_from_href(href)
        if not filename or filename in seen:
            continue
        seen.add(filename)
        parts = filename.removesuffix(".pdf").removesuffix(".PDF").split("_", 2)
        results.append(
            {
                "url": href,
                "filename": filename,
                "cert_number": parts[0] if len(parts) >= 1 else None,
                "boat_name": parts[1] if len(parts) >= 2 else None,
                "sail_number": parts[2] if len(parts) >= 3 else None,
            }
        )
    return results


def save_pdf(content: bytes, filename: str, output_dir: Path) -> str:
    """Write a downloaded PDF; returns ``new``, ``updated`` or ``unchanged``.

    A changed file (annual reissue / re-measurement) is moved to
    ``superseded/<stem>__<sha8>.pdf`` first so older certificates are kept.
    """
    dest = output_dir / filename
    if dest.exists():
        old = dest.read_bytes()
        if old == content:
            return "unchanged"
        archive = output_dir / SUPERSEDED_DIRNAME
        archive.mkdir(parents=True, exist_ok=True)
        sha8 = hashlib.sha256(old).hexdigest()[:8]
        target = archive / f"{dest.stem}__{sha8}.pdf"
        if not target.exists():
            dest.replace(target)
        dest.write_bytes(content)
        return "updated"
    dest.write_bytes(content)
    return "new"


@dataclass
class HarvestStats:
    searches: int = 0
    search_errors: int = 0
    found: int = 0
    downloaded: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_existing: int = 0
    failed: int = 0
    token_refreshes: int = 0
    seen_filenames: set = field(default_factory=set)
    seen_cert_numbers: set = field(default_factory=set)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if not isinstance(v, set)}
        d["distinct_certs_listed"] = len(self.seen_cert_numbers)
        return d


class CertificateHarvester:
    """Search ircrating.org and download wanted PDFs immediately."""

    def __init__(
        self,
        client,
        *,
        output_dir: Path | None = None,
        refresh: Iterable[str] = (),
        refresh_all: bool = False,
        limiter: RateLimiter | None = None,
        log: Callable[[str], None] = print,
    ):
        self.client = client
        self.output_dir = Path(output_dir or CERTIFICATES_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.refresh = {str(c) for c in refresh}
        self.refresh_all = refresh_all
        self.limiter = limiter or rate_limiter
        self.log = log
        self.stats = HarvestStats()
        self._done: set[str] = set()  # filenames handled this run

    def wanted(self, info: dict) -> bool:
        if info["filename"] in self._done:
            return False
        if self.refresh_all or info.get("cert_number") in self.refresh:
            return True
        return not (self.output_dir / info["filename"]).exists()

    async def search(self, term: str) -> list[dict]:
        await self.limiter.wait()
        self.stats.searches += 1
        resp = await self.client.post(
            IRC_CERTIFICATE_SEARCH_URL,
            data={"pdf_search": term},
            headers={"Referer": IRC_CERTIFICATE_SEARCH_URL},
        )
        resp.raise_for_status()
        return _parse_results_html(resp.text)

    async def _get(self, url: str):
        await self.limiter.wait()
        return await self.client.get(url, headers={"Referer": IRC_CERTIFICATE_SEARCH_URL})

    async def download(self, info: dict) -> str | None:
        resp = await self._get(info["url"])
        if resp.status_code == 403 and info.get("cert_number"):
            # Signed token expired: search the cert number for a fresh link.
            self.stats.token_refreshes += 1
            try:
                fresh = [r for r in await self.search(info["cert_number"]) if r["filename"] == info["filename"]]
            except Exception:
                fresh = []
            if fresh:
                resp = await self._get(fresh[0]["url"])
        if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
            self.stats.failed += 1
            self.log(f"  Failed: {info['filename']} (HTTP {resp.status_code})")
            return None
        status = save_pdf(resp.content, info["filename"], self.output_dir)
        self.stats.downloaded += 1
        setattr(self.stats, status, getattr(self.stats, status) + 1)
        if status != "unchanged":
            self.log(f"  {status.title()}: {info['filename']}")
        return status

    async def harvest_term(self, term: str) -> list[dict]:
        try:
            results = await self.search(term)
        except Exception as exc:
            self.stats.search_errors += 1
            self.log(f"  '{term}': search error — {exc}")
            return []
        for info in results:
            if info["filename"] not in self.stats.seen_filenames:
                self.stats.seen_filenames.add(info["filename"])
                self.stats.found += 1
            if info.get("cert_number"):
                self.stats.seen_cert_numbers.add(info["cert_number"])
            if not self.wanted(info):
                if info["filename"] not in self._done:
                    self.stats.skipped_existing += 1
                self._done.add(info["filename"])
                continue
            self._done.add(info["filename"])
            try:
                await self.download(info)
            except Exception as exc:
                self.stats.failed += 1
                self.log(f"  Failed: {info['filename']} — {exc}")
        return results


def exhaustive_terms() -> list[str]:
    """All two-character search terms (AA..ZZ, A0..Z9)."""
    combos = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    combos += [a + b for a in string.ascii_uppercase for b in string.digits]
    return combos


async def harvest(
    terms: Iterable[str],
    *,
    output_dir: Path | None = None,
    refresh: Iterable[str] = (),
    refresh_all: bool = False,
    log: Callable[[str], None] = print,
    progress_every: int = 50,
) -> HarvestStats:
    terms = list(terms)
    async with get_http_client() as client:
        h = CertificateHarvester(
            client, output_dir=output_dir, refresh=refresh, refresh_all=refresh_all, log=log
        )
        for i, term in enumerate(terms, 1):
            await h.harvest_term(term)
            if i % progress_every == 0:
                s = h.stats
                log(
                    f"  Progress: {i}/{len(terms)} terms, {s.found} listed, "
                    f"{s.new} new, {s.updated} updated, {s.failed} failed"
                )
    return h.stats


async def exhaustive_harvest(**kwargs) -> HarvestStats:
    """Two-character exhaustive search with immediate download."""
    return await harvest(exhaustive_terms(), **kwargs)


# ---------------------------------------------------------------------------
# What to (re)fetch — driven by the current TCC listing
# ---------------------------------------------------------------------------

_REFRESH_SQL = """
WITH latest AS (SELECT max(snapshot_date) AS d FROM tcc_snapshots),
cur AS (
    SELECT DISTINCT b.cert_number, t.tcc
    FROM tcc_snapshots t JOIN boats b ON b.id = t.boat_id, latest
    WHERE t.snapshot_date = latest.d AND b.cert_number IS NOT NULL AND b.cert_number <> ''
)
SELECT cur.cert_number
FROM cur
LEFT JOIN irc_certificates c ON c.cert_number = cur.cert_number
WHERE c.id IS NULL                                   -- never parsed
   OR c.issue_date IS NULL                           -- undated parse
   OR (c.issue_date + interval '1 year') < CURRENT_DATE   -- our copy has expired
   OR EXISTS (                                       -- TCC changed since we fetched
        SELECT 1 FROM tcc_snapshots t2 JOIN boats b2 ON b2.id = t2.boat_id
        WHERE b2.cert_number = cur.cert_number
          AND t2.snapshot_date > c.scraped_at::date
          AND t2.tcc IS DISTINCT FROM (
              SELECT t3.tcc FROM tcc_snapshots t3 JOIN boats b3 ON b3.id = t3.boat_id
              WHERE b3.cert_number = cur.cert_number AND t3.snapshot_date <= c.scraped_at::date
              ORDER BY t3.snapshot_date DESC LIMIT 1
          )
   )
"""


def refresh_cert_numbers(engine) -> set[str]:
    """Cert numbers on the current TCC listing whose PDF should be fetched again."""
    from sqlalchemy import text

    with engine.connect() as conn:
        return {str(r[0]).strip() for r in conn.execute(text(_REFRESH_SQL))}


def missing_current_cert_numbers(engine, listed: Iterable[str] = ()) -> list[str]:
    """Current-TCC cert numbers with no certificate row and not yet listed by
    this run's searches — candidates for a targeted cert-number search."""
    from sqlalchemy import text

    listed = set(listed)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT b.cert_number
                FROM tcc_snapshots t JOIN boats b ON b.id = t.boat_id
                WHERE t.snapshot_date = (SELECT max(snapshot_date) FROM tcc_snapshots)
                  AND b.cert_number ~ '^[0-9]+$'
                  AND NOT EXISTS (SELECT 1 FROM irc_certificates c WHERE c.cert_number = b.cert_number)
                """
            )
        ).all()
    return sorted({r[0] for r in rows} - listed, key=int)


async def discover_and_download(
    engine=None,
    *,
    output_dir: Path | None = None,
    exhaustive: bool = True,
    targeted: bool = True,
    refresh_all: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    """Weekly discovery: exhaustive search + targeted cert-number search.

    Downloads PDFs not on disk, plus re-downloads for current-TCC boats whose
    stored certificate is missing, undated, expired or predates a TCC change.
    """
    refresh: set[str] = set()
    if engine is not None:
        refresh = refresh_cert_numbers(engine)
        log(f"Refresh candidates from TCC listing: {len(refresh)}")
    out: dict = {"refresh_candidates": len(refresh)}
    listed: set[str] = set()
    async with get_http_client() as client:
        h = CertificateHarvester(
            client, output_dir=output_dir, refresh=refresh, refresh_all=refresh_all, log=log
        )
        if exhaustive:
            terms = exhaustive_terms()
            for i, term in enumerate(terms, 1):
                await h.harvest_term(term)
                if i % 50 == 0:
                    s = h.stats
                    log(f"  Progress: {i}/{len(terms)} terms, {s.found} listed, {s.new} new, "
                        f"{s.updated} updated, {s.failed} failed")
            listed = set(h.stats.seen_cert_numbers)
            out["exhaustive"] = h.stats.as_dict()
        if targeted and engine is not None:
            # Anything the exhaustive pass listed is already handled; search
            # the remaining current-TCC cert numbers (with no cert row) and
            # the refresh set the exhaustive pass did not cover.
            targets = sorted(
                (set(missing_current_cert_numbers(engine, listed)) | (refresh - listed)),
                key=lambda c: int(c) if c.isdigit() else 0,
            )
            log(f"Targeted cert-number searches: {len(targets)}")
            hits = 0
            for i, cert_no in enumerate(targets, 1):
                results = await h.harvest_term(cert_no)
                if any(r.get("cert_number") == cert_no for r in results):
                    hits += 1
                if i % 100 == 0:
                    log(f"  Targeted: {i}/{len(targets)}, {hits} listed")
            out["targeted"] = {"searched": len(targets), "listed": hits}
    out["stats"] = h.stats.as_dict()
    return out


# ---------------------------------------------------------------------------
# Back-compat entry points
# ---------------------------------------------------------------------------


async def exhaustive_enumerate() -> list[dict]:
    """Deprecated: enumerating first lets signed links expire before download.

    Kept for callers that only need the listing; returns info dicts without
    downloading anything.
    """
    found: dict[str, dict] = {}
    async with get_http_client() as client:
        h = CertificateHarvester(client, log=lambda *_: None)
        for term in exhaustive_terms():
            try:
                for info in await h.search(term):
                    found.setdefault(info["filename"], info)
            except Exception:
                continue
    return list(found.values())


async def enumerate_all_certificates() -> list[dict]:
    """Listing of every current certificate.

    Used to search single characters, which the site now ignores (two
    characters minimum), so this delegates to the two-character sweep.
    """
    return await exhaustive_enumerate()


async def download_certificates(
    cert_list: list[dict],
    output_dir: Path | None = None,
    skip_existing: bool = True,
) -> list[Path]:
    """Download PDFs for already-listed certs, refreshing expired links."""
    output_dir = Path(output_dir or CERTIFICATES_DIR)
    downloaded: list[Path] = []
    async with get_http_client() as client:
        h = CertificateHarvester(client, output_dir=output_dir, refresh_all=not skip_existing)
        for info in cert_list:
            if not h.wanted(info):
                downloaded.append(output_dir / info["filename"])
                continue
            h._done.add(info["filename"])
            if await h.download(info):
                downloaded.append(output_dir / info["filename"])
    return downloaded


async def download_all_certificates(output_dir: Path | None = None) -> list[Path]:
    """Exhaustively search and download every listed certificate."""
    stats = await exhaustive_harvest(output_dir=output_dir)
    out = Path(output_dir or CERTIFICATES_DIR)
    return [out / f for f in sorted(stats.seen_filenames)]
