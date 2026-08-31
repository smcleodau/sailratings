"""Pydantic schema for the governed Data Source Register (DP-01-01).

``DataSourceRecordV1`` is the handoff / output contract for every source the
platform knows about. It makes collection breadth, value, legality and health
visible by recording: owner, category, geography, access method, terms/robots
status, licensing, cadence, identifiers, format, change detection, priority
and adapter status.

Every seed entry and every row in the ``data_sources`` table can be validated
against this schema. See SPEC-012 §2 and docs/INTERIM-POLICY.md.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Controlled vocabularies
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
# Output contract
# ---------------------------------------------------------------------------


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

    # -- Collection shape -----------------------------------------------------
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

    # -- Adapter / health -----------------------------------------------------
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

    # -- Optional metadata ----------------------------------------------------
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
