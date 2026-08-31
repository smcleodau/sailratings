"""Source register: governance, lookup and policy enforcement.

Implements the DP-01-01 register from SPEC-012 §2. Every collection job must
resolve a ``data_sources`` row before fetching and must assert both the policy
version and the legal approval status. Unknown legal status blocks collection
beyond discovery metadata.

Enforcement invariant (SPEC-012 §2.3):
    If ``legal_status != 'approved'`` or ``enabled = FALSE``, raise
    ``SourceNotApprovedError`` and abort. No fallback, no silent skip.

Policy gate (SPEC-012 §3.1):
    If ``source.policy_version != CURRENT_POLICY_VERSION``, raise
    ``PolicyVersionMismatchError``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, Text, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, Session, mapped_column

from irc_data.db.models import Base
from irc_data.sources.models import LEGAL_STATUSES, DataSourceRecordV1

#: The single source of truth for the active collection policy version.
#: Bump this (and INTERIM-POLICY.md §10) whenever the policy is revised.
CURRENT_POLICY_VERSION = "interim-v0"

#: Legal statuses that permit *content* collection. Anything else blocks.
_CONTENT_ALLOWED = frozenset({"approved"})

#: Legal statuses that permit *discovery* metadata only (no content capture).
_DISCOVERY_ALLOWED = frozenset({"approved", "hold", "unknown"})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SourceNotApprovedError(RuntimeError):
    """Raised when a collection job references a source that is not approved.

    This blocks all content collection for ``hold``, ``unknown``, ``blocked``
    and disabled sources. Discovery metadata is gated separately via
    :func:`can_discover`.
    """

    def __init__(self, slug: str, reason: str = "not approved"):
        self.slug = slug
        self.reason = reason
        super().__init__(f"source {slug!r} is {reason}; content collection is blocked")


class PolicyVersionMismatchError(RuntimeError):
    """Raised when a source's policy_version does not match the current policy."""

    def __init__(self, slug: str, source_version: str, current_version: str):
        self.slug = slug
        self.source_version = source_version
        self.current_version = current_version
        super().__init__(
            f"{slug} references policy {source_version!r}, "
            f"current is {current_version!r}"
        )


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


class DataSource(Base):
    """SQLAlchemy model for the ``data_sources`` register table."""

    __tablename__ = "data_sources"
    __table_args__ = (
        Index("idx_data_sources_slug", "slug", unique=True),
        Index("idx_data_sources_legal_status", "legal_status"),
        Index("idx_data_sources_category", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)

    # Governance / legality
    owner: Mapped[str] = mapped_column(Text, nullable=False, server_default="data-platform")
    geography: Mapped[str] = mapped_column(Text, nullable=False, server_default="GLOBAL")
    legal_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    terms_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="unreviewed")
    robots_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="unchecked")
    licensing: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")

    # Collection shape
    access_method: Mapped[str] = mapped_column(Text, nullable=False, server_default="html_scrape")
    cadence: Mapped[str] = mapped_column(Text, nullable=False, server_default="nightly")
    format: Mapped[str] = mapped_column(Text, nullable=False, server_default="html")
    change_detection: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="content_hash"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")

    # Adapter / health
    adapter_class: Mapped[str | None] = mapped_column(Text)
    adapter_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="planned")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    # Optional metadata
    contact_email: Mapped[str | None] = mapped_column(Text)
    robots_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def to_record(self) -> DataSourceRecordV1:
        """Project this ORM row into the ``DataSourceRecordV1`` contract."""
        return DataSourceRecordV1(
            slug=self.slug,
            display_name=self.display_name,
            base_url=self.base_url,
            owner=self.owner,
            category=self.category,
            geography=self.geography,
            legal_status=self.legal_status,
            policy_version=self.policy_version,
            terms_status=self.terms_status,
            robots_status=self.robots_status,
            licensing=self.licensing,
            access_method=self.access_method,
            cadence=self.cadence,
            format=self.format,
            change_detection=self.change_detection,
            priority=self.priority,
            adapter_class=self.adapter_class,
            adapter_status=self.adapter_status,
            enabled=self.enabled,
            contact_email=self.contact_email,
            robots_checked_at=self.robots_checked_at,
            notes=self.notes,
        )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def get_source(engine: Engine, slug: str) -> DataSource:
    """Return the ``DataSource`` ORM row for ``slug``.

    Raises :class:`SourceNotApprovedError` if no such source exists — an
    unknown slug is treated as blocked (SPEC-012 §2.3, INTERIM-POLICY §2.3).
    """
    with Session(engine) as session:
        src = session.execute(
            select(DataSource).where(DataSource.slug == slug)
        ).scalar_one_or_none()
    if src is None:
        raise SourceNotApprovedError(slug, reason="not registered")
    return src


def get_source_record(engine: Engine, slug: str) -> DataSourceRecordV1:
    """Return the ``DataSourceRecordV1`` contract for ``slug``."""
    return get_source(engine, slug).to_record()


