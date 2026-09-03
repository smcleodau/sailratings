"""Stripe checkout endpoints for report purchases and subscriptions."""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import stripe
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from irc_data.api.deps import CallerIdentity, get_db, get_optional_identity
from irc_data.api.services.users_service import (
    ensure_stripe_customer,
    get_or_create_user,
    link_checkout_customer_to_user,
)
from irc_data.env import FRONTEND_URL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/checkout", tags=["Checkout"])

# Subscription event types dispatched by the webhook (PAY-01-09).
SUBSCRIPTION_EVENT_TYPES = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
}

# Price table: currency → amount in cents
PRICES = {
    "usd": 9900,
    "gbp": 7900,
    "eur": 8900,
    "aud": 14900,
}

CURRENCY_SYMBOLS = {
    "usd": "$99",
    "gbp": "£79",
    "eur": "€89",
    "aud": "A$149",
}


class CreateSessionRequest(BaseModel):
    boat_id: int
    boat_name: str
    currency: str = "usd"
    search_query: str | None = None
    teaser_text: str | None = None


class CreateSessionResponse(BaseModel):
    checkout_url: str
    order_token: str


@router.post("/create-session", response_model=CreateSessionResponse)
def create_checkout_session(
    body: CreateSessionRequest,
    engine: Engine = Depends(get_db),
    identity: Optional[CallerIdentity] = Depends(get_optional_identity),
):
    """Create a Stripe Checkout Session for a boat report purchase.

    Signed-in callers reuse (or create) a single Stripe customer stored on
    ``users.stripe_customer_id`` and passed as ``customer=``; guests get
    ``customer_creation=always`` so the payment still produces a customer
    object that the ``checkout.session.completed`` webhook can link to a
    user on their next sign-in.
    """
    secret_key = os.environ.get("STRIPE_SECRET_KEY")
    if not secret_key:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    stripe.api_key = secret_key

    currency = body.currency.lower()
    if currency not in PRICES:
        currency = "usd"

    amount_cents = PRICES[currency]
    order_token = uuid.uuid4()

    # Verify boat exists
    with engine.connect() as conn:
        boat = conn.execute(
            text("SELECT id, boat_name FROM boats WHERE id = :id"),
            {"id": body.boat_id},
        ).first()

    if not boat:
        raise HTTPException(status_code=404, detail=f"Boat {body.boat_id} not found")

    # Resolve user + Stripe customer for signed-in callers. Guest-order
    # claiming also runs here so a purchase made while signed out is
    # attached to the user on their next checkout.
    user = None
    stripe_customer_id = None
    if identity:
        try:
            with engine.begin() as conn:
                user = get_or_create_user(conn, identity.clerk_user_id, identity.email)
                stripe_customer_id = ensure_stripe_customer(conn, user, identity.email)
        except Exception:
            # Never block a purchase on the user/customer bookkeeping;
            # fall through to the guest flow.
            logger.exception(
                "User/customer resolution failed for %s; falling back to guest checkout",
                identity.clerk_user_id,
            )
            user = None
            stripe_customer_id = None

    # Create pending order (linked to the user when signed in)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO orders (order_token, boat_id, amount_cents, currency,
                                    search_query, teaser_text, status,
                                    user_id, stripe_customer_id)
                VALUES (:token, :boat_id, :amount, :currency,
                        :search_query, :teaser_text, 'pending',
                        :user_id, :customer)
            """),
            {
                "token": str(order_token),
                "boat_id": body.boat_id,
                "amount": amount_cents,
                "currency": currency,
                "search_query": body.search_query,
                "teaser_text": body.teaser_text,
                "user_id": user["id"] if user else None,
                "customer": stripe_customer_id,
            },
        )

    # Create Stripe session
    try:
        session_params: dict = {
            "payment_method_types": ["card"],
            "line_items": [
                {
                    "price_data": {
                        "currency": currency,
                        "product_data": {
                            "name": f"IRC Rating Report — {body.boat_name}",
                            "description": (
                                "Full IRC rating analysis: measurement sensitivity, "
                                "optimisation recommendations, racing performance, "
                                "fleet comparison, and formula trend analysis."
                            ),
                        },
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }
            ],
            "mode": "payment",
            "allow_promotion_codes": True,
            "success_url": f"{FRONTEND_URL}/report/{order_token}",
            "cancel_url": f"{FRONTEND_URL}/boat/{body.boat_id}",
            "metadata": {
                "order_token": str(order_token),
                "boat_id": str(body.boat_id),
            },
        }
        if stripe_customer_id:
            # Signed-in repeat buyer: attach the session to their one
            # Stripe customer (Stripe forbids customer + customer_email
            # together; the customer already carries the email).
            session_params["customer"] = stripe_customer_id
        else:
            # Guests: always create a customer so the order can be linked
            # to the user on their next sign-in.
            session_params["customer_creation"] = "always"
        if identity:
            session_params["metadata"]["clerk_user_id"] = identity.clerk_user_id
            if identity.email and not stripe_customer_id:
                # Signed-in but no reusable customer (e.g. customer
                # creation failed): prefill the checkout email.
                session_params["customer_email"] = identity.email

        session = stripe.checkout.Session.create(**session_params)
    except stripe.StripeError as e:
        logger.error(f"Stripe session creation failed: {e}")
        raise HTTPException(status_code=502, detail="Payment session creation failed")

    # Link Stripe session to order
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE orders
                SET stripe_session_id = :session_id
                WHERE order_token = :token
            """),
            {"session_id": session.id, "token": str(order_token)},
        )

    from irc_data.api.services.analytics_service import track
    track("order_created", str(order_token), {
        "boat_id": body.boat_id,
        "boat_name": body.boat_name,
        "currency": currency,
        "amount_cents": amount_cents,
        "search_query": body.search_query,
        "clerk_user_id": identity.clerk_user_id if identity else None,
        "stripe_customer_id": stripe_customer_id,
    })

    return CreateSessionResponse(
        checkout_url=session.url,
        order_token=str(order_token),
    )


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    engine: Engine = Depends(get_db),
):
    """Handle Stripe webhook events.

    Flow (PAY-01-09):
      1. Verify the Stripe signature.
      2. INSERT the event into ``stripe_events`` with ON CONFLICT DO NOTHING.
         A conflict means Stripe is re-delivering an event we have already
         seen — acknowledge with 200 and do NOT re-dispatch (idempotency).
      3. Dispatch the event to its handler.
      4. On success, stamp ``processed_at``. On failure, record the error and
         return 500 so Stripe retries the delivery.

    Events we cannot attach to a user (no ``stripe_customer_id`` match and no
    email match) are *parked*: the row stays in ``stripe_events`` with
    ``error = 'parked: ...'`` and is visible in the admin UI, but the webhook
    still returns 200 so Stripe does not retry a permanently-unresolvable
    event forever.
    """
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_id = event["id"]
    event_type = event["type"]

    # ── 2. Idempotency ledger ────────────────────────────────────────────
    try:
        event_payload = json.loads(event.to_json())
    except Exception:  # pragma: no cover - StripeObject is always serialisable
        event_payload = {"id": event_id, "type": event_type}

    with engine.begin() as conn:
        inserted = conn.execute(
            text("""
                INSERT INTO stripe_events (event_id, type, api_version, livemode, payload)
                VALUES (:event_id, :type, :api_version, :livemode, CAST(:payload AS JSON))
                ON CONFLICT (event_id) DO NOTHING
                RETURNING id
            """),
            {
                "event_id": event_id,
                "type": event_type,
                "api_version": _event_get(event, "api_version"),
                "livemode": bool(_event_get(event, "livemode", False)),
                "payload": json.dumps(event_payload),
            },
        ).first()

    if not inserted:
        # Replay / duplicate delivery — already recorded, harmless.
        logger.info("Stripe event %s (%s) already recorded — replay ignored",
                    event_id, event_type)
        return {"status": "ok", "replay": True}

    # ── 3. Dispatch ──────────────────────────────────────────────────────
    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(
                engine, event["data"]["object"], background_tasks
            )
        elif event_type in SUBSCRIPTION_EVENT_TYPES:
            _handle_subscription_event(
                engine, event_type, event["data"]["object"]
            )
        else:
            logger.info("Unhandled Stripe event type %s (%s)", event_type, event_id)
    except _ParkedEvent as parked:
        # Permanently unresolvable right now (e.g. no matching user). Record
        # for admin visibility but ack so Stripe does not retry forever.
        logger.warning("Stripe event %s parked: %s", event_id, parked)
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE stripe_events
                    SET error = :error, processed_at = :now
                    WHERE event_id = :event_id
                """),
                {
                    "error": f"parked: {parked}",
                    "now": datetime.now(timezone.utc),
                    "event_id": event_id,
                },
            )
        return {"status": "parked"}
    except Exception as exc:
        # Transient failure — record and return 500 so Stripe retries.
        logger.error("Stripe event %s (%s) failed: %s",
                     event_id, event_type, exc, exc_info=True)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE stripe_events
                        SET error = :error
                        WHERE event_id = :event_id
                    """),
                    {"error": str(exc)[:2000], "event_id": event_id},
                )
        except SQLAlchemyError:
            logger.error("Failed to record webhook error for %s", event_id)
        raise HTTPException(status_code=500, detail="Webhook processing failed")

    # ── 4. Mark processed ────────────────────────────────────────────────
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE stripe_events
                SET processed_at = :now, error = NULL
                WHERE event_id = :event_id
            """),
            {"now": datetime.now(timezone.utc), "event_id": event_id},
        )

    return {"status": "ok"}


class _ParkedEvent(Exception):
    """Raised when an event cannot be resolved to local state (e.g. no user)."""


def _event_get(obj, key, default=None):
    """Safe accessor for StripeObject (its ``.get`` collides with HTTP GET)."""
    try:
        v = obj[key]
        return v if v is not None else default
    except (KeyError, TypeError):
        return default


def _ts_to_dt(value) -> datetime | None:
    """Convert a Stripe unix timestamp to an aware datetime."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _normalise_email(email: str | None) -> str | None:
    if not email:
        return None
    email = email.strip().lower()
    return email or None


