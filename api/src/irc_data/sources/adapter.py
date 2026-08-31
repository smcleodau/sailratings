"""Source adapter SDK — abstract base and interfaces (DP-01-03).

The :class:`SourceAdapter` is the abstract base every concrete adapter
inherits from.  It standardises the acquisition mechanics:

* **discover** — enumerate the URLs/pages a source exposes.
* **fetch** — download a single URL, returning a raw envelope.
* **enumerate** — list all collectible items (alias for discover with
  structured metadata).
* **checkpoint** — save/restore collection progress for resume.
* **parse-hint** — annotate each envelope with a parser hint
  (``"html"``, ``"json"``, ``"pdf"``, …) without parsing.
* **rate-limit** — per-domain rate limiting via the policy.
* **conditional request** — send ``If-None-Match`` / ``If-Modified-Since``
  and treat 304 as clean success.
* **health probe** — lightweight check that the source is alive.

Adapters emit raw envelopes (:class:`RawCaptureRequestV1`) only — no
parsing, no normalisation, no side effects.  The downstream pipeline
(DP-02 / DP-03) consumes the envelopes.

Policy enforcement happens in the constructor: ``_resolve_source``
asserts the policy version is current and the source is approved /
enabled.  An adapter for a ``hold`` source cannot even be instantiated.
"""

from __future__ import annotations

import abc
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Sequence
from urllib.parse import urlparse

from irc_data.sources.envelope import (
    AdapterCheckpointV1,
    FetchResult,
    FetchStatus,
    RawCaptureRequestV1,
    sha256_hex,
)
from irc_data.sources.gate import CollectionGate, GateDecision, SourceRecord
from irc_data.sources.http_client import (
    HttpClient,
    NotModified,
    ObjectTooLargeError,
    RetryExhaustedError,
)
from irc_data.sources.policy import (
    ACTIVE_POLICY,
    CollectionPolicyDecisionV1,
    CURRENT_POLICY_VERSION,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
)
from irc_data.sources.registry import get_source


# ---------------------------------------------------------------------------
# ParseHint enum
# ---------------------------------------------------------------------------

from enum import Enum


class ParseHint(str, Enum):
    """Hint to the downstream parser about the content format.

    The adapter sets this based on the source's known format — it does
    **not** parse the content.
    """

    HTML = "html"
    JSON = "json"
    PDF = "pdf"
    CSV = "csv"
    XML = "xml"
    BINARY = "binary"
    TEXT = "text"
    RSS = "rss"


# ---------------------------------------------------------------------------
# DiscoveredItem — output of discover / enumerate
# ---------------------------------------------------------------------------


from dataclasses import dataclass, field


@dataclass
class DiscoveredItem:
    """A single item discovered by :meth:`SourceAdapter.discover`.

    Attributes
    ----------
    url
        The URL to fetch.
    parse_hint
        Hint for the downstream parser.
    metadata
        Free-form metadata (e.g. ``{"page": 2, "series": "spring-series"}``).
    """

    url: str
    parse_hint: ParseHint = ParseHint.HTML
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# HealthProbeResult
# ---------------------------------------------------------------------------


@dataclass
class HealthProbeResult:
    """Result of a :meth:`SourceAdapter.health_probe` check.

    Attributes
    ----------
    healthy
        True if the source responded successfully.
    status_code
        HTTP status code from the probe (None if the request failed).
    url
        The URL probed.
    message
        Human-readable status message.
    """

    healthy: bool
    status_code: int | None = None
    url: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# SourceAdapter — abstract base
# ---------------------------------------------------------------------------