def list_sources(
    engine: Engine,
    *,
    legal_status: str | None = None,
    category: str | None = None,
    enabled: bool | None = None,
) -> list[DataSourceRecordV1]:
    """List source records, optionally filtered."""
    stmt = select(DataSource).order_by(DataSource.slug)
    if legal_status is not None:
        stmt = stmt.where(DataSource.legal_status == legal_status)
    if category is not None:
        stmt = stmt.where(DataSource.category == category)
    if enabled is not None:
        stmt = stmt.where(DataSource.enabled == enabled)
    with Session(engine) as session:
        rows = session.execute(stmt).scalars().all()
    return [row.to_record() for row in rows]


# ---------------------------------------------------------------------------
# Policy enforcement
# ---------------------------------------------------------------------------


def assert_policy_current(source: DataSource | DataSourceRecordV1) -> None:
    """Raise if the source's policy version is not the current one.

    Every adapter calls this before the first fetch (SPEC-012 §3.1).
    """
    if source.policy_version != CURRENT_POLICY_VERSION:
        raise PolicyVersionMismatchError(
            source.slug, source.policy_version, CURRENT_POLICY_VERSION
        )


def assert_approved(source: DataSource | DataSourceRecordV1) -> None:
    """Raise ``SourceNotApprovedError`` if content collection is not allowed.

    A source may collect content only when it is enabled *and* its legal
    status is ``approved``. Hold, unknown and blocked statuses all raise, as
    does the kill switch (``enabled = False``).
    """
    if not source.enabled:
        raise SourceNotApprovedError(source.slug, reason="disabled")
    if source.legal_status not in _CONTENT_ALLOWED:
        raise SourceNotApprovedError(
            source.slug, reason=f"legal_status={source.legal_status!r}"
        )


def resolve_and_assert_approved(
    engine: Engine, slug: str
) -> DataSourceRecordV1:
    """Resolve a source by slug and assert both policy version and approval.

    This is the single entry point every collection job uses. It enforces, in
    order: existence, policy version, and legal approval. Unknown legal status
    blocks content collection beyond discovery metadata.

    Returns the validated :class:`DataSourceRecordV1` on success.
    """
    source = get_source(engine, slug)
    assert_policy_current(source)
    assert_approved(source)
    return source.to_record()


# ---------------------------------------------------------------------------
# Discovery gating
# ---------------------------------------------------------------------------


def can_collect(source: DataSource | DataSourceRecordV1) -> bool:
    """True iff full content collection is permitted for this source."""
    return bool(source.enabled) and source.legal_status in _CONTENT_ALLOWED


def can_discover(source: DataSource | DataSourceRecordV1) -> bool:
    """True iff discovery metadata (URL/title/date) may be logged.

    Approved, hold and unknown sources may be discovered. Blocked and
    disabled sources may not — no HTTP fetches at all (INTERIM-POLICY §2.2).
    """
    return bool(source.enabled) and source.legal_status in _DISCOVERY_ALLOWED


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def seed_sources(
    engine: Engine,
    seeds: list[DataSourceRecordV1] | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Idempotently seed the ``data_sources`` table from ``SEED_SOURCES``.

    Returns the number of rows present after seeding (which equals the number
    of seed entries). Existing rows are updated in place so re-running the
    seed is a no-op. Does not touch rows that are not in the seed set.
    """
    from irc_data.sources.seed_data import SEED_SOURCES

    seeds = seeds if seeds is not None else SEED_SOURCES
    now = now or datetime.now(timezone.utc)

    with Session(engine) as session:
        for record in seeds:
            existing = session.execute(
                select(DataSource).where(DataSource.slug == record.slug)
            ).scalar_one_or_none()
            values: dict[str, Any] = {
                "slug": record.slug,
                "display_name": record.display_name,
                "base_url": record.base_url,
                "category": record.category,
                "owner": record.owner,
                "geography": record.geography,
                "legal_status": record.legal_status,
                "policy_version": record.policy_version,
                "terms_status": record.terms_status,
                "robots_status": record.robots_status,
                "licensing": record.licensing,
                "access_method": record.access_method,
                "cadence": record.cadence,
                "format": record.format,
                "change_detection": record.change_detection,
                "priority": record.priority,
                "adapter_class": record.adapter_class,
                "adapter_status": record.adapter_status,
                "enabled": record.enabled,
                "contact_email": record.contact_email,
                "notes": record.notes,
                "updated_at": now,
            }
            if existing is None:
                values["created_at"] = now
                session.add(DataSource(**values))
            else:
                for key, value in values.items():
                    setattr(existing, key, value)
        session.commit()

        count = session.execute(select(func.count(DataSource.id))).scalar_one()
    return int(count)


__all__ = [
    "CURRENT_POLICY_VERSION",
    "DataSource",
    "LEGAL_STATUSES",
    "PolicyVersionMismatchError",
    "SourceNotApprovedError",
    "assert_approved",
    "assert_policy_current",
    "can_collect",
    "can_discover",
    "get_source",
    "get_source_record",
    "list_sources",
    "resolve_and_assert_approved",
    "seed_sources",
]
