"""Raw capture envelopes and checkpoint contracts (DP-01-03).

This module defines the **handoff / output contracts** for the source
adapter SDK:

* :class:`FetchResult` — the low-level result of a single HTTP fetch
  (URL, content bytes, SHA-256 hash, conditional-request headers).

* :class:`RawCaptureRequestV1` — the *raw envelope* that every adapter
  emits.  It wraps a :class:`FetchResult` with source metadata, a parse
  hint, and a status that tells the downstream pipeline whether the body
  was freshly fetched, unchanged (304), or skipped because the content
  hash matched the last stored artifact.

* :class:`AdapterCheckpointV1` — the *checkpoint* contract that lets an
  interrupted collection run resume from the last completed page.  It
  records completed URLs, per-URL content hashes, and the next URL to
  fetch.

All three dataclasses support JSON round-trip (``to_dict`` /
``from_dict`` / ``to_json`` / ``from_json``) so they can be persisted to
the database, passed across Temporal activity boundaries, or written to
a file for offline inspection.

Design principles
----------------

* **Adapters emit raw envelopes only.**  No parsing, no normalisation,
  no side effects.  The envelope carries everything the downstream
  pipeline needs to decide what to do with the bytes.
* **Content hashing is mandatory.**  Every fetched body is SHA-256'd
  before it leaves the adapter.  The hash is part of the contract.
* **Checkpoints are append-only.**  Each completed URL is appended to
  the checkpoint so resume is idempotent.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sha256_hex(content: bytes | str) -> str:
    """Return the SHA-256 hex digest of *content*.

    Strings are encoded as UTF-8 before hashing.
    """
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Fetch status
# ---------------------------------------------------------------------------


class FetchStatus(str, enum.Enum):
    """Status of a single raw-capture envelope.

    ``FETCHED``
        The body was downloaded successfully (HTTP 200).
    ``NOT_MODIFIED``
        The server returned 304 — content unchanged since the last
        fetch (conditional request).  The body is empty and the hash
        matches the previously stored value.
    ``SKIPPED_UNCHANGED``
        The body was downloaded but the SHA-256 hash matched the
        last stored artifact for this URL, so it was skipped.
    """

    FETCHED = "fetched"
    NOT_MODIFIED = "not_modified"
    SKIPPED_UNCHANGED = "skipped_unchanged"


# ---------------------------------------------------------------------------
# FetchResult — low-level HTTP result
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    """Low-level result of a single HTTP fetch.

    This is the building block that :class:`RawCaptureRequestV1` wraps.
    It carries the raw bytes, their SHA-256 hash, and any conditional-
    request headers (ETag / Last-Modified) the server returned.
    """

    url: str
    content: bytes
    content_hash: str  # SHA-256 hex
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: str = field(default_factory=_now_iso)
    policy_version: str = "v1.0"

    @classmethod
    def from_response(
        cls,
        url: str,
        content: bytes,
        etag: str | None = None,
        last_modified: str | None = None,
        policy_version: str = "v1.0",
    ) -> "FetchResult":
        """Build a :class:`FetchResult` from an HTTP response body."""
        return cls(
            url=url,
            content=content,
            content_hash=sha256_hex(content),
            etag=etag,
            last_modified=last_modified,
            policy_version=policy_version,
        )

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "content": self.content.hex(),  # bytes → hex for JSON
            "content_hash": self.content_hash,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "fetched_at": self.fetched_at,
            "policy_version": self.policy_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FetchResult":
        return cls(
            url=d["url"],
            content=bytes.fromhex(d["content"]),
            content_hash=d["content_hash"],
            etag=d.get("etag"),
            last_modified=d.get("last_modified"),
            fetched_at=d.get("fetched_at", _now_iso()),
            policy_version=d.get("policy_version", "v1.0"),
        )


# ---------------------------------------------------------------------------
# RawCaptureRequestV1 — the raw envelope (handoff / output contract)
# ---------------------------------------------------------------------------


@dataclass
class RawCaptureRequestV1:
    """DP-01-03 handoff contract — the raw envelope every adapter emits.

    This is what the adapter yields from ``collect()``.  It contains
    everything the downstream pipeline needs to store, hash-check, and
    eventually parse the artifact — but **no parsed data**.  Adapters
    emit raw envelopes only.

    Fields
    ------
    source_slug
        The ``data_sources.slug`` the content was collected from.
    url
        The URL the content was fetched from.
    content
        The raw response body (bytes).
    content_hash
        SHA-256 hex digest of *content*.
    content_type
        The Content-Type header from the response (e.g.
        ``text/html; charset=utf-8``).
    parse_hint
        A hint to the downstream parser (``"html"``, ``"json"``,
        ``"pdf"``, ``"csv"``, ``"binary"``).  The adapter sets this
        based on the source's known format — it does **not** parse.
    etag
        ETag header from the response (for future conditional requests).
    last_modified
        Last-Modified header from the response.
    fetched_at
        ISO-8601 timestamp of the fetch.
    policy_version
        The policy version under which the content was collected.
    status
        :class:`FetchStatus` — fetched, not_modified, or skipped_unchanged.
    """

    source_slug: str
    url: str
    content: bytes
    content_hash: str
    content_type: str | None = None
    parse_hint: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: str = field(default_factory=_now_iso)
    policy_version: str = "v1.0"
    status: FetchStatus = FetchStatus.FETCHED

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_fetch_result(
        cls,
        fetch_result: FetchResult,
        source_slug: str,
        parse_hint: str | None = None,
        content_type: str | None = None,
        status: FetchStatus = FetchStatus.FETCHED,
    ) -> "RawCaptureRequestV1":
        """Build an envelope from a :class:`FetchResult`."""
        return cls(
            source_slug=source_slug,
            url=fetch_result.url,
            content=fetch_result.content,
            content_hash=fetch_result.content_hash,
            content_type=content_type,
            parse_hint=parse_hint,
            etag=fetch_result.etag,
            last_modified=fetch_result.last_modified,
            fetched_at=fetch_result.fetched_at,
            policy_version=fetch_result.policy_version,
            status=status,
        )

    @classmethod
    def not_modified(
        cls,
        source_slug: str,
        url: str,
        content_hash: str,
        etag: str | None = None,
        last_modified: str | None = None,
        policy_version: str = "v1.0",
    ) -> "RawCaptureRequestV1":
        """Build an envelope for a 304 Not Modified response."""
        return cls(
            source_slug=source_slug,
            url=url,
            content=b"",
            content_hash=content_hash,
            content_type=None,
            parse_hint=None,
            etag=etag,
            last_modified=last_modified,
            fetched_at=_now_iso(),
            policy_version=policy_version,
            status=FetchStatus.NOT_MODIFIED,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "source_slug": self.source_slug,
            "url": self.url,
            "content": self.content.hex(),
            "content_hash": self.content_hash,
            "content_type": self.content_type,
            "parse_hint": self.parse_hint,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "fetched_at": self.fetched_at,
            "policy_version": self.policy_version,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RawCaptureRequestV1":
        return cls(
            source_slug=d["source_slug"],
            url=d["url"],
            content=bytes.fromhex(d["content"]),
            content_hash=d["content_hash"],
            content_type=d.get("content_type"),
            parse_hint=d.get("parse_hint"),
            etag=d.get("etag"),
            last_modified=d.get("last_modified"),
            fetched_at=d.get("fetched_at", _now_iso()),
            policy_version=d.get("policy_version", "v1.0"),
            status=FetchStatus(d.get("status", "fetched")),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "RawCaptureRequestV1":
        return cls.from_dict(json.loads(s))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RawCaptureRequestV1):
            return NotImplemented
        return self.to_dict() == other.to_dict()


# ---------------------------------------------------------------------------
# AdapterCheckpointV1 — resume contract (handoff / output contract)
# ---------------------------------------------------------------------------


@dataclass
class AdapterCheckpointV1:
    """DP-01-03 handoff contract — checkpoint for collection resume.

    When an adapter is interrupted mid-collection (crash, timeout,
    SIGTERM), the orchestrator persists this checkpoint and resumes
    from it on the next run.  The checkpoint records:

    * ``completed_urls`` — URLs whose content has been fetched and
      emitted as envelopes.
    * ``content_hashes`` — ``{url: sha256}`` so the adapter can skip
      unchanged content on resume (content-hash deduplication).
    * ``next_url`` — the URL to resume from (the first un-fetched
      page).  ``None`` when the collection is complete.
    * ``status`` — ``"in_progress"`` or ``"completed"``.

    Append-only semantics: :meth:`mark_completed` appends a URL to
    ``completed_urls`` and records its hash.  Resume skips any URL
    already in ``completed_urls``.
    """

    source_slug: str
    policy_version: str = "v1.0"
    completed_urls: list[str] = field(default_factory=list)
    content_hashes: dict[str, str] = field(default_factory=dict)
    next_url: str | None = None
    total_pages: int | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    status: str = "in_progress"

    # ------------------------------------------------------------------
    # Mutation helpers (append-only)
    # ------------------------------------------------------------------

    def mark_completed(self, url: str, content_hash: str) -> None:
        """Record that *url* has been fetched and emitted.

        Idempotent: calling twice with the same URL is a no-op.
        """
        if url not in self.completed_urls:
            self.completed_urls.append(url)
        self.content_hashes[url] = content_hash
        self.updated_at = _now_iso()

    def is_completed(self, url: str) -> bool:
        """Return True if *url* has already been fetched."""
        return url in self.completed_urls

    def has_hash(self, url: str, content_hash: str) -> bool:
        """Return True if *url*'s stored hash matches *content_hash*."""
        return self.content_hashes.get(url) == content_hash

    def mark_complete(self) -> None:
        """Mark the entire collection as finished."""
        self.status = "completed"
        self.next_url = None
        self.updated_at = _now_iso()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "source_slug": self.source_slug,
            "policy_version": self.policy_version,
            "completed_urls": list(self.completed_urls),
            "content_hashes": dict(self.content_hashes),
            "next_url": self.next_url,
            "total_pages": self.total_pages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AdapterCheckpointV1":
        return cls(
            source_slug=d["source_slug"],
            policy_version=d.get("policy_version", "v1.0"),
            completed_urls=list(d.get("completed_urls", [])),
            content_hashes=dict(d.get("content_hashes", {})),
            next_url=d.get("next_url"),
            total_pages=d.get("total_pages"),
            created_at=d.get("created_at", _now_iso()),
            updated_at=d.get("updated_at", _now_iso()),
            status=d.get("status", "in_progress"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "AdapterCheckpointV1":
        return cls.from_dict(json.loads(s))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AdapterCheckpointV1):
            return NotImplemented
        return self.to_dict() == other.to_dict()
