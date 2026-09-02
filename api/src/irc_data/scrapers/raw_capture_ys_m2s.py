"""Raw archival capture for Yacht Scoring + Manage2Sail (DP-00-03).

Policy: v1.0 (DP-01-02; supersedes interim-v0 / DP-00-01).  The nightly job
spec references the *interim-v0* politeness rules — those are the ``§3``
responsible-collection rules that DP-00-01 froze and that v1.0 carried
forward unchanged (robots.txt, 1 req / 2 s + jitter, nightly window,
conditional requests, hash dedup, hard caps, kill switch).

Scope
-----
Begin capturing the two highest-yield unexplored results platforms as
**raw archives — no parsing**.  This module performs a Temporal-scheduled
nightly ``discover → fetch → hash → store`` pass over the public Yacht
Scoring and Manage2Sail race-results pages.

Fetch primitive (per the DP-00-03 DECISION)
-------------------------------------------
* **Plain HTTP with conditional requests** is the primary fetch primitive
  (cheaper, no Firecrawl credits) for these *known-structure* pages.  Both
  platforms publish race-results pages whose URLs follow a stable,
  discoverable pattern — rendered fetch is unnecessary for archival capture
  of the raw bytes.
* **Firecrawl (or equivalent) is reserved** for the discovery / map phase
  and for any JavaScript-rendered page plain HTTP cannot capture.  Every
  such provider call is gated and logged through
  :mod:`irc_data.discovery.crawl_telemetry` (the OPS-01-05 crawl ledger).

Every HTTP/provider call is logged:

  * raw byte captures → ``retrieval_events`` (provenance envelope) and the
    content-addressed :class:`~irc_data.sources.provenance.RawObjectStore`;
  * every fetch (plain or provider) → ``firecrawl_calls`` via
    :func:`irc_data.discovery.crawl_telemetry.log_call` (OPS-01-05).

Discovery is via **public index pages only** — we never guess event IDs or
scrape authenticated areas.

Envelope / handoff contract
---------------------------
Identical to DP-00-04 — every fetched object is persisted via
:func:`irc_data.sources.provenance.persist_raw_artifact`, producing a
:class:`~irc_data.sources.provenance.ProvenanceRefV1` envelope::

    RawArtifactV0 = bytes + SHA-256 + URL + fetch time + policy_version 'v1.0'

Idempotency
-----------
Content is SHA-256 hashed before storage.  The content-addressed raw store
deduplicates identical bytes, and repeat fetches send
``If-None-Match`` / ``If-Modified-Since`` (sourced from the prior night's
``retrieval_events`` rows) so unchanged pages come back as HTTP 304 no-ops.
A rerun of the same night therefore stores **zero** new raw objects.

Canary mode
-----------
``run_nightly(..., canary=True)`` (or the CLI ``--canary`` flag) caps
discovery to a handful of result pages per source so a *live canary night*
stays well inside the rate caps while proving the end-to-end path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urljoin, urlparse

import httpx

from irc_data.scrapers.raw_capture import (
    CaptureItem,
    CaptureLedger as _BaseCaptureLedger,
    MAX_FETCHES_PER_RUN,
    MAX_OBJECT_BYTES,
    _make_client,
    _polite_sleep,
    fetch_robots_rules,
    is_source_collectable,
    is_url_allowed,
)
from irc_data.sources.policy import (
    CURRENT_POLICY_VERSION,
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

ADAPTER_VERSION = "dp-00-03/1.0"

#: Nightly collection window (source-local / UTC fallback), policy §3.3.
COLLECTION_WINDOW_START = 1  # 01:00
COLLECTION_WINDOW_END = 6    # 06:00

#: Sources DP-00-03 is responsible for.
SOURCE_SLUG_YACHTSCORING = "yachtscoring"
SOURCE_SLUG_MANAGE2SAIL = "manage2sail"
DP_00_03_SOURCES: tuple[str, ...] = (
    SOURCE_SLUG_YACHTSCORING,
    SOURCE_SLUG_MANAGE2SAIL,
)

#: OPS-01-05 crawl-ledger caller tag for every call this module makes.
CRAWL_CALLER = "dp-00-03.raw-capture"

#: Public index pages (discovery entry points — public index pages only).
#: Yacht Scoring publishes a public event-results archive; Manage2Sail
#: publishes a public event list.  These are the *only* pages we hit for
#: discovery — we never enumerate event IDs by brute force.
YACHTSCORING_INDEX_URL = "https://www.yachtscoring.com/event_results_archive.cfm"
MANAGE2SAIL_INDEX_URL = "https://www.manage2sail.com/event"

#: Default caps on the discovery frontier per source (full nightly run).
DEFAULT_MAX_DISCOVERY_PAGES = 200
#: Canary-mode discovery cap — keeps the live canary night small and safely
#: inside the rate caps.
CANARY_MAX_DISCOVERY_PAGES = 12


class CaptureLedger(_BaseCaptureLedger):
    """DP-00-03 capture ledger — adds the conditional-request cache.

    The ``etag_cache`` carries the run's final ``{url: {etag, last_modified}}``
    map so the caller (CLI / activity) can persist it for the next night.
    It is deliberately excluded from :meth:`to_dict` (the run summary).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.etag_cache: dict[str, dict[str, str]] = {}


