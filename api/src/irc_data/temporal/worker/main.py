"""OPS-01-02 — Temporal worker for the ``data-pipeline`` task queue.

Serves:

  * ``SourceRunWorkflow``         — one run of one registered source
  * ``ScheduleSyncLoopWorkflow``  — register → schedule reconciliation loop
  * the register/ledger/sync activities they depend on
  * the legacy DP-00 scrape activities (so the interim adapters are callable)

Run with::

    TEMPORAL_ADDRESS=localhost:7233 python -m irc_data.temporal.worker.main
"""

from __future__ import annotations

import asyncio
import os
import traceback

from temporalio.client import Client
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from irc_data.temporal.ledger import activities as ledger_activities
from irc_data.temporal.ledger.workflows import SourceRunWorkflow
from irc_data.temporal.schedules.cadence import schedule_id_for_slug
from irc_data.temporal.schedules.registry import (
    SOURCE_RUN_TASK_QUEUE,
    ScheduleRegistry,
    ScheduleSyncLoopWorkflow,
)
from irc_data.temporal.activities import scrape_activities


async def _ensure_sync_loop(client: Client) -> None:
    """Start the schedule sync loop workflow if it isn't already running."""
    from temporalio.client import WorkflowIDReusePolicy
    from temporalio.service import RPCError, RPCStatusCode

    try:
        await client.start_workflow(
            ScheduleSyncLoopWorkflow.run,
            args=[300, 0],
            id="schedule-sync-loop",
            task_queue=SOURCE_RUN_TASK_QUEUE,
            id_reuse_policy=WorkflowIDReusePolicy.WORKFLOW_ID_REUSE_POLICY_REJECT_DUPLICATE,
        )
        print("started ScheduleSyncLoopWorkflow")
    except RPCError as exc:  # already running
        if exc.status == RPCStatusCode.ALREADY_EXISTS:
            print("ScheduleSyncLoopWorkflow already running")
        else:
            raise


async def main() -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "sailratings")

    # Imported lazily (inside main) so loading this module has no heavy
    # dependency chain and to avoid any circular-import risk at import time.
    from irc_data.temporal.activities import raw_capture_ys_m2s_activities
    from irc_data.temporal.workflows.raw_capture_ys_m2s_workflow import (
        NightlyRawCaptureYsM2sWorkflow,
    )

    try:
        client = await Client.connect(address, namespace=namespace)

        # Reconcile schedules once at worker start so an existing register
        # is reflected in Temporal without waiting for the first loop tick.
        try:
            registry = ScheduleRegistry(client, task_queue=SOURCE_RUN_TASK_QUEUE)
            from irc_data.db.connection import get_engine

            summary = await registry.sync_from_register(get_engine())
            print(f"initial schedule sync: {summary}")
        except Exception:
            print("initial schedule sync failed (continuing)")
            traceback.print_exc()

        # Start the reconciliation loop.
        try:
            await _ensure_sync_loop(client)
        except Exception:
            print("could not start sync loop (continuing)")
            traceback.print_exc()

        worker = Worker(
            client,
            task_queue=SOURCE_RUN_TASK_QUEUE,
            workflows=[
                SourceRunWorkflow,
                ScheduleSyncLoopWorkflow,
                NightlyRawCaptureYsM2sWorkflow,
            ],
            activities=[
                ledger_activities.fetch_source_record,
                ledger_activities.open_source_run,
                ledger_activities.run_registered_adapter,
                ledger_activities.close_source_run,
                ledger_activities.sync_schedules_from_register,
                # OPS-02-04 / AD-01-06 admin start/pause/resume helpers
                ledger_activities.set_schedule_paused,
                ledger_activities.trigger_source_run,
                # legacy DP-00 scrape activities (interim adapters)
                scrape_activities.scrape_orc,
                scrape_activities.scrape_tcc,
                scrape_activities.scrape_sailsys,
                scrape_activities.scrape_topyacht,
                scrape_activities.scrape_boat_news,
                scrape_activities.scrape_certs_exhaustive,
                scrape_activities.parse_certs,
                scrape_activities.scrape_isora,
                scrape_activities.scrape_rhkyc,
                scrape_activities.scrape_sailracehq,
                scrape_activities.scrape_wayback,
                scrape_activities.scrape_wayback_tcc,
                # DP-00-03 raw capture (Yacht Scoring + Manage2Sail)
                raw_capture_ys_m2s_activities.list_sources_activity,
                raw_capture_ys_m2s_activities.capture_source_activity,
                raw_capture_ys_m2s_activities.write_ledger_activity,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )

        print(
            f"data-pipeline worker up on {address} ns={namespace} "
            f"task_queue={SOURCE_RUN_TASK_QUEUE}"
        )
        await worker.run()
    except Exception:
        print("WORKER FAILED")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
