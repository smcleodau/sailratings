"""Provenance envelope and content-addressed raw object store (DP-02-01).

This module defines the two **handoff / output contracts** required by
DP-02-01 (SPEC-013):

* :class:`ProvenanceRefV1` — the provenance envelope.  It carries
  everything a downstream consumer needs to locate, verify, and audit
  a piece of evidence **without** the raw bytes inline:

  - **source** — the governed source slug.
  - **requested URI** — the URL the adapter asked for.
  - **resolved URI** — the final URL after redirects.
  - **retrieval time** — ISO-8601 timestamp of the fetch.
  - **policy version** — the collection policy version.
  - **headers subset** — a curated subset of HTTP response headers.
  - **status** — HTTP status code.
  - **content hash** — SHA-256 hex digest (the content address).
  - **object location** — content-addressed path to the immutable blob.
  - **adapter version** — version of the adapter that produced this.
  - **lineage** — list of upstream artifact hashes.

* :class:`RawObjectStore` — a content-addressed filesystem blob store
  that enforces the immutability and deduplication guarantees:

  - Raw objects are stored at ``<root>/<sha[:2]>/<sha[2:4]>/<sha>``.
  - ``put(content)`` writes bytes only if they do not already exist.
  - ``get(content_hash)`` reads bytes back and **verifies** the hash.
  - ``exists(content_hash)`` checks for an existing object.
  - ``object_location(content_hash)`` returns the content-addressed path.

Design principles
----------------

* **Content-addressed and immutable.**  The SHA-256 hash of the bytes
  *is* the address.  Once written, a raw object never changes.  Two
  captures of identical bytes reference the same underlying object.

* **Duplicate captures retain retrieval events.**  When the same
  content is fetched again (e.g. a page that hasn't changed), no new
  raw object is written, but a *new* :class:`ProvenanceRefV1` retrieval
  event is recorded — preserving the distinct ``retrieved_at``,
  ``requested_uri``, and ``status``.

* **Hash verification on read.**  Every ``get()`` recomputes the hash
  and raises :class:`HashMismatchError` if the stored bytes do not match
  the requested hash.  This proves byte fidelity.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HashMismatchError(Exception):
    """Raised when a raw object's stored bytes do not match its hash.

    This indicates corruption or tampering.  The store is designed so
    that this should never happen under normal operation.
    """

    def __init__(self, content_hash: str, actual_hash: str, path: str):
        self.content_hash = content_hash
        self.actual_hash = actual_hash
        self.path = path
        super().__init__(
            f"Hash mismatch for {content_hash}: stored bytes hash to "
            f"{actual_hash} at {path}"
        )


class RawObjectNotFoundError(Exception):
    """Raised when a raw object with the given hash does not exist."""

    def __init__(self, content_hash: str):
        self.content_hash = content_hash
        super().__init__(f"No raw object found for hash {content_hash}")


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
# ProvenanceRefV1 — the provenance envelope (handoff / output contract)
# ---------------------------------------------------------------------------


@dataclass
class ProvenanceRefV1:
    """DP-02-01 handoff contract — the provenance envelope.

    This is the **provenance reference** that every downstream consumer
    (parser, normalisation pipeline, audit trail) receives alongside or
    in place of raw bytes.  It carries *where* the evidence is stored,
    *how* it was obtained, and *what* it was derived from.

    The envelope is deliberately **byte-free** — the raw bytes live at
    ``object_location`` and are retrieved on demand via
    :class:`RawObjectStore.get()`.  This keeps the contract lightweight
    enough to serialise into a database row, a Temporal activity result,
    or a JSON file.

    Fields
    ------
    content_hash
        SHA-256 hex digest of the raw bytes — the content address.
    source
        The governed source slug (e.g. ``"sailsys"``).
    requested_uri
        The URL the adapter asked for (before redirects).
    resolved_uri
        The final URL after redirects / normalisation.
    retrieved_at
        ISO-8601 timestamp of the fetch.
    policy_version
        The collection policy version under which the content was
        retrieved.
    headers_subset
        A curated subset of HTTP response headers (ETag,
        Last-Modified, Content-Type, …).
    status
        HTTP status code of the response.
    object_location
        Content-addressed path to the immutable blob in the
        :class:`RawObjectStore`.
    adapter_version
        Version string of the adapter that produced this artifact.
    lineage
        List of upstream artifact hashes this artifact was derived
        from.  Empty for a fresh fetch; non-empty for a derived /
        transformed artifact.
    schema_version
        Schema version of this contract (currently ``"1"``).
    """

    content_hash: str
    source: str
    requested_uri: str = ""
    resolved_uri: str = ""
    retrieved_at: str = field(default_factory=_now_iso)
    policy_version: str = "v1.0"
    headers_subset: dict[str, str] = field(default_factory=dict)
    status: int = 200
    object_location: str = ""
    adapter_version: str = ""
    lineage: list[str] = field(default_factory=list)
    schema_version: str = "1"

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON / DB storage."""
        return {
            "schema_version": self.schema_version,
            "content_hash": self.content_hash,
            "source": self.source,
            "requested_uri": self.requested_uri,
            "resolved_uri": self.resolved_uri,
            "retrieved_at": self.retrieved_at,
            "policy_version": self.policy_version,
            "headers_subset": dict(self.headers_subset),
            "status": self.status,
            "object_location": self.object_location,
            "adapter_version": self.adapter_version,
            "lineage": list(self.lineage),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProvenanceRefV1:
        """Deserialise from a plain dict."""
        return cls(
            content_hash=d["content_hash"],
            source=d["source"],
            requested_uri=d.get("requested_uri", ""),
            resolved_uri=d.get("resolved_uri", ""),
            retrieved_at=d.get("retrieved_at", _now_iso()),
            policy_version=d.get("policy_version", "v1.0"),
            headers_subset=dict(d.get("headers_subset", {})),
            status=d.get("status", 200),
            object_location=d.get("object_location", ""),
            adapter_version=d.get("adapter_version", ""),
            lineage=list(d.get("lineage", [])),
            schema_version=d.get("schema_version", "1"),
        )

    def to_json(self) -> str:
        """Serialise to a JSON string."""
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> ProvenanceRefV1:
        """Deserialise from a JSON string."""
        return cls.from_dict(json.loads(s))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProvenanceRefV1):
            return NotImplemented
        return self.to_dict() == other.to_dict()