# ---------------------------------------------------------------------------
# Source configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceConfig:
    """Per-source capture configuration.

    ``link_predicate`` decides which discovered ``href`` values count as
    race-results pages to archive.  ``rendered`` marks sources that require
    a JavaScript-rendering fetch primitive (Firecrawl) rather than plain
    HTTP; it is ``False`` for both DP-00-03 platforms because their public
    results pages are server-rendered enough to archive as raw bytes.
    """

    slug: str
    index_url: str
    max_discovery_pages: int
    rendered: bool
    link_predicate: Callable[[str], bool]


def _ys_is_results_link(href_lower: str) -> bool:
    """Yacht Scoring results pages: event results / cumulative pages.

    Matches the public results URL families (``event_results_cumulative``,
    ``event_results`` with an ``eid`` / numeric id, …) and excludes obvious
    non-result navigation links.
    """
    if "yachtscoring.com" not in href_lower and not href_lower.startswith("/"):
        return False
    if any(
        token in href_lower
        for token in (
            "event_results_cumulative",
            "event_results.cfm",
            "event_results/",
            "emenu.cfm",
            "results",
        )
    ):
        # Exclude pure navigation / non-result anchors.
        if any(
            bad in href_lower
            for bad in ("login", "signin", "register", "contact", "mailto:")
        ):
            return False
        # Require an event identifier of some sort.
        return (
            "eid=" in href_lower
            or "event_results_cumulative" in href_lower
            or any(ch.isdigit() for ch in href_lower)
        )
    return False


def _m2s_is_results_link(href_lower: str) -> bool:
    """Manage2Sail results pages: ``/event/<slug>`` / ``/results/...``."""
    if "manage2sail.com" not in href_lower and not href_lower.startswith("/"):
        return False
    if any(bad in href_lower for bad in ("login", "signin", "mailto:", "javascript:")):
        return False
    return (
        "/event/" in href_lower
        or "/results" in href_lower
        or "/regatta" in href_lower
        or "?e=" in href_lower
        or "eventid" in href_lower
    )


def _source_config(
    source_slug: str,
    *,
    max_discovery_pages: int | None = None,
) -> SourceConfig:
    """Resolve the :class:`SourceConfig` for a DP-00-03 source."""
    cap = max_discovery_pages or DEFAULT_MAX_DISCOVERY_PAGES
    if source_slug == SOURCE_SLUG_YACHTSCORING:
        return SourceConfig(
            slug=SOURCE_SLUG_YACHTSCORING,
            index_url=YACHTSCORING_INDEX_URL,
            max_discovery_pages=cap,
            rendered=False,
            link_predicate=_ys_is_results_link,
        )
    if source_slug == SOURCE_SLUG_MANAGE2SAIL:
        return SourceConfig(
            slug=SOURCE_SLUG_MANAGE2SAIL,
            index_url=MANAGE2SAIL_INDEX_URL,
            max_discovery_pages=cap,
            rendered=False,
            link_predicate=_m2s_is_results_link,
        )
    raise ValueError(
        f"source '{source_slug}' is not a DP-00-03 source ({DP_00_03_SOURCES})"
    )


