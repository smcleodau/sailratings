"""Tests for DP-02-01 — immutable raw artifact and provenance envelope.

These tests prove:

1. **Byte fidelity** — bytes written to the store are read back
   identically.
2. **Hash verification** — ``get()`` recomputes and verifies the SHA-256
   hash; tampered bytes raise ``HashMismatchError``.
3. **Content-addressed dedup** — identical content maps to the same
   hash and path; ``put()`` is idempotent.
4. **Duplicate captures retain retrieval events** — two
   ``ProvenanceRefV1`` envelopes with different retrieval times can
   reference the same ``content_hash`` (the same raw object).
5. **Schema round-trip** — ``ProvenanceRefV1`` serialises to/from dict
   and JSON without loss.
6. **RawArtifactV1** — the extended contract carries all envelope
   fields and can project a ``ProvenanceRefV1``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

from irc_data.sources.provenance import (
    HashMismatchError,
    ProvenanceRefV1,
    RawObjectNotFoundError,
    RawObjectStore,
    persist_raw_artifact,
    sha256_hex,
)
from irc_data.sources.models import FetchResult, RawArtifactV1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    """A fresh RawObjectStore rooted in a temp directory."""
    return RawObjectStore(root=str(tmp_path / "raw-objects"))


@pytest.fixture
def sample_content():
    """Sample HTML content for testing."""
    return b"<html><body><h1>Race Results</h1><p>Boat A: 1st</p></body></html>"


@pytest.fixture
def sample_hash(sample_content):
    """The expected SHA-256 of sample_content."""
    return sha256_hex(sample_content)


# ---------------------------------------------------------------------------
# RawObjectStore — byte fidelity
# ---------------------------------------------------------------------------


class TestByteFidelity:
    """Prove that bytes written are read back identically."""

    def test_put_then_get_returns_identical_bytes(self, store, sample_content):
        """Bytes written via put() are read back byte-for-byte identical."""
        content_hash = store.put(sample_content)
        retrieved = store.get(content_hash)
        assert retrieved == sample_content
        assert retrieved is not None

    def test_byte_fidelity_with_binary_content(self, store):
        """Binary content (PDF magic bytes) round-trips correctly."""
        content = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\n"
        content_hash = store.put(content)
        retrieved = store.get(content_hash)
        assert retrieved == content

    def test_byte_fidelity_with_empty_content(self, store):
        """Empty content round-trips correctly."""
        content = b""
        content_hash = store.put(content)
        retrieved = store.get(content_hash)
        assert retrieved == content

    def test_byte_fidelity_with_large_content(self, store):
        """Large content (1 MB) round-trips correctly."""
        content = os.urandom(1024 * 1024)
        content_hash = store.put(content)
        retrieved = store.get(content_hash)
        assert retrieved == content

    def test_byte_fidelity_with_unicode(self, store):
        """Unicode string content round-trips correctly."""
        content = "Résultats de la course — Üniçödé Test".encode("utf-8")
        content_hash = store.put(content)
        retrieved = store.get(content_hash)
        assert retrieved == content


# ---------------------------------------------------------------------------
# RawObjectStore — hash verification
# ---------------------------------------------------------------------------


class TestHashVerification:
    """Prove that get() verifies the hash and detects tampering."""

    def test_get_recomputes_hash(self, store, sample_content, sample_hash):
        """get() recomputes the hash and it matches."""
        store.put(sample_content)
        # The hash should match
        retrieved = store.get(sample_hash)
        assert sha256_hex(retrieved) == sample_hash

    def test_hash_mismatch_on_tampered_bytes(self, store, sample_content, sample_hash):
        """Tampered bytes raise HashMismatchError."""
        content_hash = store.put(sample_content)
        path = store.object_path(content_hash)

        # Tamper with the stored bytes
        with open(path, "r+b") as f:
            f.seek(0)
            f.write(b"TAMPERED")

        with pytest.raises(HashMismatchError) as exc_info:
            store.get(content_hash)

        assert exc_info.value.content_hash == content_hash
        assert exc_info.value.actual_hash != content_hash

    def test_get_nonexistent_raises(self, store):
        """Getting a hash that doesn't exist raises RawObjectNotFoundError."""
        fake_hash = "a" * 64
        with pytest.raises(RawObjectNotFoundError):
            store.get(fake_hash)

    def test_hash_is_sha256(self, store, sample_content):
        """The returned hash is a valid 64-character SHA-256 hex digest."""
        content_hash = store.put(sample_content)
        assert len(content_hash) == 64
        assert all(c in "0123456789abcdef" for c in content_hash)
        assert content_hash == hashlib.sha256(sample_content).hexdigest()


