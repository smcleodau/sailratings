"""AUTH-01-03: account settings, data export and deletion.

Members control their own data. This module owns the member-facing account
lifecycle on top of the AUTH-01-01 ``users`` identity row:

* ``get_settings`` / ``update_profile`` / ``update_notifications`` —
  one ``user_settings`` row per user (created lazily with privacy-first
  defaults: every non-essential notification OFF until the member opts in).
* ``build_account_export`` — GDPR-style full export of everything we hold
  on the member (profile, settings, boats claimed, orders, subscriptions),
  returned as a JSON-serialisable document with a generated-at stamp and
  schema version.
* ``delete_account`` — honours the privacy policy retention model:
  personal data is destroyed (settings, notification preferences, boat
  claims, subscriptions, Stripe customer linkage, email/name on the
  identity row) and the identity row is kept only as an anonymised
  audit stub so ``orders`` keeps referential integrity for the
  financial-record retention window. ``orders.user_id`` is nulled via
  its ON DELETE SET NULL semantics only if the row itself were removed —
  instead we explicitly detach PII and keep the stub.

The cascade is explicit SQL (rather than relying on ORM cascades) so it is
unit-testable on SQLite and reviewable in one place. All functions take an
open SQLAlchemy connection and leave transaction control to the caller.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

EXPORT_SCHEMA_VERSION = "1.0"

# Notification preference columns — single source of truth shared by the
# defaults, the PATCH allow-list and the export.
NOTIFICATION_FIELDS = (
    "notify_product_updates",
    "notify_rating_changes",
    "notify_event_reminders",
    "notify_marketing",
)

PROFILE_FIELDS = ("display_name", "home_club", "country")


def _get_user_row(conn: Connection, clerk_user_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        text(
            "SELECT id, clerk_id, email, full_name, role, plan,"
            " subscription_status, stripe_customer_id, created_at,"
            " last_seen_at, deletion_requested_at, deletion_completed_at"
            " FROM users WHERE clerk_id = :clerk_id"
        ),
        {"clerk_id": clerk_user_id},
    ).mappings().first()
    return dict(row) if row else None


def _settings_select() -> str:
    cols = ", ".join(
        ["user_id", *PROFILE_FIELDS, *NOTIFICATION_FIELDS, "created_at", "updated_at"]
    )
    return f"SELECT {cols} FROM user_settings WHERE user_id = :user_id"


def _coerce_bool(value: Any) -> bool:
    """SQLite returns 0/1 for booleans; normalise for the API surface."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return bool(value)


def get_settings(conn: Connection, clerk_user_id: str) -> dict[str, Any]:
    """Return the member's settings row, creating it with defaults if absent.

    Requires the caller to have already run ``get_or_create_user`` (the
    settings row is keyed on ``users.id``).
    """
    user = _get_user_row(conn, clerk_user_id)
    if user is None:
        return {}
    user_id = user["id"]

    row = conn.execute(
        text(_settings_select()), {"user_id": user_id}
    ).mappings().first()
    if row is None:
        conn.execute(
            text("INSERT INTO user_settings (user_id) VALUES (:user_id)"),
            {"user_id": user_id},
        )
        row = conn.execute(
            text(_settings_select()), {"user_id": user_id}
        ).mappings().first()

    settings = dict(row) if row else {"user_id": user_id}
    for field in NOTIFICATION_FIELDS:
        settings[field] = _coerce_bool(settings.get(field))
    return settings


def update_profile(
    conn: Connection,
    clerk_user_id: str,
    *,
    full_name: Optional[str] = None,
    display_name: Optional[str] = None,
    home_club: Optional[str] = None,
    country: Optional[str] = None,
) -> dict[str, Any]:
    """Update editable profile fields. ``None`` leaves the field untouched.

    ``full_name`` lives on the AUTH-01-01 ``users`` row (it is what the
    admin Customers zone shows); the other profile fields live in
    ``user_settings``.
    """
    user = _get_user_row(conn, clerk_user_id)
    if user is None:
        return {}
    user_id = user["id"]

    if full_name is not None:
        conn.execute(
            text(
                "UPDATE users SET full_name = :full_name,"
                " updated_at = CURRENT_TIMESTAMP WHERE id = :user_id"
            ),
            {"full_name": full_name.strip() or None, "user_id": user_id},
        )

    settings_updates = {
        field: (value.strip() or None if isinstance(value, str) else value)
        for field, value in (
            ("display_name", display_name),
            ("home_club", home_club),
            ("country", country),
        )
        if value is not None
    }
    if settings_updates:
        # Ensure the settings row exists before updating it.
        get_settings(conn, clerk_user_id)
        assignments = ", ".join(f"{k} = :{k}" for k in settings_updates)
        conn.execute(
            text(
                f"UPDATE user_settings SET {assignments},"
                " updated_at = CURRENT_TIMESTAMP WHERE user_id = :user_id"
            ),
            {**settings_updates, "user_id": user_id},
        )

    return {
        "user": _get_user_row(conn, clerk_user_id),
        "settings": get_settings(conn, clerk_user_id),
    }


