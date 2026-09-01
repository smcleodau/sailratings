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
            default="interim-v0",
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
    policy_version: str = "interim-v0"
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
    policy_version: str = "interim-v0"
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
    """The persisted artifact contract (version 1).

    Adapters produce ``FetchResult`` objects which are normalised into
    ``RawArtifactV1`` before storage.  This is the *only* shape that
    downstream consumers (parsers, normalisation pipelines) should
    accept.
    """

    url: str
    source_slug: str
    content_type: str
    content_hash: str
    fetched_at: str
    policy_version: str
    content: bytes = b""
    etag: str | None = None
    last_modified: str | None = None
    status_code: int = 200
    not_modified: bool = False
    screenshot_path: str | None = None
    schema_version: str = "1"

    @classmethod
    def from_fetch_result(
        cls,
        fetch_result: FetchResult,
        source_slug: str,
        content_type: str,
    ) -> RawArtifactV1:
        """Build a ``RawArtifactV1`` from a ``FetchResult``."""
        return cls(
            url=fetch_result.url,
            source_slug=source_slug,
            content_type=content_type,
            content=fetch_result.content,
            content_hash=fetch_result.content_hash,
            fetched_at=fetch_result.fetched_at,
            policy_version=fetch_result.policy_version,
            etag=fetch_result.etag,
            last_modified=fetch_result.last_modified,
            status_code=fetch_result.status_code,
            not_modified=fetch_result.not_modified,
            screenshot_path=fetch_result.screenshot_path,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (excluding raw ``content`` bytes)."""
        return {
            "schema_version": self.schema_version,
            "url": self.url,
            "source_slug": self.source_slug,
            "content_type": self.content_type,
            "content_hash": self.content_hash,
            "fetched_at": self.fetched_at,
            "policy_version": self.policy_version,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "status_code": self.status_code,
            "not_modified": self.not_modified,
            "screenshot_path": self.screenshot_path,
        }