# ---------------------------------------------------------------------------
# RawObjectStore — content-addressed dedup
# ---------------------------------------------------------------------------


class TestContentAddressedDedup:
    """Prove content-addressed deduplication and immutability."""

    def test_same_content_same_hash(self, store, sample_content):
        """Identical content produces the same hash."""
        hash1 = store.put(sample_content)
        hash2 = store.put(sample_content)
        assert hash1 == hash2

    def test_different_content_different_hash(self, store):
        """Different content produces different hashes."""
        hash1 = store.put(b"content A")
        hash2 = store.put(b"content B")
        assert hash1 != hash2

    def test_put_is_idempotent(self, store, sample_content):
        """Second put() of the same content is a no-op."""
        hash1 = store.put(sample_content)
        path1 = store.object_path(hash1)

        # Record the file's mtime
        mtime1 = os.path.getmtime(path1)

        hash2 = store.put(sample_content)
        assert hash1 == hash2

        # The file should not have been rewritten
        mtime2 = os.path.getmtime(path1)
        assert mtime1 == mtime2

    def test_dedup_does_not_create_duplicate_files(self, store, sample_content):
        """Duplicate puts don't create extra files."""
        store.put(sample_content)
        store.put(sample_content)
        store.put(sample_content)
        assert store.count() == 1

    def test_multiple_different_objects(self, store):
        """Multiple different objects are stored separately."""
        for i in range(10):
            store.put(f"content {i}".encode())
        assert store.count() == 10

    def test_exists(self, store, sample_content):
        """exists() returns True for stored objects, False for missing."""
        content_hash = store.put(sample_content)
        assert store.exists(content_hash)
        assert not store.exists("b" * 64)

    def test_byte_size(self, store, sample_content):
        """byte_size() returns the correct size."""
        content_hash = store.put(sample_content)
        assert store.byte_size(content_hash) == len(sample_content)


# ---------------------------------------------------------------------------
# RawObjectStore — content-addressed path structure
# ---------------------------------------------------------------------------


class TestContentAddressedPath:
    """Prove the content-addressed path structure."""

    def test_path_is_sharded(self, store, sample_content, sample_hash):
        """The path uses the first 4 hex chars as shard directories."""
        content_hash = store.put(sample_content)
        path = store.object_path(content_hash)

        # Path should contain shard directories: <root>/<sha[:2]>/<sha[2:4]>/<sha>
        assert sample_hash[:2] in path
        assert sample_hash[2:4] in path
        assert sample_hash in path

    def test_object_location_returns_path(self, store, sample_content):
        """object_location() returns the same path as object_path()."""
        content_hash = store.put(sample_content)
        assert store.object_location(content_hash) == store.object_path(content_hash)


# ---------------------------------------------------------------------------
# ProvenanceRefV1 — schema round-trip
# ---------------------------------------------------------------------------


