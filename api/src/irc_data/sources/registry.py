"""DB-backed source registry (DP-01-01 / DP-01-03 / DP-01-04).

Provides two complementary interfaces:

**DP-01-03 interface** — ``get_source(db, slug)`` and ``get_all_sources(db)``
that return :class:`~irc_data.sources.gate.SourceRecord` objects for use with
the adapter SDK and enforcement gate.  Falls back to in-memory seed records
when no DB is available.

**DP-01-04 interface** — SQLAlchemy ORM :class:`DataSource` model,
:class:`~irc_data.sources.models.DataSourceRecordV1` Pydantic schema, and
acquisition-primitive helpers (``list_sources``, ``can_collect``,
``can_discover``, ``resolve_and_assert_approved``).

The two interfaces share the same ``data_sources`` table and seed data.

Enforcement invariant (SPEC-012 §2.3):
    If ``legal_status != 'approved'`` or ``enabled = FALSE``, raise
    ``SourceNotApprovedError`` and abort.

Policy gate (SPEC-012 §3.1):
    If ``source.policy_version != CURRENT_POLICY_VERSION``, raise
    ``PolicyVersionMismatchError``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from irc_data.sources.gate import SourceRecord
from irc_data.sources.policy import (
    CURRENT_POLICY_VERSION,
    LegalStatus,
)


# ---------------------------------------------------------------------------
# Policy / enforcement helpers (DP-01-04)
# ---------------------------------------------------------------------------

#: Legal statuses that permit *content* collection.
_CONTENT_ALLOWED = frozenset({"approved"})

#: Legal statuses that permit *discovery* metadata only.
_DISCOVERY_ALLOWED = frozenset({"approved", "hold", "unknown"})


# ---------------------------------------------------------------------------
# Exceptions — re-exported for backward compat with DP-01-04 imports
# ---------------------------------------------------------------------------

# These are the same exceptions/helpers from policy.py; we re-export them
# here so that DP-01-04 code that does
#   from irc_data.sources.registry import SourceNotApprovedError
#   from irc_data.sources.registry import assert_policy_current
# continues to work.
from irc_data.sources.policy import (
    SourceNotApprovedError,
    PolicyVersionMismatchError,
    assert_policy_current,
)

# Also expose LEGAL_STATUSES from models (DP-01-04 needs it from registry too)
from irc_data.sources.models import LEGAL_STATUSES


# ---------------------------------------------------------------------------
# SQLAlchemy ORM DataSource model (DP-01-04)
# ---------------------------------------------------------------------------

try:
    from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, Text, func, select
    from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Mapped, Session, mapped_column

    from irc_data.db.models import Base
    from irc_data.sources.models import DataSourceRecordV1

    #: ``data_sources.robots_disallow`` is a native ``TEXT[]`` array on
    #: Postgres (see alembic ``0023`` / ``20260830a``) but plain ``Text``
    #: suffices for the in-memory SQLite engines used by the test-suite.
    #: ``with_variant`` gives us both.
    _RobotsDisallowType = Text().with_variant(PG_ARRAY(Text), "postgresql")

    class DataSource(Base):
        """SQLAlchemy model for the ``data_sources`` register table (DP-01-04)."""

        __tablename__ = "data_sources"
        __table_args__ = (
            Index("idx_data_sources_slug", "slug", unique=True),
            Index("idx_data_sources_legal_status", "legal_status"),
            Index("idx_data_sources_category", "category"),
            {"extend_existing": True},  # avoid conflicts with existing table
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

        # Notion register lineage (DP-01-01)
        tier: Mapped[str | None] = mapped_column(Text)
        notion_status: Mapped[str | None] = mapped_column(Text)
        notion_license: Mapped[str | None] = mapped_column(Text)

        # Scheduling policy (OPS-01-01 / docs/SCHEDULING-POLICY.md).  These
        # columns are nullable at the schema level so the ORM can map legacy
        # rows, but *active* sources must carry values — enforced by
        # ``validate_scheduling()`` below and by migration 20260903a CHECKs.
        cadence_class: Mapped[str | None] = mapped_column(Text)
        staleness_budget_hours: Mapped[float | None] = mapped_column(Float)
        nightly_window_start: Mapped[str | None] = mapped_column(Text)
        nightly_window_end: Mapped[str | None] = mapped_column(Text)
        retry_policy: Mapped[dict | None] = mapped_column(JSON)
        cooldown_hours: Mapped[float | None] = mapped_column(Float)
        kill_switch_ack_hours: Mapped[int | None] = mapped_column(Integer)

        # Adapter / health
        adapter_class: Mapped[str | None] = mapped_column(Text)
        adapter_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="planned")
        enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

        # Optional metadata
        contact_email: Mapped[str | None] = mapped_column(Text)
        robots_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
        # Native TEXT[] on Postgres (see 0023/20260830a); Text elsewhere.
        robots_disallow: Mapped[list[str] | None] = mapped_column(_RobotsDisallowType)
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
                # Scheduling policy (OPS-01-01)
                cadence_class=self.cadence_class,
                staleness_budget_hours=self.staleness_budget_hours,
                nightly_window_start=self.nightly_window_start,
                nightly_window_end=self.nightly_window_end,
                retry_policy=self.retry_policy,
                cooldown_hours=self.cooldown_hours,
                kill_switch_ack_hours=self.kill_switch_ack_hours,
                adapter_class=self.adapter_class,
                adapter_status=self.adapter_status,
                enabled=self.enabled,
                tier=self.tier,
                notion_status=self.notion_status,
                notion_license=self.notion_license,
                contact_email=self.contact_email,
                robots_checked_at=self.robots_checked_at,
                notes=self.notes,
            )

    _ORM_AVAILABLE = True

except (ImportError, Exception):
    # SQLAlchemy or db.models not available in this environment
    DataSource = None  # type: ignore[assignment,misc]
    Engine = Any  # type: ignore[assignment,misc]
    Session = None  # type: ignore[assignment,misc]
    _ORM_AVAILABLE = False


# ---------------------------------------------------------------------------
# DP-01-04 lookup helpers (ORM-backed)
# ---------------------------------------------------------------------------


def get_source_record(engine: Any, slug: str) -> Any:
    """Return the ``DataSourceRecordV1`` contract for ``slug``.

    Requires SQLAlchemy and the ``data_sources`` table to be set up.
    """
    if not _ORM_AVAILABLE:
        raise RuntimeError("SQLAlchemy ORM not available")
    with Session(engine) as session:
        src = session.execute(
            select(DataSource).where(DataSource.slug == slug)
        ).scalar_one_or_none()
    if src is None:
        raise SourceNotApprovedError(slug, "not registered")
    return src.to_record()


def list_sources(
    engine: Any,
    *,
    legal_status: str | None = None,
    category: str | None = None,
    enabled: bool | None = None,
) -> list[Any]:
    """List source records, optionally filtered.

    Requires SQLAlchemy and the ``data_sources`` table.
    """
    if not _ORM_AVAILABLE:
        return get_in_memory_sources()  # type: ignore[return-value]
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


def assert_approved(source: Any) -> None:
    """Raise ``SourceNotApprovedError`` if content collection is not allowed.

    Accepts both ORM ``DataSource`` and Pydantic ``DataSourceRecordV1``.
    A source may collect content only when it is enabled *and* its legal
    status is ``approved``.
    """
    enabled = getattr(source, "enabled", True)
    legal_status = getattr(source, "legal_status", None)
    slug = getattr(source, "slug", "<unknown>")
    status_val = legal_status.value if hasattr(legal_status, "value") else (legal_status or "")

    if not enabled:
        raise SourceNotApprovedError(slug, "disabled")
    if status_val not in _CONTENT_ALLOWED:
        raise SourceNotApprovedError(slug, f"legal_status={status_val!r}")


def resolve_and_assert_approved(engine: Any, slug: str) -> Any:
    """Resolve a source by slug and assert both policy version and approval.

    This is the single entry point every DP-01-04 collection job uses.
    Returns the validated :class:`DataSourceRecordV1` on success.
    """
    if not _ORM_AVAILABLE:
        raise RuntimeError("SQLAlchemy ORM not available")
    from sqlalchemy.orm import Session as _Session

    with _Session(engine) as session:
        src = session.execute(
            select(DataSource).where(DataSource.slug == slug)
        ).scalar_one_or_none()
    if src is None:
        raise SourceNotApprovedError(slug, "not registered")

    # Policy version check
    if src.policy_version != CURRENT_POLICY_VERSION:
        raise PolicyVersionMismatchError(slug, src.policy_version)

    # Approval check
    assert_approved(src)

    return src.to_record()


def can_collect(source: Any) -> bool:
    """True iff full content collection is permitted for this source."""
    enabled = getattr(source, "enabled", True)
    legal_status = getattr(source, "legal_status", None)
    status_val = legal_status.value if hasattr(legal_status, "value") else (legal_status or "")
    return bool(enabled) and status_val in _CONTENT_ALLOWED


def can_discover(source: Any) -> bool:
    """True iff discovery metadata (URL/title/date) may be logged.

    Approved, hold and unknown sources may be discovered. Blocked and
    disabled sources may not (SOURCE-POLICY.md §2).
    """
    enabled = getattr(source, "enabled", True)
    legal_status = getattr(source, "legal_status", None)
    status_val = legal_status.value if hasattr(legal_status, "value") else (legal_status or "")
    return bool(enabled) and status_val in _DISCOVERY_ALLOWED


# ---------------------------------------------------------------------------
# Scheduling validation (OPS-01-01)
# ---------------------------------------------------------------------------


def validate_scheduling(
    db: Any = None,
    *,
    sources: list[Any] | None = None,
    include_inactive: bool = False,
    raise_on_error: bool = False,
) -> dict[str, list[str]]:
    """Validate OPS-01-01 scheduling fields across the source register.

    Every *active* source (``enabled`` and ``legal_status = 'approved'``)
    must carry values for cadence class, cadence, staleness budget, nightly
    window, retry/backoff, cooldown and the kill-switch acknowledgement
    window (docs/SCHEDULING-POLICY.md §3).  This is the register-validation
    enforcement the OPS-01-01 acceptance criteria require.

    Parameters
    ----------
    db
        SQLAlchemy engine/connection for the ``data_sources`` register.
        When ``None`` (and *sources* is ``None``), the canonical
        :mod:`irc_data.sources.seed_data` register is validated.
    sources
        Explicit list of records to validate (overrides *db*).
    include_inactive
        When True, also validate hold/blocked/disabled sources.
    raise_on_error
        When True, raise :class:`SchedulingPolicyError` on any failure.

    Returns ``{slug: [errors...]}``; empty dict == register fully compliant.
    """
    from irc_data.sources.scheduling import (
        SCHEDULING_POLICY,
        SchedulingPolicyError,
        validate_source_scheduling,
    )

    if sources is None:
        if db is not None and _ORM_AVAILABLE:
            sources = list(list_sources(db))
        else:
            from irc_data.sources.seed_data import SEED_SOURCES

            sources = list(SEED_SOURCES)

    failures: dict[str, list[str]] = {}
    for source in sources:
        errors = validate_source_scheduling(
            source, require_when_inactive=include_inactive
        )
        if errors:
            slug = getattr(source, "slug", None) or (
                source.get("slug") if isinstance(source, dict) else "<unknown>"
            )
            failures[str(slug)] = errors

    if failures and raise_on_error:
        raise SchedulingPolicyError(
            err for errs in failures.values() for err in errs
        )
    return failures


# ---------------------------------------------------------------------------
# Seed sources (mirrors alembic/versions/0023_data_sources_and_policy.py)
# ---------------------------------------------------------------------------

_SEED_SOURCES: list[tuple[str, str, str, str, str, str]] = [
    ("sailsys", "SailSys", "https://app.sailsys.com.au", "results", "approved",
     "Australian race management; publicly published results"),
    ("topyacht", "TopYacht", "https://www.topyacht.net.au", "results", "approved",
     "Australian race management; publicly published results"),
    ("irc-tcc", "IRC TCC Listings", "https://ircrating.org", "ratings", "approved",
     "Published for racing administration; CSV download"),
    ("orc", "ORC", "https://data.orc.org", "ratings", "approved",
     "Published for racing administration; JSON API"),
    ("yachtscoring", "Yacht Scoring", "https://www.yachtscoring.com", "results", "approved",
     "US/international race results; publicly published"),
    ("manage2sail", "Manage2Sail", "https://manage2sail.com", "results", "approved",
     "European race management; publicly published results"),
    ("sailwave", "Sailwave", "https://www.sailwave.com", "results", "approved",
     "Results files publicly linked from club sites"),
    ("sailing-news", "Sailing News Feeds", "https://example.com/news", "news", "approved",
     "RSS/Atom feeds; explicitly published for syndication"),
    ("irc-certs", "IRC Certificate PDFs", "https://ircrating.org/pdfdirectory",
     "certificates", "approved",
     "Publicly accessible; core platform data (see SOURCE-POLICY.md §6)"),
    ("clubspot", "ClubSpot", "https://clubspot.com", "results", "hold",
     "Rights ruling pending; ToS review incomplete"),
    ("kwindoo", "Kwindoo", "https://www.kwindoo.com", "results", "hold",
     "Rights ruling pending; ToS review incomplete"),
]

#: Number of seed sources (used by tests to verify the registry).
SEED_COUNT = len(_SEED_SOURCES)

#: Slugs with ``legal_status = 'hold'``.
HOLD_SOURCES = [s[0] for s in _SEED_SOURCES if s[4] == "hold"]


def _seed_to_source(row: tuple[str, str, str, str, str, str]) -> SourceRecord:
    """Convert a seed tuple to a :class:`SourceRecord`."""
    slug, name, url, category, status, notes = row
    return SourceRecord(
        slug=slug,
        display_name=name,
        base_url=url,
        category=category,
        policy_version=CURRENT_POLICY_VERSION,
        legal_status=LegalStatus(status),
        enabled=True,
        robots_disallow=[],
        notes=notes,
    )


# ---------------------------------------------------------------------------
# In-memory registry (fallback when no DB)
# ---------------------------------------------------------------------------

_SEED_RECORDS: dict[str, SourceRecord] = {
    s[0]: _seed_to_source(s) for s in _SEED_SOURCES
}

# Mutable overlay — tests can inject sources via register_source()
_REGISTRY_OVERLAY: dict[str, SourceRecord] = {}


def _copy_source(src: SourceRecord | None) -> SourceRecord | None:
    """Return a defensive copy of a :class:`SourceRecord`.

    Seed records are shared, mutable dataclass instances.  Tests (and
    callers) mutate ``enabled``, ``robots_disallow`` and ``policy_version``
    to exercise enforcement; returning a copy prevents that mutation from
    leaking into the shared registry and polluting subsequent lookups.
    """
    if src is None:
        return None
    import copy

    return copy.copy(src)


def get_in_memory_sources() -> list[SourceRecord]:
    """Return all 11 in-memory seed sources (no DB required)."""
    merged = {**_SEED_RECORDS, **_REGISTRY_OVERLAY}
    return [_copy_source(s) for s in merged.values()]


def get_in_memory_source(slug: str) -> SourceRecord | None:
    """Return a single in-memory seed source by slug, or None.

    Returns a defensive copy so callers may mutate the record without
    polluting the shared registry.
    """
    return _copy_source(_REGISTRY_OVERLAY.get(slug) or _SEED_RECORDS.get(slug))


# ---------------------------------------------------------------------------
# DB-backed registry (DP-01-03 interface)
# ---------------------------------------------------------------------------


def _row_to_source(row: Any) -> SourceRecord:
    """Convert a SQLAlchemy row to a :class:`SourceRecord`."""
    if hasattr(row, "_mapping"):
        m = row._mapping
    elif isinstance(row, dict):
        m = row
    else:
        m = {k: getattr(row, k) for k in [
            "slug", "display_name", "base_url", "category",
            "policy_version", "legal_status", "enabled",
            "robots_disallow", "robots_checked_at", "contact_email",
            "notes",
        ] if hasattr(row, k)}

    quarantine = m.get("quarantine_until") if isinstance(m, dict) else getattr(row, "quarantine_until", None)

    return SourceRecord(
        slug=m["slug"],
        display_name=m["display_name"],
        base_url=m["base_url"],
        category=m["category"],
        policy_version=m.get("policy_version", CURRENT_POLICY_VERSION),
        legal_status=LegalStatus(m.get("legal_status", "approved")),
        enabled=bool(m.get("enabled", True)),
        robots_disallow=list(m.get("robots_disallow") or []),
        robots_checked_at=m.get("robots_checked_at"),
        contact_email=m.get("contact_email"),
        notes=m.get("notes"),
        quarantine_until=quarantine,
    )


def get_source(db: Any = None, slug: str | None = None) -> SourceRecord:
    """Resolve a :class:`SourceRecord` from the ``data_sources`` table.

    Accepts either ``get_source(db, slug)`` or the convenience form
    ``get_source(slug)`` (db defaults to ``None`` → in-memory registry).
    When *db* is ``None``, falls back to the in-memory seed registry.
    Raises :class:`SourceNotApprovedError` if no source found.

    Accepts both ``get_source(db, slug)`` and the legacy single-argument
    form ``get_source(slug)``.
    """
    # Convenience form: get_source("sailsys") — first arg is the slug.
    if slug is None and isinstance(db, str):
        db, slug = None, db
    if slug is None:
        raise TypeError("get_source() missing required argument: 'slug'")
    if db is None:
        src = get_in_memory_source(slug)
        if src is None:
            raise SourceNotApprovedError(slug, "No source record found")
        return src

    from sqlalchemy import text

    with _connect(db) as conn:
        result = conn.execute(
            text(
                "SELECT slug, display_name, base_url, category, "
                "policy_version, legal_status, enabled, robots_disallow, "
                "robots_checked_at, contact_email, notes "
                "FROM data_sources WHERE slug = :slug"
            ),
            {"slug": slug},
        )
        row = result.fetchone()
    if row is None:
        raise SourceNotApprovedError(slug, "No source record found in data_sources")
    return _row_to_source(row)


def _connect(db: Any):
    """Return a connection context for *db*.

    Accepts either a SQLAlchemy ``Engine`` (calls ``.connect()``) or an
    existing ``Connection`` (returned as-is, not closed by the caller via
    a no-op context).  This lets the registry work with both styles.
    """
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        # Engine → open + close a connection; Connection → use directly.
        if hasattr(db, "connect") and not hasattr(db, "execute"):
            conn = db.connect()
            try:
                yield conn
            finally:
                conn.close()
        else:
            yield db

    return _ctx()


def get_all_sources(db: Any) -> list[SourceRecord]:
    """Return all :class:`SourceRecord` objects from ``data_sources``.

    When *db* is ``None``, returns the in-memory seed registry.
    """
    if db is None:
        return get_in_memory_sources()

    from sqlalchemy import text

    with _connect(db) as conn:
        result = conn.execute(
            text(
                "SELECT slug, display_name, base_url, category, "
                "policy_version, legal_status, enabled, robots_disallow, "
                "robots_checked_at, contact_email, notes "
                "FROM data_sources ORDER BY slug"
            ),
        )
        return [_row_to_source(row) for row in result.fetchall()]


def seed_sources(
    db: Any,
    seeds: list | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Seed the ``data_sources`` table.

    By default (or when *seeds* is a list of ``DataSourceRecordV1``), the
    full 30-source seed set from :mod:`irc_data.sources.seed_data` is
    upserted via the ORM — this is the DP-01-01 / DP-01-04 path and is
    idempotent (re-running updates in place, so the row count returned is
    the total number of seeded records).

    When *seeds* is ``None``, the canonical 30-entry ``SEED_SOURCES``
    register (DP-01-01) is used.

    Returns the total row count in ``data_sources`` after seeding
    (idempotent upsert — re-seeding does not duplicate rows).
    """
    if seeds is None and _ORM_AVAILABLE:
        # Default to the canonical 30-entry register.
        from irc_data.sources.seed_data import SEED_SOURCES

        seeds = SEED_SOURCES

    if seeds is not None and _ORM_AVAILABLE:
        # DP-01-04 path: ORM upsert with DataSourceRecordV1 records
        now = now or datetime.now(timezone.utc)
        with Session(db) as session:
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
                    # Scheduling policy (OPS-01-01)
                    "cadence_class": record.cadence_class,
                    "staleness_budget_hours": record.staleness_budget_hours,
                    "nightly_window_start": record.nightly_window_start,
                    "nightly_window_end": record.nightly_window_end,
                    "retry_policy": record.retry_policy,
                    "cooldown_hours": record.cooldown_hours,
                    "kill_switch_ack_hours": record.kill_switch_ack_hours,
                    "adapter_class": record.adapter_class,
                    "adapter_status": record.adapter_status,
                    "enabled": record.enabled,
                    "tier": record.tier,
                    "notion_status": record.notion_status,
                    "notion_license": record.notion_license,
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

    # DP-01-03 path: simple ON CONFLICT DO NOTHING INSERT
    from sqlalchemy import text

    inserted = 0
    with db.begin() as conn:
        for slug, name, url, category, status, notes in _SEED_SOURCES:
            result = conn.execute(
                text(
                    "INSERT INTO data_sources (slug, display_name, base_url, category, "
                    "policy_version, legal_status, notes, enabled) "
                    f"VALUES (:slug, :name, :url, :cat, '{CURRENT_POLICY_VERSION}', :status, :notes, true) "
                    "ON CONFLICT (slug) DO NOTHING"
                ),
                {"slug": slug, "name": name, "url": url, "cat": category,
                 "status": status, "notes": notes},
            )
            inserted += result.rowcount
    return inserted


