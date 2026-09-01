"""Temporal activities for the IRC certificate PDF capture workflow (DP-00-05).

Policy: interim-v0 (DP-00-01)

Activities:
  enumerate_certs_activity          — list cert numbers from DB
  fetch_and_store_pdf_batch_activity — fetch + store a batch of certs (heartbeat)
  write_ledger_activity              — persist the run ledger to DB / log

All activities are designed to be idempotent: re-running them after a
Temporal worker crash is safe — the content-addressed store deduplicates
already-stored PDFs and the DB upserts retrieval events by content hash.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from temporalio import activity

logger = logging.getLogger(__name__)


@activity.defn
async def enumerate_certs_activity() -> list[str]:
    """Return all distinct IRC cert numbers from the platform DB.

    Queries ``boats.cert_number`` and ``irc_certificates.cert_number``.
    Falls back to TCC listing CSVs if DB is unavailable.
    """
    from irc_data.db.connection import get_engine
    from irc_data.scrapers.irc_pdf import enumerate_cert_nos_from_db

    try:
        engine = get_engine()
        cert_nos = enumerate_cert_nos_from_db(engine)
        activity.logger.info("Enumerated %d cert numbers from DB", len(cert_nos))
        return cert_nos
    except Exception as exc:
        activity.logger.warning("DB enumeration failed (%s), trying TCC dir", exc)

    # Fallback: TCC listing CSVs
    from irc_data.config import TCC_LISTINGS_DIR
    from irc_data.scrapers.irc_pdf import enumerate_cert_nos_from_tcc_dir

    cert_nos = enumerate_cert_nos_from_tcc_dir(TCC_LISTINGS_DIR)
    activity.logger.info(
        "Enumerated %d cert numbers from TCC dir (fallback)", len(cert_nos)
    )
    return cert_nos


@activity.defn
async def fetch_and_store_pdf_batch_activity(
    cert_nos: list[str],
    max_fetches: int = 5000,
    enforce_window: bool = True,
) -> dict:
    """Fetch and store PDFs for a batch of cert numbers.

    Sends heartbeats during the loop so Temporal knows the activity is alive.
    Each cert requires two HTTP requests (POST search + GET PDF).

    Args:
        cert_nos: List of cert numbers to process.
        max_fetches: Maximum total HTTP requests (POST + GET combined).
        enforce_window: Abort if outside the nightly collection window.

    Returns:
        A ledger dict with run statistics.
    """
    from irc_data.scrapers.irc_pdf import scrape_irc_pdfs, get_default_store
    from irc_data.db.connection import get_engine

    store = get_default_store()

    try:
        db_engine = get_engine()
    except Exception:
        db_engine = None

    activity.logger.info(
        "fetch_and_store_pdf_batch_activity: %d certs, max_fetches=%d",
        len(cert_nos),
        max_fetches,
    )

    # We wrap scrape_irc_pdfs with periodic heartbeats.
    # Since scrape_irc_pdfs is synchronous, we run it in a thread executor
    # and heartbeat from within a thin wrapper.
    import asyncio

    loop = asyncio.get_event_loop()

    def _run_with_heartbeat() -> dict:
        """Synchronous wrapper that heartbeats after each cert block."""
        from irc_data.scrapers.irc_pdf import (
            RunLedger,
            _make_client,
            _polite_sleep,
            _is_source_enabled,
            search_cert,
            download_pdf,
            persist_raw_artifact,
            _write_retrieval_event,
            CURRENT_POLICY_VERSION as _CPV,
            SOURCE_SLUG,
            CONTENT_TYPE_PDF,
            ADAPTER_VERSION,
            COLLECTION_WINDOW_UK_START,
            COLLECTION_WINDOW_UK_END,
        )
        from irc_data.sources.policy import is_within_collection_window
        from irc_data.sources.provenance import persist_raw_artifact as _persist
        import hashlib, time

        ledger = RunLedger(source_slug=SOURCE_SLUG, policy_version=_CPV)

        if enforce_window and not is_within_collection_window(
            start=COLLECTION_WINDOW_UK_START, end=COLLECTION_WINDOW_UK_END
        ):
            ledger.finish("window_closed")
            return ledger.to_dict()

        if db_engine is not None and not _is_source_enabled(db_engine, SOURCE_SLUG):
            ledger.finish("kill_switch")
            return ledger.to_dict()

        client = _make_client()
        last_request = 0.0
        fetch_count = 0

        try:
            for idx, cert_no in enumerate(cert_nos):
                if fetch_count >= max_fetches:
                    break

                # Heartbeat every 10 certs
                if idx % 10 == 0:
                    try:
                        activity.heartbeat(f"processing cert {idx}/{len(cert_nos)}")
                    except Exception:
                        pass

                if db_engine is not None and not _is_source_enabled(db_engine, SOURCE_SLUG):
                    break

                last_request = _polite_sleep(last_request)
                fetch_count += 1

                try:
                    records, _ = search_cert(client, cert_no)
                except Exception as exc:
                    ledger.add_error(cert_no, f"search: {exc}")
                    continue

                if not records:
                    continue

                ledger.certs_found += len(records)

                for record in records:
                    if fetch_count >= max_fetches:
                        break

                    last_request = _polite_sleep(last_request)
                    fetch_count += 1

                    try:
                        pdf_bytes = download_pdf(client, record.download_url)
                    except Exception as exc:
                        ledger.add_error(cert_no, f"download: {exc}")
                        continue

                    sha = hashlib.sha256(pdf_bytes).hexdigest()
                    if store.exists(sha):
                        ledger.certs_unchanged += 1
                        continue

                    fetched_at = datetime.now(timezone.utc).isoformat()
                    content_hash, prov_ref = persist_raw_artifact(
                        store=store,
                        content=pdf_bytes,
                        source=SOURCE_SLUG,
                        requested_uri=record.download_url,
                        resolved_uri=record.download_url,
                        retrieved_at=fetched_at,
                        policy_version=_CPV,
                        headers_subset={
                            "Content-Type": CONTENT_TYPE_PDF,
                            "X-Cert-No": record.cert_no,
                            "X-Boat-Name": record.boat_name,
                            "X-Sail-No": record.sail_no,
                            "X-Filename": record.filename,
                            "X-Listing-Ref": record.listing_ref,
                        },
                        status=200,
                        adapter_version=ADAPTER_VERSION,
                    )

                    if db_engine is not None:
                        _write_retrieval_event(
                            db_engine,
                            prov_ref=prov_ref,
                            cert_no=record.cert_no,
                            boat_name=record.boat_name,
                            sail_no=record.sail_no,
                            filename=record.filename,
                            byte_size=len(pdf_bytes),
                        )

                    ledger.certs_new += 1
        finally:
            client.close()
            ledger.fetch_count = fetch_count
            ledger.finish("ok")

        return ledger.to_dict()

    # Run the synchronous scraper in an executor thread
    result = await loop.run_in_executor(None, _run_with_heartbeat)
    return result


@activity.defn
async def write_ledger_activity(summary: dict) -> str:
    """Persist the run ledger to the database ingestion_log and return JSON.

    Falls back gracefully — if the DB write fails, logs the error but
    does not raise (the workflow should still complete successfully).
    """
    import json as _json

    # Log to structured output regardless of DB availability
    activity.logger.info(
        "IRC PDF capture run complete: %s",
        _json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k != "errors"
            }
        ),
    )

    # Persist to DB ingestion_log if available
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
                    "source": "irc-certs-pdf",
                    "started_at": summary.get("started_at"),
                    "completed_at": summary.get("finished_at"),
                    "status": summary.get("status", "ok"),
                    "found": summary.get("certs_found", 0),
                    "new": summary.get("certs_new", 0),
                    "updated": summary.get("certs_unchanged", 0),
                    "error_msg": (
                        _json.dumps(summary.get("errors", [])[:5])
                        if summary.get("errors")
                        else None
                    ),
                    "metadata": _json.dumps(
                        {
                            "fetch_count": summary.get("fetch_count"),
                            "certs_unchanged": summary.get("certs_unchanged"),
                            "certs_total": summary.get("certs_total"),
                            "policy_version": summary.get("policy_version"),
                            "error_count": summary.get("error_count"),
                            "adapter_version": "dp-00-05/1.0",
                        }
                    ),
                },
            )
    except Exception as exc:
        activity.logger.warning("Failed to write ledger to DB: %s", exc)

    return _json.dumps(summary, default=str)
