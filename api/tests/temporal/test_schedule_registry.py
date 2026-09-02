"""OPS-01-02 integration test — register drives schedules; run → ledger row.

Verification flow (from the issue's "Verification" criterion):

    add source → schedule appears;  disable → pauses;  run → ledger row.

Runs against:
  * a live Temporal server (``TEMPORAL_ADDRESS``, default ``localhost:7233``)
    in the ``default`` namespace;
  * the local Postgres at ``localhost:5433/irc_data`` (test rows are seeded
    with a unique slug and cleaned up at the end).

Requires migration ``0025`` (``source_runs`` / ``source_schedule_state``).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from temporalio.client import Client
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from irc_data.db.connection import get_engine
from irc_data.temporal.ledger import activities as ledger_activities
from irc_data.temporal.ledger.workflows import SourceRunWorkflow
from irc_data.temporal.schedules.cadence import schedule_id_for_slug
from irc_data.temporal.schedules.registry import ScheduleRegistry

pytestmark = [pytest.mark.asyncio]

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
NAMESPACE = os.environ.get("TEMPORAL_TEST_NAMESPACE", "default")
TEST_TASK_QUEUE = f"ops0102-test-{uuid.uuid4().hex[:8]}"


def _slug() -> str:
    return f"ops0102-it-{uuid.uuid4().hex[:8]}"


def _seed_source(slug: str, *, enabled: bool = True, cadence: str = "nightly") -> None:
    """Insert a register row (idempotent on slug)."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO data_sources
                    (slug, display_name, base_url, category, policy_version,
                     legal_status, enabled, cadence, adapter_status)
                VALUES
                    (:slug, 'OPS-01-02 IT', 'https://ops0102.example.com', 'results',
                     'v1.0', 'approved', :enabled, :cadence, 'active')
                ON CONFLICT (slug) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    cadence = EXCLUDED.cadence,
                    legal_status = 'approved'
                """
            ),
            {"slug": slug, "enabled": enabled, "cadence": cadence},
        )


def _set_enabled(slug: str, enabled: bool) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE data_sources SET enabled = :e WHERE slug = :s"),
            {"e": enabled, "s": slug},
        )


def _ledger_rows(slug: str) -> list[dict]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT source_slug, run_key, status, workflow_id, started_at, finished_at "
                "FROM source_runs WHERE source_slug = :s ORDER BY id"
            ),
            {"s": slug},
        ).mappings().all()
    return [dict(r) for r in rows]


def _cleanup(slug: str, schedule_ids: list[str]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM source_runs WHERE source_slug = :s"), {"s": slug})
        conn.execute(text("DELETE FROM source_schedule_state WHERE source_slug = :s"), {"s": slug})
        conn.execute(text("DELETE FROM data_sources WHERE slug = :s"), {"s": slug})
    for sid in schedule_ids:
        try:
            from temporalio.client import Client as _C  # noqa: F401
        except Exception:
            pass


async def _wait_for(predicate, timeout: float = 30.0, interval: float = 0.5):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last = None
    while loop.time() < deadline:
        last = predicate()
        if last:
            return last
        await asyncio.sleep(interval)
    return last


async def test_register_drives_schedules_and_ledger():
    slug = _slug()
    schedule_id = schedule_id_for_slug(slug)
    run_key = f"it-{uuid.uuid4().hex[:12]}"

    client = await Client.connect(TEMPORAL_ADDRESS, namespace=NAMESPACE)
    registry = ScheduleRegistry(client, task_queue=TEST_TASK_QUEUE)

    # Stub the (potentially slow/networked) adapter so the workflow is fast
    # and hermetic.  The ledger + register activities still hit the real DB.
    stub_adapter_calls: list[tuple[dict, str]] = []

    async def _stub_run_adapter(record: dict, key: str) -> dict:
        stub_adapter_calls.append((record, key))
        return {"records_written": 3, "adapter": "stub"}

    original_run_adapter = ledger_activities.run_registered_adapter
    # Copy the activity definition metadata from the real activity onto the
    # stub so Temporal registers it under the same name.
    from temporalio import activity as _activity

    stub = _activity.defn(name="run_registered_adapter")(_stub_run_adapter)

    worker = Worker(
        client,
        task_queue=TEST_TASK_QUEUE,
        workflows=[SourceRunWorkflow],
        activities=[
            ledger_activities.fetch_source_record,
            ledger_activities.open_source_run,
            stub,
            ledger_activities.close_source_run,
        ],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )

    try:
        # --------------------------------------------------------------
        # 1. add source → schedule appears
        # --------------------------------------------------------------
        _seed_source(slug, enabled=True, cadence="nightly")
        outcome = await registry.ensure_schedule(
            type("_S", (), {
                "slug": slug,
                "base_url": "https://ops0102.example.com",
                "cadence": "nightly",
                "enabled": True,
                "legal_status": "approved",
            })()
        )
        assert outcome == "created", f"expected schedule to be created, got {outcome}"

        handle = client.get_schedule_handle(schedule_id)
        desc = await handle.describe()
        assert desc.schedule.state.paused is False, "new enabled source should be unpaused"
        # cadence → 24h interval
        from datetime import timedelta
        assert desc.schedule.spec.intervals[0].every == timedelta(hours=24)

        # --------------------------------------------------------------
        # 2. disable → pauses (within one sync cycle)
        # --------------------------------------------------------------
        _set_enabled(slug, False)
        summary = await registry.sync_from_register(get_engine())
        assert slug in summary["paused"], f"disable should pause schedule: {summary}"
        desc = await handle.describe()
        assert desc.schedule.state.paused is True, "disabled source should be paused"

        # re-enable → unpauses
        _set_enabled(slug, True)
        summary = await registry.sync_from_register(get_engine())
        assert slug in summary["unpaused"], f"re-enable should unpause: {summary}"
        desc = await handle.describe()
        assert desc.schedule.state.paused is False

        # --------------------------------------------------------------
        # 3. run → ledger row
        # --------------------------------------------------------------
        async with worker:
            result = await client.execute_workflow(
                SourceRunWorkflow.run,
                args=[slug, run_key],
                id=f"ops0102-run-{uuid.uuid4().hex[:8]}",
                task_queue=TEST_TASK_QUEUE,
            )
        assert result["status"] == "success"
        assert result["source_slug"] == slug
        assert stub_adapter_calls, "adapter should have been invoked"
        assert stub_adapter_calls[0][0]["slug"] == slug

        rows = _ledger_rows(slug)
        assert len(rows) == 1, f"expected exactly one ledger row, got {rows}"
        row = rows[0]
        assert row["run_key"] == run_key
        assert row["status"] == "success"
        assert row["started_at"] is not None
        assert row["finished_at"] is not None
        assert row["workflow_id"] is not None

    finally:
        # --------------------------------------------------------------
        # cleanup: delete schedule + DB rows
        # --------------------------------------------------------------
        try:
            await client.get_schedule_handle(schedule_id).delete()
        except Exception:
            pass
        _cleanup(slug, [schedule_id])
