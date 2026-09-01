"""Fault-injection tests for the raw lake object storage (DP-02-02).

Covers the four fault-injection scenarios mandated by the issue:

1. **Partial upload** — an interrupted write leaves no visible final
   object; the index does not point to a missing object.
2. **Duplicate hash** — storing the same content twice returns the
   same receipt without re-writing.
3. **Index outage** — if the SQLite index is lost/corrupted, objects on
   disk are still intact and the index can be rebuilt from sidecar
   metadata.
4. **Corruption** — if an object on disk is modified, retrieval
   detects the hash mismatch and raises ``RawLakeCorruptionError``.

Plus additional tests for:
* Authorised replay retrieves exact bytes.
* Retention enforcement prevents premature deletion.
* Legal hold prevents deletion regardless of retention.
* Encryption at rest — objects on disk are not plaintext.
* Atomic write visibility — no partial objects visible during write.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from irc_data.sources.raw_lake import (
    LegalHoldError,
    MetadataIndex,
    RawArtifactReceiptV1,
    RawLakeCorruptionError,
    RawLakeIntegrityError,
    RawLakeStorage,
    RetentionNotExpiredError,
    create_raw_lake,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lake(tmp_path):
    """Create a fresh raw lake in a temporary directory."""
    lake_dir = tmp_path / "lake"
    lake_dir.mkdir()
    storage = RawLakeStorage(lake_dir, encryption_key=Fernet.generate_key())
    yield storage
    storage.cleanup_temp_files()


@pytest.fixture
def sample_content():
    return b"<html><body><h1>Race Results 2026</h1></body></html>"


@pytest.fixture
def sample_receipt(lake, sample_content):
    """Store sample content and return the receipt."""
    return lake.store(
        sample_content,
        source_slug="sailsys",
        url="https://app.sailsys.com.au/results/123",
        content_type="text/html",
    )


# ---------------------------------------------------------------------------
# 1. Basic store + retrieve (exact bytes replay)
# ---------------------------------------------------------------------------


class TestStoreAndRetrieve:
    """Authorised replay can retrieve exact bytes."""

    def test_store_returns_receipt(self, lake, sample_content):
        receipt = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/results/1",
            content_type="text/html",
        )
        assert isinstance(receipt, RawArtifactReceiptV1)
        assert receipt.schema_version == "1"
        assert receipt.content_hash
        assert receipt.content_length == len(sample_content)
        assert receipt.source_slug == "sailsys"
        assert receipt.url == "https://app.sailsys.com.au/results/1"
        assert receipt.content_type == "text/html"
        assert receipt.encrypted is True
        assert receipt.encryption_key_id is not None

    def test_retrieve_returns_exact_bytes(self, lake, sample_content, sample_receipt):
        retrieved = lake.retrieve(sample_receipt.artifact_id)
        assert retrieved == sample_content

    def test_retrieve_by_hash(self, lake, sample_content, sample_receipt):
        retrieved = lake.retrieve_by_hash(sample_receipt.content_hash)
        assert retrieved == sample_content

    def test_retrieve_unknown_id_raises(self, lake):
        with pytest.raises(RawLakeIntegrityError):
            lake.retrieve("nonexistent-id")

    def test_receipt_serialization_roundtrip(self, sample_receipt):
        d = sample_receipt.to_dict()
        assert d["artifact_id"] == sample_receipt.artifact_id
        r2 = RawArtifactReceiptV1.from_dict(d)
        assert r2 == sample_receipt

        j = sample_receipt.to_json()
        r3 = RawArtifactReceiptV1.from_json(j)
        assert r3 == sample_receipt


# ---------------------------------------------------------------------------
# 2. Fault injection: partial upload (interrupted writes)
# ---------------------------------------------------------------------------


class TestPartialUpload:
    """Interrupted writes are invisible."""

    def test_interrupted_write_leaves_no_final_object(self, lake, sample_content):
        """Simulate a crash during the write — temp file exists but
        the final object does not, and the index has no row.
        """
        # Patch os.replace to raise (simulating crash after temp write)
        with patch("irc_data.sources.raw_lake.os.replace", side_effect=OSError("simulated crash")):
            with pytest.raises(OSError):
                lake.store(
                    sample_content,
                    source_slug="sailsys",
                    url="https://app.sailsys.com.au/results/crash",
                    content_type="text/html",
                )

        # No final object should exist
        receipts = lake.index.search(source_slug="sailsys")
        crash_receipts = [r for r in receipts if r.url.endswith("/crash")]
        assert len(crash_receipts) == 0

        # The index should not contain the crashed artifact
        assert lake.index.count() == 0

    def test_temp_file_cleaned_after_crash(self, lake, sample_content):
        """After a simulated crash, cleanup_temp_files removes stale temps."""
        with patch("irc_data.sources.raw_lake.os.replace", side_effect=OSError("crash")):
            with pytest.raises(OSError):
                lake.store(
                    sample_content,
                    source_slug="sailsys",
                    url="https://app.sailsys.com.au/results/crash2",
                )

        # There should be a stale .tmp file
        tmp_files = list(lake.lake_dir.rglob("*.tmp"))
        assert len(tmp_files) > 0

        # Cleanup
        removed = lake.cleanup_temp_files()
        assert removed > 0
        assert len(list(lake.lake_dir.rglob("*.tmp"))) == 0

    def test_index_never_points_to_missing_object(self, lake, sample_content):
        """After an interrupted write, retrieval by any means fails cleanly."""
        with patch("irc_data.sources.raw_lake.os.replace", side_effect=OSError("crash")):
            with pytest.raises(OSError):
                lake.store(
                    sample_content,
                    source_slug="sailsys",
                    url="https://app.sailsys.com.au/results/crash3",
                )

        # No artifacts in the index
        assert lake.index.count() == 0

        # Retrieval by any ID raises cleanly
        with pytest.raises(RawLakeIntegrityError):
            lake.retrieve("any-id")

    def test_successful_store_after_failed_store(self, lake, sample_content):
        """A failed write doesn't prevent a subsequent successful write."""
        # First attempt fails
        with patch("irc_data.sources.raw_lake.os.replace", side_effect=OSError("crash")):
            with pytest.raises(OSError):
                lake.store(
                    sample_content,
                    source_slug="sailsys",
                    url="https://app.sailsys.com.au/results/fail",
                )

        lake.cleanup_temp_files()

        # Second attempt succeeds
        receipt = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/results/success",
            content_type="text/html",
        )
        retrieved = lake.retrieve(receipt.artifact_id)
        assert retrieved == sample_content


