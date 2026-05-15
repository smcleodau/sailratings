"""Stripe checkout endpoints for report purchases."""

import logging
import os
import uuid

import stripe
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db
from irc_data.env import FRONTEND_URL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/checkout", tags=["Checkout"])

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
):
    """Create a Stripe Checkout Session for a boat report purchase."""
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

    # Create pending order
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO orders (order_token, boat_id, amount_cents, currency,
                                    search_query, teaser_text, status)
                VALUES (:token, :boat_id, :amount, :currency,
                        :search_query, :teaser_text, 'pending')
            """),
            {
                "token": str(order_token),
                "boat_id": body.boat_id,
                "amount": amount_cents,
                "currency": currency,
                "search_query": body.search_query,
                "teaser_text": body.teaser_text,
            },
        )

    # Create Stripe session
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
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
            mode="payment",
            allow_promotion_codes=True,
            success_url=f"{FRONTEND_URL}/report/{order_token}",
            cancel_url=f"{FRONTEND_URL}/boat/{body.boat_id}",
            metadata={
                "order_token": str(order_token),
                "boat_id": str(body.boat_id),
            },
        )
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
    """Handle Stripe webhook events."""
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

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        _handle_checkout_completed(engine, session, background_tasks)

    return {"status": "ok"}


def _handle_checkout_completed(
    engine: Engine,
    session: dict,
    background_tasks: BackgroundTasks,
) -> None:
    """Process a successful checkout."""
    from datetime import datetime, timezone

    session_id = session["id"]
    email = session.get("customer_details", {}).get("email")
    payment_intent = session.get("payment_intent")

    with engine.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE orders
                SET status = 'paid',
                    paid_at = :now,
                    email = COALESCE(:email, email),
                    stripe_payment_intent = :pi
                WHERE stripe_session_id = :sid
                  AND status = 'pending'
                RETURNING id
            """),
            {
                "now": datetime.now(timezone.utc),
                "email": email,
                "pi": payment_intent,
                "sid": session_id,
            },
        )
        row = result.first()

    if not row:
        logger.warning(f"No pending order found for session {session_id}")
        return

    order_id = row.id
    logger.info(f"Order {order_id} marked as paid (session {session_id})")

    from irc_data.api.services.analytics_service import track
    track("order_paid", session.get("metadata", {}).get("order_token") or str(order_id), {
        "order_id": order_id,
        "session_id": session_id,
        "amount_total": session.get("amount_total"),
        "currency": session.get("currency"),
        "email": email,
        "boat_id": session.get("metadata", {}).get("boat_id"),
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
