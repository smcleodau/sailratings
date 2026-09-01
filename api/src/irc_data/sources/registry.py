"""DB-backed source registry (DP-01-01 / DP-01-03).

Provides ``get_source(db, slug)`` and ``get_all_sources(db)`` that read
the ``data_sources`` table and return :class:`SourceRecord` objects the
adapter SDK and enforcement gate can consume.

When no database session is available (e.g. unit tests), the registry
falls back to the 11 in-memory seed records defined in the migration
file — the same rows the Alembic migration inserts.

Backward-compatibility aliases (``all_sources``, ``approved_sources``,
``register_source``, ``get_source_by_base_url``) are retained for
existing scrapers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from irc_data.sources.gate import SourceRecord
from irc_data.sources.policy import (
    CURRENT_POLICY_VERSION,
    LegalStatus,
)


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
     "Publicly accessible; core platform data (see INTERIM-POLICY §4)"),
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


def get_in_memory_sources() -> list[SourceRecord]:
    """Return all 11 in-memory seed sources (no DB required)."""
    merged = {**_SEED_RECORDS, **_REGISTRY_OVERLAY}
    return list(merged.values())


def get_in_memory_source(slug: str) -> SourceRecord | None:
    """Return a single in-memory seed source by slug, or None."""
    return _REGISTRY_OVERLAY.get(slug) or _SEED_RECORDS.get(slug)


# ---------------------------------------------------------------------------
# DB-backed registry
# ---------------------------------------------------------------------------


def _row_to_source(row: Any) -> SourceRecord:
    """Convert a SQLAlchemy row to a :class:`SourceRecord`."""
    # Handle both Row objects (attribute access) and dicts (key access)
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

    # quarantine_until may or may not be a column
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


def get_source(db: Any, slug: str) -> SourceRecord:
    """Resolve a :class:`SourceRecord` from the ``data_sources`` table.

    Parameters
    ----------
    db
        A SQLAlchemy connection / session.  When ``None``, falls back
        to the in-memory seed registry.
    slug
        The ``data_sources.slug`` to look up.

    Raises
    ------
    SourceNotApprovedError
        If no source with *slug* exists.
    """
    from irc_data.sources.policy import SourceNotApprovedError

    if db is None:
        src = get_in_memory_source(slug)
        if src is None:
            raise SourceNotApprovedError(slug, "No source record found")
        return src

    from sqlalchemy import text

    result = db.execute(
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


def get_all_sources(db: Any) -> list[SourceRecord]:
    """Return all :class:`SourceRecord` objects from ``data_sources``.

    When *db* is ``None``, returns the in-memory seed registry.
    """
    if db is None:
        return get_in_memory_sources()

    from sqlalchemy import text

    result = db.execute(
        text(
            "SELECT slug, display_name, base_url, category, "
            "policy_version, legal_status, enabled, robots_disallow, "
            "robots_checked_at, contact_email, notes "
            "FROM data_sources ORDER BY slug"
        ),
    )
    return [_row_to_source(row) for row in result.fetchall()]


def seed_sources(db: Any) -> int:
    """Insert seed rows into ``data_sources`` if they don't exist.

    Idempotent — uses ``ON CONFLICT DO NOTHING``.  Returns the number
    of rows inserted.
    """
    from sqlalchemy import text

    inserted = 0
    for slug, name, url, category, status, notes in _SEED_SOURCES:
        result = db.execute(
            text(
                "INSERT INTO data_sources (slug, display_name, base_url, category, "
                "policy_version, legal_status, notes, enabled) "
                "VALUES (:slug, :name, :url, :cat, 'interim-v0', :status, :notes, true) "
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