# ---------------------------------------------------------------------------
# 3. Fault injection: duplicate hash
# ---------------------------------------------------------------------------


class TestDuplicateHash:
    """Duplicate hash returns existing receipt without re-writing."""

    def test_duplicate_hash_returns_same_receipt(self, lake, sample_content):
        receipt1 = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/results/dup",
            content_type="text/html",
        )

        # Store the same content again (different URL, same hash)
        receipt2 = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/results/dup2",
            content_type="text/html",
        )

        # Should be the same receipt (dedup)
        assert receipt1.artifact_id == receipt2.artifact_id
        assert receipt1.content_hash == receipt2.content_hash
        assert receipt1.storage_key == receipt2.storage_key

        # Only one object in the index
        assert lake.index.count() == 1

    def test_duplicate_hash_different_content(self, lake, sample_content):
        """Different content produces different receipts."""
        content_b = b"<html><body><h1>Different</h1></body></html>"

        receipt1 = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/results/a",
        )
        receipt2 = lake.store(
            content_b,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/results/b",
        )

        assert receipt1.artifact_id != receipt2.artifact_id
        assert receipt1.content_hash != receipt2.content_hash
        assert lake.index.count() == 2

    def test_exists_by_hash(self, lake, sample_content, sample_receipt):
        assert lake.index.exists(sample_receipt.content_hash)
        assert not lake.index.exists("0" * 64)


# ---------------------------------------------------------------------------
# 4. Fault injection: index outage
# ---------------------------------------------------------------------------


