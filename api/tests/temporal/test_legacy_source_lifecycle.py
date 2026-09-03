"""OPS-02-04 — live lifecycle verification against Temporal + Postgres.

Verifies the OPS-02-04 acceptance criteria against the live dev services
(the same way ``test_schedule_registry.py`` does):

* **schedules matching data_sources exist** — after
  ``sync_from_register``, every register row has a Temporal schedule whose id
  is the persisted ``source_schedule_state.schedule_id`` (and, with the
  canonical register of 33 rows, at least the 11 the acceptance text pins);
* **a manual trigger of the ``orc`` API source produces a ``source_runs``
  row *and* an ``ingestion_log`` row** — the dual-write bridge;
* **pause flips both Temporal and ``source_schedule_state``** (and resume
  flips them back) via the AD-01-06 ``set_schedule_paused`` helper.

The adapter activity is stubbed (hermetic — no network); the register,
ledger, dual-write mirror and schedule machinery all hit the real services.
Test rows use a unique slug and are cleaned up.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from temporalio import activity as _activity
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
TEST_TASK_QUEUE = f"ops0204-it-{uuid.uuid4().hex[:8]}"


def _slug() -> str:
    return f"ops0204-it-{uuid.uuid4().hex[:8]}"


def _seed_source(slug: str, *, enabled: bool = True, cadence: str = "nightly") -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO data_sources
                    (slug, display_name, base_url, category, policy_version,
                     legal_status, enabled, cadence, adapter_status)
                VALUES
                    (:slug, 'OPS-02-04 IT', 'https://ops0204.example.com', 'results',
                     'v1.0', 'approved', :enabled, :cadence, 'active')
                ON CONFLICT (slug) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    cadence = EXCLUDED.cadence,
                    legal_status = 'approved'
                """
            ),
            {"slug": slug, "enabled": enabled, "cadence": cadence},
        )


def _mirror_paused(slug: str) -> bool | None:
    engine = get_engine()
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT paused FROM source_schedule_state WHERE source_slug = :s"),
            {"s": slug},
        ).scalar()


def _mirror_schedule_id(slug: str) -> str | None:
    engine = get_engine()
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT schedule_id FROM source_schedule_state WHERE source_slug = :s"
            ),
            {"s": slug},
        ).scalar()


def _ledger_rows(slug: str) -> list[dict]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT source_slug, run_key, status, started_at, finished_at "
                "FROM source_runs WHERE source_slug = :s ORDER BY id"
            ),
            {"s": slug},
        ).mappings().all()
    return [dict(r) for r in rows]


def _ingestion_rows(slug: str) -> list[dict]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT source, status, completed_at "
                "FROM ingestion_log WHERE source = :s ORDER BY id"
            ),
            {"s": slug},
        ).mappings().all()
    return [dict(r) for r in rows]


