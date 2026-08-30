"""Source registry (SPEC-012 §2).

The registry is the bridge between the ``data_sources`` table (owned by
DP-01-01) and the adapter SDK.  Adapters only ever see a
:class:`DataSource` dataclass; they never talk to the DB directly.

A :class:`SourceRegistry` is a minimal protocol — ``get(slug)`` returns
a :class:`DataSource` or raises :class:`KeyError`.  The SDK ships an
in-memory implementation seeded with the 11 sources from SPEC-012 §2.2
so the reference adapter and the contract suite run with zero DB
dependency.  A DB-backed implementation (DP-01-01) can drop in behind
the same protocol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import CURRENT_POLICY_VERSION, DataSource


@runtime_checkable
class SourceRegistry(Protocol):
    """Read-only registry of approved / hold / blocked sources."""

    def get(self, slug: str) -> DataSource:  # pragma: no cover - protocol
        ...

    def all(self) -> list[DataSource]:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Seed sources (SPEC-012 §2.2 / INTERIM-POLICY.md §2)
# ---------------------------------------------------------------------------
_SEEDS: tuple[DataSource, ...] = (
    DataSource(slug="sailsys", display_name="SailSys",
               base_url="https://app.sailsys.com.au", category="results"),
    DataSource(slug="topyacht", display_name="TopYacht",
               base_url="https://www.topyacht.net.au", category="results"),
    DataSource(slug="irc-tcc", display_name="IRC TCC Listings",
               base_url="https://ircrating.org", category="ratings"),
    DataSource(slug="orc", display_name="ORC",
               base_url="https://data.orc.org", category="ratings"),
    DataSource(slug="yachtscoring", display_name="Yacht Scoring",
               base_url="https://www.yachtscoring.com", category="results"),
    DataSource(slug="manage2sail", display_name="Manage2Sail",
               base_url="https://manage2sail.com", category="results"),
    DataSource(slug="sailwave", display_name="Sailwave",
               base_url="https://www.sailwave.com", category="results"),
    DataSource(slug="sailing-news", display_name="Sailing News Feeds",
               base_url="https://example.com/sailing-news", category="news"),
    DataSource(slug="irc-certs", display_name="IRC Certificate PDFs",
               base_url="https://ircrating.org/pdfdirectory",
               category="certificates",
               contact_email="stuart@sailratings.com",
               notes="See INTERIM-POLICY.md §4 — approved interim-v0."),
    # Hold sources: discovery metadata only, zero content capture.
    DataSource(slug="clubspot", display_name="ClubSpot",
               base_url="https://clubspot.com", category="results",
               legal_status="hold",
               notes="Rights ruling pending; ToS review incomplete."),
    DataSource(slug="kwindoo", display_name="Kwindoo",
               base_url="https://kwindoo.com", category="results",
               legal_status="hold",
               notes="Rights ruling pending; ToS review incomplete."),
)


class InMemorySourceRegistry:
    """In-process registry backed by a dict.

    Thread / coroutine safe for reads; mutations (``upsert``) are used by
    tests to flip a source's approval / policy version.
    """

    def __init__(self, sources: "Iterable[DataSource] | None" = None) -> None:
        self._sources: dict[str, DataSource] = {
            s.slug: s for s in (sources or _SEEDS)
        }

    def get(self, slug: str) -> DataSource:
        try:
            return self._sources[slug]
        except KeyError as exc:
            raise KeyError(f"unknown source slug: {slug!r}") from exc

    def all(self) -> list[DataSource]:
        return list(self._sources.values())

    def upsert(self, source: DataSource) -> None:
        """Insert or replace a source record (used by tests / migrations)."""
        self._sources[source.slug] = source

    def __contains__(self, slug: object) -> bool:
        return slug in self._sources

    def __len__(self) -> int:
        return len(self._sources)


def seed_registry() -> InMemorySourceRegistry:
    """Return a fresh registry seeded with the SPEC-012 §2.2 sources."""
    return InMemorySourceRegistry()


def get_source(registry: SourceRegistry, slug: str) -> DataSource:
    """Look up a source or raise :class:`KeyError`.

    Mirrors the ``get_source(db, slug)`` helper referenced in
    SPEC-012 §4.1; the first arg is a registry (DB-backed or in-memory)
    so the SDK stays DB-agnostic.
    """
    return registry.get(slug)


# Imported here to avoid a circular import at module load time.
from collections.abc import Iterable  # noqa: E402  (placed late on purpose)
