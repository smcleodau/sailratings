"""Temporal activities for the DP-00-04 nightly raw-capture workflow.

Policy: v1.0 (DP-01-02; supersedes interim-v0 / DP-00-01)

Activities
----------
  list_sources_activity      — return the DP-00-04 source slugs
  capture_source_activity    — run one source's capture (heartbeat-friendly)
  write_ledger_activity      — persist the run ledger to ``ingestion_log``

All activities are idempotent: the content-addressed raw store deduplicates
bytes, and ``retrieval_events`` rows are append-only audit records.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from temporalio import activity

logger = logging.getLogger(__name__)


@activity.defn
async def list_sources_activity() -> list[str]:
    """Return the source slugs this workflow is responsible for (DP-00-04)."""
    from irc_data.scrapers.raw_capture import DP_00_04_SOURCES

    return list(DP_00_04_SOURCES)


@activity.defn
async def capture_source_activity(
    source_slug: str,
    max_fetches: int = 5_000,
    enforce_window: bool = True,
) -> dict[str, Any]:
    """Run the nightly raw capture for a single source.

    Executes the synchronous capture loop in a thread executor and sends
    heartbeats.  Returns the :class:`CaptureLedger` as a dict.
    """
    import asyncio

    from irc_data.scrapers.raw_capture import run_nightly

    try:
        from irc_data.db.connection import get_engine

        db_engine = get_engine()
    except Exception:
        db_engine = None

    activity.logger.info(
        "capture_source_activity: source=%s max_fetches=%d enforce_window=%s",
        source_slug,
        max_fetches,
        enforce_window,
    )

    loop = asyncio.get_event_loop()

    def _run() -> dict[str, Any]:
        # Heartbeat before starting (the capture itself is synchronous;
        # heartbeats during long runs are emitted from within the scraper
        # via activity.heartbeat where supported).
        try:
            activity.heartbeat(f"starting {source_slug}")
        except Exception:
            pass

        ledger = run_nightly(
            source_slug,
            db_engine=db_engine,
            enforce_window=enforce_window,
            max_fetches=max_fetches,
        )
        return ledger.to_dict()

    return await loop.run_in_executor(None, _run)


@activity.defn
async def write_ledger_activity(summary: dict[str, Any]) -> str:
    """Persist the aggregated run ledger to ``ingestion_log`` and return JSON.

    Fails open: a DB error is logged but does not raise, so the workflow
    completes even when the DB is unavailable.
    """
    import json as _json

    activity.logger.info(
        "Raw capture run complete: %s",
        _json.dumps({k: v for k, v in summary.items() if k != "errors"}),
    )

    try:
        from irc_data.db.connection import get_engine
        from sqlalchemy import text

        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO ingestion_log
                      (source, started_at, completed_at, status, records_found,
                       records_new, records_updated, error_message, metadata)
                    VALUES
                      (:source, :started_at, :completed_at, :status,
                       :found, :new, :updated, :error_msg, :metadata::json)
                    """
                ),
                {
                    "source": summary.get("source_slug", "dp-00-04"),
                    "started_at": summary.get("started_at"),
                    "completed_at": summary.get("finished_at"),
                    "status": summary.get("status", "ok"),
                    "found": summary.get("urls_attempted", 0),
                    "new": summary.get("urls_new", 0),
                    "updated": summary.get("urls_unchanged", 0),
                    "error_msg": (
                        _json.dumps(summary.get("errors", [])[:5])
                        if summary.get("errors")
                        else None
                    ),
                    "metadata": _json.dumps(
                        {
                            "fetch_count": summary.get("fetch_count"),
                            "urls_not_modified": summary.get("urls_not_modified"),
                            "urls_skipped": summary.get("urls_skipped"),
                            "bytes_downloaded": summary.get("bytes_downloaded"),
                            "policy_version": summary.get("policy_version"),
                            "error_count": summary.get("error_count"),
                            "adapter_version": "dp-00-04/1.0",
                        }
                    ),
                },
            )
    except Exception as exc:
        activity.logger.warning("Failed to write ledger to DB: %s", exc)

    return _json.dumps(summary, default=str)