# ---------------------------------------------------------------------------
# RawObjectStore — content-addressed immutable blob store
# ---------------------------------------------------------------------------


class RawObjectStore:
    """Content-addressed filesystem blob store (DP-02-01).

    Raw objects are stored at a content-addressed path derived from the
    SHA-256 hash of the bytes:

        <root>/<sha[:2]>/<sha[2:4]>/<sha>

    This sharding keeps directories manageable for large stores.

    Guarantees
    ----------
    * **Content-addressed:** the hash *is* the address.  Identical
      content always maps to the same path.
    * **Immutable:** once written, a blob is never modified.  ``put()``
      is a no-op if the blob already exists (idempotent).
    * **Deduplicated:** duplicate captures of the same bytes do not
      write a second copy — the second ``put()`` returns immediately.
    * **Hash-verified:** ``get()`` recomputes the hash and raises
      :class:`HashMismatchError` if the stored bytes have been
      corrupted or tampered with.

    Usage
    -----
    ::

        store = RawObjectStore(root="/var/data/raw-objects")
        content_hash = store.put(b"<html>...</html>")
        # content_hash == sha256_hex(b"<html>...</html>")

        bytes_back = store.get(content_hash)  # verifies hash
        assert bytes_back == b"<html>...</html>"
    """

    #: Number of hex characters per path-shard level.
    _SHARD_SIZE = 2

    def __init__(self, root: str):
        """Initialise the store rooted at *root*.

        The directory is created if it does not exist.
        """
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    # ------------------------------------------------------------------
    # Path derivation
    # ------------------------------------------------------------------

    def object_path(self, content_hash: str) -> str:
        """Return the filesystem path for a content hash.

        The path is sharded by the first two pairs of hex characters::

            <root>/<sha[:2]>/<sha[2:4]>/<sha>
        """
        h = content_hash.lower()
        shard1 = h[: self._SHARD_SIZE]
        shard2 = h[self._SHARD_SIZE : self._SHARD_SIZE * 2]
        return os.path.join(self.root, shard1, shard2, h)

    def object_location(self, content_hash: str) -> str:
        """Return the content-addressed location string for a hash.

        This is the same as :meth:`object_path` but is the canonical
        ``object_location`` stored in :class:`ProvenanceRefV1`.
        """
        return self.object_path(content_hash)

    # ------------------------------------------------------------------
    # Existence check
    # ------------------------------------------------------------------

    def exists(self, content_hash: str) -> bool:
        """Return ``True`` if a raw object with this hash exists."""
        return os.path.isfile(self.object_path(content_hash))

    # ------------------------------------------------------------------
    # Write (idempotent, content-addressed)
    # ------------------------------------------------------------------

    def put(self, content: bytes | str) -> str:
        """Store *content* in the content-addressed store.

        Returns the SHA-256 hex hash of the content.

        If an object with the same hash already exists, this is a
        **no-op** — the existing bytes are left untouched (immutable)
        and the hash is returned.  This is the deduplication guarantee.

        Parameters
        ----------
        content
            The raw bytes (or a string, which is UTF-8 encoded).

        Returns
        -------
        str
            The SHA-256 hex digest of *content*.
        """
        if isinstance(content, str):
            content = content.encode("utf-8")

        content_hash = sha256_hex(content)
        path = self.object_path(content_hash)

        # Idempotent: if the object already exists, do not overwrite.
        if os.path.exists(path):
            return content_hash

        # Create shard directories
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Write atomically: write to a temp file, then rename.
        tmp_path = path + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, path)

        return content_hash

    # ------------------------------------------------------------------
    # Read (hash-verified)
    # ------------------------------------------------------------------

    def get(self, content_hash: str) -> bytes:
        """Retrieve and verify the raw bytes for *content_hash*.

        The hash is recomputed after reading and compared to
        *content_hash*.  If they do not match,
        :class:`HashMismatchError` is raised — this proves byte
        fidelity and detects corruption or tampering.

        Raises
        ------
        RawObjectNotFoundError
            If no object with this hash exists.
        HashMismatchError
            If the stored bytes do not hash to *content_hash*.
        """
        path = self.object_path(content_hash)

        if not os.path.isfile(path):
            raise RawObjectNotFoundError(content_hash)

        with open(path, "rb") as f:
            content = f.read()

        # Hash verification — the core of the byte-fidelity guarantee.
        actual_hash = sha256_hex(content)
        if actual_hash != content_hash.lower():
            raise HashMismatchError(
                content_hash=content_hash,
                actual_hash=actual_hash,
                path=path,
            )

        return content

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def byte_size(self, content_hash: str) -> int:
        """Return the size of the stored object in bytes.

        Raises :class:`RawObjectNotFoundError` if it does not exist.
        """
        path = self.object_path(content_hash)
        if not os.path.isfile(path):
            raise RawObjectNotFoundError(content_hash)
        return os.path.getsize(path)

    def count(self) -> int:
        """Return the total number of raw objects in the store."""
        count = 0
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for fname in filenames:
                # Skip temp files
                if not fname.endswith(".tmp"):
                    count += 1
        return count


