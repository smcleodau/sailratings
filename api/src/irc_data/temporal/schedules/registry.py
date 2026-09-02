"""OPS-01-02 — ``ScheduleRegistry``: the register drives the schedules.

For every row in the ``data_sources`` register that is **enabled** and
**approved**, there is exactly one Temporal schedule of the form
``source-<slug>``.  When the register changes:

  * **add source (enabled+approved)**  → schedule is *created*
  * **change cadence**                 → schedule is *updated*
  * **disable source / un-approve**    → schedule is *paused*
  * **re-enable**                      → schedule is *unpaused*

The registry never deletes schedules (deletion loses history); a disabled
source's schedule is paused instead — this is what makes "disabling a source
pauses its schedule within one cycle" testable.

Usage
-----
::

    from irc_data.temporal.schedules.registry import ScheduleRegistry

    registry = await ScheduleRegistry.connect()
    summary = await registry.sync_from_register(engine)

The reconciliation loop is also available as a long-running Temporal
workflow (:class:`ScheduleSyncLoopWorkflow`) so the "sync" is itself a
Temporal workflow and doesn't rely on cron.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
)
from temporalio.common import RetryPolicy

from irc_data.temporal.schedules.cadence import (
    cadence_to_timedelta,
    schedule_id_for_slug,
)

#: Task queue the SourceRunWorkflow runs on.
SOURCE_RUN_TASK_QUEUE = os.environ.get("SOURCE_RUN_TASK_QUEUE", "data-pipeline")

#: Default workflow-level retry policy attached to each scheduled action.
_SOURCE_RUN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=10),
    maximum_attempts=3,
)


@dataclass
class SyncSummary:
    """Result of one ``sync_from_register`` pass."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    paused: list[str] = field(default_factory=list)
    unpaused: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "created": list(self.created),
            "updated": list(self.updated),
            "paused": list(self.paused),
            "unpaused": list(self.unpaused),
            "unchanged": list(self.unchanged),
        }