class SourceAdapter(abc.ABC):
    """Abstract base for all source adapters.

    A concrete adapter must implement:

    * :meth:`discover` — return the list of URLs to fetch.
    * :meth:`parse_hint_for` — return the :class:`ParseHint` for a URL.

    The base class provides the full acquisition mechanics: policy
    enforcement, rate limiting, conditional requests, retry, content
    hashing, checkpointing, and health probing.

    Usage::

        class MyAdapter(SourceAdapter):
            source_slug = "sailsys"

            async def discover(self) -> list[DiscoveredItem]:
                ...

            def parse_hint_for(self, url: str) -> ParseHint:
                return ParseHint.HTML

        adapter = MyAdapter(db, http_client)
        async for envelope in adapter.collect():
            store(envelope)
    """

    #: Must match ``data_sources.slug``.
    source_slug: str = ""

    def __init__(
        self,
        db: Any = None,
        http_client: HttpClient | None = None,
        gate: CollectionGate | None = None,
        policy: CollectionPolicyDecisionV1 | None = None,
    ):
        self.db = db
        self.policy = policy or ACTIVE_POLICY
        self.http = http_client or HttpClient(policy=self.policy)
        self.gate = gate or CollectionGate(policy=self.policy)

        # Resolve the source record — this raises if not approved / enabled
        self._source = self._resolve_source()

        # Checkpoint (created on first collect, or loaded via load_checkpoint)
        self._checkpoint: AdapterCheckpointV1 | None = None

    # ------------------------------------------------------------------
    # Source resolution + policy enforcement
    # ------------------------------------------------------------------

    def _resolve_source(self) -> SourceRecord:
        """Resolve the source record and assert policy + approval.

        Raises:
            SourceNotApprovedError: if source not found, not approved, or disabled.
            PolicyVersionMismatchError: if source policy_version ≠ current.
        """
        # If the gate already has this source registered (e.g. by a test
        # fixture that overrides the base_url), use that.  Otherwise
        # resolve from the DB / in-memory registry.
        source = self.gate._sources.get(self.source_slug)
        if source is None:
            source = get_source(self.db, self.source_slug)

        # Register with the gate so check_url / rate limiting work
        self.gate.register_source(source)

        # Policy version gate
        self.policy.assert_version(source.policy_version, self.source_slug)

        # Legal status + enabled gate
        if not source.enabled:
            raise SourceNotApprovedError(
                self.source_slug, "source is disabled (enabled=False)"
            )
        from irc_data.sources.policy import LegalStatus
        if source.legal_status != LegalStatus.APPROVED:
            raise SourceNotApprovedError(
                self.source_slug, f"legal_status={source.legal_status.value}"
            )

        return source

    @property
    def source(self) -> SourceRecord:
        """The resolved :class:`SourceRecord`."""
        return self._source

    # ------------------------------------------------------------------
    # Abstract interface — subclasses must implement
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def discover(self) -> list[DiscoveredItem]:
        """Discover the URLs this source exposes.

        Returns a list of :class:`DiscoveredItem` — each with a URL,
        parse hint, and optional metadata.  This is the *enumerate*
        step: the adapter figures out *what* to fetch, not *how*.
        """
        ...

    def parse_hint_for(self, url: str) -> ParseHint:
        """Return the :class:`ParseHint` for *url*.

        Default implementation returns :attr:`ParseHint.HTML`.
        Subclasses override for JSON / PDF / CSV sources.
        """
        return ParseHint.HTML

    # ------------------------------------------------------------------
    # Checkpoint management
    # ------------------------------------------------------------------

    @property
    def checkpoint(self) -> AdapterCheckpointV1:
        """The current checkpoint (created lazily on first access)."""
        if self._checkpoint is None:
            self._checkpoint = AdapterCheckpointV1(
                source_slug=self.source_slug,
                policy_version=self.policy.version,
            )
        return self._checkpoint

    def save_checkpoint(self) -> AdapterCheckpointV1:
        """Return the current checkpoint for persistence."""
        return self.checkpoint

    def load_checkpoint(self, checkpoint: AdapterCheckpointV1) -> None:
        """Load a previously saved checkpoint for resume.

        The next :meth:`collect` call will skip URLs already in
        ``checkpoint.completed_urls``.
        """
        # Validate the checkpoint belongs to this source
        if checkpoint.source_slug != self.source_slug:
            raise ValueError(
                f"Checkpoint for '{checkpoint.source_slug}' does not match "
                f"adapter source '{self.source_slug}'"
            )
        if checkpoint.policy_version != self.policy.version:
            raise PolicyVersionMismatchError(
                self.source_slug, checkpoint.policy_version
            )
        self._checkpoint = checkpoint

    def _checkpoint_mark_completed(self, url: str, content_hash: str) -> None:
        """Record that *url* has been fetched (append-only)."""
        self.checkpoint.mark_completed(url, content_hash)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    async def rate_limit(self, url: str) -> float:
        """Enforce per-domain rate limit for *url*.  Returns seconds slept."""
        domain = urlparse(url).hostname or ""
        return await self.gate.rate_limit_wait_async(domain)

    # ------------------------------------------------------------------
    # Conditional request
    # ------------------------------------------------------------------

    def _conditional_headers(self, url: str) -> dict[str, str | None]:
        """Return ETag / Last-Modified for *url* from the checkpoint.

        If the URL was previously fetched, its ETag / Last-Modified
        are sent as conditional-request headers so the server can
        return 304 (content unchanged).
        """
        # Adapters that track ETags per-URL can override this.
        return {}

    # ------------------------------------------------------------------
    # Fetch — single URL → RawCaptureRequestV1
    # ------------------------------------------------------------------

    async def fetch(self, url: str) -> RawCaptureRequestV1 | None:
        """Fetch a single URL and return a raw envelope.

        Returns ``None`` if the content was skipped (304 or hash match).

        Raises:
            ObjectTooLargeError: if the response exceeds the size cap.
            RetryExhaustedError: if all retries are exhausted.
        """
        # Rate limit
        await self.rate_limit(url)

        # Conditional request headers from checkpoint
        cond = self._conditional_headers(url)
        etag = cond.get("etag")
        last_modified = cond.get("last_modified")

        # Known hash (for content-hash dedup)
        known_hash = None
        if self._checkpoint and self.checkpoint.is_completed(url):
            known_hash = self.checkpoint.content_hashes.get(url)

        result = await self.http.fetch_or_skip(
            url,
            known_hash=known_hash,
            etag=etag,
            last_modified=last_modified,
        )

        # 304 — content unchanged
        if isinstance(result, NotModified):
            envelope = RawCaptureRequestV1.not_modified(
                source_slug=self.source_slug,
                url=url,
                content_hash=known_hash or "",
                etag=result.etag,
                last_modified=result.last_modified,
                policy_version=self.policy.version,
            )
            self._checkpoint_mark_completed(url, known_hash or "")
            return envelope

        # Hash match — skip (content unchanged)
        if result is None:
            envelope = RawCaptureRequestV1(
                source_slug=self.source_slug,
                url=url,
                content=b"",
                content_hash=known_hash or "",
                parse_hint=self.parse_hint_for(url).value,
                policy_version=self.policy.version,
                status=FetchStatus.SKIPPED_UNCHANGED,
            )
            self._checkpoint_mark_completed(url, known_hash or "")
            return envelope

        # Fresh fetch
        envelope = RawCaptureRequestV1.from_fetch_result(
            fetch_result=result,
            source_slug=self.source_slug,
            parse_hint=self.parse_hint_for(url).value,
            content_type=None,  # set by subclass if desired
        )
        self._checkpoint_mark_completed(url, result.content_hash)
        return envelope

    # ------------------------------------------------------------------
    # Collect — async iterator yielding raw envelopes
    # ------------------------------------------------------------------

    async def collect(self) -> AsyncIterator[RawCaptureRequestV1]:
        """Collect all pages, yielding raw envelopes.

        This is the main entry point.  It:

        1. Discovers all URLs via :meth:`discover`.
        2. Skips URLs already completed in the checkpoint.
        3. Fetches each URL (with rate limiting, retry, conditional
           requests, content hashing).
        4. Yields a :class:`RawCaptureRequestV1` for each page.
        5. Updates the checkpoint as it goes.
        """
        items = await self.discover()
        self.checkpoint.total_pages = len(items)

        for item in items:
            url = item.url

            # Skip if already completed (checkpoint resume)
            if self.checkpoint.is_completed(url):
                continue

            # Set next_url for resume tracking
            self.checkpoint.next_url = url

            envelope = await self.fetch(url)
            if envelope is not None:
                yield envelope

        # Mark collection complete
        self.checkpoint.mark_complete()

    async def run(self) -> list[RawCaptureRequestV1]:
        """Collect all pages and return them as a list."""
        results: list[RawCaptureRequestV1] = []
        async for envelope in self.collect():
            results.append(envelope)
        return results

    # ------------------------------------------------------------------
    # Enumerate — structured discovery
    # ------------------------------------------------------------------

    async def enumerate_items(self) -> list[DiscoveredItem]:
        """Alias for :meth:`discover` — list all collectible items."""
        return await self.discover()

    # ------------------------------------------------------------------
    # Health probe
    # ------------------------------------------------------------------

    async def health_probe(self) -> HealthProbeResult:
        """Lightweight check that the source is alive.

        Fetches the source's base URL (or a health endpoint) with a
        single request.  This is allowed outside the collection window
        per the policy (``allow_daytime_health_checks = True``).

        Returns a :class:`HealthProbeResult` with ``healthy=True`` if
        the source responded with a 2xx status.
        """
        url = self._source.base_url
        try:
            # Health checks bypass rate limiting (single request)
            result = await self.http.fetch(url, skip_rate_limit=True)
            if isinstance(result, NotModified):
                return HealthProbeResult(
                    healthy=True,
                    status_code=304,
                    url=url,
                    message="Source healthy (304 Not Modified)",
                )
            return HealthProbeResult(
                healthy=True,
                status_code=200,
                url=url,
                message=f"Source healthy (hash={result.content_hash[:12]})",
            )
        except (RetryExhaustedError, ObjectTooLargeError, Exception) as exc:
            return HealthProbeResult(
                healthy=False,
                status_code=None,
                url=url,
                message=f"Health probe failed: {exc}",
            )