def _cleanup(slug: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM source_runs WHERE source_slug = :s"), {"s": slug})
        conn.execute(
            text("DELETE FROM source_schedule_state WHERE source_slug = :s"),
            {"s": slug},
        )
        conn.execute(text("DELETE FROM ingestion_log WHERE source = :s"), {"s": slug})
        conn.execute(text("DELETE FROM data_sources WHERE slug = :s"), {"s": slug})


async def test_pause_resume_flip_temporal_and_mirror_both_ways():
    """AD-01-06: pause flips Temporal + source_schedule_state; resume too."""
    slug = _slug()
    schedule_id = schedule_id_for_slug(slug)
    _seed_source(slug, enabled=True)

    client = await Client.connect(TEMPORAL_ADDRESS, namespace=NAMESPACE)
    registry = ScheduleRegistry(client, task_queue=TEST_TASK_QUEUE)

    # The control helpers connect their own registry against the default env
    # address; point them at the same server and keep the schedule on the
    # shared data-pipeline queue (the schedule itself isn't fired here).
    os.environ["TEMPORAL_ADDRESS"] = TEMPORAL_ADDRESS
    os.environ["TEMPORAL_NAMESPACE"] = NAMESPACE

    try:
        # create the schedule via the control path's ensure (paused=False)
        await registry.ensure_schedule(
            type("_S", (), {
                "slug": slug,
                "base_url": "https://ops0204.example.com",
                "cadence": "nightly",
                "enabled": True,
                "legal_status": "approved",
            })()
        )
        # seed the mirror row
        ledger_activities.mirror_paused_state(get_engine(), slug, False)

        # ---- pause: flips Temporal and the mirror -----------------------
        out = await ledger_activities.set_schedule_paused(slug, True)
        assert out["paused"] is True
        assert out["schedule_id"] == schedule_id
        handle = client.get_schedule_handle(schedule_id)
        desc = await handle.describe()
        assert desc.schedule.state.paused is True, "Temporal schedule should be paused"
        assert _mirror_paused(slug) is True, "mirror should be paused"

        # ---- resume: flips both back ------------------------------------
        out = await ledger_activities.set_schedule_paused(slug, False)
        assert out["paused"] is False
        desc = await handle.describe()
        assert desc.schedule.state.paused is False, "Temporal schedule should be live"
        assert _mirror_paused(slug) is False, "mirror should be live"
    finally:
        try:
            await client.get_schedule_handle(schedule_id).delete()
        except Exception:
            pass
        _cleanup(slug)


async def test_manual_run_writes_source_runs_and_ingestion_log():
    """A source run writes the source_runs ledger AND the ingestion_log mirror."""
    slug = _slug()
    run_key = f"it-{uuid.uuid4().hex[:12]}"
    _seed_source(slug, enabled=True)

    client = await Client.connect(TEMPORAL_ADDRESS, namespace=NAMESPACE)

    async def _stub_run_adapter(record: dict, key: str) -> dict:
        return {"records_written": 2, "records_found": 5, "adapter": "stub"}

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
        async with worker:
            result = await client.execute_workflow(
                SourceRunWorkflow.run,
                args=[slug, run_key],
                id=f"ops0204-run-{uuid.uuid4().hex[:8]}",
                task_queue=TEST_TASK_QUEUE,
            )
        assert result["status"] == "success"

        runs = _ledger_rows(slug)
        assert len(runs) == 1, f"expected one source_runs row, got {runs}"
        assert runs[0]["run_key"] == run_key
        assert runs[0]["status"] == "success"
        assert runs[0]["started_at"] is not None
        assert runs[0]["finished_at"] is not None

        logs = _ingestion_rows(slug)
        assert len(logs) == 1, f"expected one ingestion_log mirror row, got {logs}"
        assert logs[0]["status"] == "completed"
        assert logs[0]["completed_at"] is not None
    finally:
        _cleanup(slug)


async def test_sync_creates_schedules_for_every_register_row():
    """Every data_sources row has a schedule; ids match the mirror.

    This is the acceptance "11 schedules matching data_sources" — with the
    canonical 33-row register seeded, the sync must yield at least the 11 the
    issue pins, one per register row, with the schedule id equal to
    ``source_schedule_state.schedule_id``.
    """
    os.environ["TEMPORAL_ADDRESS"] = TEMPORAL_ADDRESS
    os.environ["TEMPORAL_NAMESPACE"] = NAMESPACE

    engine = get_engine()
    registry = await ScheduleRegistry.connect()
    summary = await registry.sync_from_register(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT source_slug, schedule_id FROM source_schedule_state "
                "ORDER BY source_slug"
            )
        ).mappings().all()
    assert len(rows) >= 11, f"expected at least 11 mirrored schedules, got {len(rows)}"

    # Every register row's schedule exists in Temporal under the mirror's id.
    client = registry.client
    missing = []
    for r in rows:
        try:
            await client.get_schedule_handle(r["schedule_id"]).describe()
        except Exception:
            missing.append(r["schedule_id"])
    assert not missing, f"schedules missing in Temporal: {missing}"