class ScheduleRegistry:
    """Reconciles Temporal schedules with the Data Source Register."""

    def __init__(self, client: Client, task_queue: str = SOURCE_RUN_TASK_QUEUE):
        self.client = client
        self.task_queue = task_queue

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    async def connect(
        cls,
        address: str | None = None,
        namespace: str | None = None,
        task_queue: str = SOURCE_RUN_TASK_QUEUE,
    ) -> "ScheduleRegistry":
        """Connect to the Temporal server and return a registry."""
        addr = address or os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
        ns = namespace or os.environ.get("TEMPORAL_NAMESPACE", "sailratings")
        client = await Client.connect(addr, namespace=ns)
        return cls(client, task_queue=task_queue)

    # ------------------------------------------------------------------
    # Schedule construction
    # ------------------------------------------------------------------

    def _build_schedule(self, source: Any, *, paused: bool, note: str) -> Schedule:
        """Build the desired Temporal schedule for a register row.

        ``source`` may be an ORM ``DataSource`` row or any object with
        ``slug``/``base_url``/``cadence`` attributes.

        OPS-01-01: the workflow-level retry policy is derived from the
        register's per-source ``retry_policy`` field (``max_attempts`` +
        ``backoff_seconds`` from ``docs/SCHEDULING-POLICY.md``), falling
        back to :data:`_SOURCE_RUN_RETRY` when the row predates the
        scheduling-policy columns.
        """
        slug = source.slug
        interval = cadence_to_timedelta(getattr(source, "cadence", None) or "nightly")
        schedule_id = schedule_id_for_slug(slug)

        action = ScheduleActionStartWorkflow(
            "SourceRunWorkflow",
            args=[slug, "scheduled"],  # run_key filled by workflow via its id
            id=f"source-run-{slug}",
            task_queue=self.task_queue,
            retry_policy=self._retry_policy_for(source),
        )

        return Schedule(
            action=action,
            spec=ScheduleSpec(
                intervals=[ScheduleIntervalSpec(every=interval)],
                jitter=None,  # jitter is applied inside the workflow
            ),
            policy=SchedulePolicy(
                overlap=ScheduleOverlapPolicy.SKIP,  # one in-flight run per source
                catchup_window=timedelta(minutes=10),
                pause_on_failure=False,
            ),
            state=ScheduleState(
                note=note,
                paused=paused,
            ),
        )

    @staticmethod
    def _retry_policy_for(source: Any) -> RetryPolicy:
        """Resolve the Temporal ``RetryPolicy`` from the register row.

        Reads the OPS-01-01 ``retry_policy`` register field
        (``{"max_attempts": int, "backoff_seconds": […]}``).  Falls back to
        :data:`_SOURCE_RUN_RETRY` when the field is absent or malformed so
        legacy rows still schedule safely.
        """
        raw = getattr(source, "retry_policy", None)
        if isinstance(raw, dict):
            try:
                from irc_data.sources.scheduling import RetryPolicy as _RP

                rp = _RP.from_value(raw)
                initial = timedelta(seconds=rp.backoff_seconds[0])
                maximum = timedelta(seconds=rp.backoff_seconds[-1])
                return RetryPolicy(
                    initial_interval=initial,
                    backoff_coefficient=(
                        rp.backoff_seconds[1] / rp.backoff_seconds[0]
                        if len(rp.backoff_seconds) > 1 else 1.0
                    ),
                    maximum_interval=maximum,
                    maximum_attempts=rp.max_attempts,
                )
            except Exception:
                pass
        return _SOURCE_RUN_RETRY

    # ------------------------------------------------------------------
    # Public API — upsert / pause / sync
    # ------------------------------------------------------------------

    async def ensure_schedule(self, source: Any) -> str:
        """Create or update the schedule for *source*; return the schedule id.

        * Idempotent: re-calling with an unchanged source is a no-op
          ("unchanged").
        * Disabled / un-approved sources get a *paused* schedule (so the
          mapping register→schedule is total).
        """
        slug = source.slug
        schedule_id = schedule_id_for_slug(slug)
        desired_paused = not (bool(source.enabled) and source.legal_status == "approved")
        note = (
            f"register: {'enabled' if not desired_paused else 'paused'}"
            f" cadence={getattr(source, 'cadence', None) or 'nightly'}"
        )

        desired = self._build_schedule(source, paused=desired_paused, note=note)
        handle = self.client.get_schedule_handle(schedule_id)

        try:
            existing = await handle.describe()
        except Exception:
            existing = None  # not found → create

        if existing is None:
            await self.client.create_schedule(schedule_id, desired)
            return "created"

        # Compare desired vs actual (interval + paused).  Only update when
        # something actually changed to avoid pointless schedule churn.
        actual_interval = None
        try:
            if existing.schedule.spec.intervals:
                actual_interval = existing.schedule.spec.intervals[0].every
        except Exception:
            actual_interval = None
        desired_interval = desired.spec.intervals[0].every
        actual_paused = bool(existing.schedule.state.paused)

        spec_changed = actual_interval != desired_interval
        state_changed = actual_paused != desired_paused

        if spec_changed:
            from temporalio.client import ScheduleUpdate

            async def _apply(input: Any) -> None:
                new_schedule = input.description.schedule
                new_schedule.spec = desired.spec
                input.schedule = ScheduleUpdate(schedule=new_schedule)

            await handle.update(_apply)

        # paused state is authoritative via pause()/unpause() (the update
        # path does not reliably flip the paused bit on this server version).
        if state_changed:
            if desired_paused:
                await handle.pause(note=note)
            else:
                await handle.unpause(note=note)

        if desired_paused:
            return "paused"
        return "unpaused" if actual_paused else ("updated" if spec_changed else "unchanged")

    async def pause_schedule(self, slug: str, note: str = "disabled in register") -> bool:
        """Pause the schedule for *slug*.  Returns True if it existed."""
        handle = self.client.get_schedule_handle(schedule_id_for_slug(slug))
        try:
            await handle.describe()
        except Exception:
            return False
        await handle.pause(note=note)
        return True

    async def trigger(self, slug: str) -> None:
        """Fire a source's schedule immediately (used by tests / manual runs)."""
        handle = self.client.get_schedule_handle(schedule_id_for_slug(slug))
        await handle.trigger()

    # ------------------------------------------------------------------
    # The reconciliation pass
    # ------------------------------------------------------------------

    async def sync_from_register(self, engine: Any) -> dict:
        """Create/update/pause every register row's schedule.

        This is the single entry point used by both the long-running
        ``ScheduleSyncLoopWorkflow`` and ad-hoc calls (tests, admin).
        Returns a :class:`SyncSummary` dict.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from irc_data.sources.registry import DataSource

        summary = SyncSummary()
        with Session(engine) as session:
            rows = session.execute(select(DataSource).order_by(DataSource.slug)).scalars().all()
            # detach — we only need the column values
            sources = [
                type(
                    "_Src",
                    (),
                    {
                        "slug": r.slug,
                        "base_url": r.base_url,
                        "cadence": getattr(r, "cadence", None) or "nightly",
                        "enabled": bool(r.enabled),
                        "legal_status": r.legal_status,
                    },
                )()
                for r in rows
            ]

        for source in sources:
            try:
                outcome = await self.ensure_schedule(source)
            except ScheduleAlreadyRunningError:
                outcome = "unchanged"
            # bucket the outcome
            if outcome == "created":
                summary.created.append(source.slug)
            elif outcome in ("paused",):
                summary.paused.append(source.slug)
            elif outcome == "unpaused":
                summary.unpaused.append(source.slug)
            elif outcome == "updated":
                summary.updated.append(source.slug)
            else:
                summary.unchanged.append(source.slug)

            # Mirror the desired state in the DB (best-effort, useful when
            # Temporal is unreachable in dev).
            self._mirror_state(engine, source)

        return summary.to_dict()

    # ------------------------------------------------------------------
    # DB mirror (so `psql` can see the desired schedule state)
    # ------------------------------------------------------------------

    def _mirror_state(self, engine: Any, source: Any) -> None:
        try:
            from datetime import datetime, timezone

            from sqlalchemy import text

            paused = not (bool(source.enabled) and source.legal_status == "approved")
            now = datetime.now(timezone.utc)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO source_schedule_state
                            (source_slug, schedule_id, cadence, paused, notes, last_synced_at)
                        VALUES (:slug, :schedule_id, :cadence, :paused, :notes, :now)
                        ON CONFLICT (source_slug) DO UPDATE SET
                            cadence = EXCLUDED.cadence,
                            paused = EXCLUDED.paused,
                            notes = EXCLUDED.notes,
                            last_synced_at = EXCLUDED.last_synced_at
                        """
                    ),
                    {
                        "slug": source.slug,
                        "schedule_id": schedule_id_for_slug(source.slug),
                        "cadence": getattr(source, "cadence", None) or "nightly",
                        "paused": paused,
                        "notes": "enabled" if not paused else "disabled/unapproved",
                        "now": now,
                    },
                )
        except Exception:
            # Mirroring is best-effort; the Temporal schedule is authoritative.
            pass


