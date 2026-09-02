"""Quality gates API (DP-05-02) — validation, quarantine and promotion.

Admin-credential endpoints backing the batch review UI:

  GET  /admin/quality/batches                     — batch versions, filterable
                                                    by pipeline / source /
                                                    status
  GET  /admin/quality/batches/{batch_key}         — batch detail (rows,
                                                    verdicts, quarantine,
                                                    receipt)
  GET  /admin/quality/quarantine                  — open quarantine queue
  GET  /admin/quality/quarantine/{quarantine_id}  — quarantine detail with
                                                    rule failures + samples
  POST /admin/quality/batches/{batch_key}/promote — explicit promotion
                                                    (the only transition
                                                    that changes
                                                    consumer-visible state)
  GET  /admin/quality/consumer-view               — the promoted-only view
                                                    a consumer would see
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db
from irc_data.api.routers.admin import _verify_admin
from irc_data.quality import gate_store
from irc_data.quality.gate_store import PromotionError

router = APIRouter(prefix="/admin/quality", tags=["Admin"])


def _batch_key_or_400(batch_key: str) -> str:
    try:
        pipeline, rest = batch_key.split(":", 1)
        source_slug, version = rest.rsplit(":v", 1)
        int(version)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"batch_key {batch_key!r} malformed "
                   f"(expected <pipeline>:<source_slug>:v<version>)",
        )
    return batch_key


@router.get("/batches")
async def list_batches(
    pipeline: str | None = None,
    source_slug: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """List batch versions with their gate status (newest first)."""
    _verify_admin(authorization)
    batches = gate_store.list_batches(
        engine,
        pipeline=pipeline,
        source_slug=source_slug,
        status=status,
        limit=limit,
    )
    return {"count": len(batches), "batches": batches}


@router.get("/batches/{batch_key}")
async def batch_detail(
    batch_key: str,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Batch detail: staged rows, verdicts, quarantine record, receipt."""
    _verify_admin(authorization)
    _batch_key_or_400(batch_key)
    batch = gate_store.get_batch(engine, batch_key)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"batch {batch_key!r} not found")

    rows = gate_store.get_batch_rows(engine, batch_key)
    verdicts = [v.to_dict() for v in gate_store.get_verdicts(engine, batch_key)]
    quarantine = gate_store.get_quarantine(engine, batch_key)
    receipt = gate_store.get_receipt(engine, batch_key)
    return {
        "batch": batch,
        "rows": rows[:200],  # bounded for the UI
        "row_count": len(rows),
        "verdicts": verdicts,
        "quarantine": quarantine.to_dict() if quarantine else None,
        "receipt": receipt.to_dict() if receipt else None,
    }


@router.get("/quarantine")
async def quarantine_queue(
    status: str = Query(default="open"),
    pipeline: str | None = None,
    source_slug: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """The quarantine review queue (default: open records)."""
    _verify_admin(authorization)
    records = gate_store.list_quarantine(
        engine,
        status=status,
        pipeline=pipeline,
        source_slug=source_slug,
        limit=limit,
    )
    return {"count": len(records), "quarantine": records}


@router.get("/quarantine/{quarantine_id}")
async def quarantine_detail(
    quarantine_id: str,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Quarantine detail: rule failures and sample rows."""
    _verify_admin(authorization)
    # Look up by quarantine_id via the queue list (bounded).
    records = gate_store.list_quarantine(engine, status="open", limit=500)
    records += gate_store.list_quarantine(engine, status="released", limit=500)
    records += gate_store.list_quarantine(engine, status="overridden", limit=500)
    match = next(
        (r for r in records if r["quarantine_id"] == quarantine_id), None
    )
    if match is None:
        raise HTTPException(
            status_code=404, detail=f"quarantine {quarantine_id!r} not found"
        )
    record = gate_store.get_quarantine(engine, match["batch_key"])
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"quarantine {quarantine_id!r} not found"
        )
    return record.to_dict()


class PromoteRequest(BaseModel):
    promoted_by: str = ""
    auto: bool = False


@router.post("/batches/{batch_key}/promote")
async def promote(
    batch_key: str,
    body: PromoteRequest | None = None,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Explicitly promote an ``awaiting_promotion`` batch version.

    This is the **only** endpoint that changes consumer-visible state.
    It is atomic: the batch is promoted and any prior promoted version
    is superseded in the same transaction.  A quarantined or pending
    batch returns 409 — partial publication cannot occur.
    """
    _verify_admin(authorization)
    _batch_key_or_400(batch_key)
    body = body or PromoteRequest()
    try:
        receipt = gate_store.promote_batch(
            engine,
            batch_key,
            promoted_by=body.promoted_by,
            auto=body.auto,
        )
    except PromotionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"receipt": receipt.to_dict()}


@router.get("/consumer-view")
async def consumer_view(
    pipeline: str,
    source_slug: str,
    row_kind: str | None = None,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """What a consumer of ``(pipeline, source_slug)`` currently sees.

    Promoted version only — quarantined / pending / superseded versions
    are invisible here (acceptance: consumers see only promoted
    versions).
    """
    _verify_admin(authorization)
    rows = gate_store.get_consumer_view_rows(
        engine, pipeline, source_slug, row_kind=row_kind
    )
    promoted = gate_store.get_promoted_batch(engine, pipeline, source_slug)
    return {
        "pipeline": pipeline,
        "source_slug": source_slug,
        "promoted_batch": promoted,
        "count": len(rows),
        "rows": rows,
    }