def _plan_from_lookup_key(lookup_key: str | None) -> str | None:
    """Plan is the lookup_key prefix: ``premium_annual`` → ``premium``."""
    if not lookup_key:
        return None
    return lookup_key.split("_", 1)[0]


def _subscription_email(sub) -> str | None:
    """Best-effort email for the subscription's customer."""
    email = _event_get(sub, "customer_email")
    if not email:
        metadata = _event_get(sub, "metadata") or {}
        try:
            email = metadata["email"] if "email" in metadata else None
        except (TypeError, KeyError):
            email = None
    return _normalise_email(email)


def _find_user_id(conn, stripe_customer_id: str | None, email: str | None) -> int | None:
    """Resolve a user by stripe_customer_id first, then by email."""
    if stripe_customer_id:
        row = conn.execute(
            text("SELECT id FROM users WHERE stripe_customer_id = :cid"),
            {"cid": stripe_customer_id},
        ).first()
        if row:
            return row.id
    if email:
        row = conn.execute(
            text("SELECT id FROM users WHERE lower(email) = :email"),
            {"email": email},
        ).first()
        if row:
            # Back-fill the stripe_customer_id link now that we know it.
            if stripe_customer_id:
                conn.execute(
                    text("""
                        UPDATE users
                        SET stripe_customer_id = COALESCE(stripe_customer_id, :cid),
                            updated_at = now()
                        WHERE id = :id
                    """),
                    {"cid": stripe_customer_id, "id": row.id},
                )
            return row.id
    return None