class TestProvenanceRefSerialization:
    """Prove ProvenanceRefV1 serialises and deserialises correctly."""

    def test_to_dict_from_dict_roundtrip(self, store, sample_content):
        """to_dict() → from_dict() preserves all fields."""
        content_hash = store.put(sample_content)
        ref = ProvenanceRefV1(
            content_hash=content_hash,
            source="sailsys",
            requested_uri="https://app.sailsys.com.au/results/123",
            resolved_uri="https://app.sailsys.com.au/results/123",
            retrieved_at="2026-09-01T02:00:00+00:00",
            policy_version="interim-v0",
            headers_subset={"ETag": '"abc123"', "Content-Type": "text/html"},
            status=200,
            object_location=store.object_location(content_hash),
            adapter_version="sailsys-v1.2.0",
            lineage=["abc123", "def456"],
        )

        d = ref.to_dict()
        ref2 = ProvenanceRefV1.from_dict(d)

        assert ref == ref2
        assert ref2.content_hash == content_hash
        assert ref2.source == "sailsys"
        assert ref2.requested_uri == "https://app.sailsys.com.au/results/123"
        assert ref2.resolved_uri == "https://app.sailsys.com.au/results/123"
        assert ref2.retrieved_at == "2026-09-01T02:00:00+00:00"
        assert ref2.policy_version == "interim-v0"
        assert ref2.headers_subset == {"ETag": '"abc123"', "Content-Type": "text/html"}
        assert ref2.status == 200
        assert ref2.adapter_version == "sailsys-v1.2.0"
        assert ref2.lineage == ["abc123", "def456"]

    def test_to_json_from_json_roundtrip(self, store, sample_content):
        """to_json() → from_json() preserves all fields."""
        content_hash = store.put(sample_content)
        ref = ProvenanceRefV1(
            content_hash=content_hash,
            source="topyacht",
            requested_uri="https://topyacht.net.au/race/42",
            resolved_uri="https://topyacht.net.au/race/42",
            retrieved_at="2026-09-01T02:05:00+00:00",
            policy_version="interim-v0",
            headers_subset={"Last-Modified": "Mon, 01 Sep 2026 00:00:00 GMT"},
            status=200,
            object_location=store.object_location(content_hash),
            adapter_version="topyacht-v0.9.1",
            lineage=[],
        )

        json_str = ref.to_json()
        ref2 = ProvenanceRefV1.from_json(json_str)

        assert ref == ref2

    def test_json_is_valid(self, store, sample_content):
        """to_json() produces valid JSON."""
        content_hash = store.put(sample_content)
        ref = ProvenanceRefV1(
            content_hash=content_hash,
            source="sailsys",
        )
        json_str = ref.to_json()
        parsed = json.loads(json_str)
        assert parsed["content_hash"] == content_hash
        assert parsed["source"] == "sailsys"

    def test_default_values(self):
        """ProvenanceRefV1 has sensible defaults."""
        ref = ProvenanceRefV1(
            content_hash="a" * 64,
            source="sailsys",
        )
        assert ref.requested_uri == ""
        assert ref.resolved_uri == ""
        assert ref.policy_version == "interim-v0"
        assert ref.headers_subset == {}
        assert ref.status == 200
        assert ref.adapter_version == ""
        assert ref.lineage == []
        assert ref.schema_version == "1"

    def test_all_envelope_fields_present(self, store, sample_content):
        """All envelope fields from the spec are present in to_dict()."""
        content_hash = store.put(sample_content)
        ref = ProvenanceRefV1(
            content_hash=content_hash,
            source="sailsys",
            requested_uri="http://req.example",
            resolved_uri="http://res.example",
            retrieved_at="2026-09-01T02:00:00+00:00",
            policy_version="interim-v0",
            headers_subset={"X-Test": "true"},
            status=200,
            object_location="/path/to/blob",
            adapter_version="v1.0",
            lineage=["hash1"],
        )
        d = ref.to_dict()

        # Every field required by the spec
        assert "source" in d
        assert "requested_uri" in d
        assert "resolved_uri" in d
        assert "retrieved_at" in d
        assert "policy_version" in d
        assert "headers_subset" in d
        assert "status" in d
        assert "content_hash" in d
        assert "object_location" in d
        assert "adapter_version" in d
        assert "lineage" in d


# ---------------------------------------------------------------------------
# Duplicate captures reference existing bytes while retaining retrieval events
# ---------------------------------------------------------------------------