# ---------------------------------------------------------------------------
# Convenience: persist an artifact end-to-end
# ---------------------------------------------------------------------------


def persist_raw_artifact(
    store: RawObjectStore,
    content: bytes | str,
    source: str,
    requested_uri: str,
    resolved_uri: str = "",
    retrieved_at: str = "",
    policy_version: str = "v1.0",
    headers_subset: dict[str, str] | None = None,
    status: int = 200,
    adapter_version: str = "",
    lineage: list[str] | None = None,
) -> tuple[str, ProvenanceRefV1]:
    """Persist raw bytes to *store* and return the provenance envelope.

    This is a convenience function that combines
    :meth:`RawObjectStore.put` with :class:`ProvenanceRefV1`
    construction in a single call.

    Parameters
    ----------
    store
        The :class:`RawObjectStore` to write to.
    content
        The raw bytes (or string).
    source
        The governed source slug.
    requested_uri
        The URL the adapter asked for.
    resolved_uri
        The final URL after redirects.  Defaults to *requested_uri*.
    retrieved_at
        ISO-8601 timestamp.  Defaults to now.
    policy_version
        The collection policy version.
    headers_subset
        A curated subset of HTTP response headers.
    status
        HTTP status code.
    adapter_version
        Version of the producing adapter.
    lineage
        List of upstream artifact hashes.

    Returns
    -------
    tuple[str, ProvenanceRefV1]
        The content hash and the provenance envelope.
    """
    content_hash = store.put(content)

    ref = ProvenanceRefV1(
        content_hash=content_hash,
        source=source,
        requested_uri=requested_uri,
        resolved_uri=resolved_uri or requested_uri,
        retrieved_at=retrieved_at or _now_iso(),
        policy_version=policy_version,
        headers_subset=headers_subset or {},
        status=status,
        object_location=store.object_location(content_hash),
        adapter_version=adapter_version,
        lineage=lineage or [],
    )

    return content_hash, ref
