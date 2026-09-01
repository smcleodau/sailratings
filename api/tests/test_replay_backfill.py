"""Tests for the replay / backfill pipeline (DP-02-04 / SPEC-013).

Verification criterion from the issue:
    "Backfill fixture stops mid-range, resumes and promotes exactly one batch."

Acceptance criteria:
    - Replay is idempotent, resumable and auditable.
    - Publication is an explicit promotion, not an in-place rewrite.

These tests use in-memory SQLite with the replay_store's own schema
init (no Postgres or Alembic dependency).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine

from irc_data.temporal.replay.contracts import (
    SCHEMA_VERSION,
    ArtifactFilter,
    BatchStatus,
    ComparisonResult,
    PublicationReceiptV1,
    ReplayPlanV1,
)
from irc_data.temporal.replay.replay_store import (
    compare_batches,
    count_batch_artifacts,
    create_or_get_batch,
    get_batch,
    get_batch_artifacts,
    get_currently_promoted_batch,
    get_receipt,
    init_published_artifacts_table,
    init_replay_tables,
    insert_published_artifact,
    promote_batch,
    reject_batch,
    select_artifacts,
    store_parsed_output,
    update_batch_status,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    """Fresh in-memory SQLite engine with replay tables."""
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    init_replay_tables(eng)
    init_published_artifacts_table(eng)
    return eng


@pytest.fixture()
def published_artifacts(engine):
    """Insert 10 published artifacts from the 'sailsys' source."""
    artifacts = []
    for i in range(10):
        art_id = insert_published_artifact(
            engine,
            source_slug="sailsys",
            artifact_url=f"https://sailsys.test/results/{i}",
            content_hash=f"hash_{i:03d}",
            parsed_output={"place": i + 1, "boat": f"Boat{i}", "sail": f"AUS{i:04d}"},
            parser_version="1.0.0",
        )
        artifacts.append(art_id)
    return artifacts


@pytest.fixture()
def plan():
    """A replay plan for the 'sailsys' source with parser v2.0.0."""
    return ReplayPlanV1(
        source_slug="sailsys",
        new_parser_version="2.0.0",
        artifact_filter=ArtifactFilter(source_slug="sailsys"),
    )


def _run_parser_on_artifacts(engine, batch_id: int, plan: ReplayPlanV1, artifacts: list):
    """Simulate running a parser on each artifact into the isolated batch.

    The "new parser" adds an extra field to each parsed output.
    """
    for art in artifacts:
        old_output = art.get("parsed_output")
        # Simulate the new parser adding a "corrected_place" field.
        new_output = dict(old_output) if old_output else {}
        new_output["corrected_place"] = (new_output.get("place") or 0) + 1
        store_parsed_output(
            engine,
            batch_id=batch_id,
            artifact_url=art["artifact_url"],
            content_hash=art["content_hash"],
            parsed_output=new_output,
            old_parsed_output=old_output,
        )


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


def test_replay_plan_v1_contract():
    """ReplayPlanV1 has the expected schema_version and fields."""
    plan = ReplayPlanV1(
        source_slug="sailsys",
        new_parser_version="2.0.0",
    )
    assert plan.schema_version == SCHEMA_VERSION
    assert plan.schema_version == "v1"
    assert plan.plan_id != ""  # auto-derived
    assert plan.source_slug == "sailsys"
    assert plan.new_parser_version == "2.0.0"
    assert isinstance(plan.artifact_filter, ArtifactFilter)

    d = plan.to_dict()
    required_keys = {
        "schema_version", "plan_id", "source_slug", "new_parser_version",
        "artifact_filter", "created_at", "created_by", "notes",
    }
    assert required_keys.issubset(d.keys())


def test_replay_plan_v1_roundtrip():
    """ReplayPlanV1 survives JSON round-trip."""
    plan = ReplayPlanV1(
        source_slug="sailsys",
        new_parser_version="2.0.0",
        artifact_filter=ArtifactFilter(source_slug="sailsys", limit=50),
        created_by="operator@example.com",
        notes="test plan",
    )
    json_str = plan.to_json()
    restored = ReplayPlanV1.from_json(json_str)
    assert restored == plan
    assert restored.artifact_filter.source_slug == "sailsys"
    assert restored.artifact_filter.limit == 50


def test_publication_receipt_v1_contract():
    """PublicationReceiptV1 has the expected schema_version and fields."""
    receipt = PublicationReceiptV1(
        receipt_id="rcpt-test-1",
        batch_id=1,
        plan_id="plan123",
        source_slug="sailsys",
        old_batch_id=None,
        artifact_count=10,
        promoted_by="admin",
    )
    assert receipt.schema_version == SCHEMA_VERSION
    assert receipt.old_retained is True
    assert receipt.artifact_count == 10

    d = receipt.to_dict()
    required_keys = {
        "schema_version", "receipt_id", "batch_id", "plan_id",
        "source_slug", "promoted_at", "old_batch_id", "old_retained",
        "artifact_count", "promoted_by",
    }
    assert required_keys.issubset(d.keys())


def test_publication_receipt_v1_roundtrip():
    """PublicationReceiptV1 survives JSON round-trip."""
    receipt = PublicationReceiptV1(
        receipt_id="rcpt-test-2",
        batch_id=2,
        plan_id="plan456",
        source_slug="topyacht",
        old_batch_id=1,
        artifact_count=5,
    )
    json_str = receipt.to_json()
    restored = PublicationReceiptV1.from_json(json_str)
    assert restored == receipt
    assert restored.old_batch_id == 1
    assert restored.old_retained is True


def test_plan_id_is_deterministic():
    """Same filter + parser version → same plan_id."""
    plan1 = ReplayPlanV1(
        source_slug="sailsys",
        new_parser_version="2.0.0",
        artifact_filter=ArtifactFilter(source_slug="sailsys"),
    )
    plan2 = ReplayPlanV1(
        source_slug="sailsys",
        new_parser_version="2.0.0",
        artifact_filter=ArtifactFilter(source_slug="sailsys"),
    )
    assert plan1.plan_id == plan2.plan_id


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


def test_create_batch_is_idempotent(engine, plan):
    """Calling create_or_get_batch twice returns the same batch."""
    batch1 = create_or_get_batch(engine, plan)
    batch2 = create_or_get_batch(engine, plan)

    assert batch1["id"] == batch2["id"]
    assert batch1["plan_id"] == batch2["plan_id"]
    assert batch1["status"] == batch2["status"]


def test_idempotent_batch_does_not_duplicate(engine, plan):
    """Re-running with the same plan_id does not create a duplicate row."""
    create_or_get_batch(engine, plan)
    create_or_get_batch(engine, plan)
    create_or_get_batch(engine, plan)

    batch = get_batch(engine, plan.plan_id)
    assert batch is not None
    # There should be exactly one batch row for this plan_id.
    from sqlalchemy import text
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM replay_batches WHERE plan_id = :pid"),
            {"pid": plan.plan_id},
        ).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# Artifact selection tests
# ---------------------------------------------------------------------------


def test_select_artifacts_by_source(engine, published_artifacts):
    """select_artifacts filters by source_slug."""
    artifacts = select_artifacts(
        engine,
        ArtifactFilter(source_slug="sailsys"),
    )
    assert len(artifacts) == 10
    assert all(a["artifact_url"].startswith("https://sailsys.test/") for a in artifacts)


def test_select_artifacts_with_limit(engine, published_artifacts):
    """select_artifacts respects the limit field."""
    artifacts = select_artifacts(
        engine,
        ArtifactFilter(source_slug="sailsys", limit=5),
    )
    assert len(artifacts) == 5


def test_select_artifacts_empty_filter(engine, published_artifacts):
    """select_artifacts with no filter returns all artifacts."""
    artifacts = select_artifacts(engine, ArtifactFilter())
    assert len(artifacts) == 10


def test_select_artifacts_no_match(engine, published_artifacts):
    """select_artifacts with non-matching source returns empty."""
    artifacts = select_artifacts(
        engine,
        ArtifactFilter(source_slug="nonexistent"),
    )
    assert len(artifacts) == 0


# ---------------------------------------------------------------------------
# Parsed output storage tests
# ---------------------------------------------------------------------------


def test_store_parsed_output_isolated(engine, plan, published_artifacts):
    """Storing parsed outputs does not modify the published store."""
    batch = create_or_get_batch(engine, plan)
    artifacts = select_artifacts(engine, plan.artifact_filter)

    _run_parser_on_artifacts(engine, batch["id"], plan, artifacts[:3])

    # The isolated batch has 3 artifacts.
    assert count_batch_artifacts(engine, batch["id"]) == 3

    # The published store still has 10 artifacts (unchanged).
    from sqlalchemy import text
    with engine.connect() as conn:
        pub_count = conn.execute(
            text("SELECT COUNT(*) FROM published_artifacts")
        ).scalar()
    assert pub_count == 10


def test_store_parsed_output_with_old(engine, plan, published_artifacts):
    """Stored artifacts include the old_parsed_output for comparison."""
    batch = create_or_get_batch(engine, plan)
    artifacts = select_artifacts(engine, plan.artifact_filter)

    _run_parser_on_artifacts(engine, batch["id"], plan, artifacts[:1])

    batch_arts = get_batch_artifacts(engine, batch["id"])
    assert len(batch_arts) == 1
    assert batch_arts[0]["old_parsed_output"] is not None
    assert batch_arts[0]["parsed_output"] is not None
    # The new output has the extra field.
    assert "corrected_place" in batch_arts[0]["parsed_output"]


# ---------------------------------------------------------------------------
# Comparison tests
# ---------------------------------------------------------------------------


def test_compare_detects_changes(engine, plan, published_artifacts):
    """compare_batches detects changed outputs."""
    batch = create_or_get_batch(engine, plan)
    artifacts = select_artifacts(engine, plan.artifact_filter)
    _run_parser_on_artifacts(engine, batch["id"], plan, artifacts)

    result = compare_batches(engine, batch["id"])
    assert isinstance(result, ComparisonResult)
    assert result.total_artifacts == 10
    assert result.changed == 10  # all have the extra field
    assert result.identical == 0
    assert result.has_changes()


def test_compare_identical_outputs(engine, plan, published_artifacts):
    """compare_batches reports identical when outputs match."""
    batch = create_or_get_batch(engine, plan)
    artifacts = select_artifacts(engine, plan.artifact_filter)

    # Store the SAME parsed output (no changes).
    for art in artifacts:
        store_parsed_output(
            engine,
            batch_id=batch["id"],
            artifact_url=art["artifact_url"],
            content_hash=art["content_hash"],
            parsed_output=art.get("parsed_output"),
            old_parsed_output=art.get("parsed_output"),
        )

    result = compare_batches(engine, batch["id"])
    assert result.identical == 10
    assert result.changed == 0
    assert not result.has_changes()


# ---------------------------------------------------------------------------
# Promotion tests (explicit, not in-place)
# ---------------------------------------------------------------------------


def test_promote_batch_produces_receipt(engine, plan, published_artifacts):
    """promote_batch produces a PublicationReceiptV1."""
    batch = create_or_get_batch(engine, plan)
    artifacts = select_artifacts(engine, plan.artifact_filter)
    _run_parser_on_artifacts(engine, batch["id"], plan, artifacts)
    update_batch_status(engine, batch["id"], BatchStatus.AWAITING_APPROVAL)

    receipt = promote_batch(engine, batch["id"], plan, promoted_by="admin")

    assert isinstance(receipt, PublicationReceiptV1)
    assert receipt.batch_id == batch["id"]
    assert receipt.plan_id == plan.plan_id
    assert receipt.source_slug == "sailsys"
    assert receipt.old_retained is True
    assert receipt.artifact_count == 10
    assert receipt.promoted_by == "admin"
    assert receipt.old_batch_id is None  # first promotion


def test_promote_batch_marks_old_as_superseded(engine, published_artifacts):
    """When a second batch is promoted, the old one is retained as superseded."""
    # First batch.
    plan1 = ReplayPlanV1(
        source_slug="sailsys",
        new_parser_version="2.0.0",
        artifact_filter=ArtifactFilter(source_slug="sailsys"),
    )
    batch1 = create_or_get_batch(engine, plan1)
    arts = select_artifacts(engine, plan1.artifact_filter)
    _run_parser_on_artifacts(engine, batch1["id"], plan1, arts)
    update_batch_status(engine, batch1["id"], BatchStatus.AWAITING_APPROVAL)
    receipt1 = promote_batch(engine, batch1["id"], plan1, promoted_by="admin")

    # Second batch (different parser version).
    plan2 = ReplayPlanV1(
        source_slug="sailsys",
        new_parser_version="3.0.0",
        artifact_filter=ArtifactFilter(source_slug="sailsys"),
    )
    batch2 = create_or_get_batch(engine, plan2)
    _run_parser_on_artifacts(engine, batch2["id"], plan2, arts)
    update_batch_status(engine, batch2["id"], BatchStatus.AWAITING_APPROVAL)
    receipt2 = promote_batch(engine, batch2["id"], plan2, promoted_by="admin")

    # The second receipt references the first batch as old_batch_id.
    assert receipt2.old_batch_id == batch1["id"]
    assert receipt2.old_retained is True

    # The first batch is now superseded (not deleted).
    old_batch = get_batch(engine, plan1.plan_id)
    assert old_batch["status"] == BatchStatus.SUPERSEDED.value

    # Its artifacts are still in the database (retained).
    old_arts = get_batch_artifacts(engine, batch1["id"])
    assert len(old_arts) == 10


def test_promote_is_idempotent(engine, plan, published_artifacts):
    """Calling promote_batch twice returns the same receipt."""
    batch = create_or_get_batch(engine, plan)
    arts = select_artifacts(engine, plan.artifact_filter)
    _run_parser_on_artifacts(engine, batch["id"], plan, arts)
    update_batch_status(engine, batch["id"], BatchStatus.AWAITING_APPROVAL)

    receipt1 = promote_batch(engine, batch["id"], plan, promoted_by="admin")
    receipt2 = promote_batch(engine, batch["id"], plan, promoted_by="admin")

    assert receipt1.receipt_id == receipt2.receipt_id
    assert receipt1.batch_id == receipt2.batch_id


def test_promote_rejects_wrong_status(engine, plan, published_artifacts):
    """promote_batch raises if batch is not awaiting approval."""
    batch = create_or_get_batch(engine, plan)
    # Batch is still 'pending' — not awaiting_approval.
    with pytest.raises(ValueError, match="must be 'awaiting_approval'"):
        promote_batch(engine, batch["id"], plan)


def test_reject_batch(engine, plan, published_artifacts):
    """reject_batch marks the batch as rejected without touching published data."""
    batch = create_or_get_batch(engine, plan)
    reject_batch(engine, batch["id"], reason="parser regression")

    updated = get_batch(engine, plan.plan_id)
    assert updated["status"] == BatchStatus.REJECTED.value


def test_get_receipt(engine, plan, published_artifacts):
    """get_receipt returns the receipt for a promoted batch."""
    batch = create_or_get_batch(engine, plan)
    arts = select_artifacts(engine, plan.artifact_filter)
    _run_parser_on_artifacts(engine, batch["id"], plan, arts)
    update_batch_status(engine, batch["id"], BatchStatus.AWAITING_APPROVAL)
    promote_batch(engine, batch["id"], plan, promoted_by="admin")

    receipt = get_receipt(engine, batch["id"])
    assert receipt is not None
    assert receipt.batch_id == batch["id"]
    assert receipt.plan_id == plan.plan_id


def test_get_receipt_none_if_not_promoted(engine, plan):
    """get_receipt returns None for a non-promoted batch."""
    batch = create_or_get_batch(engine, plan)
    receipt = get_receipt(engine, batch["id"])
    assert receipt is None


# ---------------------------------------------------------------------------
# VERIFICATION CRITERION: Backfill stops mid-range, resumes, promotes one batch
# ---------------------------------------------------------------------------


def test_backfill_stops_midrange_resumes_and_promotes(engine, published_artifacts):
    """Backfill fixture stops mid-range, resumes and promotes exactly one batch.

    This is the verification criterion from the issue.

    Scenario:
      1. Create a replay plan for 10 artifacts.
      2. Parse the first 5 artifacts (simulating a mid-range stop).
      3. "Resume" — parse the remaining 5 artifacts.
      4. Compare.
      5. Promote — exactly one batch is promoted, old outputs retained.
    """
    plan = ReplayPlanV1(
        source_slug="sailsys",
        new_parser_version="2.0.0",
        artifact_filter=ArtifactFilter(source_slug="sailsys"),
    )

    # Step 1: Create batch (idempotent).
    batch = create_or_get_batch(engine, plan)
    assert batch["status"] == BatchStatus.PENDING.value

    # Step 2: Select all 10 artifacts.
    all_artifacts = select_artifacts(engine, plan.artifact_filter)
    assert len(all_artifacts) == 10

    # Step 3: Parse the FIRST 5 artifacts (simulating mid-range stop).
    update_batch_status(engine, batch["id"], BatchStatus.RUNNING)
    _run_parser_on_artifacts(engine, batch["id"], plan, all_artifacts[:5])

    # Verify 5 are stored.
    assert count_batch_artifacts(engine, batch["id"]) == 5

    # --- Simulate crash / resume ---

    # Step 4: Resume — check how many are already done.
    existing_count = count_batch_artifacts(engine, batch["id"])
    assert existing_count == 5

    # Parse the REMAINING 5 artifacts (resume from where we left off).
    remaining = all_artifacts[existing_count:]
    assert len(remaining) == 5
    _run_parser_on_artifacts(engine, batch["id"], plan, remaining)

    # Verify all 10 are now stored.
    assert count_batch_artifacts(engine, batch["id"]) == 10

    # Step 5: Compare.
    update_batch_status(engine, batch["id"], BatchStatus.COMPARING)
    result = compare_batches(engine, batch["id"])
    assert result.total_artifacts == 10
    assert result.changed == 10  # all have the extra "corrected_place" field

    # Step 6: Await approval → promote.
    update_batch_status(engine, batch["id"], BatchStatus.AWAITING_APPROVAL)
    receipt = promote_batch(engine, batch["id"], plan, promoted_by="backfill-operator")

    # Verify: exactly ONE batch is promoted.
    assert receipt.batch_id == batch["id"]
    assert receipt.artifact_count == 10
    assert receipt.old_retained is True
    assert receipt.old_batch_id is None  # first promotion for this source

    # Verify the batch status is 'promoted'.
    final_batch = get_batch(engine, plan.plan_id)
    assert final_batch["status"] == BatchStatus.PROMOTED.value

    # Verify exactly one receipt exists.
    from sqlalchemy import text
    with engine.connect() as conn:
        receipt_count = conn.execute(
            text("SELECT COUNT(*) FROM publication_receipts WHERE batch_id = :bid"),
            {"bid": batch["id"]},
        ).scalar()
    assert receipt_count == 1

    # Verify the published store is UNCHANGED (not an in-place rewrite).
    with engine.connect() as conn:
        pub_count = conn.execute(
            text("SELECT COUNT(*) FROM published_artifacts")
        ).scalar()
    assert pub_count == 10  # still 10, not modified


def test_backfill_resume_is_idempotent(engine, published_artifacts):
    """Re-running the same plan after a mid-range stop resumes correctly."""
    plan = ReplayPlanV1(
        source_slug="sailsys",
        new_parser_version="2.0.0",
        artifact_filter=ArtifactFilter(source_slug="sailsys"),
    )

    # First run: create batch and parse 3 artifacts.
    batch1 = create_or_get_batch(engine, plan)
    all_arts = select_artifacts(engine, plan.artifact_filter)
    update_batch_status(engine, batch1["id"], BatchStatus.RUNNING)
    _run_parser_on_artifacts(engine, batch1["id"], plan, all_arts[:3])

    # Second run (resume): calling create_or_get_batch returns the SAME batch.
    batch2 = create_or_get_batch(engine, plan)
    assert batch2["id"] == batch1["id"]
    assert batch2["status"] == BatchStatus.RUNNING.value

    # Existing count is 3.
    assert count_batch_artifacts(engine, batch1["id"]) == 3

    # Parse the remaining 7.
    remaining = all_arts[3:]
    _run_parser_on_artifacts(engine, batch1["id"], plan, remaining)
    assert count_batch_artifacts(engine, batch1["id"]) == 10


# ---------------------------------------------------------------------------
# Auditable tests
# ---------------------------------------------------------------------------


def test_audit_trail_complete(engine, published_artifacts):
    """Every batch, artifact, and promotion is a queryable DB row."""
    plan = ReplayPlanV1(
        source_slug="sailsys",
        new_parser_version="2.0.0",
        artifact_filter=ArtifactFilter(source_slug="sailsys"),
    )

    batch = create_or_get_batch(engine, plan)
    arts = select_artifacts(engine, plan.artifact_filter)
    _run_parser_on_artifacts(engine, batch["id"], plan, arts)
    update_batch_status(engine, batch["id"], BatchStatus.AWAITING_APPROVAL)
    receipt = promote_batch(engine, batch["id"], plan, promoted_by="auditor")

    # Query the batch.
    from sqlalchemy import text
    with engine.connect() as conn:
        batch_row = conn.execute(
            text("SELECT * FROM replay_batches WHERE id = :bid"),
            {"bid": batch["id"]},
        ).first()
        assert batch_row is not None
        assert batch_row.status == BatchStatus.PROMOTED.value
        assert batch_row.promoted_by == "auditor"
        assert batch_row.promoted_at is not None

        # Query artifacts.
        art_count = conn.execute(
            text("SELECT COUNT(*) FROM replay_artifacts WHERE batch_id = :bid"),
            {"bid": batch["id"]},
        ).scalar()
        assert art_count == 10

        # Query receipt.
        receipt_row = conn.execute(
            text("SELECT * FROM publication_receipts WHERE batch_id = :bid"),
            {"bid": batch["id"]},
        ).first()
        assert receipt_row is not None
        assert receipt_row.receipt_id == receipt.receipt_id
        assert receipt_row.old_retained in (True, 1)
        assert receipt_row.schema_version == "v1"


def test_multiple_sources_independent(engine, published_artifacts):
    """Promoting one source's batch does not affect another source."""
    # Insert artifacts for a second source.
    for i in range(5):
        insert_published_artifact(
            engine,
            source_slug="topyacht",
            artifact_url=f"https://topyacht.test/results/{i}",
            content_hash=f"ty_hash_{i}",
            parsed_output={"place": i + 1, "boat": f"TYBoat{i}"},
            parser_version="1.0.0",
        )

    plan1 = ReplayPlanV1(
        source_slug="sailsys",
        new_parser_version="2.0.0",
        artifact_filter=ArtifactFilter(source_slug="sailsys"),
    )
    batch1 = create_or_get_batch(engine, plan1)
    arts1 = select_artifacts(engine, plan1.artifact_filter)
    _run_parser_on_artifacts(engine, batch1["id"], plan1, arts1)
    update_batch_status(engine, batch1["id"], BatchStatus.AWAITING_APPROVAL)
    receipt1 = promote_batch(engine, batch1["id"], plan1)

    # The topyacht source has no promoted batch.
    assert get_currently_promoted_batch(engine, "topyacht") is None
    assert get_currently_promoted_batch(engine, "sailsys") == batch1["id"]

    # Promote a topyacht batch.
    plan2 = ReplayPlanV1(
        source_slug="topyacht",
        new_parser_version="2.0.0",
        artifact_filter=ArtifactFilter(source_slug="topyacht"),
    )
    batch2 = create_or_get_batch(engine, plan2)
    arts2 = select_artifacts(engine, plan2.artifact_filter)
    _run_parser_on_artifacts(engine, batch2["id"], plan2, arts2)
    update_batch_status(engine, batch2["id"], BatchStatus.AWAITING_APPROVAL)
    receipt2 = promote_batch(engine, batch2["id"], plan2)

    # Both sources have their own promoted batch.
    assert get_currently_promoted_batch(engine, "sailsys") == batch1["id"]
    assert get_currently_promoted_batch(engine, "topyacht") == batch2["id"]

    # The second promotion does not supersede the first (different source).
    assert receipt2.old_batch_id is None  # first promotion for topyacht
    batch1_after = get_batch(engine, plan1.plan_id)
    assert batch1_after["status"] == BatchStatus.PROMOTED.value  # still promoted


# ---------------------------------------------------------------------------
# Parser registry tests
# ---------------------------------------------------------------------------


def test_register_custom_parser():
    """Custom parsers can be registered and resolved."""
    from irc_data.temporal.replay.replay_activities import (
        _resolve_parser,
        register_parser,
    )

    def my_parser(artifact):
        return {"custom": True, "url": artifact.get("artifact_url")}

    register_parser("my_parser", my_parser)
    resolved = _resolve_parser("my_parser")
    assert resolved is my_parser

    result = resolved({"artifact_url": "http://test"})
    assert result == {"custom": True, "url": "http://test"}


# ---------------------------------------------------------------------------
# ComparisonResult tests
# ---------------------------------------------------------------------------


def test_comparison_result_has_changes():
    """ComparisonResult.has_changes() detects non-zero diffs."""
    r = ComparisonResult(batch_id=1, changed=5)
    assert r.has_changes()

    r = ComparisonResult(batch_id=1, identical=10)
    assert not r.has_changes()

    r = ComparisonResult(batch_id=1, added=3)
    assert r.has_changes()