class TestDuplicateCapturesRetainEvents:
    """Prove the key acceptance criterion: duplicate captures reference
    existing bytes while retaining retrieval events."""

    def test_duplicate_captures_same_hash_different_timestamps(self, store, sample_content):
        """Two captures of the same content produce the same content_hash
        but different retrieval events."""
        # First capture
        hash1, ref1 = persist_raw_artifact(
            store=store,
            content=sample_content,
            source="sailsys",
            requested_uri="https://app.sailsys.com.au/results/123",
            retrieved_at="2026-09-01T02:00:00+00:00",
        )

        # Second capture of the same content (e.g. page hasn't changed)
        hash2, ref2 = persist_raw_artifact(
            store=store,
            content=sample_content,
            source="sailsys",
            requested_uri="https://app.sailsys.com.au/results/123",
            retrieved_at="2026-09-02T02:00:00+00:00",
        )

        # Same content → same hash → same raw object
        assert hash1 == hash2
        assert store.exists(hash1)

        # Only one raw object exists
        assert store.count() == 1

        # But two distinct retrieval events (different timestamps)
        assert ref1.retrieved_at != ref2.retrieved_at
        assert ref1.retrieved_at == "2026-09-01T02:00:00+00:00"
        assert ref2.retrieved_at == "2026-09-02T02:00:00+00:00"

        # Both reference the same object_location
        assert ref1.object_location == ref2.object_location

        # Both reference the same content_hash
        assert ref1.content_hash == ref2.content_hash

    def test_duplicate_capture_does_not_overwrite_bytes(self, store, sample_content):
        """A second put() of the same content does not modify the stored bytes."""
        hash1 = store.put(sample_content)
        path = store.object_path(hash1)

        # Read back the stored bytes
        with open(path, "rb") as f:
            original_bytes = f.read()

        # Put again (duplicate)
        hash2 = store.put(sample_content)
        assert hash1 == hash2

        # Bytes should be unchanged
        with open(path, "rb") as f:
            after_bytes = f.read()
        assert original_bytes == after_bytes

    def test_multiple_retrieval_events_for_same_object(self, store, sample_content):
        """N captures of the same content produce N retrieval events
        but only 1 raw object."""
        refs = []
        for i in range(5):
            _, ref = persist_raw_artifact(
                store=store,
                content=sample_content,
                source="sailsys",
                requested_uri=f"https://app.sailsys.com.au/results/{i}",
                retrieved_at=f"2026-09-0{i+1}T02:00:00+00:00",
            )
            refs.append(ref)

        # Only one raw object
        assert store.count() == 1

        # All 5 retrieval events have the same content_hash
        assert all(r.content_hash == refs[0].content_hash for r in refs)

        # All 5 have different retrieval times and URIs
        times = {r.retrieved_at for r in refs}
        uris = {r.requested_uri for r in refs}
        assert len(times) == 5
        assert len(uris) == 5

    def test_duplicate_capture_different_source(self, store, sample_content):
        """Two captures of the same content from different sources still
        deduplicate the bytes but retain distinct provenance."""
        _, ref1 = persist_raw_artifact(
            store=store,
            content=sample_content,
            source="sailsys",
            requested_uri="https://app.sailsys.com.au/results/1",
        )
        _, ref2 = persist_raw_artifact(
            store=store,
            content=sample_content,
            source="topyacht",
            requested_uri="https://topyacht.net.au/race/1",
        )

        # Same bytes → same hash → same object
        assert ref1.content_hash == ref2.content_hash
        assert store.count() == 1

        # Different provenance
        assert ref1.source != ref2.source
        assert ref1.requested_uri != ref2.requested_uri


# ---------------------------------------------------------------------------
# persist_raw_artifact convenience function
# ---------------------------------------------------------------------------


class TestPersistRawArtifact:
    """Test the persist_raw_artifact convenience function."""

    def test_returns_content_hash_and_provenance(self, store, sample_content):
        """persist_raw_artifact returns the hash and a ProvenanceRefV1."""
        content_hash, ref = persist_raw_artifact(
            store=store,
            content=sample_content,
            source="sailsys",
            requested_uri="https://app.sailsys.com.au/results/1",
            adapter_version="v1.0",
            lineage=["upstream-hash"],
            headers_subset={"ETag": '"v1"'},
        )

        assert content_hash == sha256_hex(sample_content)
        assert isinstance(ref, ProvenanceRefV1)
        assert ref.content_hash == content_hash
        assert ref.source == "sailsys"
        assert ref.requested_uri == "https://app.sailsys.com.au/results/1"
        assert ref.resolved_uri == "https://app.sailsys.com.au/results/1"
        assert ref.adapter_version == "v1.0"
        assert ref.lineage == ["upstream-hash"]
        assert ref.headers_subset == {"ETag": '"v1"'}
        assert ref.object_location == store.object_location(content_hash)

    def test_resolved_uri_defaults_to_requested(self, store, sample_content):
        """resolved_uri defaults to requested_uri when not specified."""
        _, ref = persist_raw_artifact(
            store=store,
            content=sample_content,
            source="sailsys",
            requested_uri="https://example.com/page",
        )
        assert ref.resolved_uri == "https://example.com/page"

    def test_lineage_defaults_to_empty(self, store, sample_content):
        """lineage defaults to empty list."""
        _, ref = persist_raw_artifact(
            store=store,
            content=sample_content,
            source="sailsys",
            requested_uri="https://example.com/page",
        )
        assert ref.lineage == []


