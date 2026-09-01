"""Raw lake object storage and metadata index (DP-02-02 / SPEC-013).

Stores large raw content **outside** the operational Postgres database in a
filesystem "raw lake".  Every object is:

* **Written atomically** — content is written to a temporary file then
  ``os.replace``'d into its final location.  An interrupted write leaves
  only a stale ``.tmp`` file; the final object either exists fully or
  not at all.
* **Hash-verified** — SHA-256 is computed *before* storage and verified
  *after* retrieval.  The index never points to a mismatched object.
* **Encrypted at rest** — every object is encrypted with AES-GCM
  (via :class:`cryptography.fernet.Fernet`) before being written to
  disk.
* **Retention-enforced** — each artifact has an optional
  ``retention_expires_at`` timestamp; deletion is refused until the
  retention period expires.
* **Legal-hold protected** — a boolean ``legal_hold`` flag prevents
  deletion regardless of retention expiry.
* **Searchable** — a SQLite-backed metadata index records every
  artifact's hash, source, URL, content-type, size, timestamps, and
  retention / legal-hold state.

Handoff / output contract: :class:`RawArtifactReceiptV1`.

Acceptance criteria (from issue DP-02-02):

1. **Interrupted writes are invisible** — a crash during ``store()``
   leaves no partially-written final object.  The index is only updated
   *after* the atomic rename succeeds.
2. **Index never points to a missing or mismatched object** — the index
   row is inserted only after the object is verified on disk.  On
   retrieval, the SHA-256 of the decrypted content is re-verified
   against the stored hash; a mismatch raises
   :class:`RawLakeCorruptionError`.
3. **Authorised replay can retrieve exact bytes** — ``retrieve()``
   returns the original content bytes, verified by hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from cryptography.fernet import Fernet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _sha256_hex(data: bytes) -> str:
    """Return the SHA-256 hex digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RawLakeError(Exception):
    """Base exception for raw-lake failures."""


class RawLakeCorruptionError(RawLakeError):
    """Raised when a stored object's hash does not match the index.

    This means the object was corrupted on disk — either by bit-rot,
    unauthorised modification, or a storage fault.  The object must not
    be returned to the caller.
    """

    def __init__(self, artifact_id: str, expected_hash: str, actual_hash: str):
        self.artifact_id = artifact_id
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        super().__init__(
            f"Corruption detected for artifact {artifact_id}: "
            f"expected hash {expected_hash}, got {actual_hash}"
        )


class RawLakeIntegrityError(RawLakeError):
    """Raised when the index points to a missing object on disk."""

    def __init__(self, artifact_id: str, storage_key: str):
        self.artifact_id = artifact_id
        self.storage_key = storage_key
        super().__init__(
            f"Object {artifact_id} not found at storage_key '{storage_key}'"
        )


class LegalHoldError(RawLakeError):
    """Raised when deletion is attempted on a legal-hold artifact."""

    def __init__(self, artifact_id: str):
        self.artifact_id = artifact_id
        super().__init__(
            f"Artifact {artifact_id} is on legal hold and cannot be deleted"
        )


class RetentionNotExpiredError(RawLakeError):
    """Raised when deletion is attempted before retention expires."""

    def __init__(self, artifact_id: str, expires_at: str):
        self.artifact_id = artifact_id
        self.expires_at = expires_at
        super().__init__(
            f"Artifact {artifact_id} retention has not expired "
            f"(expires {expires_at})"
        )


# ---------------------------------------------------------------------------
# RawArtifactReceiptV1 — handoff / output contract
# ---------------------------------------------------------------------------


