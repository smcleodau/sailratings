"""IRC Certificate PDF raw-capture scraper (DP-00-05).

Policy: interim-v0 (DP-00-01)

Fetches IRC certificate PDFs from the public search widget at:
  https://ircrating.org/boat-data-for-valid-irc-certificates/

The widget accepts a plain HTTP POST with field ``pdf_search`` and returns an
HTML page.  Results in ``#pdf-results`` contain signed download URLs.  No
JavaScript or browser automation is required.

Each PDF is stored as a ``RawArtifactV1`` in the content-addressed
``RawObjectStore`` with full provenance metadata.  Idempotency is enforced by
the store's SHA-256 content addressing — same bytes → no new object, but a new
``retrieval_event`` is logged.  Re-checks run monthly to catch reissued
certificates (same cert number, changed content → different hash → new object).

Politeness rules (per ``CollectionRules``):
  - 1 request / 2s minimum with 1s jitter
  - Nightly window 01:00–06:00 UK (see ``COLLECTION_WINDOW_UK_*``)
  - Max 5,000 fetches per run
  - robots.txt: ``Disallow:`` is empty → all paths allowed
  - Kill switch: checks ``data_sources.enabled`` flag before each cycle
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from html import unescape
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

import httpx

from irc_data.config import IRC_CERTIFICATE_SEARCH_URL
from irc_data.sources.policy import (
    CURRENT_POLICY_VERSION,
    POLICY_USER_AGENT,
    is_within_collection_window,
)
from irc_data.sources.provenance import (
    RawObjectStore,
    persist_raw_artifact,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADAPTER_VERSION = "dp-00-05/1.0"
SOURCE_SLUG = "irc-certs"
SEARCH_URL = IRC_CERTIFICATE_SEARCH_URL  # "https://ircrating.org/boat-data-for-valid-irc-certificates/"
CONTENT_TYPE_PDF = "application/pdf"

# Nightly collection window — UK local hours (01:00–06:00)
COLLECTION_WINDOW_UK_START = 1
COLLECTION_WINDOW_UK_END = 6

# Politeness
MIN_DELAY_SECONDS = 2.0
JITTER_SECONDS = 1.0
MAX_FETCHES_PER_RUN = 5_000

# PDF validation
PDF_MAGIC = b"%PDF"

# Pattern matching the signed download URL in the HTML response:
#   href="https://ircrating.org/?irc_dl=...&amp;#038;tk=..."
# or:
#   href="https://ircrating.org/?irc_dl=...&#038;tk=..."
_DL_HREF_RE = re.compile(
    r'href="(https://ircrating\.org/\?irc_dl=[^"]+)"',
    re.IGNORECASE,
)

# Pattern for the filename label in the results paragraph:
#   <p>14163_KOA_AUS52152.pdf <a href="...">Download</a></p>
_RESULT_PARA_RE = re.compile(
    r'<p>\s*([^\s<]+\.pdf)\s+<a\s+href=',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class CertRecord:
    """Lightweight cert descriptor from the search result."""

    __slots__ = (
        "cert_no",
        "boat_name",
        "sail_no",
        "filename",
        "download_url",
        "listing_ref",
    )

    def __init__(
        self,
        cert_no: str,
        boat_name: str,
        sail_no: str,
        filename: str,
        download_url: str,
        listing_ref: str = "",
    ):
        self.cert_no = cert_no
        self.boat_name = boat_name
        self.sail_no = sail_no
        self.filename = filename
        self.download_url = download_url
        self.listing_ref = listing_ref

    def __repr__(self) -> str:
        return f"CertRecord(cert_no={self.cert_no!r}, sail_no={self.sail_no!r})"


class RunLedger:
    """Tracks statistics for a single scraper run."""

    def __init__(self, source_slug: str, policy_version: str):
        self.source_slug = source_slug
        self.policy_version = policy_version
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at: str | None = None
        self.status: str = "running"
        self.certs_found: int = 0
        self.certs_new: int = 0
        self.certs_unchanged: int = 0
        self.errors: list[dict] = []
        self.fetch_count: int = 0

    def finish(self, status: str = "ok") -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self.status = status

    def add_error(self, cert_no: str, message: str) -> None:
        self.errors.append({"cert_no": cert_no, "message": message})

    def to_dict(self) -> dict:
        return {
            "source_slug": self.source_slug,
            "policy_version": self.policy_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "certs_found": self.certs_found,
            "certs_new": self.certs_new,
            "certs_unchanged": self.certs_unchanged,
            "fetch_count": self.fetch_count,
            "error_count": len(self.errors),
            "errors": self.errors[:20],  # cap for log size
        }


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_filename(filename: str) -> tuple[str, str, str]:
    """Extract (cert_no, boat_name, sail_no) from a PDF filename.

    Format: ``{cert_no}_{boat_name}_{sail_no}.pdf``

    Examples:
        ``14163_KOA_AUS52152.pdf``  → ("14163", "KOA", "AUS52152")
        ``48182_KOA - SEC_AUS52152.pdf`` → ("48182", "KOA - SEC", "AUS52152")
    """
    name = filename
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    parts = name.split("_", 2)
    if len(parts) >= 3:
        return parts[0].strip(), parts[1].strip(), parts[2].strip()
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), ""
    return name.strip(), "", ""


def parse_search_results(html: str) -> list[CertRecord]:
    """Parse ``#pdf-results`` div from the search-page HTML.

    Returns a list of :class:`CertRecord` for each PDF download link found.
    Returns an empty list when the server returns ``No files found.``
    """
    # Locate the #pdf-results section
    start = html.find('id="pdf-results"')
    if start == -1:
        start = html.find("id='pdf-results'")
    if start == -1:
        return []

    # End at the next </div>
    end = html.find("</div>", start)
    section = html[start:end] if end != -1 else html[start:]

    if "No files found" in section:
        return []

    records = []
    # Find all download hrefs
    for href_match in _DL_HREF_RE.finditer(section):
        raw_href = href_match.group(1)
        # Unescape HTML entities (&amp; → &, &#038; → &)
        download_url = unescape(raw_href)

        # Extract filename from the URL's irc_dl parameter
        filename = ""
        if "irc_dl=" in download_url:
            fragment = download_url.split("irc_dl=", 1)[1]
            filename = fragment.split("&")[0]
            # URL-decode the filename
            from urllib.parse import unquote
            filename = unquote(filename)

        if not filename.lower().endswith(".pdf"):
            continue

        cert_no, boat_name, sail_no = _parse_filename(filename)

        records.append(
            CertRecord(
                cert_no=cert_no,
                boat_name=boat_name,
                sail_no=sail_no,
                filename=filename,
                download_url=download_url,
                listing_ref=SEARCH_URL,
            )
        )

    return records


# ---------------------------------------------------------------------------
# Cert number enumeration
# ---------------------------------------------------------------------------


def enumerate_cert_nos_from_db(db_engine) -> list[str]:
    """Return all distinct cert numbers from the platform DB.

    Sources:
    - ``boats.cert_number`` (primary source)
    - ``irc_certificates.cert_number`` (parsed-cert store)

    Returns a deduplicated, sorted list of cert number strings.
    """
    from sqlalchemy import text

    cert_nos: set[str] = set()

    with db_engine.connect() as conn:
        # From boats table
        rows = conn.execute(
            text("SELECT DISTINCT cert_number FROM boats WHERE cert_number IS NOT NULL AND cert_number != ''")
        )
        for row in rows:
            cert_nos.add(str(row[0]).strip())

        # From irc_certificates table
        rows = conn.execute(
            text("SELECT DISTINCT cert_number FROM irc_certificates WHERE cert_number IS NOT NULL AND cert_number != ''")
        )
        for row in rows:
            cert_nos.add(str(row[0]).strip())

    return sorted(cert_nos, key=lambda x: int(x) if x.isdigit() else 0)


def enumerate_cert_nos_from_tcc_dir(tcc_dir: Path) -> list[str]:
    """Return cert numbers from TCC listing CSVs (fallback when DB unavailable).

    Uses the existing ``cert_index`` module.
    """
    from irc_data.scrapers.cert_index import build_index_from_tcc_dir

    records = build_index_from_tcc_dir(tcc_dir)
    return sorted(
        {r["cert_number"] for r in records if r.get("cert_number")},
        key=lambda x: int(x) if x.isdigit() else 0,
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _make_client() -> httpx.Client:
    """Create a synchronous httpx client with policy-compliant headers."""
    return httpx.Client(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
        headers={
            "User-Agent": POLICY_USER_AGENT,
            "X-SailRatings-Source": SOURCE_SLUG,
        },
    )


def _polite_sleep(last_request_time: float, min_delay: float = MIN_DELAY_SECONDS) -> float:
    """Sleep if needed to respect the rate limit. Returns the new last_request_time."""
    import random as _random

    now = time.monotonic()
    elapsed = now - last_request_time
    delay = min_delay + _random.uniform(0, JITTER_SECONDS)
    if elapsed < delay:
        time.sleep(delay - elapsed)
    return time.monotonic()


def search_cert(client: httpx.Client, cert_no: str) -> tuple[list[CertRecord], str]:
    """POST a search for ``cert_no`` and parse the results.

    Returns (records, raw_html). Raises ``httpx.HTTPStatusError`` on failure.
    """
    response = client.post(
        SEARCH_URL,
        data={"pdf_search": cert_no},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": SEARCH_URL,
        },
    )
    response.raise_for_status()
    html = response.text
    records = parse_search_results(html)
    return records, html


def download_pdf(client: httpx.Client, download_url: str) -> bytes:
    """Download a PDF from the signed URL.

    Returns raw bytes. Raises on HTTP error or invalid PDF.
    """
    response = client.get(
        download_url,
        headers={"Referer": SEARCH_URL},
    )
    response.raise_for_status()
    content = response.content
    if not content.startswith(PDF_MAGIC):
        raise ValueError(
            f"Response from {download_url} is not a valid PDF "
            f"(first 4 bytes: {content[:4]!r})"
        )
    return content


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------


def scrape_irc_pdfs(
    cert_nos: list[str],
    store: RawObjectStore,
    *,
    max_fetches: int = MAX_FETCHES_PER_RUN,
    enforce_window: bool = True,
    check_kill_switch: bool = True,
    db_engine=None,
) -> RunLedger:
    """Fetch and archive IRC certificate PDFs for the given cert numbers.

    Args:
        cert_nos: List of IRC certificate numbers to fetch.
        store: Content-addressed raw object store (writes PDFs here).
        max_fetches: Maximum total fetches (POST + GET) per run. Default 5,000.
        enforce_window: If True, abort if outside the nightly collection window.
        check_kill_switch: If True, check the DB kill switch before each cert.
        db_engine: SQLAlchemy engine for kill-switch checks. Required if
            ``check_kill_switch=True``.

    Returns:
        A :class:`RunLedger` with run statistics and error list.
    """
    ledger = RunLedger(
        source_slug=SOURCE_SLUG,
        policy_version=CURRENT_POLICY_VERSION,
    )

    # Collection-window check (nightly 01:00–06:00 UK = UTC in winter)
    if enforce_window and not is_within_collection_window(
        start=COLLECTION_WINDOW_UK_START,
        end=COLLECTION_WINDOW_UK_END,
    ):
        logger.warning(
            "Outside collection window %02d:00–%02d:00 — aborting",
            COLLECTION_WINDOW_UK_START,
            COLLECTION_WINDOW_UK_END,
        )
        ledger.finish("window_closed")
        return ledger

    # Kill-switch check (per-source disable flag in data_sources)
    if check_kill_switch and db_engine is not None:
        if not _is_source_enabled(db_engine, SOURCE_SLUG):
            logger.warning("Kill switch active for source '%s' — aborting", SOURCE_SLUG)
            ledger.finish("kill_switch")
            return ledger

    client = _make_client()
    last_request = 0.0
    fetch_count = 0

    try:
        for cert_no in cert_nos:
            if fetch_count >= max_fetches:
                logger.info("Hit max_fetches cap (%d) — stopping", max_fetches)
                break

            # Kill switch re-checked before each cert
            if check_kill_switch and db_engine is not None and not _is_source_enabled(db_engine, SOURCE_SLUG):
                logger.warning("Kill switch activated mid-run — stopping")
                break

            # --- POST search ---
            last_request = _polite_sleep(last_request)
            fetch_count += 1
            try:
                records, _ = search_cert(client, cert_no)
            except Exception as exc:
                logger.warning("Search failed for cert %s: %s", cert_no, exc)
                ledger.add_error(cert_no, f"search: {exc}")
                continue

            if not records:
                logger.debug("No results for cert %s", cert_no)
                continue

            ledger.certs_found += len(records)

            for record in records:
                if fetch_count >= max_fetches:
                    break

                # --- GET PDF ---
                last_request = _polite_sleep(last_request)
                fetch_count += 1
                try:
                    pdf_bytes = download_pdf(client, record.download_url)
                except Exception as exc:
                    logger.warning(
                        "PDF download failed for cert %s (%s): %s",
                        cert_no,
                        record.filename,
                        exc,
                    )
                    ledger.add_error(cert_no, f"download: {exc}")
                    continue

                # --- Idempotency: check if already stored ---
                sha = hashlib.sha256(pdf_bytes).hexdigest()
                if store.exists(sha):
                    logger.debug(
                        "Cert %s (%s): already stored (hash %s…)",
                        cert_no,
                        record.filename,
                        sha[:12],
                    )
                    ledger.certs_unchanged += 1
                    continue

                # --- Persist to content-addressed store ---
                fetched_at = datetime.now(timezone.utc).isoformat()
                content_hash, prov_ref = persist_raw_artifact(
                    store=store,
                    content=pdf_bytes,
                    source=SOURCE_SLUG,
                    requested_uri=record.download_url,
                    resolved_uri=record.download_url,
                    retrieved_at=fetched_at,
                    policy_version=CURRENT_POLICY_VERSION,
                    headers_subset={
                        "Content-Type": CONTENT_TYPE_PDF,
                        "X-Cert-No": record.cert_no,
                        "X-Boat-Name": record.boat_name,
                        "X-Sail-No": record.sail_no,
                        "X-Filename": record.filename,
                        "X-Listing-Ref": record.listing_ref,
                    },
                    status=200,
                    adapter_version=ADAPTER_VERSION,
                )

                # --- Persist to DB retrieval_events if engine available ---
                if db_engine is not None:
                    _write_retrieval_event(
                        db_engine,
                        prov_ref=prov_ref,
                        cert_no=record.cert_no,
                        boat_name=record.boat_name,
                        sail_no=record.sail_no,
                        filename=record.filename,
                        byte_size=len(pdf_bytes),
                    )

                logger.info(
                    "Stored cert %s (%s): %d bytes, hash %s…",
                    cert_no,
                    record.filename,
                    len(pdf_bytes),
                    content_hash[:12],
                )
                ledger.certs_new += 1

    finally:
        client.close()
        ledger.fetch_count = fetch_count
        ledger.finish("ok")

    return ledger


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _is_source_enabled(db_engine, slug: str) -> bool:
    """Check the kill switch flag in ``data_sources``.

    Returns True if the source is enabled (or if the table/row doesn't
    exist — fail-open so we don't block collection when the table is empty).
    """
    try:
        from sqlalchemy import text

        with db_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT enabled FROM data_sources WHERE slug = :slug LIMIT 1"
                ),
                {"slug": slug},
            ).fetchone()
            if row is None:
                return True  # source not in DB → permit by default
            return bool(row[0])
    except Exception as exc:
        logger.warning("Kill-switch DB check failed (fail-open): %s", exc)
        return True


def _write_retrieval_event(
    db_engine,
    prov_ref,
    cert_no: str,
    boat_name: str,
    sail_no: str,
    filename: str,
    byte_size: int,
) -> None:
    """Write a row to ``raw_objects`` and ``retrieval_events`` tables."""
    from sqlalchemy import text

    try:
        with db_engine.begin() as conn:
            # Upsert raw_objects
            conn.execute(
                text(
                    """
                    INSERT INTO raw_objects (content_hash, byte_size, content_type, object_location)
                    VALUES (:hash, :size, :ctype, :loc)
                    ON CONFLICT (content_hash) DO NOTHING
                    """
                ),
                {
                    "hash": prov_ref.content_hash,
                    "size": byte_size,
                    "ctype": CONTENT_TYPE_PDF,
                    "loc": prov_ref.object_location,
                },
            )

            # Insert retrieval_event
            conn.execute(
                text(
                    """
                    INSERT INTO retrieval_events
                      (content_hash, source, requested_uri, resolved_uri, retrieved_at,
                       policy_version, headers_subset, status, object_location,
                       adapter_version, lineage, schema_version)
                    VALUES
                      (:hash, :source, :req_uri, :res_uri, :retrieved_at,
                       :policy_ver, :headers::json, :status, :obj_loc,
                       :adapter_ver, :lineage::json, :schema_ver)
                    """
                ),
                {
                    "hash": prov_ref.content_hash,
                    "source": prov_ref.source,
                    "req_uri": prov_ref.requested_uri,
                    "res_uri": prov_ref.resolved_uri,
                    "retrieved_at": prov_ref.retrieved_at,
                    "policy_ver": prov_ref.policy_version,
                    "headers": json.dumps(
                        {
                            "Content-Type": CONTENT_TYPE_PDF,
                            "X-Cert-No": cert_no,
                            "X-Boat-Name": boat_name,
                            "X-Sail-No": sail_no,
                            "X-Filename": filename,
                        }
                    ),
                    "status": prov_ref.status,
                    "obj_loc": prov_ref.object_location,
                    "adapter_ver": prov_ref.adapter_version,
                    "lineage": json.dumps(prov_ref.lineage),
                    "schema_ver": prov_ref.schema_version,
                },
            )
    except Exception as exc:
        logger.warning("Failed to write retrieval event for cert %s: %s", cert_no, exc)


# ---------------------------------------------------------------------------
# High-level entry points
# ---------------------------------------------------------------------------


def run_nightly(
    store: RawObjectStore,
    db_engine=None,
    tcc_dir: Path | None = None,
    *,
    enforce_window: bool = True,
    max_fetches: int = MAX_FETCHES_PER_RUN,
) -> RunLedger:
    """Run the nightly IRC PDF capture job.

    Enumerates cert numbers from the DB (preferred) or TCC listing CSVs
    (fallback), then calls :func:`scrape_irc_pdfs`.

    Args:
        store: Content-addressed raw object store.
        db_engine: SQLAlchemy engine (for cert enumeration and kill switch).
        tcc_dir: Directory of TCC CSV snapshots (fallback enumeration).
        enforce_window: Abort if outside nightly window.
        max_fetches: Hard cap on total HTTP fetches.
    """
    # Enumerate cert numbers
    if db_engine is not None:
        cert_nos = enumerate_cert_nos_from_db(db_engine)
        logger.info("Enumerated %d cert numbers from DB", len(cert_nos))
    elif tcc_dir is not None:
        cert_nos = enumerate_cert_nos_from_tcc_dir(tcc_dir)
        logger.info("Enumerated %d cert numbers from TCC dir", len(cert_nos))
    else:
        logger.error("No cert source provided — pass db_engine or tcc_dir")
        ledger = RunLedger(SOURCE_SLUG, CURRENT_POLICY_VERSION)
        ledger.finish("error")
        return ledger

    return scrape_irc_pdfs(
        cert_nos=cert_nos,
        store=store,
        max_fetches=max_fetches,
        enforce_window=enforce_window,
        check_kill_switch=(db_engine is not None),
        db_engine=db_engine,
    )


def get_default_store() -> RawObjectStore:
    """Return the default RawObjectStore rooted at ``data/raw/irc_pdfs``."""
    from irc_data.config import RAW_DIR

    store_root = RAW_DIR / "irc_pdfs"
    return RawObjectStore(str(store_root))