class TestIndexOutage:
    """Index outage doesn't lose objects; index can be rebuilt."""

    def test_objects_survive_index_loss(self, lake, sample_content, sample_receipt):
        """If the SQLite index is deleted, objects are still on disk."""
        # Verify the object exists
        assert lake.retrieve(sample_receipt.artifact_id) == sample_content

        # Record the storage key
        storage_key = sample_receipt.storage_key
        obj_path = lake.lake_dir / storage_key
        assert obj_path.exists()

        # Simulate index loss: delete the SQLite file
        index_path = lake.index.db_path
        lake.index.close()
        if index_path.exists():
            index_path.unlink()
        # Also remove WAL/SHM files
        for suffix in ["-wal", "-shm"]:
            p = Path(str(index_path) + suffix)
            if p.exists():
                p.unlink()

        # The object is still on disk
        assert obj_path.exists()

        # The sidecar metadata is still on disk
        meta_path = lake.lake_dir / (storage_key + ".meta")
        assert meta_path.exists()

    def test_rebuild_index_from_sidecars(self, lake, sample_content, sample_receipt):
        """Rebuild the index from sidecar .meta files after index loss."""
        # Store another artifact
        content2 = b'{"results": [1, 2, 3]}'
        receipt2 = lake.store(
            content2,
            source_slug="topyacht",
            url="https://topyacht.net.au/results/456",
            content_type="application/json",
        )

        assert lake.index.count() == 2

        # Simulate index loss
        index_path = lake.index.db_path
        lake.index.close()
        if index_path.exists():
            index_path.unlink()
        for suffix in ["-wal", "-shm"]:
            p = Path(str(index_path) + suffix)
            if p.exists():
                p.unlink()

        # Rebuild
        rebuilt_count = lake.rebuild_index()
        assert rebuilt_count == 2

        # Verify retrieval works after rebuild
        assert lake.retrieve(sample_receipt.artifact_id) == sample_content
        assert lake.retrieve(receipt2.artifact_id) == content2

        assert lake.index.count() == 2

    def test_index_corruption_does_not_affect_objects(
        self, lake, sample_content, sample_receipt
    ):
        """Even with a corrupted index file, objects remain intact on disk."""
        storage_key = sample_receipt.storage_key
        obj_path = lake.lake_dir / storage_key

        # Corrupt the index file (write garbage)
        index_path = lake.index.db_path
        lake.index.close()
        index_path.write_bytes(b"CORRUPTED INDEX DATA")

        # The object is still on disk
        assert obj_path.exists()

        # Rebuild the index (sidecar files are intact)
        # First, remove the corrupted index
        index_path.unlink()
        for suffix in ["-wal", "-shm"]:
            p = Path(str(index_path) + suffix)
            if p.exists():
                p.unlink()

        rebuilt = lake.rebuild_index()
        assert rebuilt == 1

        # Retrieval works after rebuild
        assert lake.retrieve(sample_receipt.artifact_id) == sample_content


# ---------------------------------------------------------------------------
# 5. Fault injection: corruption detection
# ---------------------------------------------------------------------------


class TestCorruptionDetection:
    """Corruption of stored objects is detected on retrieval."""

    def test_corrupted_object_raises_on_retrieve(
        self, lake, sample_content, sample_receipt
    ):
        """If an object on disk is modified, retrieval detects hash mismatch."""
        obj_path = lake.lake_dir / sample_receipt.storage_key

        # Corrupt the encrypted object on disk
        original = obj_path.read_bytes()
        corrupted = original + b"TAMPERED"
        obj_path.write_bytes(corrupted)

        # Retrieval should detect corruption
        with pytest.raises(RawLakeCorruptionError) as exc_info:
            lake.retrieve(sample_receipt.artifact_id)

        assert exc_info.value.artifact_id == sample_receipt.artifact_id
        assert exc_info.value.expected_hash == sample_receipt.content_hash
        assert exc_info.value.actual_hash != sample_receipt.content_hash

    def test_corrupted_object_overwrite(
        self, lake, sample_content, sample_receipt
    ):
        """Overwriting the encrypted object with random bytes is detected."""
        obj_path = lake.lake_dir / sample_receipt.storage_key

        # Overwrite with random encrypted-looking bytes
        obj_path.write_bytes(os.urandom(len(sample_content) + 100))

        with pytest.raises((RawLakeCorruptionError, Exception)):
            lake.retrieve(sample_receipt.artifact_id)

    def test_verify_integrity_returns_false_for_corrupted(
        self, lake, sample_content, sample_receipt
    ):
        """verify_integrity returns False for a corrupted object."""
        obj_path = lake.lake_dir / sample_receipt.storage_key
        original = obj_path.read_bytes()
        obj_path.write_bytes(original + b"EXTRA")

        assert lake.verify_integrity(sample_receipt.artifact_id) is False

    def test_verify_integrity_returns_true_for_intact(
        self, lake, sample_content, sample_receipt
    ):
        """verify_integrity returns True for an intact object."""
        assert lake.verify_integrity(sample_receipt.artifact_id) is True

    def test_verify_all_detects_corruption(
        self, lake, sample_content, sample_receipt
    ):
        """verify_all reports corrupted objects."""
        # Store a second intact artifact
        content2 = b"second artifact"
        receipt2 = lake.store(
            content2,
            source_slug="topyacht",
            url="https://topyacht.net.au/2",
        )

        # Corrupt the first
        obj_path = lake.lake_dir / sample_receipt.storage_key
        original = obj_path.read_bytes()
        obj_path.write_bytes(original + b"CORRUPT")

        result = lake.verify_all()
        assert result["ok"] == 1
        assert result["corrupted"] == 1
        assert result["missing"] == 0