def update_notifications(
    conn: Connection,
    clerk_user_id: str,
    preferences: dict[str, bool],
) -> dict[str, Any]:
    """Update notification preferences (allow-listed booleans only)."""
    user = _get_user_row(conn, clerk_user_id)
    if user is None:
        return {}
    user_id = user["id"]

    updates = {
        key: bool(value)
        for key, value in preferences.items()
        if key in NOTIFICATION_FIELDS
    }
    if updates:
        get_settings(conn, clerk_user_id)  # ensure the row exists
        assignments = ", ".join(f"{k} = :{k}" for k in updates)
        conn.execute(
            text(
                f"UPDATE user_settings SET {assignments},"
                " updated_at = CURRENT_TIMESTAMP WHERE user_id = :user_id"
            ),
            {**updates, "user_id": user_id},
        )

    return get_settings(conn, clerk_user_id)


def _rows(conn: Connection, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    result = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in result]


def _serialise(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


def _serialise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: _serialise(value) for key, value in row.items()} for row in rows
    ]


def build_account_export(conn: Connection, clerk_user_id: str) -> dict[str, Any]:
    """Assemble the complete data export for a member.

    Completeness contract (test-backed): the document contains the member's
    profile, settings + notification preferences, every boat claim, every
    order (including guest orders later claimed by sign-in), and every
    subscription record — plus the audit timestamps from the identity row.
    """
    user = _get_user_row(conn, clerk_user_id)
    if user is None:
        return {}
    user_id = user["id"]

    settings = get_settings(conn, clerk_user_id)

    boats = _rows(
        conn,
        """
        SELECT bc.id, bc.boat_id, bc.status, bc.evidence, bc.created_at,
               bc.verified_at, b.boat_name, b.sail_number
        FROM boat_claims bc
        LEFT JOIN boats b ON b.id = bc.boat_id
        WHERE bc.user_id = :user_id
        ORDER BY bc.created_at
        """,
        {"user_id": user_id},
    )

    orders = _rows(
        conn,
        """
        SELECT id, order_token, boat_id, email, amount_cents, currency,
               status, paid_at, user_id, stripe_customer_id, created_at
        FROM orders
        WHERE user_id = :user_id
        ORDER BY created_at
        """,
        {"user_id": user_id},
    )

    # Column set matches the canonical 0027 subscriptions schema exactly —
    # the live table has no ``cancel_at`` column (it carries ``lookup_key``,
    # ``price_id`` and the ``raw`` Stripe payload instead). Selecting a
    # non-existent column would 500 the export on Postgres even though the
    # SQLite test double (which fabricated ``cancel_at``) never complained.
    subscriptions = _rows(
        conn,
        """
        SELECT id, stripe_subscription_id, stripe_customer_id, plan, status,
               current_period_start, current_period_end, cancel_at_period_end,
               canceled_at, ended_at, created_at, updated_at
        FROM subscriptions
        WHERE user_id = :user_id
        ORDER BY created_at
        """,
        {"user_id": user_id},
    )

    export = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "clerk_id": user.get("clerk_id"),
            "email": user.get("email"),
            "full_name": user.get("full_name"),
            "role": user.get("role"),
            "plan": user.get("plan"),
            "subscription_status": user.get("subscription_status"),
            "stripe_customer_id": user.get("stripe_customer_id"),
            "created_at": _serialise(user.get("created_at")),
            "last_seen_at": _serialise(user.get("last_seen_at")),
        },
        "settings": {
            "display_name": settings.get("display_name"),
            "home_club": settings.get("home_club"),
            "country": settings.get("country"),
        },
        "notification_preferences": {
            field: _coerce_bool(settings.get(field))
            for field in NOTIFICATION_FIELDS
        },
        "boats": _serialise_rows(boats),
        "orders": _serialise_rows(orders),
        "subscriptions": _serialise_rows(subscriptions),
        "audit": {
            "deletion_requested_at": _serialise(user.get("deletion_requested_at")),
            "deletion_completed_at": _serialise(user.get("deletion_completed_at")),
        },
    }
    return export