# ---------------------------------------------------------------------------
# Backward-compatibility aliases (for existing scrapers / tests)
# ---------------------------------------------------------------------------


def all_sources() -> list[SourceRecord]:
    """Return all registered sources (in-memory)."""
    return get_in_memory_sources()


def approved_sources() -> list[SourceRecord]:
    """Return only sources that are ``approved`` and ``enabled``."""
    return [s for s in get_in_memory_sources()
            if s.legal_status == LegalStatus.APPROVED and s.enabled]


def register_source(source: SourceRecord) -> None:
    """Add or replace a source in the in-memory overlay (for testing)."""
    _REGISTRY_OVERLAY[source.slug] = source


def get_source_by_base_url(url: str) -> SourceRecord | None:
    """Return the source whose ``base_url`` hostname matches *url*."""
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    for src in get_in_memory_sources():
        src_host = urlparse(src.base_url).hostname or ""
        if src_host and src_host == host:
            return src
    return None


__all__ = [
    # Policy constants
    "CURRENT_POLICY_VERSION",
    "LEGAL_STATUSES",
    # Exceptions (re-exported for DP-01-04 compat)
    "SourceNotApprovedError",
    "PolicyVersionMismatchError",
    "assert_policy_current",
    # ORM model (DP-01-04)
    "DataSource",
    # DP-01-03 SourceRecord functions
    "get_source",
    "get_all_sources",
    "get_source_record",
    "get_in_memory_source",
    "get_in_memory_sources",
    "seed_sources",
    # DP-01-04 acquisition primitives
    "list_sources",
    "assert_approved",
    "resolve_and_assert_approved",
    "can_collect",
    "can_discover",
    # OPS-01-01 scheduling validation
    "validate_scheduling",
    # Seed constants
    "SEED_COUNT",
    "HOLD_SOURCES",
    # Backward compat
    "all_sources",
    "approved_sources",
    "register_source",
    "get_source_by_base_url",
]