def _handle_subscription_event(engine: Engine, event_type: str, sub) -> None:
    """Upsert ``subscriptions`` from a customer.subscription.* event.

    Subscription state in Postgres is always what Stripe says: the upsert is
    keyed on ``stripe_subscription_id`` and overwrites every mutable column.
    """
    sub_id = sub["id"]
    stripe_customer_id = _event_get(sub, "customer")
    customer_obj = _event_get(sub, "customer")
    # Stripe can expand the customer object; normalise to the id string.
    if isinstance(customer_obj, dict) or (
        hasattr(customer_obj, "id") and not isinstance(customer_obj, str)
    ):
        stripe_customer_id = _event_get(customer_obj, "id", stripe_customer_id)

    status = _event_get(sub, "status")
    canceled_at = _ts_to_dt(_event_get(sub, "canceled_at"))
    ended_at = _ts_to_dt(_event_get(sub, "ended_at"))
    if event_type == "customer.subscription.deleted":
        status = "canceled"
        ended_at = ended_at or datetime.now(timezone.utc)

    # Plan/price come from items.data[0].price
    items = _event_get(sub, "items") or {}
    item_list = _event_get(items, "data") or []
    first_item = item_list[0] if len(item_list) else {}
    price = _event_get(first_item, "price") or {}
    lookup_key = _event_get(price, "lookup_key")
    price_id = _event_get(price, "id")
    plan = _plan_from_lookup_key(lookup_key)

    # Period bounds: on the item in newer API versions, else on the sub.
    period_start = _ts_to_dt(
        _event_get(first_item, "current_period_start")
        or _event_get(sub, "current_period_start")
    )
    period_end = _ts_to_dt(
        _event_get(first_item, "current_period_end")
        or _event_get(sub, "current_period_end")
    )

    email = _subscription_email(sub)

    with engine.begin() as conn:
        user_id = _find_user_id(conn, stripe_customer_id, email)
        if user_id is None:
            raise _ParkedEvent(
                f"no user for stripe_customer_id={stripe_customer_id} email={email}"
            )

        conn.execute(
            text("""
                INSERT INTO subscriptions (
                    stripe_subscription_id, user_id, stripe_customer_id, status,
                    plan, lookup_key, price_id,
                    current_period_start, current_period_end,
                    cancel_at_period_end, canceled_at, ended_at,
                    raw, updated_at
                )
                VALUES (
                    :sub_id, :user_id, :customer_id, :status,
                    :plan, :lookup_key, :price_id,
                    :period_start, :period_end,
                    :cancel_at_period_end, :canceled_at, :ended_at,
                    CAST(:raw AS JSON), now()
                )
                ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    stripe_customer_id = EXCLUDED.stripe_customer_id,
                    status = EXCLUDED.status,
                    plan = COALESCE(EXCLUDED.plan, subscriptions.plan),
                    lookup_key = COALESCE(EXCLUDED.lookup_key, subscriptions.lookup_key),
                    price_id = COALESCE(EXCLUDED.price_id, subscriptions.price_id),
                    current_period_start = COALESCE(EXCLUDED.current_period_start,
                                                    subscriptions.current_period_start),
                    current_period_end = COALESCE(EXCLUDED.current_period_end,
                                                  subscriptions.current_period_end),
                    cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                    canceled_at = EXCLUDED.canceled_at,
                    ended_at = EXCLUDED.ended_at,
                    raw = EXCLUDED.raw,
                    updated_at = now()
            """),
            {
                "sub_id": sub_id,
                "user_id": user_id,
                "customer_id": stripe_customer_id,
                "status": status,
                "plan": plan,
                "lookup_key": lookup_key,
                "price_id": price_id,
                "period_start": period_start,
                "period_end": period_end,
                "cancel_at_period_end": bool(
                    _event_get(sub, "cancel_at_period_end", False)
                ),
                "canceled_at": canceled_at,
                "ended_at": ended_at,
                "raw": json.dumps(
                    json.loads(sub.to_json()) if hasattr(sub, "to_json") else {}
                ),
            },
        )

        # Mirror the effective entitlement onto users.subscription_status.
        if status in ("active", "trialing"):
            conn.execute(
                text("""
                    UPDATE users
                    SET subscription_status = COALESCE(:plan, 'premium'),
                        updated_at = now()
                    WHERE id = :id
                """),
                {"plan": plan, "id": user_id},
            )
        elif status in ("canceled", "unpaid", "incomplete_expired"):
            conn.execute(
                text("""
                    UPDATE users
                    SET subscription_status = 'none', updated_at = now()
                    WHERE id = :id
                """),
                {"id": user_id},
            )

    logger.info(
        "Subscription %s upserted from %s (status=%s, cancel_at_period_end=%s)",
        sub_id, event_type, status, _event_get(sub, "cancel_at_period_end"),
    )


