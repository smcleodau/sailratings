"""User + Stripe customer linkage (PAY-01-08).

One Stripe customer per signed-in user:

* ``get_or_create_user`` — upserts the ``users`` row for a Clerk identity
  (canonical column ``clerk_id`` per the PAY-01-07/09 schema) and claims
  guest purchases made with the same email address (guest checkout creates
  a customer via ``customer_creation=always``; the order is linked on the
  user's next sign-in / checkout).
* ``ensure_stripe_customer`` — returns the user's cached Stripe customer,
  creating it (email + ``metadata.clerk_id``) on first use so subscriptions
  have a customer object to attach to.
* ``link_checkout_customer_to_user`` — used by the
  ``checkout.session.completed`` handler: links ``session.customer`` to
  the user by ``stripe_customer_id`` first, else by email, and the order
  gets ``orders.user_id`` set.

All functions take an open SQLAlchemy connection and leave transaction
control to the caller. SQL stays dialect-neutral (``CURRENT_TIMESTAMP``
instead of ``now()``) so the logic is unit-testable on SQLite.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import stripe
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


def _normalize_email(email: Optional[str]) -> Optional[str]:
    """Case-insensitive, whitespace-trimmed email for join-key matching."""
    if not email:
        return None
    normalized = email.strip().lower()
    return normalized or None


def get_or_create_user(
    conn: Connection,
    clerk_user_id: str,
    email: Optional[str],
) -> dict[str, Any]:
    """Return the ``users`` row for ``clerk_user_id``, creating it if needed.

    Claims guest purchases on sign-in: orders placed with this user's email
    while signed out get ``user_id`` set, and a Stripe customer previously
    linked to an email-matched row is adopted onto this row.
    """
    email = _normalize_email(email)

    row = conn.execute(
        text(
            "SELECT id, clerk_id, email, stripe_customer_id "
            "FROM users WHERE clerk_id = :clerk_id"
        ),
        {"clerk_id": clerk_user_id},
    ).mappings().first()

    if row is None:
        # users.email is UNIQUE — only carry the email onto the new row if
        # no other row already owns it (otherwise we keep NULL and let the
        # adopt/claim path below reconcile).
        email_taken = False
        if email:
            email_taken = conn.execute(
                text(
                    "SELECT 1 FROM users WHERE lower(email) = :email LIMIT 1"
                ),
                {"email": email},
            ).first() is not None

        insert_email = None if email_taken else email
        row = conn.execute(
            text(
                """
                INSERT INTO users (clerk_id, email)
                VALUES (:clerk_id, :email)
                ON CONFLICT (clerk_id) DO NOTHING
                RETURNING id, clerk_id, email, stripe_customer_id
                """
            ),
            {"clerk_id": clerk_user_id, "email": insert_email},
        ).mappings().first()
        if row is None:
            # Lost an insert race — the row now exists.
            row = conn.execute(
                text(
                    "SELECT id, clerk_id, email, stripe_customer_id "
                    "FROM users WHERE clerk_id = :clerk_id"
                ),
                {"clerk_id": clerk_user_id},
            ).mappings().first()
    elif email and not row["email"]:
        updated = conn.execute(
            text(
                """
                UPDATE users SET email = :email,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
                  AND NOT EXISTS (
                      SELECT 1 FROM users
                      WHERE lower(email) = :email AND id != :id
                  )
                RETURNING id
                """
            ),
            {"email": email, "id": row["id"]},
        ).first()
        if updated:
            row = {**row, "email": email}

    user = dict(row)

    # ── Claim guest purchases on sign-in ────────────────────────────────
    if email:
        # Adopt a customer previously created for this email on a
        # different (email-matched) user row.
        if not user.get("stripe_customer_id"):
            other = conn.execute(
                text(
                    """
                    SELECT id, stripe_customer_id FROM users
                    WHERE lower(email) = :email
                      AND clerk_id != :clerk_id
                      AND stripe_customer_id IS NOT NULL
                    ORDER BY created_at
                    LIMIT 1
                    """
                ),
                {"email": email, "clerk_id": clerk_user_id},
            ).mappings().first()
            if other:
                # Transfer the customer: clear it from the donor row first
                # so the UNIQUE constraint on stripe_customer_id holds.
                conn.execute(
                    text(
                        "UPDATE users SET stripe_customer_id = NULL, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = :id"
                    ),
                    {"id": other["id"]},
                )
                conn.execute(
                    text(
                        """
                        UPDATE users
                        SET stripe_customer_id = :customer,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :id
                        """
                    ),
                    {"customer": other["stripe_customer_id"], "id": user["id"]},
                )
                user["stripe_customer_id"] = other["stripe_customer_id"]
                logger.info(
                    "Adopted stripe customer %s onto user %s (from user %s)",
                    other["stripe_customer_id"], clerk_user_id, other["id"],
                )

        # Back-fill orders that were placed as a guest with this email
        # (no user attached yet).  The webhook also runs this path for the
        # most recent order, but sign-in may happen long after purchase.
        claimed = conn.execute(
            text(
                """
                UPDATE orders
                SET user_id = :user_id
                WHERE user_id IS NULL
                  AND lower(email) = :email
                """
            ),
            {"user_id": user["id"], "email": email},
        )
        if claimed.rowcount:
            logger.info(
                "Claimed %d guest order(s) for user %s on email match",
                claimed.rowcount, clerk_user_id,
            )

    return user


def ensure_stripe_customer(
    conn: Connection,
    user: dict[str, Any],
    email: Optional[str] = None,
) -> Optional[str]:
    """Return the user's Stripe customer id, creating it on first use.

    The customer is created with the user's email and
    ``metadata.clerk_id`` so it is traceable back to the Clerk identity,
    and the id is cached on ``users.stripe_customer_id``. ``None`` is
    returned when Stripe is not configured — callers must treat the user
    as a guest in that case.
    """
    existing = user.get("stripe_customer_id")
    if existing:
        return existing

    if not stripe.api_key:
        logger.warning("ensure_stripe_customer called without stripe.api_key set")
        return None

    email = _normalize_email(email) or user.get("email")
    clerk_id = user["clerk_id"]
    try:
        customer = stripe.Customer.create(
            email=email,
            metadata={"clerk_id": clerk_id},
        )
    except stripe.StripeError:
        logger.exception("Stripe customer creation failed for user %s", clerk_id)
        return None

    conn.execute(
        text(
            "UPDATE users SET stripe_customer_id = :customer, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = :id"
        ),
        {"customer": customer.id, "id": user["id"]},
    )
    user["stripe_customer_id"] = customer.id
    logger.info("Created stripe customer %s for user %s", customer.id, clerk_id)
    return customer.id


def link_checkout_customer_to_user(
    conn: Connection,
    stripe_customer_id: Optional[str],
    email: Optional[str],
) -> Optional[int]:
    """Link a completed checkout's customer to a user row.

    Match order: ``stripe_customer_id`` first, else email. On an email
    match the customer id is back-filled onto the user (idempotent — an
    existing different customer is never overwritten).

    Returns the matched ``users.id`` (``None`` if no user matches).
    """
    email = _normalize_email(email)

    user = None
    if stripe_customer_id:
        user = conn.execute(
            text(
                "SELECT id, stripe_customer_id FROM users "
                "WHERE stripe_customer_id = :customer"
            ),
            {"customer": stripe_customer_id},
        ).mappings().first()

    if user is None and email:
        user = conn.execute(
            text(
                "SELECT id, stripe_customer_id FROM users "
                "WHERE lower(email) = :email "
                "ORDER BY created_at LIMIT 1"
            ),
            {"email": email},
        ).mappings().first()

    if user is None:
        return None

    if (
        stripe_customer_id
        and not user["stripe_customer_id"]
    ):
        conn.execute(
            text(
                """
                UPDATE users
                SET stripe_customer_id = :customer,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
                  AND (stripe_customer_id IS NULL OR stripe_customer_id = :customer)
                """
            ),
            {"customer": stripe_customer_id, "id": user["id"]},
        )

    return user["id"]