# ---------------------------------------------------------------------------
# Crawl ledger (OPS-01-05) — every call logged
# ---------------------------------------------------------------------------


def _log_crawl_call(
    db_engine,
    *,
    mode: str,
    url: str,
    status: str,
    duration_ms: int,
    credits: int | None = 0,
    response_chars: int | None = None,
    links_found: int | None = None,
    error_message: str | None = None,
) -> None:
    """Log one call to the crawl ledger (OPS-01-05).  Never raises.

    Plain-HTTP fetches are logged with ``credits=0`` (no Firecrawl credit
    spent) so the ledger reflects every fetch while keeping the credit
    budget accounting accurate.
    """
    if db_engine is None:
        return
    try:
        from irc_data.discovery.crawl_telemetry import log_call

        log_call(
            db_engine,
            mode=mode,
            url=url,
            status=status,
            duration_ms=int(duration_ms),
            credits=credits,
            response_chars=response_chars,
            links_found=links_found,
            error_message=error_message,
            caller=CRAWL_CALLER,
        )
    except Exception as exc:  # pragma: no cover - telemetry must not break capture
        logger.warning("crawl ledger log failed for %s: %s", url, exc)


# ---------------------------------------------------------------------------
# Conditional-request cache (prior night's ETag / Last-Modified)
# ---------------------------------------------------------------------------


def _dialect_name(db_engine) -> str:
    """Return the lowercase dialect name (``'postgresql'``, ``'sqlite'``, …)."""
    try:
        return (getattr(db_engine.dialect, "name", "") or "").lower()
    except Exception:
        return ""


def _is_postgres(db_engine) -> bool:
    return "postgres" in _dialect_name(db_engine)


def load_conditional_cache(db_engine, source_slug: str) -> dict[str, dict[str, str]]:
    """Return ``{requested_uri: {etag, last_modified}}`` from prior runs.

    Reads the most recent ``retrieval_events`` row per URI that carried an
    ETag / Last-Modified so the next nightly run can issue conditional
    requests (policy §3.4).  Returns an empty dict when the DB or table is
    unavailable (fail-open → first run fetches unconditionally).
    """
    cache: dict[str, dict[str, str]] = {}
    if db_engine is None:
        return cache
    try:
        from sqlalchemy import text

        with db_engine.connect() as conn:
            if _is_postgres(db_engine):
                rows = conn.execute(
                    text(
                        """
                        SELECT DISTINCT ON (requested_uri)
                            requested_uri,
                            headers_subset ->> 'ETag'          AS etag,
                            headers_subset ->> 'Last-Modified' AS last_modified
                        FROM retrieval_events
                        WHERE source = :source
                          AND (
                                headers_subset ? 'ETag'
                             OR headers_subset ? 'Last-Modified'
                          )
                        ORDER BY requested_uri, retrieved_at DESC
                        """
                    ),
                    {"source": source_slug},
                ).fetchall()
            else:
                # Portable path (SQLite and others): pull the JSON header
                # subset and extract ETag / Last-Modified in Python.
                rows = conn.execute(
                    text(
                        """
                        SELECT requested_uri, headers_subset, retrieved_at
                        FROM retrieval_events
                        WHERE source = :source
                        ORDER BY requested_uri, retrieved_at DESC
                        """
                    ),
                    {"source": source_slug},
                ).fetchall()
                seen: set[str] = set()
                parsed: list[tuple[str, str | None, str | None]] = []
                for uri, headers_json, _ts in rows:
                    if uri in seen:
                        continue
                    seen.add(uri)
                    etag = last_modified = None
                    if headers_json:
                        try:
                            h = json.loads(headers_json)
                            etag = h.get("ETag")
                            last_modified = h.get("Last-Modified")
                        except Exception:
                            etag = last_modified = None
                    parsed.append((uri, etag, last_modified))
                rows = parsed
        for uri, etag, last_modified in rows:
            entry: dict[str, str] = {}
            if etag:
                entry["etag"] = etag
            if last_modified:
                entry["last_modified"] = last_modified
            if entry:
                cache[uri] = entry
    except Exception as exc:
        logger.warning("conditional cache load failed for %s (fail-open): %s", source_slug, exc)
    return cache