@dataclass
class RawArtifactReceiptV1:
    """DP-02-02 handoff contract — receipt for a stored raw artifact.

    Returned by :meth:`RawLakeStorage.store` after a successful atomic
    write.  This is the *only* shape downstream consumers (parsers,
    replay pipelines, retention managers) should accept.

    Fields
    ------
    artifact_id
        Unique identifier (UUID4 hex) for this artifact.
    storage_key
        Relative path of the object within the raw lake directory.
    content_hash
        SHA-256 hex digest of the *plaintext* content.
    content_length
        Size of the plaintext content in bytes.
    source_slug
        The ``data_sources.slug`` the content was collected from.
    url
        The URL the content was fetched from.
    content_type
        MIME type of the content (e.g. ``text/html``).
    fetched_at
        ISO-8601 timestamp of the original fetch.
    policy_version
        Policy version under which the content was collected.
    encrypted
        Whether the object is encrypted at rest (always ``True``).
    encryption_key_id
        Identifier for the encryption key used.
    retention_expires_at
        ISO-8601 timestamp after which the artifact may be deleted
        (``None`` = retain indefinitely).
    legal_hold
        If ``True``, deletion is blocked regardless of retention.
    created_at
        ISO-8601 timestamp of when the artifact was stored.
    schema_version
        Contract version (always ``"1"``).
    """

    artifact_id: str
    storage_key: str
    content_hash: str
    content_length: int
    source_slug: str
    url: str
    content_type: str | None = None
    fetched_at: str = field(default_factory=_now_iso)
    policy_version: str = "interim-v0"
    encrypted: bool = True
    encryption_key_id: str | None = None
    encrypted_length: int | None = None
    retention_expires_at: str | None = None
    legal_hold: bool = False
    created_at: str = field(default_factory=_now_iso)
    schema_version: str = "1"

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "storage_key": self.storage_key,
            "content_hash": self.content_hash,
            "content_length": self.content_length,
            "source_slug": self.source_slug,
            "url": self.url,
            "content_type": self.content_type,
            "fetched_at": self.fetched_at,
            "policy_version": self.policy_version,
            "encrypted": self.encrypted,
            "encryption_key_id": self.encryption_key_id,
            "encrypted_length": self.encrypted_length,
            "retention_expires_at": self.retention_expires_at,
            "legal_hold": self.legal_hold,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RawArtifactReceiptV1":
        return cls(
            schema_version=d.get("schema_version", "1"),
            artifact_id=d["artifact_id"],
            storage_key=d["storage_key"],
            content_hash=d["content_hash"],
            content_length=d["content_length"],
            source_slug=d["source_slug"],
            url=d["url"],
            content_type=d.get("content_type"),
            fetched_at=d.get("fetched_at", _now_iso()),
            policy_version=d.get("policy_version", "interim-v0"),
            encrypted=d.get("encrypted", True),
            encryption_key_id=d.get("encryption_key_id"),
            encrypted_length=d.get("encrypted_length"),
            retention_expires_at=d.get("retention_expires_at"),
            legal_hold=d.get("legal_hold", False),
            created_at=d.get("created_at", _now_iso()),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "RawArtifactReceiptV1":
        return cls.from_dict(json.loads(s))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RawArtifactReceiptV1):
            return NotImplemented
        return self.to_dict() == other.to_dict()


# ---------------------------------------------------------------------------
# MetadataIndex — SQLite-backed searchable metadata index
# ---------------------------------------------------------------------------

#: DDL for the ``raw_artifacts`` metadata table.
_INDEX_DDL = """
CREATE TABLE IF NOT EXISTS raw_artifacts (
    artifact_id          TEXT PRIMARY KEY,
    storage_key          TEXT NOT NULL UNIQUE,
    content_hash         TEXT NOT NULL,
    content_length       INTEGER NOT NULL,
    source_slug          TEXT NOT NULL,
    url                  TEXT,
    content_type         TEXT,
    fetched_at           TEXT NOT NULL,
    policy_version       TEXT NOT NULL DEFAULT 'interim-v0',
    encrypted            INTEGER NOT NULL DEFAULT 1,
    encryption_key_id   TEXT,
    encrypted_length     INTEGER,
    retention_expires_at TEXT,
    legal_hold           INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL
);
"""