def export_as_json_bytes(export: dict[str, Any]) -> bytes:
    """Deterministic, pretty JSON for the download response."""
    return (
        json.dumps(export, indent=2, sort_keys=False, default=str) + "\n"
    ).encode("utf-8")


def delete_account(
    conn: Connection,
    clerk_user_id: str,
    *,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """Delete the member's personal data and anonymise the identity row.

    Cascade (privacy-policy retention model):

    * ``user_settings``  — DELETEd outright (profile extras + notification
      preferences are personal data with no retention justification).
    * ``boat_claims``    — DELETEd outright (ownership claims are personal).
    * ``subscriptions``  — DELETEd outright (Stripe remains the system of
      record for the financial retention window; our mirror holds no
      unique financial evidence).
    * ``orders``         — KEPT for financial-record retention, but the
      member's email is detached from the rows and ``user_id`` is cleared
      so nothing personal remains joinable to the identity.
    * ``users``          — anonymised in place (email and full name are
      replaced with tombstones, the Stripe customer id is cleared) and
      stamped ``deletion_completed_at``; the row survives only as an audit
      stub so admin aggregates never break. ``clerk_id`` is deliberately
      kept so deletion stays idempotent (a repeat call returns
      ``already_deleted``) and a still-valid Clerk session gets 410 Gone
      instead of silently resurrecting an empty account.

    Returns a summary of what was deleted / anonymised for the API surface.
    """
    user = _get_user_row(conn, clerk_user_id)
    if user is None:
        return {}
    user_id = user["id"]

    if user.get("deletion_completed_at") is not None:
        # Idempotent: a second call after deletion is a no-op summary.
        return {
            "deleted": True,
            "already_deleted": True,
            "user_id": str(user_id),
        }

    now = datetime.now(timezone.utc)

    deleted_settings = conn.execute(
        text("DELETE FROM user_settings WHERE user_id = :user_id"),
        {"user_id": user_id},
    ).rowcount

    deleted_claims = conn.execute(
        text("DELETE FROM boat_claims WHERE user_id = :user_id"),
        {"user_id": user_id},
    ).rowcount

    deleted_subscriptions = conn.execute(
        text("DELETE FROM subscriptions WHERE user_id = :user_id"),
        {"user_id": user_id},
    ).rowcount

    # Detach personal data from retained financial records.
    detached_orders = conn.execute(
        text(
            "UPDATE orders SET user_id = NULL, email = NULL,"
            " stripe_customer_id = NULL WHERE user_id = :user_id"
        ),
        {"user_id": user_id},
    ).rowcount

    tombstone = f"deleted-{user_id}@deleted.invalid"
    conn.execute(
        text(
            """
            UPDATE users
            SET email = :email,
                full_name = NULL,
                stripe_customer_id = NULL,
                deletion_requested_at = COALESCE(deletion_requested_at, :now),
                deletion_completed_at = :now,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :user_id
            """
        ),
        {
            "email": tombstone,
            "now": now,
            "user_id": user_id,
        },
    )

    logger.info(
        "Account deleted for user %s (reason=%s): settings=%s claims=%s "
        "subscriptions=%s detached_orders=%s",
        user_id, reason, deleted_settings, deleted_claims,
        deleted_subscriptions, detached_orders,
    )

    return {
        "deleted": True,
        "already_deleted": False,
        "user_id": str(user_id),
        "cascade": {
            "user_settings_deleted": deleted_settings,
            "boat_claims_deleted": deleted_claims,
            "subscriptions_deleted": deleted_subscriptions,
            "orders_detached": detached_orders,
            "identity_anonymised": True,
        },
        "completed_at": now.isoformat(),
    }
