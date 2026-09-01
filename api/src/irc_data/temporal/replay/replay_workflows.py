"""Temporal replay / backfill workflows (DP-02-04 / SPEC-013).

Defines:

* :class:`ReplayWorkflow` — the main replay / reparse workflow.
  Selects published artifacts, runs a new parser into an isolated
  batch, compares old vs new, waits for explicit approval, then
  promotes (retaining old outputs).

* :class:`BackfillWorkflow` — a resumable backfill workflow that
  processes artifacts in chunks.  If it stops mid-range (crash /
  timeout), it resumes from the last completed chunk.  On approval it
  promotes exactly one batch.

Both workflows are **idempotent** (keyed by ``plan_id``) and
**resumable** (batch status is persisted between steps).

Approval model
--------------
Publication is an **explicit promotion**, not an automatic step.  The
workflow pauses after comparison and waits for an ``approve`` or
``reject`` Temporal signal.  Only ``approve`` triggers promotion.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from irc_data.temporal.replay.contracts import (
        BatchStatus,
        ReplayPlanV1,
    )
    from irc_data.temporal.replay.replay_activities import (
        compare_batches_activity,
        count_batch_artifacts_activity,
        create_batch_activity,
        promote_batch_activity,
        run_parser_activity,
        select_artifacts_activity,
    )


# ---------------------------------------------------------------------------
# Approval mixin (shared by both workflows)
# ---------------------------------------------------------------------------


class _ApprovalMixin:
    """Mixin providing approval/reject signal handling.

    The workflow instance stores ``_approval_decision`` (initially
    ``None``).  Signal handlers set it to ``"approve"`` or
    ``"reject"``.  The :meth:`_await_approval` method blocks until a
    signal arrives or the 72-hour timeout expires.
    """

    def __init__(self) -> None:
        self._approval_decision: str | None = None

    @workflow.signal
    def approve(self) -> None:
        """Signal the workflow to approve promotion."""
        self._approval_decision = "approve"

    @workflow.signal
    def reject(self) -> None:
        """Signal the workflow to reject the batch."""
        self._approval_decision = "reject"

    async def _await_approval(self) -> bool:
        """Block until an approve/reject signal arrives.

        Returns ``True`` if approved, ``False`` if rejected or timed
        out.
        """
        try:
            await workflow.wait_condition(
                lambda: self._approval_decision is not None,
                timeout=timedelta(hours=72),  # 3-day approval window
            )
        except Exception:
            # Timeout — treat as rejection.
            return False

        return self._approval_decision == "approve"


# ---------------------------------------------------------------------------
# ReplayWorkflow
# ---------------------------------------------------------------------------


@workflow.defn
class ReplayWorkflow(_ApprovalMixin):
    """Replay / reparse workflow.

    Steps:
      1. Create or get batch (idempotent by ``plan_id``).
      2. Select published artifacts by source/time/version.
      3. Run new parser into isolated batch.
      4. Compare old vs new outputs.
      5. Wait for explicit approval signal.
      6. Promote batch (old outputs retained).
    """

    @workflow.run
    async def run(self, plan_dict: dict[str, Any]) -> dict[str, Any]:
        plan = ReplayPlanV1.from_dict(plan_dict)

        # Step 1: Create or get batch (idempotent).
        batch = await workflow.execute_activity(
            create_batch_activity,
            plan.to_dict(),
            start_to_close_timeout=timedelta(minutes=1),
        )

        batch_id = batch["id"]
        status = batch["status"]

        # Idempotent: already promoted → return result.
        if status == BatchStatus.PROMOTED.value:
            return {
                "batch_id": batch_id,
                "plan_id": plan.plan_id,
                "status": status,
                "message": "Batch already promoted (idempotent resume).",
            }

        # Already rejected → return early.
        if status == BatchStatus.REJECTED.value:
            return {
                "batch_id": batch_id,
                "plan_id": plan.plan_id,
                "status": status,
                "message": "Batch was rejected.",
            }

        # Steps 2-3: Select artifacts and run parser (skip if past this stage).
        if status in (
            BatchStatus.PENDING.value,
            BatchStatus.RUNNING.value,
        ):
            artifacts = await workflow.execute_activity(
                select_artifacts_activity,
                args=[plan.to_dict(), batch_id],
                start_to_close_timeout=timedelta(minutes=5),
            )

            if not artifacts:
                return {
                    "batch_id": batch_id,
                    "plan_id": plan.plan_id,
                    "status": "no_artifacts",
                    "message": "No artifacts matched the filter.",
                }

            await workflow.execute_activity(
                run_parser_activity,
                args=[plan.to_dict(), batch_id, artifacts],
                start_to_close_timeout=timedelta(hours=2),
            )

        # Step 4: Compare (skip if already awaiting approval).
        if status != BatchStatus.AWAITING_APPROVAL.value:
            await workflow.execute_activity(
                compare_batches_activity,
                batch_id,
                start_to_close_timeout=timedelta(minutes=10),
            )

        # Step 5: Wait for explicit approval.
        approved = await self._await_approval()

        if not approved:
            return {
                "batch_id": batch_id,
                "plan_id": plan.plan_id,
                "status": BatchStatus.REJECTED.value,
                "message": "Batch rejected.",
            }

        # Step 6: Promote (explicit, old outputs retained).
        receipt = await workflow.execute_activity(
            promote_batch_activity,
            args=[plan.to_dict(), batch_id],
            start_to_close_timeout=timedelta(minutes=1),
        )

        return {
            "batch_id": batch_id,
            "plan_id": plan.plan_id,
            "status": BatchStatus.PROMOTED.value,
            "receipt": receipt,
            "message": "Batch promoted. Old outputs retained.",
        }


# ---------------------------------------------------------------------------
# BackfillWorkflow
# ---------------------------------------------------------------------------


@workflow.defn
class BackfillWorkflow(_ApprovalMixin):
    """Resumable backfill workflow.

    Processes artifacts in chunks.  If the workflow stops mid-range
    (crash, timeout, SIGTERM), it resumes from the last completed
    chunk on the next run.  On approval, it promotes exactly one batch.

    The chunk size is configurable (default 50).  Each chunk is parsed
    and stored in the isolated batch.  After all chunks are processed,
    the batch is compared and awaits approval.

    Resumability is achieved by counting artifacts already stored in
    the batch at the start of each chunk.  If the count indicates a
    chunk was already processed, it is skipped.
    """

    @workflow.run
    async def run(self, plan_dict: dict[str, Any]) -> dict[str, Any]:
        plan = ReplayPlanV1.from_dict(plan_dict)

        # Step 1: Create or get batch (idempotent).
        batch = await workflow.execute_activity(
            create_batch_activity,
            plan.to_dict(),
            start_to_close_timeout=timedelta(minutes=1),
        )

        batch_id = batch["id"]
        status = batch["status"]

        # Idempotent: already done?
        if status == BatchStatus.PROMOTED.value:
            return {
                "batch_id": batch_id,
                "plan_id": plan.plan_id,
                "status": status,
                "message": "Batch already promoted (idempotent resume).",
            }

        if status == BatchStatus.REJECTED.value:
            return {
                "batch_id": batch_id,
                "plan_id": plan.plan_id,
                "status": status,
                "message": "Batch was rejected.",
            }

        # Steps 2-3: Select artifacts and process in chunks.
        if status in (
            BatchStatus.PENDING.value,
            BatchStatus.RUNNING.value,
        ):
            artifacts = await workflow.execute_activity(
                select_artifacts_activity,
                args=[plan.to_dict(), batch_id],
                start_to_close_timeout=timedelta(minutes=5),
            )

            if not artifacts:
                return {
                    "batch_id": batch_id,
                    "plan_id": plan.plan_id,
                    "status": "no_artifacts",
                    "message": "No artifacts matched the filter.",
                }

            # Process in chunks (resumable).
            chunk_size = 50
            total = len(artifacts)

            for i in range(0, total, chunk_size):
                chunk = artifacts[i : i + chunk_size]

                # Check how many artifacts are already in the batch
                # (resume from last completed chunk).
                existing_count = await workflow.execute_activity(
                    count_batch_artifacts_activity,
                    batch_id,
                    start_to_close_timeout=timedelta(seconds=30),
                )

                # If this chunk's range is already covered, skip it.
                if existing_count >= i + len(chunk):
                    continue

                # Process only the remaining artifacts in this chunk.
                remaining_start = max(0, existing_count - i)
                chunk_to_process = chunk[remaining_start:]

                if chunk_to_process:
                    await workflow.execute_activity(
                        run_parser_activity,
                        args=[plan.to_dict(), batch_id, chunk_to_process],
                        start_to_close_timeout=timedelta(hours=1),
                    )

        # Step 4: Compare.
        if status != BatchStatus.AWAITING_APPROVAL.value:
            await workflow.execute_activity(
                compare_batches_activity,
                batch_id,
                start_to_close_timeout=timedelta(minutes=10),
            )

        # Step 5: Wait for approval.
        approved = await self._await_approval()

        if not approved:
            return {
                "batch_id": batch_id,
                "plan_id": plan.plan_id,
                "status": BatchStatus.REJECTED.value,
                "message": "Backfill batch rejected.",
            }

        # Step 6: Promote exactly one batch.
        receipt = await workflow.execute_activity(
            promote_batch_activity,
            args=[plan.to_dict(), batch_id],
            start_to_close_timeout=timedelta(minutes=1),
        )

        return {
            "batch_id": batch_id,
            "plan_id": plan.plan_id,
            "status": BatchStatus.PROMOTED.value,
            "receipt": receipt,
            "message": "Backfill batch promoted. Old outputs retained.",
        }
