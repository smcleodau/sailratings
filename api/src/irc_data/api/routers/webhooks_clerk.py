"""Clerk -> Postgres user sync webhook (AUTH-01-01, SPEC-23 §1-2).

``POST /v1/webhooks/clerk`` receives Clerk's svix-signed webhooks so the
``users`` table reflects sign-ups, profile changes and deletions without
the user having to load a page.

* ``user.created`` / ``user.updated`` — upsert ``users`` on ``clerk_id``.
* ``user.deleted``  — set ``users.deleted_at``.
* ``session.created`` — set ``users.last_seen_at``.
* anything else — acknowledged with 200.

Idempotency: the ``svix-id`` header is inserted into ``clerk_events`` in
the same transaction as the handler's writes, so a redelivery of the same
message is a 200 no-op, while a handler failure rolls the ledger row back
and Clerk's retry is processed normally.
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from svix.webhooks import Webhook, WebhookVerificationError

from irc_data.api.deps import get_db
from irc_data.api.services.users_service import upsert_from_clerk_payload

logger = logging.getLogger(__name__)

router = APIRouter()


def _handle_event(conn: Connection, event_type: str, data: dict) -> None:
    if event_type in ("user.created", "user.updated"):
        upsert_from_clerk_payload(conn, data)
    elif event_type == "user.deleted":
        clerk_id = data.get("id")
        if clerk_id:
            conn.execute(
                text(
                    "UPDATE users SET deleted_at = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE clerk_id = :clerk_id"
                ),
                {"clerk_id": clerk_id},
            )
    elif event_type == "session.created":
        clerk_id = data.get("user_id")
        if clerk_id:
            conn.execute(
                text(
                    "UPDATE users SET last_seen_at = CURRENT_TIMESTAMP "
                    "WHERE clerk_id = :clerk_id"
                ),
                {"clerk_id": clerk_id},
            )
    else:
        logger.info("Clerk event type %s ignored", event_type)


@router.post("/webhooks/clerk")
async def clerk_webhook(request: Request, engine: Engine = Depends(get_db)):
    secret = os.environ.get("CLERK_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    payload = await request.body()
    svix_headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }
    try:
        Webhook(secret).verify(payload, svix_headers)
    except WebhookVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception:
        # Malformed secret/headers must never become a 500 for the sender.
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(payload)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")
    event_type = event.get("type") or ""
    data = event.get("data") or {}
    svix_id = svix_headers["svix-id"]

    with engine.begin() as conn:
        first_delivery = conn.execute(
            text(
                "INSERT INTO clerk_events (id, type) VALUES (:id, :type) "
                "ON CONFLICT (id) DO NOTHING RETURNING id"
            ),
            {"id": svix_id, "type": event_type},
        ).first()
        if first_delivery is None:
            logger.info("Clerk event %s (%s) already recorded — replay ignored",
                        svix_id, event_type)
            return {"status": "ok", "replay": True}
        try:
            _handle_event(conn, event_type, data)
        except Exception:
            logger.exception("Clerk event %s (%s) failed", svix_id, event_type)
            raise HTTPException(status_code=500, detail="Webhook processing failed")

    return {"status": "ok"}