# ---------------------------------------------------------------------------
# RawArtifactV1 — extended contract
# ---------------------------------------------------------------------------


class TestRawArtifactV1Extended:
    """Test the extended RawArtifactV1 contract."""

    def test_from_fetch_result_populates_all_fields(self):
        """from_fetch_result populates all envelope fields."""
        fetch_result = FetchResult(
            url="https://app.sailsys.com.au/results/123",
            content=b"<html>test</html>",
            content_hash=sha256_hex(b"<html>test</html>"),
            etag='"abc"',
            last_modified="Mon, 01 Sep 2026 00:00:00 GMT",
            fetched_at="2026-09-01T02:00:00+00:00",
            policy_version="interim-v0",
            status_code=200,
        )

        artifact = RawArtifactV1.from_fetch_result(
            fetch_result=fetch_result,
            source_slug="sailsys",
            content_type="text/html",
            adapter_version="sailsys-v1.2.0",
            object_location="/data/raw-objects/ab/cd/abcdef1234567890",
            lineage=["upstream-hash-1"],
            headers_subset={"ETag": '"abc"'},
        )

        # All envelope fields
        assert artifact.content_hash == sha256_hex(b"<html>test</html>")
        assert artifact.source_slug == "sailsys"
        assert artifact.requested_uri == "https://app.sailsys.com.au/results/123"
        assert artifact.resolved_uri == "https://app.sailsys.com.au/results/123"
        assert artifact.fetched_at == "2026-09-01T02:00:00+00:00"
        assert artifact.policy_version == "interim-v0"
        assert artifact.content_type == "text/html"
        assert artifact.byte_size == len(b"<html>test</html>")
        assert artifact.adapter_version == "sailsys-v1.2.0"
        assert artifact.object_location == "/data/raw-objects/ab/cd/abcdef1234567890"
        assert artifact.lineage == ["upstream-hash-1"]
        assert artifact.headers_subset == {"ETag": '"abc"'}
        assert artifact.status_code == 200
        assert artifact.etag == '"abc"'
        assert artifact.last_modified == "Mon, 01 Sep 2026 00:00:00 GMT"

    def test_to_dict_excludes_content_bytes(self):
        """to_dict() does not include the raw content bytes."""
        artifact = RawArtifactV1(
            content_hash="a" * 64,
            source_slug="sailsys",
            fetched_at="2026-09-01T02:00:00+00:00",
            policy_version="interim-v0",
            content=b"<html>secret</html>",
        )
        d = artifact.to_dict()
        assert "content" not in d
        assert "content_hash" in d

    def test_to_provenance_ref(self):
        """to_provenance_ref() produces a ProvenanceRefV1."""
        artifact = RawArtifactV1(
            content_hash="a" * 64,
            source_slug="sailsys",
            fetched_at="2026-09-01T02:00:00+00:00",
            policy_version="interim-v0",
            requested_uri="http://req.test",
            resolved_uri="http://res.test",
            object_location="/data/raw/aa/bb/aabb",
            byte_size=42,
            content_type="text/html",
            adapter_version="v1.0",
            headers_subset={"ETag": '"x"'},
            lineage=["parent-hash"],
            status_code=200,
        )

        ref = artifact.to_provenance_ref()

        assert isinstance(ref, ProvenanceRefV1)
        assert ref.content_hash == "a" * 64
        assert ref.source == "sailsys"
        assert ref.requested_uri == "http://req.test"
        assert ref.resolved_uri == "http://res.test"
        assert ref.retrieved_at == "2026-09-01T02:00:00+00:00"
        assert ref.policy_version == "interim-v0"
        assert ref.object_location == "/data/raw/aa/bb/aabb"
        assert ref.adapter_version == "v1.0"
        assert ref.headers_subset == {"ETag": '"x"'}
        assert ref.lineage == ["parent-hash"]
        assert ref.status == 200

    def test_to_dict_contains_all_envelope_fields(self):
        """to_dict() contains every field required by the envelope spec."""
        artifact = RawArtifactV1(
            content_hash="a" * 64,
            source_slug="sailsys",
            fetched_at="2026-09-01T02:00:00+00:00",
            policy_version="interim-v0",
            requested_uri="http://req.test",
            resolved_uri="http://res.test",
            object_location="/data/raw/aa/bb/aabb",
            byte_size=42,
            content_type="text/html",
            adapter_version="v1.0",
            headers_subset={"ETag": '"x"'},
            lineage=["parent-hash"],
            status_code=200,
        )
        d = artifact.to_dict()

        # Envelope fields from the spec
        assert "source_slug" in d          # source
        assert "requested_uri" in d        # requested URI
        assert "resolved_uri" in d          # resolved URI
        assert "fetched_at" in d           # retrieval time
        assert "policy_version" in d        # policy version
        assert "headers_subset" in d       # headers subset
        assert "status_code" in d           # status
        assert "content_hash" in d         # content hash
        assert "object_location" in d      # object location
        assert "adapter_version" in d      # adapter version
        assert "lineage" in d              # lineage