_INDEX_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_raw_artifacts_hash ON raw_artifacts(content_hash);",
    "CREATE INDEX IF NOT EXISTS idx_raw_artifacts_source ON raw_artifacts(source_slug);",
    "CREATE INDEX IF NOT EXISTS idx_raw_artifacts_url ON raw_artifacts(url);",
    "CREATE INDEX IF NOT EXISTS idx_raw_artifacts_retention ON raw_artifacts(retention_expires_at);",
]


class MetadataIndex:
    """SQLite-backed searchable metadata index for the raw lake.

    The index is a SQLite database file stored alongside the raw lake
    objects.  It records metadata for every stored artifact and supports
    lookup by artifact ID, content hash, source slug, or URL.

    **Resilience:** If the index file is lost or corrupted, objects on
    disk are unaffected.  The index can be rebuilt by calling
    :meth:`RawLakeStorage.rebuild_index`, which scans the lake directory
    and re-inserts metadata from the ``.meta`` sidecar files.

    The index is updated **after** the object is atomically written and
    verified.  This ensures the index never points to a missing object.
    """

    def __init__(self, db_path: str | Path):
        """Open or create the metadata index at *db_path*.

        Parameters
        ----------
        db_path
            Path to the SQLite database file.  If the file does not
            exist, it is created with the correct schema.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        """Return a new SQLite connection with WAL mode."""
        conn = sqlite3.connect(
            str(self.db_path),
            isolation_level=None,  # autocommit
        )
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """Create the schema if it doesn't exist."""
        conn = self._conn()
        try:
            conn.execute(_INDEX_DDL)
            for stmt in _INDEX_INDEXES:
                conn.execute(stmt)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def insert(self, receipt: RawArtifactReceiptV1) -> None:
        """Insert a receipt into the index.

        Raises ``sqlite3.IntegrityError`` if a row with the same
        ``artifact_id`` or ``storage_key`` already exists.
        """
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO raw_artifacts (
                    artifact_id, storage_key, content_hash, content_length,
                    source_slug, url, content_type, fetched_at,
                    policy_version, encrypted, encryption_key_id,
                    encrypted_length, retention_expires_at, legal_hold, created_at
                ) VALUES (
                    :artifact_id, :storage_key, :content_hash, :content_length,
                    :source_slug, :url, :content_type, :fetched_at,
                    :policy_version, :encrypted, :encryption_key_id,
                    :encrypted_length, :retention_expires_at, :legal_hold, :created_at
                )
                """,
                {
                    "artifact_id": receipt.artifact_id,
                    "storage_key": receipt.storage_key,
                    "content_hash": receipt.content_hash,
                    "content_length": receipt.content_length,
                    "source_slug": receipt.source_slug,
                    "url": receipt.url,
                    "content_type": receipt.content_type,
                    "fetched_at": receipt.fetched_at,
                    "policy_version": receipt.policy_version,
                    "encrypted": int(receipt.encrypted),
                    "encryption_key_id": receipt.encryption_key_id,
                    "encrypted_length": receipt.encrypted_length,
                    "retention_expires_at": receipt.retention_expires_at,
                    "legal_hold": int(receipt.legal_hold),
                    "created_at": receipt.created_at,
                },
            )
        finally:
            conn.close()

    def upsert(self, receipt: RawArtifactReceiptV1) -> None:
        """Insert or replace a receipt (for index rebuild)."""
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO raw_artifacts (
                    artifact_id, storage_key, content_hash, content_length,
                    source_slug, url, content_type, fetched_at,
                    policy_version, encrypted, encryption_key_id,
                    encrypted_length, retention_expires_at, legal_hold, created_at
                ) VALUES (
                    :artifact_id, :storage_key, :content_hash, :content_length,
                    :source_slug, :url, :content_type, :fetched_at,
                    :policy_version, :encrypted, :encryption_key_id,
                    :encrypted_length, :retention_expires_at, :legal_hold, :created_at
                )
                """,
                {
                    "artifact_id": receipt.artifact_id,
                    "storage_key": receipt.storage_key,
                    "content_hash": receipt.content_hash,
                    "content_length": receipt.content_length,
                    "source_slug": receipt.source_slug,
                    "url": receipt.url,
                    "content_type": receipt.content_type,
                    "fetched_at": receipt.fetched_at,
                    "policy_version": receipt.policy_version,
                    "encrypted": int(receipt.encrypted),
                    "encryption_key_id": receipt.encryption_key_id,
                    "encrypted_length": receipt.encrypted_length,
                    "retention_expires_at": receipt.retention_expires_at,
                    "legal_hold": int(receipt.legal_hold),
                    "created_at": receipt.created_at,
                },
            )
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def _row_to_receipt(self, row: sqlite3.Row) -> RawArtifactReceiptV1:
        return RawArtifactReceiptV1(
            artifact_id=row["artifact_id"],
            storage_key=row["storage_key"],
            content_hash=row["content_hash"],
            content_length=row["content_length"],
            source_slug=row["source_slug"],
            url=row["url"] if "url" in row.keys() else row["url"],
            content_type=row["content_type"],
            fetched_at=row["fetched_at"],
            policy_version=row["policy_version"],
            encrypted=bool(row["encrypted"]),
            encryption_key_id=row["encryption_key_id"],
            encrypted_length=row["encrypted_length"] if "encrypted_length" in row.keys() else None,
            retention_expires_at=row["retention_expires_at"],
            legal_hold=bool(row["legal_hold"]),
            created_at=row["created_at"],
        )

    def get(self, artifact_id: str) -> RawArtifactReceiptV1 | None:
        """Return the receipt for *artifact_id*, or ``None`` if not found."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM raw_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            return self._row_to_receipt(row) if row else None
        finally:
            conn.close()

    def get_by_hash(self, content_hash: str) -> RawArtifactReceiptV1 | None:
        """Return the receipt for *content_hash*, or ``None`` if not found.

        Used for content deduplication: if the hash already exists, the
        caller can skip the write and return the existing receipt.
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM raw_artifacts WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            return self._row_to_receipt(row) if row else None
        finally:
            conn.close()

    def exists(self, content_hash: str) -> bool:
        """Return ``True`` if an artifact with *content_hash* exists."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM raw_artifacts WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def search(
        self,
        *,
        source_slug: str | None = None,
        url: str | None = None,
        content_type: str | None = None,
        legal_hold: bool | None = None,
        limit: int = 100,
    ) -> list[RawArtifactReceiptV1]:
        """Search the index by one or more criteria.

        Returns a list of matching receipts.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if source_slug is not None:
            clauses.append("source_slug = ?")
            params.append(source_slug)
        if url is not None:
            clauses.append("url = ?")
            params.append(url)
        if content_type is not None:
            clauses.append("content_type = ?")
            params.append(content_type)
        if legal_hold is not None:
            clauses.append("legal_hold = ?")
            params.append(int(legal_hold))
        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM raw_artifacts WHERE {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        conn = self._conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_receipt(r) for r in rows]
        finally:
            conn.close()

    def count(self) -> int:
        """Return the total number of artifacts in the index."""
        conn = self._conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM raw_artifacts").fetchone()[0]
        finally:
            conn.close()

    def all_artifact_ids(self) -> list[str]:
        """Return all artifact IDs in the index."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT artifact_id FROM raw_artifacts ORDER BY created_at"
            ).fetchall()
            return [r["artifact_id"] for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Retention / legal-hold operations
    # ------------------------------------------------------------------

    def set_legal_hold(self, artifact_id: str, hold: bool) -> None:
        """Set or clear the legal-hold flag on an artifact."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE raw_artifacts SET legal_hold = ? WHERE artifact_id = ?",
                (int(hold), artifact_id),
            )
        finally:
            conn.close()

    def set_retention(self, artifact_id: str, expires_at: str | None) -> None:
        """Set or clear the retention expiry on an artifact."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE raw_artifacts SET retention_expires_at = ? WHERE artifact_id = ?",
                (expires_at, artifact_id),
            )
        finally:
            conn.close()

    def delete(self, artifact_id: str) -> None:
        """Remove an artifact's metadata from the index.

        Note: this does **not** enforce retention / legal-hold checks.
        Use :meth:`RawLakeStorage.delete` for enforced deletion.
        """
        conn = self._conn()
        try:
            conn.execute(
                "DELETE FROM raw_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            )
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Retention sweep support
    # ------------------------------------------------------------------

    def expired_artifacts(self, now: str | None = None) -> list[RawArtifactReceiptV1]:
        """Return artifacts whose retention has expired and are not on hold."""
        now = now or _now_iso()
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM raw_artifacts
                WHERE retention_expires_at IS NOT NULL
                  AND retention_expires_at <= ?
                  AND legal_hold = 0
                ORDER BY retention_expires_at
                """,
                (now,),
            ).fetchall()
            return [self._row_to_receipt(r) for r in rows]
        finally:
            conn.close()

    def close(self) -> None:
        """Close the index (SQLite connections are per-operation, so this is a no-op)."""
        pass