def load_known_hashes(db_engine, source_slug: str) -> set[str]:
    """Return content hashes already captured for *source_slug*.

    Used to short-circuit unchanged pages without re-writing the raw object.
    Fail-open: returns an empty set when unavailable.
    """
    hashes: set[str] = set()
    if db_engine is None:
        return hashes
    try:
        from sqlalchemy import text

        with db_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT content_hash FROM retrieval_events WHERE source = :s"),
                {"s": source_slug},
            ).fetchall()
        hashes = {r[0] for r in rows if r and r[0]}
    except Exception as exc:
        logger.warning("known-hash load failed for %s (fail-open): %s", source_slug, exc)
    return hashes


# ---------------------------------------------------------------------------
# ETag / Last-Modified file cache (DB-free operation, e.g. CLI canary runs)
# ---------------------------------------------------------------------------


def load_etag_file(path: str | Path) -> dict[str, dict[str, str]]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception as exc:
        logger.warning("failed to read etag cache %s: %s", path, exc)
        return {}


def save_etag_file(path: str | Path, cache: dict[str, dict[str, str]]) -> None:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(cache, indent=2, sort_keys=True))
        tmp.replace(p)
    except Exception as exc:
        logger.warning("failed to write etag cache %s: %s", path, exc)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _write_retrieval_event(db_engine, prov_ref: ProvenanceRefV1, byte_size: int) -> None:
    """Upsert ``raw_objects`` and insert a ``retrieval_events`` row (audit)."""
    from sqlalchemy import text

    # ``::json`` is a Postgres-only cast; other dialects store the JSON text.
    json_cast = "::json" if _is_postgres(db_engine) else ""
    insert_retrieval = (
        """
        INSERT INTO retrieval_events
          (content_hash, source, requested_uri, resolved_uri, retrieved_at,
           policy_version, headers_subset, status, object_location,
           adapter_version, lineage, schema_version)
        VALUES
          (:hash, :source, :req_uri, :res_uri, :retrieved_at,
           :policy_ver, :headers"""
        + json_cast
        + """, :status, :obj_loc,
           :adapter_ver, :lineage"""
        + json_cast
        + """, :schema_ver)
        """
    )

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
                text(insert_retrieval),
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
# Discovery (public index pages only, plain HTTP)
# ---------------------------------------------------------------------------


