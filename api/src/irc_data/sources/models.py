"""Core data structures for the source framework.

Defines ``DataSource`` (the governed source record), ``FetchResult``
(the atomic output of every fetch primitive), and ``RawArtifactV1``
(the persisted handoff contract).

Also defines ``DataSourceRecordV1`` — the Pydantic schema for the governed
Data Source Register (DP-01-01).  ``DataSourceRecordV1`` is the handoff /
output contract for every source the platform knows about.  It makes
collection breadth, value, legality and health visible by recording:
owner, category, geography, access method, terms/robots status, licensing,
cadence, identifiers, format, change detection, priority and adapter status.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Controlled vocabularies (DP-01-01)
# ---------------------------------------------------------------------------

#: Legal status of a source. ``approved`` → content collection permitted.
#: ``hold`` / ``unknown`` → discovery metadata only. ``blocked`` → nothing.
LegalStatus = Literal["approved", "hold", "blocked", "unknown"]

#: Broad category of data the source provides.
Category = Literal["results", "ratings", "certificates", "news", "events", "other"]

#: How the source is accessed / collected.
AccessMethod = Literal[
    "rest_api",
    "html_scrape",
    "csv_download",
    "pdf_download",
    "rss",
    "playwright",
    "file_download",
    "manual",
    "other",
]

#: robots.txt evaluation status for the source's domain.
RobotsStatus = Literal["allowed", "disallowed", "unchecked", "no_robots"]

#: Lifecycle / health of the adapter that collects from this source.
AdapterStatus = Literal["active", "planned", "beta", "deprecated", "none"]

#: How a change on the source is detected between collection runs.
ChangeDetection = Literal["etag", "content_hash", "last_modified", "poll", "manual", "none"]

#: Allowed values for the ``legal_status`` column (mirrors the DB check
#: constraint). Kept as a plain tuple for membership tests.
LEGAL_STATUSES: tuple[str, ...] = ("approved", "hold", "blocked", "unknown")

#: Allowed categories.
CATEGORIES: tuple[str, ...] = (
    "results",
    "ratings",
    "certificates",
    "news",
    "events",
    "other",
)


# ---------------------------------------------------------------------------
# DataSourceRecordV1 — Pydantic output contract (DP-01-01)
# ---------------------------------------------------------------------------

try:
    from pydantic import BaseModel, ConfigDict, Field

    class DataSourceRecordV1(BaseModel):
        """Governed description of a single external data source.

        This is the canonical record produced by the source register and consumed
        by adapters, the source monitor and the policy enforcement layer.
        """

        model_config = ConfigDict(extra="forbid", populate_by_name=True)

        # -- Identity -----------------------------------------------------------
        slug: str = Field(..., description="Unique source slug, e.g. 'sailsys'.")
        display_name: str = Field(..., description="Human-readable source name.")
        base_url: str = Field(..., description="Canonical landing/index URL.")

        # -- Governance / legality ----------------------------------------------
        owner: str = Field(
            default="data-platform",
            description="Team or individual responsible for this source.",
        )
        category: str = Field(..., description="Broad data category.")
        geography: str = Field(
            default="GLOBAL",
            description="ISO-ish region/country code, or 'GLOBAL'.",
        )
        legal_status: str = Field(
            default="unknown",
            description="'approved' | 'hold' | 'blocked' | 'unknown'.",
        )
        policy_version: str = Field(
            default="v1.0",
            description="Policy version this source was approved under.",
        )
        terms_status: str = Field(
            default="unreviewed",
            description="Terms-of-service review status.",
        )
        robots_status: str = Field(
            default="unchecked",
            description="robots.txt status: allowed|disallowed|unchecked|no_robots.",
        )
        licensing: str = Field(
            default="unknown",
            description="Licence under which the data is published.",
        )

        # -- Collection shape -------------------------------------------------
        access_method: str = Field(
            default="html_scrape",
            description="rest_api|html_scrape|csv_download|pdf_download|rss|playwright|file_download.",
        )
        cadence: str = Field(
            default="nightly",
            description="Collection cadence, e.g. 'nightly', '30min', 'weekly'.",
        )
        format: str = Field(
            default="html",
            description="Primary payload format: html|json|csv|pdf|xml|binary.",
        )
        identifiers: list[str] = Field(
            default_factory=list,
            description="Stable identifiers the source exposes (sail_number, cert_number, …).",
        )
        change_detection: str = Field(
            default="content_hash",
            description="etag|content_hash|last_modified|poll|manual|none.",
        )
        priority: int = Field(
            default=3,
            ge=1,
            le=5,
            description="Collection priority, 1 (highest) – 5 (lowest).",
        )

        # -- Scheduling policy (OPS-01-01 / docs/SCHEDULING-POLICY.md) ------
        cadence_class: str | None = Field(
            default=None,
            description=(
                "Cadence class: daily_results | weekly_certificates | "
                "annual_identifiers | manual."
            ),
        )
        staleness_budget_hours: float | None = Field(
            default=None,
            description="Max hours since last successful run before the source is stale.",
        )
        nightly_window_start: str | None = Field(
            default=None,
            description="Nightly collection window start, HH:MM (collection policy §4.3).",
        )
        nightly_window_end: str | None = Field(
            default=None,
            description="Nightly collection window end, HH:MM (collection policy §4.3).",
        )
        retry_policy: dict[str, Any] | None = Field(
            default=None,
            description="Retry/backoff: {'max_attempts': int, 'backoff_seconds': […]}.",
        )
        cooldown_hours: float | None = Field(
            default=None,
            description="Alert / re-run cooldown in hours (design default 4).",
        )
        kill_switch_ack_hours: int | None = Field(
            default=None,
            description="Takedown / kill-switch acknowledgement window in hours (4).",
        )

        # -- Adapter / health -------------------------------------------------
        adapter_class: str | None = Field(
            default=None,
            description="Dotted Python path of the adapter implementation.",
        )
        adapter_status: str = Field(
            default="planned",
            description="active|planned|beta|deprecated|none.",
        )
        enabled: bool = Field(
            default=True,
            description="Kill switch. False → no collection from this source.",
        )

        # -- Optional metadata ------------------------------------------------
        contact_email: str | None = None
        robots_disallow: list[str] | None = Field(
            default=None,
            description="Cached robots.txt disallow paths for the domain.",
        )
        robots_checked_at: datetime | None = None
        approved_at: date | None = None
        notes: str | None = None
        extra: dict[str, Any] | None = Field(
            default=None,
            description="Free-form adapter-specific configuration.",
        )

        # ------------------------------------------------------------------
        # Convenience helpers
        # ------------------------------------------------------------------

        def is_approved(self) -> bool:
            """True iff this record permits content collection."""
            return self.enabled and self.legal_status == "approved"

        def can_discover(self) -> bool:
            """True iff discovery metadata (URL/title/date) may be logged.

            Discovery is permitted for approved, hold and unknown sources that are
            enabled. Blocked, disabled and quarantined sources may not even be
            discovered.
            """
            return self.enabled and self.legal_status in ("approved", "hold", "unknown")

        def to_row(self) -> dict[str, Any]:
            """Return a plain dict suitable for inserting into ``data_sources``."""
            return self.model_dump(mode="python", exclude_none=True)

except ImportError:
    # pydantic not available — provide a minimal stub
    DataSourceRecordV1 = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# DataSource — legacy dataclass (backward compat with DP-01-01/02 adapters)
# ---------------------------------------------------------------------------


@dataclass
class DataSource:
    """A governed source row (mirrors ``data_sources`` table).

    Every collection job MUST resolve a ``DataSource`` before fetching.
    If ``legal_status != 'approved'`` or ``enabled`` is ``False``, the
    policy layer raises ``SourceNotApprovedError``.
    """

    slug: str
    display_name: str
    base_url: str
    category: str
    adapter_class: str | None = None
    policy_version: str = "v1.0"
    legal_status: str = "approved"  # 'approved' | 'hold' | 'blocked'
    robots_checked_at: datetime | None = None
    robots_disallow: list[str] = field(default_factory=list)
    contact_email: str | None = None
    notes: str | None = None
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def is_approved(self) -> bool:
        """Return ``True`` only when the source may be actively collected."""
        return self.enabled and self.legal_status == "approved"

    def is_disallowed(self, url: str) -> bool:
        """Return ``True`` if *url* matches any cached robots disallow path."""
        from urllib.parse import urlparse

        path = urlparse(url).path or "/"
        for rule in self.robots_disallow:
            if not rule:
                continue
            # Support wildcard root "/" (disallows everything)
            if rule == "/":
                return True
            if path.startswith(rule):
                return True
        return False


# ---------------------------------------------------------------------------
# FetchResult
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    """Atomic output of every fetch primitive.

    Never return raw ``bytes`` from a primitive — always wrap in
    ``FetchResult`` so the caller receives the hash, conditional headers,
    and policy version alongside the content.
    """

    url: str
    content: bytes
    content_hash: str  # SHA-256 hex
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: str = ""  # ISO-8601
    policy_version: str = "v1.0"
    status_code: int = 200
    not_modified: bool = False
    screenshot_path: str | None = None  # render_page only

    def __post_init__(self) -> None:
        if not self.fetched_at:
            self.fetched_at = datetime.now(timezone.utc).isoformat()
        if not self.content_hash:
            self.content_hash = hashlib.sha256(self.content).hexdigest()


# ---------------------------------------------------------------------------
# RawArtifactV1 — the handoff / output contract
# ---------------------------------------------------------------------------


@dataclass
class RawArtifactV1:
    """The persisted artifact contract (version 1) — DP-02-01.

    Adapters produce ``FetchResult`` objects which are normalised into
    ``RawArtifactV1`` before storage.  This is the *only* shape that
    downstream consumers (parsers, normalisation pipelines) should
    accept.

    Raw objects are **content-addressed and immutable**.  The raw bytes
    live at ``object_location`` — a content-addressed path derived from
    ``content_hash``.  The ``content`` field is kept for convenience
    (in-process passing) but is **not** persisted to the database; the
    canonical bytes are always read back from ``object_location`` and
    verified against ``content_hash``.

    Duplicate captures of the same bytes reference the same underlying
    raw object while retaining their own :class:`ProvenanceRefV1`
    retrieval events (different ``fetched_at``, ``requested_uri``,
    etc.).

    Envelope fields (SPEC-013 / DP-02-01):

    * **source** (``source_slug``) — the governed source the content
      was collected from.
    * **requested URI** (``requested_uri``) — the URL the adapter
      asked for.
    * **resolved URI** (``resolved_uri``) — the final URL after
      redirects / normalisation.
    * **retrieval time** (``fetched_at``) — ISO-8601 timestamp.
    * **policy version** (``policy_version``) — the collection policy
      version.
    * **headers subset** (``headers_subset``) — a curated subset of
      response headers (ETag, Last-Modified, Content-Type, …).
    * **status** (``status_code``) — HTTP status code.
    * **content hash** (``content_hash``) — SHA-256 hex digest of
      the raw bytes.
    * **object location** (``object_location``) — content-addressed
      path to the immutable blob.
    * **adapter version** (``adapter_version``) — version of the
      adapter that produced this artifact.
    * **lineage** (``lineage``) — list of upstream artifact hashes
      this artifact was derived from (empty for a fresh fetch).
    """

    # -- Required identity fields -------------------------------------------
    content_hash: str          # SHA-256 hex — the content address
    source_slug: str           # governed source (e.g. "sailsys")
    fetched_at: str            # ISO-8601 retrieval timestamp
    policy_version: str        # collection policy version

    # -- URIs ---------------------------------------------------------------
    requested_uri: str = ""   # URL the adapter asked for
    resolved_uri: str = ""    # final URL after redirects

    # -- Content addressing -------------------------------------------------
    object_location: str = "" # content-addressed blob path
    byte_size: int = 0        # size of the raw bytes
    content_type: str = ""    # Content-Type header value

    # -- Provenance ---------------------------------------------------------
    adapter_version: str = ""       # version of the producing adapter
    headers_subset: dict[str, str] = field(default_factory=dict)
    lineage: list[str] = field(default_factory=list)  # upstream content_hash list

    # -- HTTP / conditional request metadata -------------------------------
    status_code: int = 200
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None

    # -- In-process convenience (NOT persisted to DB) -----------------------
    content: bytes = b""  # raw bytes — for in-process passing only
    screenshot_path: str | None = None

    # -- Schema versioning -------------------------------------------------
    schema_version: str = "1"

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_fetch_result(
        cls,
        fetch_result: FetchResult,
        source_slug: str,
        content_type: str,
        adapter_version: str = "",
        object_location: str = "",
        lineage: list[str] | None = None,
        headers_subset: dict[str, str] | None = None,
    ) -> RawArtifactV1:
        """Build a ``RawArtifactV1`` from a :class:`FetchResult`.

        Parameters
        ----------
        fetch_result
            The low-level HTTP fetch result.
        source_slug
            The governed source slug.
        content_type
            The Content-Type header value.
        adapter_version
            Version string of the adapter that produced this artifact.
        object_location
            Content-addressed path where the raw bytes are stored.
            If empty, the caller should set it after persisting to the
            :class:`~irc_data.sources.provenance.RawObjectStore`.
        lineage
            List of upstream artifact hashes this artifact was derived
            from.
        headers_subset
            A curated subset of response headers.
        """
        return cls(
            content_hash=fetch_result.content_hash,
            source_slug=source_slug,
            fetched_at=fetch_result.fetched_at,
            policy_version=fetch_result.policy_version,
            requested_uri=fetch_result.url,
            resolved_uri=fetch_result.url,
            object_location=object_location,
            byte_size=len(fetch_result.content),
            content_type=content_type,
            adapter_version=adapter_version,
            headers_subset=headers_subset or {},
            lineage=lineage or [],
            status_code=fetch_result.status_code,
            not_modified=fetch_result.not_modified,
            etag=fetch_result.etag,
            last_modified=fetch_result.last_modified,
            content=fetch_result.content,
            screenshot_path=fetch_result.screenshot_path,
        )

    # ------------------------------------------------------------------
    # Provenance projection
    # ------------------------------------------------------------------

    def to_provenance_ref(self) -> Any:
        """Return a :class:`ProvenanceRefV1` view of this artifact.

        The provenance ref carries the *envelope* metadata without the
        raw bytes — it is the handoff contract for downstream consumers
        that need to know *where* the evidence is and *how* it was
        obtained, but do not need the bytes inline.
        """
        from irc_data.sources.provenance import ProvenanceRefV1

        return ProvenanceRefV1(
            content_hash=self.content_hash,
            source=self.source_slug,
            requested_uri=self.requested_uri,
            resolved_uri=self.resolved_uri,
            retrieved_at=self.fetched_at,
            policy_version=self.policy_version,
            headers_subset=dict(self.headers_subset),
            status=self.status_code,
            object_location=self.object_location,
            adapter_version=self.adapter_version,
            lineage=list(self.lineage),
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (excluding raw ``content`` bytes)."""
        return {
            "schema_version": self.schema_version,
            "content_hash": self.content_hash,
            "source_slug": self.source_slug,
            "fetched_at": self.fetched_at,
            "policy_version": self.policy_version,
            "requested_uri": self.requested_uri,
            "resolved_uri": self.resolved_uri,
            "object_location": self.object_location,
            "byte_size": self.byte_size,
            "content_type": self.content_type,
            "adapter_version": self.adapter_version,
            "headers_subset": dict(self.headers_subset),
            "lineage": list(self.lineage),
            "status_code": self.status_code,
            "not_modified": self.not_modified,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "screenshot_path": self.screenshot_path,
        }