# ---------------------------------------------------------------------------
# End-to-end: RawArtifactV1 + RawObjectStore + ProvenanceRefV1
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """End-to-end test: fetch → store → provenance → verify."""

    def test_full_roundtrip(self, store):
        """Full roundtrip: content → store → artifact → provenance → verify."""
        content = b"<html><body>Race Results: Boat A 1st, Boat B 2nd</body></html>"
        content_hash = sha256_hex(content)

        # 1. Persist raw bytes to the content-addressed store
        stored_hash = store.put(content)
        assert stored_hash == content_hash

        # 2. Build a RawArtifactV1
        artifact = RawArtifactV1(
            content_hash=content_hash,
            source_slug="sailsys",
            fetched_at="2026-09-01T02:00:00+00:00",
            policy_version="interim-v0",
            requested_uri="https://app.sailsys.com.au/results/1",
            resolved_uri="https://app.sailsys.com.au/results/1",
            object_location=store.object_location(content_hash),
            byte_size=len(content),
            content_type="text/html",
            adapter_version="sailsys-v1.0",
            headers_subset={"ETag": '"v1"'},
            lineage=[],
            status_code=200,
            content=content,
        )

        # 3. Project to ProvenanceRefV1
        ref = artifact.to_provenance_ref()

        # 4. Verify: read bytes back from the store and check fidelity
        retrieved_bytes = store.get(ref.content_hash)
        assert retrieved_bytes == content

        # 5. Verify: hash of retrieved bytes matches
        assert sha256_hex(retrieved_bytes) == ref.content_hash

        # 6. Verify: provenance ref points to the right location
        assert ref.object_location == store.object_location(content_hash)

        # 7. Verify: the bytes at object_location match
        with open(ref.object_location, "rb") as f:
            file_bytes = f.read()
        assert file_bytes == content

    def test_lineage_chain(self, store):
        """Derived artifacts carry lineage to their upstream sources."""
        # Upstream artifact 1
        upstream1_content = b"<html>upstream page 1</html>"
        upstream1_hash = store.put(upstream1_content)

        # Upstream artifact 2
        upstream2_content = b"<html>upstream page 2</html>"
        upstream2_hash = store.put(upstream2_content)

        # Derived artifact references both upstream hashes in its lineage
        derived_content = b"<html>derived from 1 and 2</html>"
        derived_hash = store.put(derived_content)

        ref = ProvenanceRefV1(
            content_hash=derived_hash,
            source="sailsys",
            lineage=[upstream1_hash, upstream2_hash],
        )

        assert len(ref.lineage) == 2
        assert upstream1_hash in ref.lineage
        assert upstream2_hash in ref.lineage

        # All three upstream objects exist in the store
        assert store.exists(upstream1_hash)
        assert store.exists(upstream2_hash)
        assert store.exists(derived_hash)
        assert store.count() == 3