# ---------------------------------------------------------------------------
# The reconciliation loop as a Temporal workflow
# ---------------------------------------------------------------------------

with workflow.unsafe.imports_passed_through():
    from irc_data.temporal.ledger import activities as ledger_activities


@workflow.defn
class ScheduleSyncLoopWorkflow:
    """Long-running loop that keeps schedules in sync with the register.

    Runs as a Temporal workflow so the sync itself is durable, observable and
    doesn't depend on cron.  Re-runs the reconciliation every
    ``poll_seconds`` (default 5 min) until cancelled.  Uses
    ``continue_as_new`` periodically to keep history bounded.
    """

    @workflow.run
    async def run(self, poll_seconds: int = 300, cycles: int = 0) -> dict:  # noqa: ARG002
        max_cycles_per_run = 288  # ~24h at 5min cadence, then continue-as-new
        summary = await workflow.execute_activity(
            ledger_activities.sync_schedules_from_register,
            args=[],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(minutes=1),
                maximum_attempts=5,
            ),
        )
        if cycles + 1 >= max_cycles_per_run:
            workflow.continue_as_new(args=[poll_seconds, 0])
        await workflow.sleep(timedelta(seconds=poll_seconds))
        workflow.continue_as_new(args=[poll_seconds, cycles + 1])
        return summary  # pragma: no cover - continue_as_new never returns