def discover_result_urls(
    client: httpx.Client,
    config: SourceConfig,
    *,
    max_pages: int | None = None,
) -> list[str]:
    """Discover race-results URLs from the source's public index page(s).

    Plain-HTTP fetch of the public index page(s) and extraction of result
    links matching the source's ``link_predicate``.  No JavaScript, no
    authenticated area, no event-ID enumeration.
    """
    from bs4 import BeautifulSoup

    cap = max_pages if max_pages is not None else config.max_discovery_pages
    response = client.get(config.index_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    urls: list[str] = []
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if not href:
            continue
        if not config.link_predicate(href.lower()):
            continue
        urls.append(urljoin(config.index_url, href))

    # De-duplicate, keep stable order, cap the frontier.
    deduped = sorted(set(urls))
    return deduped[:cap]


# ---------------------------------------------------------------------------
# Core capture primitive (plain HTTP + conditional requests + hash + store)
# ---------------------------------------------------------------------------


def capture_url(
    client: httpx.Client,
    store: RawObjectStore,
    url: str,
    source_slug: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    known_hashes: set[str] | None = None,
    db_engine=None,
) -> tuple[CaptureItem | None, str]:
    """Fetch *url* and store raw bytes if new/changed.

    Sends conditional-request headers when *etag* / *last_modified* are
    available (§3.4).  A 304 returns ``(None, "not_modified")``.  Content is
    SHA-256 hashed before storage; identical bytes already in the store (or
    in *known_hashes*) return ``"unchanged"`` and are not re-stored.

    Outcomes: ``"new"``, ``"unchanged"``, ``"not_modified"``,
    ``"too_large"``, or ``"error: ..."``.
    """
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    t0 = time.monotonic()
    try:
        response = client.get(url, headers=headers)
    except Exception as exc:
        _log_crawl_call(
            db_engine, mode="scrape", url=url, status="error",
            duration_ms=int((time.monotonic() - t0) * 1000),
            error_message=str(exc),
        )
        return None, f"error: {exc}"

    duration_ms = int((time.monotonic() - t0) * 1000)

    # 304 Not Modified — clean success, do not re-store (§3.4).
    if response.status_code == 304:
        _log_crawl_call(
            db_engine, mode="scrape", url=url, status="ok",
            duration_ms=duration_ms, response_chars=0,
        )
        return None, "not_modified"

    if response.status_code != 200:
        _log_crawl_call(
            db_engine, mode="scrape", url=url, status="error",
            duration_ms=duration_ms,
            error_message=f"http {response.status_code}",
        )
        return None, f"error: http {response.status_code}"

    content = response.content

    # Object size cap (§3.6).
    if len(content) > MAX_OBJECT_BYTES:
        _log_crawl_call(
            db_engine, mode="scrape", url=url, status="error",
            duration_ms=duration_ms, response_chars=len(content),
            error_message=f"too_large {len(content)}",
        )
        return None, f"too_large: {len(content)} bytes exceeds {MAX_OBJECT_BYTES}"

    content_hash = hashlib.sha256(content).hexdigest()

    # Dedup: same bytes already stored → no new object (§3.5).
    if store.exists(content_hash) or (known_hashes is not None and content_hash in known_hashes):
        _log_crawl_call(
            db_engine, mode="scrape", url=url, status="ok",
            duration_ms=duration_ms, response_chars=len(content),
        )
        item = CaptureItem(
            source_slug=source_slug,
            requested_uri=url,
            resolved_uri=str(response.url),
            status=response.status_code,
            content_hash=content_hash,
            content_length=len(content),
            content_type=response.headers.get("content-type"),
            adapter_version=ADAPTER_VERSION,
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

    if db_engine is not None:
        _write_retrieval_event(db_engine, prov_ref, byte_size=len(content))
    _log_crawl_call(
        db_engine, mode="scrape", url=url, status="ok",
        duration_ms=duration_ms, response_chars=len(content),
    )

    item = CaptureItem(
        source_slug=source_slug,
        requested_uri=url,
        resolved_uri=str(response.url),
        status=response.status_code,
        content_hash=content_hash,
        content_length=len(content),
        content_type=content_type,
        fetched_at=fetched_at,
        adapter_version=ADAPTER_VERSION,
        object_location=prov_ref.object_location,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
        stored=True,
    )
    return item, "new"


# ---------------------------------------------------------------------------
# Rendered capture (Firecrawl path — reserved for JS-rendered pages)
# ---------------------------------------------------------------------------


def capture_url_rendered(
    store: RawObjectStore,
    url: str,
    source_slug: str,
    *,
    known_hashes: set[str] | None = None,
    db_engine=None,
) -> tuple[CaptureItem | None, str]:
    """Fetch a JavaScript-rendered page via Firecrawl and store raw bytes.

    This is the *secondary* fetch primitive per the DP-00-03 decision — used
    only when a results page cannot be captured by plain HTTP.  The provider
    call is budget-gated and logged through the OPS-01-05 crawl ledger by
    :func:`irc_data.discovery.firecrawl_client.scrape_url`.
    """
    try:
        from irc_data.discovery.firecrawl_client import scrape_url
    except Exception as exc:  # pragma: no cover - SDK optional
        return None, f"error: firecrawl unavailable: {exc}"

    try:
        result = scrape_url(url, caller=CRAWL_CALLER)
    except Exception as exc:
        return None, f"error: {exc}"

    # Store the *raw HTML* when available, else the rendered markdown — this
    # is a raw archival capture, no parsing.
    raw = result.html if getattr(result, "html", None) else result.markdown
    if not raw:
        return None, "error: empty rendered content"
    content = raw.encode("utf-8")

    if len(content) > MAX_OBJECT_BYTES:
        return None, f"too_large: {len(content)} bytes exceeds {MAX_OBJECT_BYTES}"

    content_hash = hashlib.sha256(content).hexdigest()
    if store.exists(content_hash) or (known_hashes is not None and content_hash in known_hashes):
        item = CaptureItem(
            source_slug=source_slug,
            requested_uri=url,
            resolved_uri=url,
            status=200,
            content_hash=content_hash,
            content_length=len(content),
            content_type="text/html",
            adapter_version=ADAPTER_VERSION,
            stored=False,
        )
        return item, "unchanged"

    fetched_at = datetime.now(timezone.utc).isoformat()
    _, prov_ref = persist_raw_artifact(
        store=store,
        content=content,
        source=source_slug,
        requested_uri=url,
        resolved_uri=url,
        retrieved_at=fetched_at,
        policy_version=CURRENT_POLICY_VERSION,
        headers_subset={"Content-Type": "text/html"},
        status=200,
        adapter_version=ADAPTER_VERSION,
    )
    if db_engine is not None:
        _write_retrieval_event(db_engine, prov_ref, byte_size=len(content))

    item = CaptureItem(
        source_slug=source_slug,
        requested_uri=url,
        resolved_uri=url,
        status=200,
        content_hash=content_hash,
        content_length=len(content),
        content_type="text/html",
        adapter_version=ADAPTER_VERSION,
        fetched_at=fetched_at,
        object_location=prov_ref.object_location,
        stored=True,
    )
    return item, "new"


# ---------------------------------------------------------------------------
# Per-source capture loop
# ---------------------------------------------------------------------------


def capture_source(
    source_slug: str,
    store: RawObjectStore,
    *,
    urls: Sequence[str] | None = None,
    max_fetches: int = MAX_FETCHES_PER_RUN,
    max_discovery_pages: int | None = None,
    canary: bool = False,
    enforce_window: bool = True,
    check_kill_switch: bool = True,
    db_engine=None,
    etag_cache: dict[str, dict[str, str]] | None = None,
) -> CaptureLedger:
    """Nightly raw capture for a single DP-00-03 source.

    Discovers result URLs from the public index (or uses *urls*), then
    fetches each with conditional requests and stores new/changed bytes.
    When *canary* is True, discovery is capped to a handful of pages.
    """
    ledger = CaptureLedger(source_slug=source_slug)

    if source_slug not in DP_00_03_SOURCES:
        raise ValueError(
            f"capture_source: '{source_slug}' is not a DP-00-03 source ({DP_00_03_SOURCES})"
        )

    if enforce_window and not is_within_collection_window(
        start=COLLECTION_WINDOW_START, end=COLLECTION_WINDOW_END
    ):
        logger.warning("Outside collection window — aborting %s capture", source_slug)
        ledger.finish("window_closed")
        return ledger

    if check_kill_switch and not is_source_collectable(source_slug, db_engine):
        logger.warning("Kill switch / §2 gate active for '%s' — aborting", source_slug)
        ledger.finish("kill_switch")
        return ledger

    # Resolve config; canary mode tightens the discovery cap.
    discovery_cap = (
        CANARY_MAX_DISCOVERY_PAGES if canary else (max_discovery_pages or DEFAULT_MAX_DISCOVERY_PAGES)
    )
    config = _source_config(source_slug, max_discovery_pages=discovery_cap)

    client = _make_client(source_slug)
    last_request = 0.0

    # Conditional-request cache: prefer DB-derived (prior nights) then layer
    # any caller-supplied file cache on top.
    conditional_cache = load_conditional_cache(db_engine, source_slug)
    if etag_cache:
        conditional_cache.update(etag_cache)
    known_hashes = load_known_hashes(db_engine, source_slug)

    try:
        # robots.txt for the index host (§3.1) — fail-closed on error.
        try:
            robots_rules = fetch_robots_rules(client, config.index_url)
        except Exception as exc:
            logger.warning("robots.txt fetch failed for %s: %s — stopping", source_slug, exc)
            ledger.finish("robots_error")
            return ledger

        # Discover URLs (or use caller-supplied list).
        if urls is None:
            last_request = _polite_sleep(last_request)
            ledger.fetch_count += 1
            try:
                t0 = time.monotonic()
                urls = discover_result_urls(client, config, max_pages=discovery_cap)
                _log_crawl_call(
                    db_engine, mode="map", url=config.index_url,
                    status="ok" if urls else "empty",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    links_found=len(urls),
                )
            except Exception as exc:
                logger.warning("%s discovery failed: %s", source_slug, exc)
                ledger.add_error(config.index_url, f"discover: {exc}")
                _log_crawl_call(
                    db_engine, mode="map", url=config.index_url, status="error",
                    duration_ms=0, error_message=str(exc),
                )
                ledger.finish("error")
                return ledger

        logger.info("%s: %d candidate result URLs (canary=%s)", source_slug, len(urls), canary)

        for url in urls:
            if ledger.fetch_count >= max_fetches:
                logger.info("%s: hit max_fetches cap (%d)", source_slug, max_fetches)
                break
            if check_kill_switch and not is_source_collectable(source_slug, db_engine):
                logger.warning("Kill switch activated mid-run — stopping")
                break
            if not is_url_allowed(url, robots_rules):
                ledger.urls_skipped += 1
                continue

            cache_entry = conditional_cache.get(url, {})
            last_request = _polite_sleep(last_request)
            ledger.fetch_count += 1
            ledger.urls_attempted += 1

            item, outcome = capture_url(
                client,
                store,
                url,
                source_slug,
                etag=cache_entry.get("etag"),
                last_modified=cache_entry.get("last_modified"),
                known_hashes=known_hashes,
                db_engine=db_engine,
            )

            if outcome == "not_modified":
                ledger.urls_not_modified += 1
            elif outcome == "unchanged":
                ledger.urls_fetched += 1
                ledger.urls_unchanged += 1
                if item is not None:
                    ledger.items.append(item)
                    _update_conditional_cache(conditional_cache, url, item)
            elif outcome == "new":
                ledger.urls_fetched += 1
                ledger.urls_new += 1
                if item is not None:
                    ledger.bytes_downloaded += item.content_length
                    ledger.items.append(item)
                    known_hashes.add(item.content_hash)
                    _update_conditional_cache(conditional_cache, url, item)
            else:
                ledger.add_error(url, outcome)
    finally:
        client.close()
        ledger.finish("ok" if not ledger.errors else "ok_with_errors")

    # Expose the updated conditional cache so callers (CLI) can persist it.
    ledger.etag_cache = conditional_cache
    return ledger


def _update_conditional_cache(
    cache: dict[str, dict[str, str]], url: str, item: CaptureItem
) -> None:
    if item.etag or item.last_modified:
        cache[url] = {
            k: v
            for k, v in {"etag": item.etag, "last_modified": item.last_modified}.items()
            if v
        }


# ---------------------------------------------------------------------------
# High-level entry points
# ---------------------------------------------------------------------------


def get_default_store(source_slug: str) -> RawObjectStore:
    """Return the default content-addressed store for a DP-00-03 source."""
    from irc_data.config import RAW_DIR

    return RawObjectStore(str(RAW_DIR / source_slug))


def run_nightly(
    source_slug: str,
    *,
    store: RawObjectStore | None = None,
    urls: Sequence[str] | None = None,
    db_engine=None,
    enforce_window: bool = True,
    max_fetches: int = MAX_FETCHES_PER_RUN,
    max_discovery_pages: int | None = None,
    canary: bool = False,
    etag_cache: dict[str, dict[str, str]] | None = None,
) -> CaptureLedger:
    """Run the nightly raw capture for a single DP-00-03 source.

    Args:
        source_slug: ``'yachtscoring'`` or ``'manage2sail'``.
        store: Raw object store (defaults to ``data/raw/<source_slug>``).
        urls: Optional explicit result URLs (skip discovery).
        db_engine: SQLAlchemy engine for kill-switch + retrieval events.
        enforce_window: Abort if outside the nightly window.
        max_fetches: Hard cap on HTTP fetches.
        max_discovery_pages: Cap on discovered result pages.
        canary: Canary mode — tight discovery cap for the live canary night.
        etag_cache: Optional caller-supplied conditional-request cache.
    """
    if source_slug not in DP_00_03_SOURCES:
        raise ValueError(
            f"run_nightly: source '{source_slug}' is not a DP-00-03 source "
            f"({DP_00_03_SOURCES})"
        )

    store = store or get_default_store(source_slug)

    return capture_source(
        source_slug,
        store,
        urls=urls,
        max_fetches=max_fetches,
        max_discovery_pages=max_discovery_pages,
        canary=canary,
        enforce_window=enforce_window,
        check_kill_switch=True,
        db_engine=db_engine,
        etag_cache=etag_cache,
    )
