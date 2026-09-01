"""Source adapter SDK — abstract base class.

All source adapters inherit from ``SourceAdapter``.  The base class
enforces policy + politeness so that individual adapter implementations
can focus on collection logic.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Protocol

from irc_data.sources.models import DataSource, FetchResult, RawArtifactV1
from irc_data.sources.policy import (
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    assert_policy_current,
    assert_source_approved,
)
from irc_data.sources.registry import get_source


class HttpClient(Protocol):
    """Minimal protocol any HTTP client must satisfy."""

    async def get(self, url: str, **kwargs):  # pragma: no cover - protocol
        ...

    async def aclose(self):  # pragma: no cover - protocol
        ...


@dataclass
class Checkpoint:
    """A checkpoint entry used for resume after interruption."""

    source_slug: str
    completed_urls: list[str] = field(default_factory=list)
    next_url: str | None = None
    page: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def is_completed(self, url: str) -> bool:
        return url in self.completed_urls

    def mark_completed(self, url: str) -> None:
        if url not in self.completed_urls:
            self.completed_urls.append(url)

    def to_json(self) -> str:
        return json.dumps(
            {
                "source_slug": self.source_slug,
                "completed_urls": self.completed_urls,
                "next_url": self.next_url,
                "page": self.page,
                "created_at": self.created_at,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> Checkpoint:
        obj = json.loads(data)
        return cls(
            source_slug=obj["source_slug"],
            completed_urls=obj.get("completed_urls", []),
            next_url=obj.get("next_url"),
            page=obj.get("page", 0),
            created_at=obj.get("created_at", ""),
        )


class SourceAdapter(ABC):
    """All source adapters inherit from this. Enforces policy + politeness."""

    source_slug: str  # must match data_sources.slug

    def __init__(self, db=None, http_client: HttpClient | None = None) -> None:
        self.db = db
        self.http = http_client
        self._source: DataSource = self._resolve_source()

    def _resolve_source(self) -> DataSource:
        """Resolve the source record and run policy checks.

        Raises ``PolicyVersionMismatchError`` or ``SourceNotApprovedError``
        if the source is not collectable.
        """
        # If a real DB is provided, we'd query it here.
        # For now we use the in-memory registry.
        if self.db is not None and hasattr(self.db, "get_source"):
            src = self.db.get_source(self.source_slug)
        else:
            src = get_source(self.source_slug)
        assert_policy_current(src)
        assert_source_approved(src)
        return src

    @property
    def source(self) -> DataSource:
        return self._source

    @abstractmethod
    async def collect(self) -> AsyncIterator[FetchResult]:
        """Yield raw ``FetchResult`` objects. No parsing, no side effects."""
        ...
        # This makes it an async generator
        yield  # type: ignore[misc]  # pragma: no cover

    async def run(self) -> list[FetchResult]:
        """Collect all pages and return as a list."""
        results: list[FetchResult] = []
        async for r in self.collect():
            results.append(r)
        return results

    def to_raw_artifact(
        self,
        fetch_result: FetchResult,
        content_type: str = "text/html",
    ) -> RawArtifactV1:
        """Convert a ``FetchResult`` to a ``RawArtifactV1`` for storage."""
        return RawArtifactV1.from_fetch_result(
            fetch_result, source_slug=self.source_slug, content_type=content_type
        )