def _handle_checkout_completed(
    engine: Engine,
    session: dict,
    background_tasks: BackgroundTasks,
) -> None:
    """Process a successful checkout."""
    # The Stripe SDK's StripeObject supports dict indexing (session["id"])
    # but does NOT expose .get() the way a plain dict does — .get collides
    # with the SDK's internal HTTP GET. Use the module-level safe accessor.
    _g = _event_get

    session_id = session["id"]
    customer_details = _g(session, "customer_details") or {}
    email = _normalise_email(_g(customer_details, "email"))
    payment_intent = _g(session, "payment_intent")
    payment_status = _g(session, "payment_status")
    stripe_customer_id = _g(session, "customer")
    metadata = _g(session, "metadata") or {}

    with engine.begin() as conn:
        # Link the checkout's Stripe customer to a user: first by
        # users.stripe_customer_id (signed-in repeat buyers), else by the
        # email collected at checkout (claims guest purchases on the
        # user's next sign-in / purchase).
        user_id = None
        try:
            user_id = link_checkout_customer_to_user(conn, stripe_customer_id, email)
        except Exception:
            logger.exception(
                "User linking failed for session %s (customer %s)",
                session_id, stripe_customer_id,
            )

        if conn.dialect.name == "postgresql":
            result = conn.execute(
                text("""
                    UPDATE orders
                    SET status = 'paid',
                        paid_at = :now,
                        email = COALESCE(:email, email),
                        stripe_payment_intent = :pi,
                        stripe_customer_id = COALESCE(:customer, stripe_customer_id),
                        user_id = COALESCE(:user_id, user_id)
                    WHERE stripe_session_id = :sid
                      AND status = 'pending'
                    RETURNING id, user_id
                """),
                {
                    "now": datetime.now(timezone.utc),
                    "email": email,
                    "pi": payment_intent,
                    "customer": stripe_customer_id,
                    "user_id": user_id,
                    "sid": session_id,
                },
            )
            row = result.mappings().first()
        else:
            # Dialect-neutral path (SQLite unit tests): UPDATE without
            # RETURNING, then SELECT the updated row.
            result = conn.execute(
                text("""
                    UPDATE orders
                    SET status = 'paid',
                        paid_at = :now,
                        email = COALESCE(:email, email),
                        stripe_payment_intent = :pi,
                        stripe_customer_id = COALESCE(:customer, stripe_customer_id),
                        user_id = COALESCE(:user_id, user_id)
                    WHERE stripe_session_id = :sid
                      AND status = 'pending'
                """),
                {
                    "now": datetime.now(timezone.utc),
                    "email": email,
                    "pi": payment_intent,
                    "customer": stripe_customer_id,
                    "user_id": user_id,
                    "sid": session_id,
                },
            )
            row = None
            if result.rowcount:
                row = conn.execute(
                    text(
                        "SELECT id, user_id FROM orders "
                        "WHERE stripe_session_id = :sid"
                    ),
                    {"sid": session_id},
                ).mappings().first()

    if not row:
        logger.warning(f"No pending order found for session {session_id}")
        return

    order_id = row["id"]
    logger.info(
        "Order %s marked as paid (session %s, customer %s, user %s)",
        order_id, session_id, stripe_customer_id, row["user_id"],
    )

    from irc_data.api.services.analytics_service import track
    track("order_paid", _g(metadata, "order_token") or str(order_id), {
        "order_id": order_id,
        "session_id": session_id,
        "amount_total": _g(session, "amount_total"),
        "currency": _g(session, "currency"),
        "email": email,
        "boat_id": _g(metadata, "boat_id"),
        "stripe_customer_id": stripe_customer_id,
        "user_id": row["user_id"],
    })

    # Kick off report generation in background
    background_tasks.add_task(_generate_and_deliver, engine, order_id)


def _generate_and_deliver(engine: Engine, order_id: int) -> None:
    """Background task: generate report content, render PDF, send email."""
    from datetime import datetime, timezone

    from irc_data.api.services.email_service import send_report_email
    from irc_data.api.services.pdf_service import render_pdf
    from irc_data.api.services.report_service import generate_report_content

    try:
        # 1. Generate report content (AI analysis + analytics)
        generate_report_content(engine, order_id)

        # 2. Render PDF
        render_pdf(engine, order_id)

        # 3. Send email
        send_report_email(engine, order_id)

    except Exception as e:
        logger.error(f"Report generation failed for order {order_id}: {e}", exc_info=True)
        # Mark order as error so frontend stops polling
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE orders
                        SET status = 'error',
                            report_generated_at = :now
                        WHERE id = :id
                          AND status IN ('paid', 'pending')
                    """),
                    {"now": datetime.now(timezone.utc), "id": order_id},
                )
            logger.info(f"Order {order_id} marked as error after generation failure")
        except Exception as inner:
            logger.error(f"Failed to mark order {order_id} as error: {inner}")
