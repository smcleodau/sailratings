"""Generic raw archival capture job framework (DP-00-03 / DP-00-04).

Policy: v1.0 (DP-01-02; supersedes interim-v0 / DP-00-01)

This module implements the shared "fetch → hash → store" nightly job
framework used by the DP-00 interim raw-capture track.  DP-00-03 uses it for
Yacht Scoring / Manage2Sail; **DP-00-04 uses it for Sailwave result files and
the approved sailing news (RSS/Atom) feeds.**

Envelope / handoff contract
---------------------------
Every fetched object is persisted via
:func:`irc_data.sources.provenance.persist_raw_artifact`, producing a
:class:`~irc_data.sources.provenance.ProvenanceRefV1` envelope:

    RawArtifactV0 = bytes + SHA-256 + URL + fetch time + policy_version 'v1.0'

Politeness rules (INTERIM-POLICY §3) — identical to DP-00-03 / DP-00-05:

  * 1 request / 2 s minimum with 1 s jitter (per-domain)
  * Nightly collection window 01:00–06:00 (source-local; UTC default)
  * Conditional requests: ``If-None-Match`` / ``If-Modified-Since`` when a
    cached ETag / Last-Modified is available; HTTP 304 is a clean no-op
  * Max 5,000 fetches per source per night; 25 MB per object
  * robots.txt ``Disallow`` rules honoured per URL path
  * Kill switch: ``data_sources.enabled`` re-checked before every fetch cycle

Idempotency
-----------
Content is SHA-256 hashed before storage.  The content-addressed
:class:`~irc_data.sources.provenance.RawObjectStore` deduplicates identical
bytes, so a re-run of the same night stores **zero** new objects.  New
``retrieval_events`` rows are written per fetch (they are the audit log) but
no duplicate raw bytes are persisted.

§2 hold / blocked sources
--------------------------
Sources whose ``legal_status`` is ``'hold'`` or ``'blocked'`` (INTERIM-POLICY
§2.2 / §2.3 — e.g. ClubSpot, Kwindoo) are **never fetched**.  The
:func:`list_approved_source_slugs` helper and the
:func:`is_source_collectable` gate enforce this before any HTTP request is
issued.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

import httpx

from irc_data.sources.policy import (
    CURRENT_POLICY_VERSION,
    POLICY_USER_AGENT,
    is_within_collection_window,
)
from irc_data.sources.provenance import (
    ProvenanceRefV1,
    RawObjectStore,
    persist_raw_artifact,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADAPTER_VERSION = "dp-00-04/1.0"

#: Nightly collection window (source-local / UTC fallback), INTERIM-POLICY §3.3
COLLECTION_WINDOW_START = 1  # 01:00
COLLECTION_WINDOW_END = 6    # 06:00

# Politeness — INTERIM-POLICY §3.2
MIN_DELAY_SECONDS = 2.0
JITTER_SECONDS = 1.0

#: Hard caps — INTERIM-POLICY §3.6
MAX_FETCHES_PER_RUN = 5_000
MAX_OBJECT_BYTES = 25 * 1024 * 1024  # 25 MB

#: Sources that DP-00-04 is responsible for.
SOURCE_SLUG_SAILWAVE = "sailwave"
SOURCE_SLUG_NEWS = "sailing-news"
DP_00_04_SOURCES: tuple[str, ...] = (SOURCE_SLUG_SAILWAVE, SOURCE_SLUG_NEWS)

#: Default approved news feeds (RSS/Atom, published for syndication).
#: Operators may extend/replace this list via the CLI ``--feed`` option.
#:
#: Verified live on 2026-09-02 (HTTP 200 + RSS/Atom body + robots allows the
#: feed path for our User-Agent):
#:   * sailweb.co.uk/feed           — SailWeb UK sailing news RSS
#:   * sail-world.com/rss           — Sail-World RSS (robots Disallow: /
#:     entries apply to other crawler UAs, not ``*``)
#:   * sailingscuttlebutt.com/feed  — Scuttlebutt Sailing News RSS
#:
#: Rejected during verification:
#:   * sailing.org/feed             — 404 (no public feed at this path)
#:   * yachtsandyachting.com/feed   — robots.txt ``Disallow: /`` for ``*``
DEFAULT_NEWS_FEEDS: tuple[str, ...] = (
    "https://www.sailweb.co.uk/feed",
    "https://www.sail-world.com/rss",
    "https://www.sailingscuttlebutt.com/feed",
)

#: Sailwave public results index — static HTML, no JavaScript required.
SAILWAVE_INDEX_URL = "https://www.sailwave.com/results"

#: File extensions considered Sailwave result files (.htm/.html/.blw).
SAILWAVE_RESULT_EXTENSIONS = (".htm", ".html", ".blw")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CaptureItem:
    """A single raw capture result (the in-memory envelope).

    This is the DP-00-04 handoff record — it carries everything the
    downstream pipeline needs (bytes hash, URL, fetch time, policy version,
    provenance envelope) without embedding the raw bytes inline.
    """

    source_slug: str
    requested_uri: str
    resolved_uri: str
    status: int
    content_hash: str
    content_length: int
    content_type: str | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    policy_version: str = CURRENT_POLICY_VERSION
    object_location: str = ""
    adapter_version: str = ADAPTER_VERSION
    etag: str | None = None
    last_modified: str | None = None
    stored: bool = True  # False when content was deduplicated (already stored)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_slug": self.source_slug,
            "requested_uri": self.requested_uri,
            "resolved_uri": self.resolved_uri,
            "status": self.status,
            "content_hash": self.content_hash,
            "content_length": self.content_length,
            "content_type": self.content_type,
            "fetched_at": self.fetched_at,
            "policy_version": self.policy_version,
            "object_location": self.object_location,
            "adapter_version": self.adapter_version,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "stored": self.stored,
        }


@dataclass
class CaptureLedger:
    """Statistics for a single capture run (one source)."""

    source_slug: str
    policy_version: str = CURRENT_POLICY_VERSION
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    status: str = "running"
    urls_attempted: int = 0
    urls_fetched: int = 0
    urls_new: int = 0
    urls_unchanged: int = 0
    urls_not_modified: int = 0
    urls_skipped: int = 0
    fetch_count: int = 0
    bytes_downloaded: int = 0
    errors: list[dict] = field(default_factory=list)
    items: list[CaptureItem] = field(default_factory=list)

    def finish(self, status: str = "ok") -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self.status = status

    def add_error(self, url: str, message: str) -> None:
        self.errors.append({"url": url, "message": message})

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_slug": self.source_slug,
            "policy_version": self.policy_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "urls_attempted": self.urls_attempted,
            "urls_fetched": self.urls_fetched,
            "urls_new": self.urls_new,
            "urls_unchanged": self.urls_unchanged,
            "urls_not_modified": self.urls_not_modified,
            "urls_skipped": self.urls_skipped,
            "fetch_count": self.fetch_count,
            "bytes_downloaded": self.bytes_downloaded,
            "error_count": len(self.errors),
            "errors": self.errors[:20],
            "items": [i.to_dict() for i in self.items],
        }


# ---------------------------------------------------------------------------
# Source gate helpers (INTERIM-POLICY §2 / §7)
# ---------------------------------------------------------------------------

#: Legal statuses under which content collection is permitted (§2.1).
_CONTENT_ALLOWED = {"approved"}


def is_source_collectable(source_slug: str, db_engine=None) -> bool:
    """Return True iff *source_slug* may be content-collected under policy §2.

    Checks the in-memory registry seed first; if a DB engine is provided the
    ``data_sources`` row is consulted for the kill switch (``enabled``) and
    ``legal_status``.  Sources not listed as ``approved`` (e.g. ``hold`` /
    ``blocked``) are never collectable.
    """
    # Registry check — the in-memory seed is authoritative for §2 membership
    from irc_data.sources.registry import get_in_memory_source

    record = get_in_memory_source(source_slug)
    if record is None:
        # Unknown sources are implicitly blocked (§2.3).
        logger.warning(
            "Source '%s' is not in the approved registry (§2.3) — not collectable",
            source_slug,
        )
        return False

    legal_status = (
        record.legal_status.value
        if hasattr(record.legal_status, "value")
        else str(record.legal_status or "")
    )
    if legal_status not in _CONTENT_ALLOWED:
        logger.warning(
            "Source '%s' has legal_status='%s' — not collectable under §2",
            source_slug,
            legal_status,
        )
        return False
    if not getattr(record, "enabled", True):
        logger.warning("Source '%s' is disabled in registry — not collectable", source_slug)
        return False

    # DB kill-switch check (§7) — fail-open if the table is missing
    if db_engine is not None:
        try:
            from sqlalchemy import text

            with db_engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT enabled, legal_status FROM data_sources "
                        "WHERE slug = :slug LIMIT 1"
                    ),
                    {"slug": source_slug},
                ).fetchone()
                if row is not None:
                    enabled, legal_status = row[0], row[1]
                    if legal_status and str(legal_status) not in _CONTENT_ALLOWED:
                        return False
                    return bool(enabled)
        except Exception as exc:
            logger.warning("Kill-switch DB check failed (fail-open): %s", exc)

    return True


def list_approved_source_slugs(db_engine=None) -> list[str]:
    """Return slugs of all sources approved for content collection (§2.1).

    Hold (§2.2) and blocked (§2.3) sources are excluded — they must not be
    fetched during content-collection windows.
    """
    from irc_data.sources.registry import get_in_memory_sources

    slugs: list[str] = []
    for record in get_in_memory_sources():
        legal_status = (
            record.legal_status.value
            if hasattr(record.legal_status, "value")
            else str(record.legal_status or "")
        )
        if legal_status in _CONTENT_ALLOWED and getattr(record, "enabled", True):
            slugs.append(record.slug)
    return slugs


# ---------------------------------------------------------------------------
# HTTP client + politeness helpers
# ---------------------------------------------------------------------------


def _make_client(source_slug: str) -> httpx.Client:
    """Create a policy-compliant synchronous HTTP client.

    Sends the mandated ``User-Agent`` and the attribution header
    ``X-SailRatings-Source`` (INTERIM-POLICY §4 / §6).
    """
    return httpx.Client(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
        headers={
            "User-Agent": POLICY_USER_AGENT,
            "X-SailRatings-Source": source_slug,
        },
    )


def _polite_sleep(last_request_time: float, min_delay: float = MIN_DELAY_SECONDS) -> float:
    """Sleep to respect the 1 req / 2 s + jitter rate limit. Returns new time."""
    now = time.monotonic()
    elapsed = now - last_request_time
    delay = min_delay + random.uniform(0, JITTER_SECONDS)
    if elapsed < delay:
        time.sleep(delay - elapsed)
    return time.monotonic()


# ---------------------------------------------------------------------------
# robots.txt helpers (INTERIM-POLICY §3.1)
# ---------------------------------------------------------------------------


def fetch_robots_rules(client: httpx.Client, base_url: str):
    """Fetch and parse robots.txt for *base_url*.

    Returns a :class:`~irc_data.sources.robots.RobotsRules` instance.  A 404
    means no disallow rules (everything allowed).  Network/5xx errors raise —
    the caller should treat this as a collection stop for the source (§3.1).
    """
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    response = client.get(robots_url)
    if response.status_code == 404:
        from irc_data.sources.robots import RobotsRules

        return RobotsRules(no_rules=True)
    response.raise_for_status()

    from irc_data.sources.robots import parse_robots_txt

    return parse_robots_txt(response.text)


def is_url_allowed(url: str, rules) -> bool:
    """Return True iff *url*'s path is allowed by *rules* for our UA or ``*``."""
    if rules is None:
        return True
    path = urlparse(url).path or "/"
    try:
        return bool(rules.is_allowed(path, "sailratings"))
    except Exception:
        # Fail-closed on unexpected matcher errors: do not fetch.
        logger.warning("robots matcher error for %s — treating as disallowed", url)
        return False