# ---------------------------------------------------------------------------
# 6. Retention enforcement
# ---------------------------------------------------------------------------


class TestRetention:
    """Retention prevents premature deletion."""

    def test_delete_before_retention_raises(self, lake, sample_content):
        future = "2099-12-31T23:59:59+00:00"
        receipt = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/retained",
            retention_expires_at=future,
        )

        with pytest.raises(RetentionNotExpiredError):
            lake.delete(receipt.artifact_id)

        # Object should still be there
        assert lake.retrieve(receipt.artifact_id) == sample_content

    def test_delete_after_retention_succeeds(self, lake, sample_content):
        past = "2020-01-01T00:00:00+00:00"
        receipt = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/expired",
            retention_expires_at=past,
        )

        lake.delete(receipt.artifact_id)

        # Object should be gone
        with pytest.raises(RawLakeIntegrityError):
            lake.retrieve(receipt.artifact_id)
        assert lake.index.get(receipt.artifact_id) is None

    def test_no_retention_allows_deletion(self, lake, sample_content):
        receipt = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/no-retention",
        )
        # No retention_expires_at → can delete anytime
        lake.delete(receipt.artifact_id)
        with pytest.raises(RawLakeIntegrityError):
            lake.retrieve(receipt.artifact_id)

    def test_set_retention_updates_expiry(self, lake, sample_content):
        receipt = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/set-retention",
        )
        future = "2099-12-31T23:59:59+00:00"
        lake.set_retention(receipt.artifact_id, future)

        updated = lake.index.get(receipt.artifact_id)
        assert updated.retention_expires_at == future

        with pytest.raises(RetentionNotExpiredError):
            lake.delete(receipt.artifact_id)

    def test_sweep_retention_deletes_expired(self, lake, sample_content):
        # Store expired artifact
        past = "2020-01-01T00:00:00+00:00"
        receipt_expired = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/expired-sweep",
            retention_expires_at=past,
        )

        # Store non-expired artifact
        future = "2099-12-31T23:59:59+00:00"
        receipt_retained = lake.store(
            b"different content for retention",
            source_slug="topyacht",
            url="https://topyacht.net.au/retained-sweep",
            retention_expires_at=future,
        )

        deleted = lake.sweep_retention()
        assert receipt_expired.artifact_id in deleted
        assert receipt_retained.artifact_id not in deleted

        # Expired is gone, retained is still there
        with pytest.raises(RawLakeIntegrityError):
            lake.retrieve(receipt_expired.artifact_id)
        assert lake.retrieve(receipt_retained.artifact_id) == b"different content for retention"


# ---------------------------------------------------------------------------
# 7. Legal hold enforcement
# ---------------------------------------------------------------------------


class TestLegalHold:
    """Legal hold prevents deletion regardless of retention."""

    def test_legal_hold_prevents_deletion(self, lake, sample_content):
        past = "2020-01-01T00:00:00+00:00"
        receipt = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/legal-hold",
            retention_expires_at=past,  # retention expired
            legal_hold=True,             # but on legal hold
        )

        with pytest.raises(LegalHoldError):
            lake.delete(receipt.artifact_id)

        # Object should still be there
        assert lake.retrieve(receipt.artifact_id) == sample_content

    def test_clear_legal_hold_allows_deletion(self, lake, sample_content):
        past = "2020-01-01T00:00:00+00:00"
        receipt = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/legal-hold-clear",
            retention_expires_at=past,
            legal_hold=True,
        )

        # Clear legal hold
        lake.set_legal_hold(receipt.artifact_id, False)

        # Now deletion should work
        lake.delete(receipt.artifact_id)
        with pytest.raises(RawLakeIntegrityError):
            lake.retrieve(receipt.artifact_id)

    def test_set_legal_hold_on_existing(self, lake, sample_content, sample_receipt):
        lake.set_legal_hold(sample_receipt.artifact_id, True)

        updated = lake.index.get(sample_receipt.artifact_id)
        assert updated.legal_hold is True

        # Cannot delete while on hold
        with pytest.raises(LegalHoldError):
            lake.delete(sample_receipt.artifact_id)

    def test_legal_hold_not_swept(self, lake, sample_content):
        """sweep_retention skips artifacts on legal hold."""
        past = "2020-01-01T00:00:00+00:00"
        receipt = lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/hold-sweep",
            retention_expires_at=past,
            legal_hold=True,
        )

        deleted = lake.sweep_retention()
        assert receipt.artifact_id not in deleted
        assert lake.retrieve(receipt.artifact_id) == sample_content


