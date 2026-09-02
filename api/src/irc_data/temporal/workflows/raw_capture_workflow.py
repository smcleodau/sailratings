"""Temporal workflow for nightly Sailwave + sailing-news raw capture (DP-00-04).

Policy: v1.0 (DP-01-02; supersedes interim-v0 / DP-00-01)

Nightly schedule: 01:30 UTC (inside the 01:00–06:00 collection window),
after the IRC certificate PDF capture at 01:00.

Workflow structure
------------------
  list_sources_activity     — the DP-00-04 source slugs (sailwave, sailing-news)
  capture_source_activity   — per-source: discover → fetch → hash → store
  write_ledger_activity     — persist the aggregated run ledger

The workflow is idempotent: re-running it within the same nightly window is
safe — the content-addressed raw store deduplicates unchanged bytes and the
conditional-request layer turns unchanged pages into HTTP 304 no-ops.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from irc_data.temporal.activities.raw_capture_activities import (
        capture_source_activity,
        list_sources_activity,
        write_ledger_activity,
    )

logger = logging.getLogger(__name__)

ACTIVITY_TIMEOUT = timedelta(hours=2)
HEARTBEAT_TIMEOUT = timedelta(minutes=5)


@workflow.defn
class NightlyRawCaptureWorkflow:
    """Nightly raw capture for DP-00-04 sources (Sailwave + sailing news).

    Policy: v1.0.  Triggered at 01:30 UTC by the scheduler.
    """

    @workflow.run
    async def run(self, params: dict | None = None) -> dict:
        params = params or {}
        max_fetches: int = params.get("max_fetches", 5_000)
        enforce_window: bool = params.get("enforce_window", True)

        workflow.logger.info(
            "Starting NightlyRawCaptureWorkflow (max_fetches=%d, enforce_window=%s)",
            max_fetches,
            enforce_window,
        )

        sources: list[str] = await workflow.execute_activity(
            list_sources_activity,
            start_to_close_timeout=timedelta(minutes=2),
        )

        if not sources:
            workflow.logger.warning("No DP-00-04 sources configured — aborting")
            return {"status": "no_sources", "sources": []}

        per_source: list[dict] = []
        for source_slug in sources:
            workflow.logger.info("Capturing source: %s", source_slug)
            ledger: dict = await workflow.execute_activity(
                capture_source_activity,
                args=[source_slug, max_fetches, enforce_window],
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
            )
            per_source.append(ledger)

            if ledger.get("status") == "kill_switch":
                workflow.logger.warning("Kill switch triggered for %s — continuing", source_slug)
            if ledger.get("status") == "window_closed":
                workflow.logger.warning("Collection window closed — stopping")
                break

        summary = _aggregate(per_source)
        await workflow.execute_activity(
            write_ledger_activity,
            args=[summary],
            start_to_close_timeout=timedelta(minutes=5),
        )

        workflow.logger.info(
            "NightlyRawCaptureWorkflow complete: new=%d unchanged=%d not_modified=%d errors=%d",
            summary.get("urls_new", 0),
            summary.get("urls_unchanged", 0),
            summary.get("urls_not_modified", 0),
            summary.get("error_count", 0),
        )
        return summary


def _aggregate(ledgers: list[dict]) -> dict:
    """Aggregate per-source ledgers into a single run summary."""
    total = {
        "source_slug": "dp-00-04",
        "policy_version": "v1.0",
        "urls_attempted": 0,
        "urls_fetched": 0,
        "urls_new": 0,
        "urls_unchanged": 0,
        "urls_not_modified": 0,
        "urls_skipped": 0,
        "fetch_count": 0,
        "bytes_downloaded": 0,
        "error_count": 0,
        "errors": [],
        "status": "ok",
        "sources": [],
    }
    for ledger in ledgers:
        for key in (
            "urls_attempted",
            "urls_fetched",
            "urls_new",
            "urls_unchanged",
            "urls_not_modified",
            "urls_skipped",
            "fetch_count",
            "bytes_downloaded",
            "error_count",
        ):
            total[key] += ledger.get(key, 0)
        total["errors"].extend(ledger.get("errors", [])[:5])
        total["sources"].append(ledger.get("source_slug"))
        if ledger.get("status") in ("kill_switch", "window_closed", "error"):
            total["status"] = ledger["status"]
    # earliest started_at / latest finished_at
    started = [l.get("started_at") for l in ledgers if l.get("started_at")]
    finished = [l.get("finished_at") for l in ledgers if l.get("finished_at")]
    if started:
        total["started_at"] = min(started)
    if finished:
        total["finished_at"] = max(finished)
    total["errors"] = total["errors"][:50]
    return total