# ---------------------------------------------------------------------------
# Core capture primitive
# ---------------------------------------------------------------------------


def capture_url(
    client: httpx.Client,
    store: RawObjectStore,
    url: str,
    source_slug: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    db_engine=None,
) -> tuple[CaptureItem | None, str]:
    """Fetch *url* and store it as a raw artifact if new/changed.

    Sends conditional-request headers when *etag* / *last_modified* are
    provided (§3.4).  A 304 response returns ``(None, "not_modified")``.

    Returns a tuple of ``(CaptureItem | None, outcome)`` where *outcome* is
    one of ``"new"``, ``"unchanged"``, ``"not_modified"``, ``"too_large"``,
    or ``"error"`` (on error the item is ``None`` and the message is the
    exception string).
    """
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        response = client.get(url, headers=headers)
    except Exception as exc:
        return None, f"error: {exc}"

    # 304 Not Modified — clean success, do not re-store (§3.4)
    if response.status_code == 304:
        return None, "not_modified"

    if response.status_code != 200:
        return None, f"error: http {response.status_code}"

    content = response.content

    # Object size cap (§3.6)
    if len(content) > MAX_OBJECT_BYTES:
        return None, f"too_large: {len(content)} bytes exceeds {MAX_OBJECT_BYTES}"

    content_hash = hashlib.sha256(content).hexdigest()

    # Dedup: same bytes already stored → no new object (§3.5)
    if store.exists(content_hash):
        item = CaptureItem(
            source_slug=source_slug,
            requested_uri=url,
            resolved_uri=str(response.url),
            status=response.status_code,
            content_hash=content_hash,
            content_length=len(content),
            content_type=response.headers.get("content-type"),
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            stored=False,
        )
        return item, "unchanged"

    fetched_at = datetime.now(timezone.utc).isoformat()
    content_type = response.headers.get("content-type", "")
    _, prov_ref = persist_raw_artifact(
        store=store,
        content=content,
        source=source_slug,
        requested_uri=url,
        resolved_uri=str(response.url),
        retrieved_at=fetched_at,
        policy_version=CURRENT_POLICY_VERSION,
        headers_subset={
            "Content-Type": content_type,
            **({"ETag": response.headers["etag"]} if "etag" in response.headers else {}),
            **(
                {"Last-Modified": response.headers["last-modified"]}
                if "last-modified" in response.headers
                else {}
            ),
        },
        status=response.status_code,
        adapter_version=ADAPTER_VERSION,
    )

    # DB retrieval event (audit log) — fail-open
    if db_engine is not None:
        _write_retrieval_event(db_engine, prov_ref, byte_size=len(content))

    item = CaptureItem(
        source_slug=source_slug,
        requested_uri=url,
        resolved_uri=str(response.url),
        status=response.status_code,
        content_hash=content_hash,
        content_length=len(content),
        content_type=content_type,
        fetched_at=fetched_at,
        object_location=prov_ref.object_location,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
        stored=True,
    )
    return item, "new"