# ---------------------------------------------------------------------------
# 8. Encryption at rest
# ---------------------------------------------------------------------------


class TestEncryption:
    """Objects on disk are encrypted, not plaintext."""

    def test_object_is_encrypted_on_disk(self, lake, sample_content, sample_receipt):
        obj_path = lake.lake_dir / sample_receipt.storage_key
        disk_bytes = obj_path.read_bytes()

        # The encrypted content should NOT contain the plaintext
        assert sample_content not in disk_bytes

        # The encrypted content should be a Fernet token (starts with 'gAAAA')
        assert disk_bytes.startswith(b"gAAAA")

    def test_decrypt_with_correct_key(self, lake, sample_content, sample_receipt):
        """Only the correct key can decrypt."""
        retrieved = lake.retrieve(sample_receipt.artifact_id)
        assert retrieved == sample_content

    def test_wrong_key_fails(self, tmp_path, sample_content):
        """Retrieval with a different key fails."""
        lake_dir = tmp_path / "lake1"
        lake_dir.mkdir()
        key1 = Fernet.generate_key()
        storage = RawLakeStorage(lake_dir, encryption_key=key1)
        receipt = storage.store(
            sample_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/key-test",
        )

        # Create a new storage with a different key pointing at the same lake
        key2 = Fernet.generate_key()
        # Need to remove the old key file so the new one is used
        key_file = lake_dir / ".lake_key"
        key_file.unlink()
        storage2 = RawLakeStorage(lake_dir, encryption_key=key2)

        # Retrieval should fail (wrong key → corruption or decrypt error)
        with pytest.raises((RawLakeCorruptionError, Exception)):
            storage2.retrieve(receipt.artifact_id)


# ---------------------------------------------------------------------------
# 9. Search functionality
# ---------------------------------------------------------------------------


class TestSearch:
    """The metadata index supports searchable queries."""

    def test_search_by_source(self, lake, sample_content):
        lake.store(sample_content, source_slug="sailsys", url="https://a.com/1")
        lake.store(b"different", source_slug="topyacht", url="https://b.com/2")
        lake.store(b"third", source_slug="sailsys", url="https://c.com/3")

        results = lake.index.search(source_slug="sailsys")
        assert len(results) == 2
        assert all(r.source_slug == "sailsys" for r in results)

    def test_search_by_url(self, lake, sample_content):
        lake.store(sample_content, source_slug="sailsys", url="https://a.com/1")
        lake.store(b"other", source_slug="sailsys", url="https://b.com/2")

        results = lake.index.search(url="https://a.com/1")
        assert len(results) == 1
        assert results[0].url == "https://a.com/1"

    def test_search_by_content_type(self, lake, sample_content):
        lake.store(
            sample_content,
            source_slug="sailsys",
            url="https://a.com/1",
            content_type="text/html",
        )
        lake.store(
            b'{"json": true}',
            source_slug="sailsys",
            url="https://a.com/2",
            content_type="application/json",
        )

        results = lake.index.search(content_type="text/html")
        assert len(results) == 1
        assert results[0].content_type == "text/html"

    def test_search_by_legal_hold(self, lake, sample_content):
        r1 = lake.store(sample_content, source_slug="sailsys", url="https://a.com/1")
        lake.set_legal_hold(r1.artifact_id, True)
        lake.store(b"other", source_slug="sailsys", url="https://b.com/2")

        results = lake.index.search(legal_hold=True)
        assert len(results) == 1
        assert results[0].artifact_id == r1.artifact_id


# ---------------------------------------------------------------------------
# 10. Large content
# ---------------------------------------------------------------------------


