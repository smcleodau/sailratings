"""Core data structures for the source framework.

Defines ``DataSource`` (the governed source record), ``FetchResult``
(the atomic output of every fetch primitive), and ``RawArtifactV1``
(the persisted handoff contract).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# DataSource
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
