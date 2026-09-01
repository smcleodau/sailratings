"""Source registry — the in-memory catalogue of governed sources.

Seeds all 11 interim-v0 approved / hold sources defined in SPEC-012 §2.2.
"""

from __future__ import annotations

from irc_data.sources.models import DataSource

# ---------------------------------------------------------------------------
# Seed entries (SPEC-012 §2.2, interim-v0)
# ---------------------------------------------------------------------------

_SEED_SOURCES: list[dict] = [
    {
        "slug": "sailsys",
        "display_name": "SailSys",
        "base_url": "https://app.sailsys.com.au",
        "category": "results",
        "legal_status": "approved",
    },
    {
        "slug": "topyacht",
        "display_name": "TopYacht",
        "base_url": "https://www.topyacht.net.au",
        "category": "results",
        "legal_status": "approved",
    },
    {
        "slug": "irc-tcc",
        "display_name": "IRC TCC Listings",
        "base_url": "https://ircrating.org",
        "category": "ratings",
        "legal_status": "approved",
    },
    {
        "slug": "orc",
        "display_name": "ORC",
        "base_url": "https://data.orc.org",
        "category": "ratings",
        "legal_status": "approved",
    },
    {
        "slug": "yachtscoring",
        "display_name": "Yacht Scoring",
        "base_url": "https://www.yachtscoring.com",
        "category": "results",
        "legal_status": "approved",
    },
    {
        "slug": "manage2sail",
        "display_name": "Manage2Sail",
        "base_url": "https://manage2sail.com",
        "category": "results",
        "legal_status": "approved",
    },
    {
        "slug": "sailwave",
        "display_name": "Sailwave",
        "base_url": "https://www.sailwave.com",
        "category": "results",
        "legal_status": "approved",
    },
    {
        "slug": "sailing-news",
        "display_name": "Sailing News Feeds",
        "base_url": "https://feeds.sailingnews.com",
        "category": "news",
        "legal_status": "approved",
    },
    {
        "slug": "irc-certs",
        "display_name": "IRC Certificate PDFs",
        "base_url": "https://ircrating.org/pdfdirectory",
        "category": "certificates",
        "legal_status": "approved",
    },
    {
        "slug": "clubspot",
        "display_name": "ClubSpot",
        "base_url": "https://clubspot.com",
        "category": "results",
        "legal_status": "hold",
    },
    {
        "slug": "kwindoo",
        "display_name": "Kwindoo",
        "base_url": "https://kwindoo.com",
        "category": "results",
        "legal_status": "hold",
    },
]


def _build_source(row: dict) -> DataSource:
    """Build a ``DataSource`` from a seed row."""
    return DataSource(
        slug=row["slug"],
        display_name=row["display_name"],
        base_url=row["base_url"],
        category=row["category"],
        policy_version="interim-v0",
        legal_status=row["legal_status"],
        adapter_class=row.get("adapter_class"),
        contact_email=row.get("contact_email"),
        notes=row.get("notes"),
    )


# Pre-built registry
_REGISTRY: dict[str, DataSource] = {
    row["slug"]: _build_source(row) for row in _SEED_SOURCES
}


def get_source(slug: str) -> DataSource:
    """Return a *copy* of the ``DataSource`` for *slug*.

    Returns a fresh copy so that callers (especially tests) can mutate
    fields like ``enabled``, ``policy_version``, or ``robots_disallow``
    without affecting the shared registry.

    Raises ``KeyError`` if the slug is not in the registry.
    """
    import copy

    return copy.deepcopy(_REGISTRY[slug])


def get_source_by_base_url(url: str) -> DataSource | None:
    """Return the ``DataSource`` whose ``base_url`` is a prefix of *url*."""
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    for src in _REGISTRY.values():
        src_host = urlparse(src.base_url).hostname or ""
        if src_host and src_host == host:
            return src
    return None


def all_sources() -> list[DataSource]:
    """Return all registered sources."""
    return list(_REGISTRY.values())


def approved_sources() -> list[DataSource]:
    """Return only sources that are ``approved`` and ``enabled``."""
    return [s for s in _REGISTRY.values() if s.is_approved()]


def register_source(source: DataSource) -> None:
    """Add or replace a source in the registry (for testing)."""
    _REGISTRY[source.slug] = source