# ---------------------------------------------------------------------------
# Sailwave capture (DP-00-04)
# ---------------------------------------------------------------------------


def discover_sailwave_urls(client: httpx.Client, index_url: str = SAILWAVE_INDEX_URL) -> list[str]:
    """Discover Sailwave result-file URLs from the public index page.

    Sailwave results are static HTML/``.blw`` files linked from club and
    event index pages.  This performs plain-HTTP discovery only (no
    JavaScript) per the DP-00-03 decision for known-structure pages.
    """
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    response = client.get(index_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    urls: list[str] = []
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        lower = href.lower()
        if any(lower.endswith(ext) for ext in SAILWAVE_RESULT_EXTENSIONS):
            urls.append(urljoin(index_url, href))
    return sorted(set(urls))


def capture_sailwave(
    store: RawObjectStore,
    *,
    index_url: str = SAILWAVE_INDEX_URL,
    urls: Sequence[str] | None = None,
    max_fetches: int = MAX_FETCHES_PER_RUN,
    enforce_window: bool = True,
    check_kill_switch: bool = True,
    db_engine=None,
    etag_cache: dict[str, dict[str, str]] | None = None,
) -> CaptureLedger:
    """Nightly raw capture of Sailwave result files.

    Discovers result-file URLs from the public index (or uses *urls* if
    provided), then fetches each with conditional requests and stores
    new/changed bytes in the content-addressed raw store.
    """
    ledger = CaptureLedger(source_slug=SOURCE_SLUG_SAILWAVE)

    if enforce_window and not is_within_collection_window(
        start=COLLECTION_WINDOW_START, end=COLLECTION_WINDOW_END
    ):
        logger.warning("Outside collection window — aborting sailwave capture")
        ledger.finish("window_closed")
        return ledger

    if check_kill_switch and not is_source_collectable(SOURCE_SLUG_SAILWAVE, db_engine):
        logger.warning("Kill switch / §2 gate active for 'sailwave' — aborting")
        ledger.finish("kill_switch")
        return ledger

    client = _make_client(SOURCE_SLUG_SAILWAVE)
    last_request = 0.0
    etag_cache = etag_cache if etag_cache is not None else {}

    try:
        # robots.txt for the index host (§3.1)
        try:
            robots_rules = fetch_robots_rules(client, index_url)
        except Exception as exc:
            logger.warning("robots.txt fetch failed for sailwave: %s — stopping", exc)
            ledger.finish("robots_error")
            return ledger

        # Discover URLs (or use caller-supplied list)
        if urls is None:
            last_request = _polite_sleep(last_request)
            ledger.fetch_count += 1
            try:
                urls = discover_sailwave_urls(client, index_url)
            except Exception as exc:
                logger.warning("Sailwave discovery failed: %s", exc)
                ledger.add_error(index_url, f"discover: {exc}")
                ledger.finish("error")
                return ledger

        logger.info("Sailwave: %d candidate result URLs", len(urls))

        for url in urls:
            if ledger.fetch_count >= max_fetches:
                logger.info("Sailwave: hit max_fetches cap (%d)", max_fetches)
                break
            if check_kill_switch and not is_source_collectable(SOURCE_SLUG_SAILWAVE, db_engine):
                logger.warning("Kill switch activated mid-run — stopping")
                break
            if not is_url_allowed(url, robots_rules):
                ledger.urls_skipped += 1
                continue

            cache_entry = etag_cache.get(url, {})
            last_request = _polite_sleep(last_request)
            ledger.fetch_count += 1
            ledger.urls_attempted += 1

            item, outcome = capture_url(
                client,
                store,
                url,
                SOURCE_SLUG_SAILWAVE,
                etag=cache_entry.get("etag"),
                last_modified=cache_entry.get("last_modified"),
                db_engine=db_engine,
            )

            if outcome == "not_modified":
                ledger.urls_not_modified += 1
            elif outcome == "unchanged":
                ledger.urls_fetched += 1
                ledger.urls_unchanged += 1
                if item is not None:
                    ledger.items.append(item)
            elif outcome == "new":
                ledger.urls_fetched += 1
                ledger.urls_new += 1
                if item is not None:
                    ledger.bytes_downloaded += item.content_length
                    ledger.items.append(item)
                    # update etag cache
                    if item.etag or item.last_modified:
                        etag_cache[url] = {
                            k: v
                            for k, v in {"etag": item.etag, "last_modified": item.last_modified}.items()
                            if v
                        }
            else:
                ledger.add_error(url, outcome)
    finally:
        client.close()
        ledger.finish("ok" if not ledger.errors else "ok_with_errors")

    return ledger


# ---------------------------------------------------------------------------
# News feed capture (DP-00-04)
# ---------------------------------------------------------------------------


def capture_news_feeds(
    store: RawObjectStore,
    *,
    feeds: Sequence[str] | None = None,
    max_fetches: int = MAX_FETCHES_PER_RUN,
    enforce_window: bool = True,
    check_kill_switch: bool = True,
    db_engine=None,
    etag_cache: dict[str, dict[str, str]] | None = None,
) -> CaptureLedger:
    """Nightly raw capture of approved sailing news feeds (RSS/Atom).

    Feeds are explicitly published for syndication (INTERIM-POLICY §2.1).
    Raw feed XML is stored unchanged — no parsing in this step.
    """
    ledger = CaptureLedger(source_slug=SOURCE_SLUG_NEWS)

    if enforce_window and not is_within_collection_window(
        start=COLLECTION_WINDOW_START, end=COLLECTION_WINDOW_END
    ):
        logger.warning("Outside collection window — aborting news capture")
        ledger.finish("window_closed")
        return ledger

    if check_kill_switch and not is_source_collectable(SOURCE_SLUG_NEWS, db_engine):
        logger.warning("Kill switch / §2 gate active for 'sailing-news' — aborting")
        ledger.finish("kill_switch")
        return ledger

    feed_urls = list(feeds) if feeds else list(DEFAULT_NEWS_FEEDS)
    client = _make_client(SOURCE_SLUG_NEWS)
    last_request = 0.0
    etag_cache = etag_cache if etag_cache is not None else {}

    # robots rules per distinct host
    host_rules: dict[str, Any] = {}

    try:
        for url in feed_urls:
            if ledger.fetch_count >= max_fetches:
                logger.info("News: hit max_fetches cap (%d)", max_fetches)
                break
            if check_kill_switch and not is_source_collectable(SOURCE_SLUG_NEWS, db_engine):
                logger.warning("Kill switch activated mid-run — stopping")
                break

            host = urlparse(url).netloc
            if host not in host_rules:
                try:
                    scheme = urlparse(url).scheme or "https"
                    host_rules[host] = fetch_robots_rules(client, f"{scheme}://{host}")
                except Exception as exc:
                    logger.warning("robots.txt failed for %s: %s — skipping host", host, exc)
                    ledger.add_error(url, f"robots: {exc}")
                    host_rules[host] = None  # sentinel: fail-closed
            rules = host_rules[host]

            if rules is None or not is_url_allowed(url, rules):
                ledger.urls_skipped += 1
                continue

            cache_entry = etag_cache.get(url, {})
            last_request = _polite_sleep(last_request)
            ledger.fetch_count += 1
            ledger.urls_attempted += 1

            item, outcome = capture_url(
                client,
                store,
                url,
                SOURCE_SLUG_NEWS,
                etag=cache_entry.get("etag"),
                last_modified=cache_entry.get("last_modified"),
                db_engine=db_engine,
            )

            if outcome == "not_modified":
                ledger.urls_not_modified += 1
            elif outcome == "unchanged":
                ledger.urls_fetched += 1
                ledger.urls_unchanged += 1
                if item is not None:
                    ledger.items.append(item)
            elif outcome == "new":
                ledger.urls_fetched += 1
                ledger.urls_new += 1
                if item is not None:
                    ledger.bytes_downloaded += item.content_length
                    ledger.items.append(item)
                    if item.etag or item.last_modified:
                        etag_cache[url] = {
                            k: v
                            for k, v in {"etag": item.etag, "last_modified": item.last_modified}.items()
                            if v
                        }
            else:
                ledger.add_error(url, outcome)
    finally:
        client.close()
        ledger.finish("ok" if not ledger.errors else "ok_with_errors")

    return ledger


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _write_retrieval_event(db_engine, prov_ref: ProvenanceRefV1, byte_size: int) -> None:
    """Upsert ``raw_objects`` and insert a ``retrieval_events`` row (audit log)."""
    from sqlalchemy import text

    try:
        with db_engine.begin() as conn:
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
                    "ctype": prov_ref.headers_subset.get("Content-Type", ""),
                    "loc": prov_ref.object_location,
                },
            )
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
                    "headers": json.dumps(prov_ref.headers_subset),
                    "status": prov_ref.status,
                    "obj_loc": prov_ref.object_location,
                    "adapter_ver": prov_ref.adapter_version,
                    "lineage": json.dumps(prov_ref.lineage),
                    "schema_ver": prov_ref.schema_version,
                },
            )
    except Exception as exc:
        logger.warning("Failed to write retrieval event for %s: %s", prov_ref.requested_uri, exc)