class TestLargeContent:
    """Store and retrieve large content."""

    def test_large_content_roundtrip(self, lake):
        """Store 1 MB of content and verify exact retrieval."""
        large_content = os.urandom(1024 * 1024)  # 1 MB
        receipt = lake.store(
            large_content,
            source_slug="sailsys",
            url="https://app.sailsys.com.au/large",
            content_type="application/octet-stream",
        )
        assert receipt.content_length == 1024 * 1024

        retrieved = lake.retrieve(receipt.artifact_id)
        assert retrieved == large_content

    def test_binary_pdf_content(self, lake):
        """Store binary PDF content."""
        pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n" + b"\x00\x01\x02\x03"
        receipt = lake.store(
            pdf_bytes,
            source_slug="irc-certs",
            url="https://ircrating.org/pdfdirectory/cert.pdf",
            content_type="application/pdf",
        )
        retrieved = lake.retrieve(receipt.artifact_id)
        assert retrieved == pdf_bytes


# ---------------------------------------------------------------------------
# 11. MetadataIndex standalone tests
# ---------------------------------------------------------------------------


class TestMetadataIndex:
    """Direct tests of the MetadataIndex class."""

    def test_index_create_and_query(self, tmp_path):
        index = MetadataIndex(tmp_path / "test.db")
        receipt = RawArtifactReceiptV1(
            artifact_id="test-123",
            storage_key="ab/cd/test-123.enc",
            content_hash="abcdef1234567890",
            content_length=100,
            source_slug="sailsys",
            url="https://example.com/test",
            content_type="text/html",
        )
        index.insert(receipt)

        assert index.count() == 1
        assert index.get("test-123") is not None
        assert index.get("test-123").source_slug == "sailsys"
        assert index.get_by_hash("abcdef1234567890") is not None
        assert index.exists("abcdef1234567890")
        assert not index.exists("0000000000000000")

    def test_index_duplicate_insert_raises(self, tmp_path):
        index = MetadataIndex(tmp_path / "test.db")
        receipt = RawArtifactReceiptV1(
            artifact_id="dup-1",
            storage_key="ab/cd/dup-1.enc",
            content_hash="hash123",
            content_length=50,
            source_slug="sailsys",
            url="https://example.com/dup",
        )
        index.insert(receipt)

        # Inserting the same artifact_id should fail
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            index.insert(receipt)

    def test_index_upsert_replaces(self, tmp_path):
        index = MetadataIndex(tmp_path / "test.db")
        receipt = RawArtifactReceiptV1(
            artifact_id="upsert-1",
            storage_key="ab/cd/upsert-1.enc",
            content_hash="hash456",
            content_length=50,
            source_slug="sailsys",
            url="https://example.com/upsert",
        )
        index.insert(receipt)
        assert index.count() == 1

        # Upsert with changed legal_hold
        receipt.legal_hold = True
        index.upsert(receipt)

        assert index.count() == 1
        updated = index.get("upsert-1")
        assert updated.legal_hold is True

    def test_index_delete(self, tmp_path):
        index = MetadataIndex(tmp_path / "test.db")
        receipt = RawArtifactReceiptV1(
            artifact_id="del-1",
            storage_key="ab/cd/del-1.enc",
            content_hash="hash789",
            content_length=50,
            source_slug="sailsys",
            url="https://example.com/del",
        )
        index.insert(receipt)
        assert index.count() == 1

        index.delete("del-1")
        assert index.count() == 0
        assert index.get("del-1") is None


# ---------------------------------------------------------------------------
# 12. create_raw_lake convenience
# ---------------------------------------------------------------------------


class TestCreateRawLake:
    """Test the create_raw_lake factory."""

    def test_create_in_temp_dir(self):
        storage = create_raw_lake()
        assert storage.lake_dir.exists()
        receipt = storage.store(
            b"test content",
            source_slug="sailsys",
            url="https://test.com/1",
        )
        assert storage.retrieve(receipt.artifact_id) == b"test content"
        # Cleanup
        shutil.rmtree(storage.lake_dir, ignore_errors=True)

    def test_create_in_specified_dir(self, tmp_path):
        storage = create_raw_lake(tmp_path / "mylake")
        assert storage.lake_dir == tmp_path / "mylake"
        receipt = storage.store(
            b"test",
            source_slug="sailsys",
            url="https://test.com/2",
        )
        assert storage.retrieve(receipt.artifact_id) == b"test"
