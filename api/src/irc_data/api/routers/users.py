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

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import CallerIdentity, get_db, get_optional_identity
from irc_data.api.services import account_service
from irc_data.api.services.users_service import get_or_create_user

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_identity(
    identity: Optional[CallerIdentity],
) -> CallerIdentity:
    """All endpoints in this router operate on the signed-in member."""
    if identity is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return identity


def _require_user(
    engine: Engine, identity: CallerIdentity
) -> tuple[int, str]:
    """Resolve (and if needed create) the local ``users`` row for the caller.

    Returns ``(user_pk, clerk_id)``. A caller whose identity row is already
    a deletion tombstone gets 410 Gone — a still-valid Clerk session after
    deletion must never silently resurrect an empty account (the frontend
    deletes the Clerk user immediately after our DELETE succeeds, but the
    session token can briefly outlive that).
    """
    with engine.begin() as conn:
        tombstone = conn.execute(
            text(
                "SELECT 1 FROM users WHERE clerk_id = :c"
                " AND deletion_completed_at IS NOT NULL"
            ),
            {"c": identity.clerk_user_id},
        ).first()
        if tombstone is not None:
            raise HTTPException(status_code=410, detail="Account deleted")

        row = get_or_create_user(conn, identity.clerk_user_id, identity.email)
        if row is None:
            logger.error(
                "get_or_create_user returned no row for clerk_id=%s",
                identity.clerk_user_id,
            )
            raise HTTPException(status_code=500, detail="Could not resolve user")
    return row["id"], identity.clerk_user_id


class MeResponse(BaseModel):
    """The caller's local user row, mirrored from their Clerk identity."""

    id: int
    clerk_id: str
    email: Optional[str] = None
    role: Optional[str] = None
    plan: Optional[str] = None
    subscription_status: Optional[str] = None
    stripe_customer_id: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    """AUTH-01-03: editable profile fields (all optional, PATCH semantics)."""

    full_name: Optional[str] = Field(default=None, max_length=200)
    display_name: Optional[str] = Field(default=None, max_length=200)
    home_club: Optional[str] = Field(default=None, max_length=200)
    country: Optional[str] = Field(default=None, max_length=100)


class NotificationPrefsRequest(BaseModel):
    """AUTH-01-03: notification preferences (allow-listed booleans)."""

    notify_product_updates: Optional[bool] = None
    notify_rating_changes: Optional[bool] = None
    notify_event_reminders: Optional[bool] = None
    notify_marketing: Optional[bool] = None


class SettingsResponse(BaseModel):
    """Settings page payload: profile + notification preferences."""

    full_name: Optional[str] = None
    email: Optional[str] = None
    display_name: Optional[str] = None
    home_club: Optional[str] = None
    country: Optional[str] = None
    notify_product_updates: bool = False
    notify_rating_changes: bool = False
    notify_event_reminders: bool = False
    notify_marketing: bool = False


