"""Temporal workflow for nightly IRC certificate PDF capture (DP-00-05).

Policy: v1.0 (DP-01-02; supersedes interim-v0 / DP-00-01)

Nightly schedule: 01:00 UK (runs inside the collection window).

Workflow structure:
  enumerate_certs_activity     — query DB for all known cert numbers
  fetch_and_store_pdf_activity — per-cert: search + download + store (with heartbeat)
  write_ledger_activity        — persist the run ledger

The workflow is designed to be idempotent: re-running it within the same
nightly window is safe — already-stored PDFs are skipped.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from irc_data.temporal.activities.irc_pdf_activities import (
        enumerate_certs_activity,
        fetch_and_store_pdf_batch_activity,
        write_ledger_activity,
    )

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CERTS_PER_BATCH = 500          # certs per activity call
ACTIVITY_TIMEOUT = timedelta(hours=2)
HEARTBEAT_TIMEOUT = timedelta(minutes=5)


@workflow.defn
class NightlyIrcPdfCaptureWorkflow:
    """Nightly IRC certificate PDF raw-capture workflow (DP-00-05).

    Policy: v1.0

    Triggered at 01:00 UK by the scheduler. Enumerates all known cert
    numbers, fetches their PDFs from ircrating.org, and stores them in
    the content-addressed raw object store.

    Uses batched activities with heartbeats so that long runs survive
    Temporal worker restarts.
    """

    @workflow.run
    async def run(self, params: dict | None = None) -> dict:
        """Run the nightly IRC PDF capture pipeline.

        Returns a summary dict with run statistics.
        """
        params = params or {}
        max_fetches: int = params.get("max_fetches", 5000)
        enforce_window: bool = params.get("enforce_window", True)

        workflow.logger.info(
            "Starting NightlyIrcPdfCaptureWorkflow (max_fetches=%d, enforce_window=%s)",
            max_fetches,
            enforce_window,
        )

        # --- Step 1: Enumerate cert numbers ---
        cert_nos: list[str] = await workflow.execute_activity(
            enumerate_certs_activity,
            start_to_close_timeout=timedelta(minutes=10),
        )

        if not cert_nos:
            workflow.logger.warning("No cert numbers found — aborting")
            return {"status": "no_certs", "certs_total": 0}

        workflow.logger.info("Found %d cert numbers to process", len(cert_nos))

        # --- Step 2: Batch fetch-and-store ---
        # Split into batches for manageability and heartbeat-friendly sizes
        batches = [
            cert_nos[i : i + MAX_CERTS_PER_BATCH]
            for i in range(0, len(cert_nos), MAX_CERTS_PER_BATCH)
        ]

        all_ledgers: list[dict] = []
        total_fetches = 0

        for batch_idx, batch in enumerate(batches):
            # Stay under nightly cap
            remaining = max_fetches - total_fetches
            if remaining <= 0:
                workflow.logger.info("Reached max_fetches cap — stopping batches")
                break

            workflow.logger.info(
                "Processing batch %d/%d (%d certs, remaining_cap=%d)",
                batch_idx + 1,
                len(batches),
                len(batch),
                remaining,
            )

            ledger_dict: dict = await workflow.execute_activity(
                fetch_and_store_pdf_batch_activity,
                args=[batch, min(remaining * 2, max_fetches), enforce_window],
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
            )

            all_ledgers.append(ledger_dict)
            total_fetches += ledger_dict.get("fetch_count", 0)

            if ledger_dict.get("status") == "kill_switch":
                workflow.logger.warning("Kill switch triggered — stopping")
                break
            if ledger_dict.get("status") == "window_closed":
                workflow.logger.warning("Collection window closed — stopping")
                break

        # --- Step 3: Write aggregated ledger ---
        summary = _aggregate_ledgers(all_ledgers)
        summary["certs_total"] = len(cert_nos)
        summary["batches"] = len(all_ledgers)

        await workflow.execute_activity(
            write_ledger_activity,
            args=[summary],
            start_to_close_timeout=timedelta(minutes=5),
        )

        workflow.logger.info(
            "NightlyIrcPdfCaptureWorkflow complete: new=%d, unchanged=%d, errors=%d",
            summary.get("certs_new", 0),
            summary.get("certs_unchanged", 0),
            summary.get("error_count", 0),
        )

        return summary


def _aggregate_ledgers(ledgers: list[dict]) -> dict:
    """Aggregate batch ledger dicts into a single summary."""
    total = {
        "source_slug": "irc-certs",
        "policy_version": "v1.0",
        "certs_found": 0,
        "certs_new": 0,
        "certs_unchanged": 0,
        "fetch_count": 0,
        "error_count": 0,
        "errors": [],
        "status": "ok",
    }

    for ledger in ledgers:
        total["certs_found"] += ledger.get("certs_found", 0)
        total["certs_new"] += ledger.get("certs_new", 0)
        total["certs_unchanged"] += ledger.get("certs_unchanged", 0)
        total["fetch_count"] += ledger.get("fetch_count", 0)
        total["error_count"] += ledger.get("error_count", 0)
        total["errors"].extend(ledger.get("errors", [])[:5])

        # Surface terminal statuses
        if ledger.get("status") in ("kill_switch", "window_closed", "error"):
            total["status"] = ledger["status"]

    # Cap stored errors
    total["errors"] = total["errors"][:50]
    return total