# ---------------------------------------------------------------------------
# High-level entry points
# ---------------------------------------------------------------------------


def get_default_store(source_slug: str) -> RawObjectStore:
    """Return the default content-addressed store for a DP-00-04 source."""
    from irc_data.config import RAW_DIR

    return RawObjectStore(str(RAW_DIR / source_slug))


def run_nightly(
    source_slug: str,
    *,
    store: RawObjectStore | None = None,
    urls: Sequence[str] | None = None,
    feeds: Sequence[str] | None = None,
    db_engine=None,
    enforce_window: bool = True,
    max_fetches: int = MAX_FETCHES_PER_RUN,
) -> CaptureLedger:
    """Run the nightly raw capture for a single DP-00-04 source.

    Args:
        source_slug: ``'sailwave'`` or ``'sailing-news'``.
        store: Raw object store (defaults to ``data/raw/<source_slug>``).
        urls: Optional explicit Sailwave URLs (skip discovery).
        feeds: Optional news feed URL list (defaults to ``DEFAULT_NEWS_FEEDS``).
        db_engine: SQLAlchemy engine for kill-switch + retrieval events.
        enforce_window: Abort if outside the nightly window.
        max_fetches: Hard cap on HTTP fetches.
    """
    if source_slug not in DP_00_04_SOURCES:
        raise ValueError(
            f"run_nightly: source '{source_slug}' is not a DP-00-04 source "
            f"({DP_00_04_SOURCES})"
        )

    store = store or get_default_store(source_slug)

    if source_slug == SOURCE_SLUG_SAILWAVE:
        return capture_sailwave(
            store,
            urls=urls,
            max_fetches=max_fetches,
            enforce_window=enforce_window,
            check_kill_switch=True,
            db_engine=db_engine,
        )
    return capture_news_feeds(
        store,
        feeds=feeds,
        max_fetches=max_fetches,
        enforce_window=enforce_window,
        check_kill_switch=True,
        db_engine=db_engine,
    )