class DeleteAccountRequest(BaseModel):
    """Deletion requires explicit confirmation text to prevent accidents."""

    confirm: str = Field(..., description="Must equal 'DELETE'")
    reason: Optional[str] = Field(default=None, max_length=2000)


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

        # Touch last_seen_at so the admin Customers zone can show activity
        # (skip tombstoned accounts — a deleted member never "reappears").
        conn.execute(
            text(
                "UPDATE users SET last_seen_at = now() WHERE id = :id"
                " AND deletion_completed_at IS NULL"
            ),
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


# ── AUTH-01-03: settings, export, deletion ───────────────────────────────


def _settings_response(
    conn, clerk_user_id: str
) -> SettingsResponse:
    user = conn.execute(
        text("SELECT full_name, email FROM users WHERE clerk_id = :c"),
        {"c": clerk_user_id},
    ).mappings().first()
    settings = account_service.get_settings(conn, clerk_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return SettingsResponse(
        full_name=user.get("full_name"),
        email=user.get("email"),
        display_name=settings.get("display_name"),
        home_club=settings.get("home_club"),
        country=settings.get("country"),
        notify_product_updates=bool(settings.get("notify_product_updates")),
        notify_rating_changes=bool(settings.get("notify_rating_changes")),
        notify_event_reminders=bool(settings.get("notify_event_reminders")),
        notify_marketing=bool(settings.get("notify_marketing")),
    )


@router.get("/users/me/settings", response_model=SettingsResponse, tags=["Users"])
def read_settings(
    engine: Engine = Depends(get_db),
    identity: Optional[CallerIdentity] = Depends(get_optional_identity),
) -> SettingsResponse:
    """Profile + notification preferences for the account settings page."""
    ident = _require_identity(identity)
    _require_user(engine, ident)
    with engine.begin() as conn:
        return _settings_response(conn, ident.clerk_user_id)


@router.patch("/users/me", response_model=SettingsResponse, tags=["Users"])
def update_profile(
    payload: ProfileUpdateRequest,
    engine: Engine = Depends(get_db),
    identity: Optional[CallerIdentity] = Depends(get_optional_identity),
) -> SettingsResponse:
    """Update editable profile fields (full name, display name, club, country)."""
    ident = _require_identity(identity)
    _require_user(engine, ident)
    with engine.begin() as conn:
        account_service.update_profile(
            conn,
            ident.clerk_user_id,
            full_name=payload.full_name,
            display_name=payload.display_name,
            home_club=payload.home_club,
            country=payload.country,
        )
        return _settings_response(conn, ident.clerk_user_id)


@router.patch(
    "/users/me/notifications", response_model=SettingsResponse, tags=["Users"]
)
def update_notifications(
    payload: NotificationPrefsRequest,
    engine: Engine = Depends(get_db),
    identity: Optional[CallerIdentity] = Depends(get_optional_identity),
) -> SettingsResponse:
    """Update notification preferences; omitted keys are left unchanged."""
    ident = _require_identity(identity)
    _require_user(engine, ident)
    prefs = payload.model_dump(exclude_none=True)
    with engine.begin() as conn:
        account_service.update_notifications(conn, ident.clerk_user_id, prefs)
        return _settings_response(conn, ident.clerk_user_id)


@router.get("/users/me/export", tags=["Users"])
def export_account_data(
    engine: Engine = Depends(get_db),
    identity: Optional[CallerIdentity] = Depends(get_optional_identity),
) -> Response:
    """Full data export (GDPR-style): profile, settings, boats, orders, subs.

    Served as a downloadable JSON document. The completeness contract is
    test-backed: every row we hold that is keyed to the member appears here.
    """
    ident = _require_identity(identity)
    _require_user(engine, ident)
    with engine.begin() as conn:
        export = account_service.build_account_export(conn, ident.clerk_user_id)
    if not export:
        raise HTTPException(status_code=404, detail="User not found")
    body = account_service.export_as_json_bytes(export)
    filename = f"sailratings-export-{ident.clerk_user_id}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/users/me", tags=["Users"])
def delete_account(
    payload: DeleteAccountRequest,
    engine: Engine = Depends(get_db),
    identity: Optional[CallerIdentity] = Depends(get_optional_identity),
) -> dict:
    """Permanently delete the account's personal data (privacy policy).

    Settings, notification preferences, boat claims and subscription
    mirrors are deleted; retained financial records (orders) are detached
    from the identity; the identity row is anonymised to an audit stub.
    The Clerk-side account is removed by the frontend immediately after
    this call succeeds.
    """
    ident = _require_identity(identity)
    if payload.confirm != "DELETE":
        raise HTTPException(
            status_code=400,
            detail="Confirmation text must be exactly 'DELETE'",
        )
    _require_user(engine, ident)
    with engine.begin() as conn:
        summary = account_service.delete_account(
            conn, ident.clerk_user_id, reason=payload.reason
        )
    if not summary:
        raise HTTPException(status_code=404, detail="User not found")
    return summary