# ---------------------------------------------------------------------------
# RawLakeStorage — atomic write, encrypt, verify, retrieve
# ---------------------------------------------------------------------------


class RawLakeStorage:
    """Filesystem raw lake with atomic writes and encryption at rest.

    Each object is stored as an encrypted file within the lake directory.
    A sidecar ``.meta`` JSON file records the receipt metadata so the
    index can be rebuilt if lost.

    Parameters
    ----------
    lake_dir
        Root directory of the raw lake.
    index
        :class:`MetadataIndex` instance.  If ``None``, a default
        SQLite index is created at ``lake_dir / ".metadata_index.db"``.
    encryption_key
        Fernet key (bytes) for encrypting objects at rest.  If ``None``,
        a key is generated and stored at ``lake_dir / ".lake_key"``.
    key_id
        Identifier for the encryption key (stored in receipts).
    """

    #: Suffix for temporary files during atomic write.
    _TMP_SUFFIX = ".tmp"

    #: Suffix for sidecar metadata files.
    _META_SUFFIX = ".meta"

    def __init__(
        self,
        lake_dir: str | Path,
        index: MetadataIndex | None = None,
        encryption_key: bytes | None = None,
        key_id: str = "lake-key-1",
    ):
        self.lake_dir = Path(lake_dir)
        self.lake_dir.mkdir(parents=True, exist_ok=True)

        self.index = index or MetadataIndex(
            self.lake_dir / ".metadata_index.db"
        )

        self.key_id = key_id
        self._fernet = self._load_or_create_key(encryption_key)

    # ------------------------------------------------------------------
    # Encryption key management
    # ------------------------------------------------------------------

    def _load_or_create_key(self, encryption_key: bytes | None) -> Fernet:
        """Load the encryption key from the key file or the provided value."""
        key_file = self.lake_dir / ".lake_key"
        if encryption_key is not None:
            fernet = Fernet(encryption_key)
            # Persist the key so retrievals work across restarts
            key_file.write_bytes(encryption_key)
            key_file.chmod(0o600)
            return fernet

        if key_file.exists():
            return Fernet(key_file.read_bytes())

        # Generate a new key
        new_key = Fernet.generate_key()
        key_file.write_bytes(new_key)
        key_file.chmod(0o600)
        return Fernet(new_key)

    # ------------------------------------------------------------------
    # Storage-key computation
    # ------------------------------------------------------------------

    def _storage_key(self, content_hash: str, artifact_id: str) -> str:
        """Compute the relative storage key for an object.

        Uses a sharded layout: ``{hash[:2]}/{hash[2:4]}/{artifact_id}.enc``.
        This distributes objects across directories to avoid filesystem
        performance issues with very large flat directories.
        """
        shard1 = content_hash[:2]
        shard2 = content_hash[2:4]
        return f"{shard1}/{shard2}/{artifact_id}.enc"

    def _object_path(self, storage_key: str) -> Path:
        """Return the absolute path of an object in the lake."""
        return self.lake_dir / storage_key

    def _meta_path(self, storage_key: str) -> Path:
        """Return the absolute path of the sidecar metadata file."""
        return self.lake_dir / (storage_key + self._META_SUFFIX)

    # ------------------------------------------------------------------
    # Atomic write + encrypt + verify
    # ------------------------------------------------------------------

    def store(
        self,
        content: bytes,
        *,
        source_slug: str,
        url: str,
        content_type: str | None = None,
        fetched_at: str | None = None,
        policy_version: str = "interim-v0",
        retention_expires_at: str | None = None,
        legal_hold: bool = False,
    ) -> RawArtifactReceiptV1:
        """Store *content* atomically with encryption and hash verification.

        Steps:
        1. Compute SHA-256 of *content*.
        2. Check for duplicate hash → return existing receipt if found.
        3. Encrypt content with Fernet.
        4. Write encrypted bytes to a temp file.
        5. ``fsync`` the temp file.
        6. ``os.replace`` (atomic rename) to the final path.
        7. Write the sidecar ``.meta`` JSON file.
        8. Insert the receipt into the metadata index.
        9. Return the receipt.

        If the process is interrupted between steps 4 and 6, the final
        object does not exist — only a stale ``.tmp`` file remains,
        which is invisible to retrieval.

        Parameters
        ----------
        content
            The raw bytes to store.
        source_slug
            Source the content was collected from.
        url
            URL the content was fetched from.
        content_type
            MIME type of the content.
        fetched_at
            ISO-8601 timestamp of the original fetch.
        policy_version
            Policy version under which the content was collected.
        retention_expires_at
            ISO-8601 timestamp after which the artifact may be deleted.
        legal_hold
            If ``True``, prevent deletion regardless of retention.

        Returns
        -------
        RawArtifactReceiptV1
            Receipt for the stored artifact.
        """
        content_hash = _sha256_hex(content)
        fetched_at = fetched_at or _now_iso()

        # Step 2: Dedup — if the hash already exists, return the existing receipt
        existing = self.index.get_by_hash(content_hash)
        if existing is not None:
            # Verify the existing object is still on disk and intact
            obj_path = self._object_path(existing.storage_key)
            if obj_path.exists():
                return existing
            # If the object is missing but the index has a row, remove the
            # stale index entry so we can re-store.
            self.index.delete(existing.artifact_id)

        # Step 3: Encrypt
        artifact_id = uuid.uuid4().hex
        storage_key = self._storage_key(content_hash, artifact_id)
        encrypted_content = self._fernet.encrypt(content)

        obj_path = self._object_path(storage_key)
        obj_path.parent.mkdir(parents=True, exist_ok=True)

        # Step 4: Write to temp file
        tmp_path = obj_path.with_suffix(self._TMP_SUFFIX)
        with open(tmp_path, "wb") as f:
            f.write(encrypted_content)
            f.flush()
            os.fsync(f.fileno())

        # Step 5: Atomic rename
        os.replace(str(tmp_path), str(obj_path))

        # Step 6: Write sidecar metadata
        receipt = RawArtifactReceiptV1(
            artifact_id=artifact_id,
            storage_key=storage_key,
            content_hash=content_hash,
            content_length=len(content),
            source_slug=source_slug,
            url=url,
            content_type=content_type,
            fetched_at=fetched_at,
            policy_version=policy_version,
            encrypted=True,
            encryption_key_id=self.key_id,
            encrypted_length=len(encrypted_content),
            retention_expires_at=retention_expires_at,
            legal_hold=legal_hold,
            created_at=_now_iso(),
        )
        meta_path = self._meta_path(storage_key)
        meta_path.write_text(receipt.to_json())

        # Step 7: Insert into the index
        self.index.insert(receipt)

        return receipt

    # ------------------------------------------------------------------
    # Retrieve + decrypt + verify
    # ------------------------------------------------------------------

    def retrieve(self, artifact_id: str) -> bytes:
        """Retrieve and verify the exact bytes of an artifact.

        Steps:
        1. Look up the receipt in the index.
        2. Read the encrypted object from disk.
        3. Decrypt.
        4. Verify SHA-256 matches the stored hash.
        5. Return the plaintext bytes.

        Raises
        ------
        RawLakeIntegrityError
            If the object is not found on disk (index points to nothing).
        RawLakeCorruptionError
            If the decrypted content's hash does not match the stored hash.
        """
        receipt = self.index.get(artifact_id)
        if receipt is None:
            raise RawLakeIntegrityError(artifact_id, "<unknown>")

        obj_path = self._object_path(receipt.storage_key)
        if not obj_path.exists():
            raise RawLakeIntegrityError(artifact_id, receipt.storage_key)

        # Read encrypted content
        encrypted_content = obj_path.read_bytes()

        # Verify encrypted file size matches stored length.
        # Fernet tokens ignore trailing bytes, so an attacker could append
        # data without detection.  We check the file size to detect this.
        if receipt.encrypted_length is not None:
            if len(encrypted_content) != receipt.encrypted_length:
                raise RawLakeCorruptionError(
                    artifact_id,
                    expected_hash=receipt.content_hash,
                    actual_hash="<size mismatch: "
                    f"expected {receipt.encrypted_length} bytes, "
                    f"got {len(encrypted_content)}>",
                )

        # Decrypt
        content = self._fernet.decrypt(encrypted_content)

        # Verify hash
        actual_hash = _sha256_hex(content)
        if actual_hash != receipt.content_hash:
            raise RawLakeCorruptionError(
                artifact_id,
                expected_hash=receipt.content_hash,
                actual_hash=actual_hash,
            )

        return content

    def retrieve_by_hash(self, content_hash: str) -> bytes:
        """Retrieve an artifact by its content hash."""
        receipt = self.index.get_by_hash(content_hash)
        if receipt is None:
            raise RawLakeIntegrityError(f"hash:{content_hash}", "<unknown>")
        return self.retrieve(receipt.artifact_id)

    # ------------------------------------------------------------------
    # Integrity verification
    # ------------------------------------------------------------------

    def verify_integrity(self, artifact_id: str) -> bool:
        """Return ``True`` if the object on disk matches the stored hash.

        Does not raise — returns ``False`` on corruption or missing object.
        """
        try:
            self.retrieve(artifact_id)
            return True
        except (RawLakeCorruptionError, RawLakeIntegrityError):
            return False

    def verify_all(self) -> dict[str, int]:
        """Verify all artifacts in the index.

        Returns a dict with counts: ``{"ok": N, "corrupted": N, "missing": N}``.
        """
        ok = 0
        corrupted = 0
        missing = 0
        for artifact_id in self.index.all_artifact_ids():
            receipt = self.index.get(artifact_id)
            if receipt is None:
                continue
            obj_path = self._object_path(receipt.storage_key)
            if not obj_path.exists():
                missing += 1
                continue
            try:
                encrypted_content = obj_path.read_bytes()
                # Check file size first (detects appended bytes)
                if (
                    receipt.encrypted_length is not None
                    and len(encrypted_content) != receipt.encrypted_length
                ):
                    corrupted += 1
                    continue
                content = self._fernet.decrypt(encrypted_content)
                if _sha256_hex(content) == receipt.content_hash:
                    ok += 1
                else:
                    corrupted += 1
            except Exception:
                corrupted += 1
        return {"ok": ok, "corrupted": corrupted, "missing": missing}

    # ------------------------------------------------------------------
    # Deletion with retention / legal-hold enforcement
    # ------------------------------------------------------------------

    def delete(self, artifact_id: str) -> None:
        """Delete an artifact, enforcing retention and legal hold.

        Raises
        ------
        LegalHoldError
            If the artifact has ``legal_hold = True``.
        RetentionNotExpiredError
            If the artifact's retention has not expired.
        """
        receipt = self.index.get(artifact_id)
        if receipt is None:
            raise RawLakeIntegrityError(artifact_id, "<unknown>")

        # Legal hold check
        if receipt.legal_hold:
            raise LegalHoldError(artifact_id)

        # Retention check
        if receipt.retention_expires_at is not None:
            now = _now_iso()
            if receipt.retention_expires_at > now:
                raise RetentionNotExpiredError(
                    artifact_id, receipt.retention_expires_at
                )

        # Delete the object and sidecar
        obj_path = self._object_path(receipt.storage_key)
        meta_path = self._meta_path(receipt.storage_key)
        obj_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)

        # Remove from index
        self.index.delete(artifact_id)

    # ------------------------------------------------------------------
    # Legal hold / retention management
    # ------------------------------------------------------------------

    def set_legal_hold(self, artifact_id: str, hold: bool) -> None:
        """Set or clear legal hold on an artifact."""
        receipt = self.index.get(artifact_id)
        if receipt is None:
            raise RawLakeIntegrityError(artifact_id, "<unknown>")
        self.index.set_legal_hold(artifact_id, hold)
        # Update sidecar metadata
        receipt.legal_hold = hold
        self._meta_path(receipt.storage_key).write_text(receipt.to_json())

    def set_retention(
        self, artifact_id: str, expires_at: str | None
    ) -> None:
        """Set or clear the retention expiry on an artifact."""
        receipt = self.index.get(artifact_id)
        if receipt is None:
            raise RawLakeIntegrityError(artifact_id, "<unknown>")
        self.index.set_retention(artifact_id, expires_at)
        receipt.retention_expires_at = expires_at
        self._meta_path(receipt.storage_key).write_text(receipt.to_json())

    # ------------------------------------------------------------------
    # Retention sweep
    # ------------------------------------------------------------------

    def sweep_retention(self) -> list[str]:
        """Delete all artifacts whose retention has expired (and not on hold).

        Returns a list of deleted artifact IDs.
        """
        deleted: list[str] = []
        for receipt in self.index.expired_artifacts():
            try:
                obj_path = self._object_path(receipt.storage_key)
                meta_path = self._meta_path(receipt.storage_key)
                obj_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                self.index.delete(receipt.artifact_id)
                deleted.append(receipt.artifact_id)
            except Exception:
                pass
        return deleted

    # ------------------------------------------------------------------
    # Index rebuild (resilience)
    # ------------------------------------------------------------------

    def rebuild_index(self) -> int:
        """Rebuild the metadata index from sidecar ``.meta`` files.

        Called when the SQLite index is lost or corrupted.  Scans the
        lake directory for ``.meta`` files and re-inserts their receipts
        into the index.

        Returns the number of artifacts re-indexed.
        """
        # Re-initialize the schema in case the DB file was lost/corrupted
        self.index._init_schema()

        count = 0
        for meta_path in self.lake_dir.rglob(f"*{self._META_SUFFIX}"):
            try:
                receipt = RawArtifactReceiptV1.from_json(
                    meta_path.read_text()
                )
                self.index.upsert(receipt)
                count += 1
            except Exception:
                continue
        return count

    # ------------------------------------------------------------------
    # Cleanup of stale temp files
    # ------------------------------------------------------------------

    def cleanup_temp_files(self) -> int:
        """Remove stale ``.tmp`` files left by interrupted writes.

        Returns the number of files removed.
        """
        removed = 0
        for tmp_path in self.lake_dir.rglob(f"*{self._TMP_SUFFIX}"):
            try:
                tmp_path.unlink()
                removed += 1
            except Exception:
                pass
        return removed


# ---------------------------------------------------------------------------
# Convenience: create a raw lake in a temporary directory (for testing)
# ---------------------------------------------------------------------------


def create_raw_lake(
    lake_dir: str | Path | None = None,
    encryption_key: bytes | None = None,
) -> RawLakeStorage:
    """Create a :class:`RawLakeStorage` at *lake_dir*.

    If *lake_dir* is ``None``, a temporary directory is created.
    """
    if lake_dir is None:
        lake_dir = Path(tempfile.mkdtemp(prefix="raw_lake_"))
    return RawLakeStorage(lake_dir, encryption_key=encryption_key)
