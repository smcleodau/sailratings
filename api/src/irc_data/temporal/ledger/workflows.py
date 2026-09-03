"""OPS-01-02 — ``SourceRunWorkflow``: the wrapper every source run goes through.

A single workflow definition wraps *any* registered adapter — interim DP-00
jobs (CLI scrapers) or DP-01 SDK adapters — so that the schedule registry
has exactly one workflow type to reason about.

Guarantees (OPS-01-02 acceptance criteria):

* **Nothing runs that isn't registered & enabled** — the first activity
  re-reads the register row and raises (non-retryably) if the source is
  disabled or not approved.
* **Idempotent** — the run is keyed by ``(source_slug, run_key)``; opening
  the ledger row is an upsert, closing is an update by the same key.
* **Jitter** — a deterministic-per-run random delay before any work so a
  full registry of nightly jobs doesn't fire simultaneously.
* **Backoff** — activities retry with exponential backoff (per activity).
* **Concurrency cap per domain** — a worker-process-local semaphore
  derived from the register's ``base_url`` host (SPEC-13 §3.2).
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from irc_data.temporal.ledger import activities as ledger_activities
    from irc_data.temporal.schedules.cadence import (
        MAX_JITTER_FRACTION,
        cadence_to_timedelta,
    )

#: Hard upper bound on the per-run start jitter.  Keeps the register from
#: thundering-herding at the top of the schedule while guaranteeing a
#: scheduled fire actually runs (and writes its ledger row) promptly.
MAX_JITTER_SECONDS = 120.0

#: Activities that fail fast when the register says "no" should not retry.
_NON_RETRYABLE = ("SourceNotApprovedError", "SourceDisabledError")


@workflow.defn
class SourceRunWorkflow:
    """Wrapper workflow: one run of one registered source.

    Inputs
    ------
    source_slug : str
        The governed source slug from the Data Source Register.
    run_key : str
        Idempotency key for this run (e.g. ``"scheduled:source-sailsys:2026-09-02T00:00"``
        or ``"ad-hoc:<uuid>"``).  Combined with the slug it uniquely
        identifies the ledger row and the workflow id.
    """

    @workflow.run
    async def run(self, source_slug: str, run_key: str) -> dict:
        workflow_id = workflow.info().workflow_id

        # --------------------------------------------------------------
        # 1. Fetch + validate the register record (fail fast, no retry).
        # --------------------------------------------------------------
        record = await workflow.execute_activity(
            ledger_activities.fetch_source_record,
            args=[source_slug],
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=2),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=3,
                non_retryable_error_types=_NON_RETRYABLE,
            ),
        )

        cadence_interval = cadence_to_timedelta(record.get("cadence"))
        # Spread the thundering herd, but never delay a single fire by more
        # than MAX_JITTER_SECONDS — a 5 %-of-nightly jitter (~45 min) stalls a
        # scheduled run for far longer than the collection itself and makes
        # "run → ledger row" unobservable in practice.
        jitter_cap = min(
            MAX_JITTER_SECONDS,
            max(1.0, cadence_interval.total_seconds() * MAX_JITTER_FRACTION),
        )

        # Deterministic jitter seeded by the run identity → stable on replay.
        rng = workflow.random()
        jitter_seconds = rng.uniform(0.0, jitter_cap)
        await workflow.sleep(timedelta(seconds=jitter_seconds))

        # --------------------------------------------------------------
        # 2. Open the ledger row (idempotent upsert on (slug, run_key)).
        #    OPS-02-04: also opens the ingestion_log mirror row(s); the ids
        #    come back so the close activity updates the same rows.
        # --------------------------------------------------------------
        opened = await workflow.execute_activity(
            ledger_activities.open_source_run,
            args=[source_slug, run_key, "schedule", workflow_id],
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=2),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=5,
            ),
        )
        ingestion_log_ids = (opened or {}).get("ingestion_log_ids") or {}
        has_prior_success = bool((opened or {}).get("has_prior_success"))

        # OPS-02-04: per-source timeout derived from data_sources — the
        # register row's ``run_timeout_seconds`` (when set) else the
        # cadence-scaled default (one cadence interval, floored at 1 h,
        # capped at 6 h). A source's very first run — before any prior
        # success is on record — is a full historical backfill, not a
        # steady-state incremental sync, and routinely runs far longer
        # than its cadence would otherwise budget (observed: sailsys
        # exhausted a cadence-derived 1 h budget on backfill and failed
        # terminally with the schedule about to retry the same doomed
        # full-scrape every cadence tick). Give it the full 6 h ceiling
        # regardless of cadence; once it succeeds once, later runs go
        # incremental (see legacy_adapters._last_successful_run_since)
        # and comfortably fit the cadence-scaled budget.
        timeout_seconds = record.get("run_timeout_seconds")
        try:
            run_timeout = timedelta(seconds=float(timeout_seconds))
        except (TypeError, ValueError):
            if not has_prior_success:
                run_timeout = timedelta(hours=6)
            else:
                run_timeout = min(
                    timedelta(hours=6),
                    max(timedelta(hours=1), cadence_interval),
                )

        # --------------------------------------------------------------
        # 3. Run the registered adapter (bounded retries + backoff +
        #    per-domain concurrency cap enforced inside the activity).
        # --------------------------------------------------------------
        result: dict = {"records_written": 0}
        status = "success"
        detail = "ok"
        try:
            result = await workflow.execute_activity(
                ledger_activities.run_registered_adapter,
                args=[record, run_key],
                start_to_close_timeout=run_timeout,
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=10),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(minutes=5),
                    maximum_attempts=4,
                    non_retryable_error_types=_NON_RETRYABLE,
                ),
            )
        except ApplicationError as exc:  # non-retryable (e.g. disabled mid-run)
            status = "failed"
            detail = f"{exc.type}: {exc.message}"
        except Exception as exc:  # exhausted retries / unexpected
            status = "failed"
            detail = f"{type(exc).__name__}: {exc}"

        # --------------------------------------------------------------
        # 4. Close the ledger row — always, so a run → a ledger row
        #    (and an ingestion_log row, until the admin reads source_runs).
        # --------------------------------------------------------------
        await workflow.execute_activity(
            ledger_activities.close_source_run,
            args=[source_slug, run_key, status, detail, result, None, ingestion_log_ids],
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=2),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=5,
            ),
        )

        if status != "success":
            # Surface the failure to Temporal so the schedule shows it.
            raise ApplicationError(detail, type="SourceRunFailed", non_retryable=True)

        return {
            "source_slug": source_slug,
            "run_key": run_key,
            "status": status,
            "jitter_seconds": round(jitter_seconds, 3),
            "result": result,
        }
