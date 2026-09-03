"""Signed-in user endpoints.

``GET /v1/users/me`` is what turns a Clerk identity into a row in our
``users`` table. Clerk owns authentication; this endpoint owns the local
mirror, and nothing else creates it on sign-in.

Before this existed, ``get_or_create_user`` was reachable from exactly one
place — ``POST /v1/checkout/create-session`` — so a signed-in visitor stayed
invisible to us until the moment they tried to pay. That left the admin
Customers zone empty, ``last_seen_at`` never set, and the guest-purchase
claim in ``get_or_create_user`` (which its own docstring describes as
happening "on sign-in") never running at sign-in.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import CallerIdentity, get_db, get_optional_identity
from irc_data.api.services.users_service import get_or_create_user

logger = logging.getLogger(__name__)

router = APIRouter()


class MeResponse(BaseModel):
    """The caller's local user row, mirrored from their Clerk identity."""

    id: int
    clerk_id: str
    email: Optional[str] = None
    role: Optional[str] = None
    plan: Optional[str] = None
    subscription_status: Optional[str] = None
    stripe_customer_id: Optional[str] = None


@router.get("/users/me", response_model=MeResponse, tags=["Users"])
def read_current_user(
    engine: Engine = Depends(get_db),
    identity: Optional[CallerIdentity] = Depends(get_optional_identity),
) -> MeResponse:
    """Return (creating if needed) the ``users`` row for the signed-in caller.

    The frontend calls this once after sign-in. It is idempotent, so calling
    it on every page load is harmless.
    """
    if identity is None:
        raise HTTPException(status_code=401, detail="Not signed in")

    with engine.begin() as conn:
        row = get_or_create_user(conn, identity.clerk_user_id, identity.email)
        if row is None:
            logger.error(
                "get_or_create_user returned no row for clerk_id=%s",
                identity.clerk_user_id,
            )
            raise HTTPException(status_code=500, detail="Could not resolve user")

        # Touch last_seen_at so the admin Customers zone can show activity.
        conn.execute(
            text("UPDATE users SET last_seen_at = now() WHERE id = :id"),
            {"id": row["id"]},
        )

        detail = conn.execute(
            text(
                "SELECT id, clerk_id, email, role, plan, subscription_status,"
                " stripe_customer_id FROM users WHERE id = :id"
            ),
            {"id": row["id"]},
        ).mappings().first()

    return MeResponse(**dict(detail))
