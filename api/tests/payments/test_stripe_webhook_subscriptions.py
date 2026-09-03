"""PAY-01-09: Stripe webhook → subscriptions, idempotent via stripe_events.

Acceptance criteria under test:
  * Resend of the same event id produces no duplicate rows and no re-dispatch.
  * A portal cancellation (customer.subscription.updated with
    cancel_at_period_end=true) lands within one delivery.
  * Events that cannot be matched to a user are parked and visible via the
    admin endpoint.
  * Handler failures record an error and return 500 so Stripe retries.
  * checkout.session.completed additionally sets orders.user_id and
    orders.stripe_payment_status.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.payments.conftest import (
    TEST_WEBHOOK_SECRET,
    make_subscription_event,
    post_event,
    sign,
)


def _rows(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).mappings().all()


def _make_user(engine, email, stripe_customer_id=None):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO users (email, stripe_customer_id) "
                "VALUES (:email, :cid) RETURNING id"
            ),
            {"email": email, "cid": stripe_customer_id},
        ).first()
    return row.id


# ── Signature verification ────────────────────────────────────────────────


def test_invalid_signature_rejected(client, db):
    event = make_subscription_event("evt_bad_sig", "customer.subscription.created")
    res = post_event(client, event, secret="whsec_wrong")
    assert res.status_code == 400
    assert _rows(db, "SELECT * FROM stripe_events") == []


# ── Idempotent replay (AC 1) ──────────────────────────────────────────────


def test_replay_same_event_id_no_duplicate_rows(client, db):
    _make_user(db, "skipper@example.com", "cus_Pay0109")
    event = make_subscription_event("evt_replay_1", "customer.subscription.created")

    first = post_event(client, event)
    assert first.status_code == 200

    # Stripe resend of the SAME event id (e.g. dashboard 'resend').
    replay = post_event(client, event)
    assert replay.status_code == 200
    assert replay.json()["replay"] is True

    events = _rows(db, "SELECT * FROM stripe_events WHERE event_id = 'evt_replay_1'")
    assert len(events) == 1
    assert events[0]["processed_at"] is not None

    subs = _rows(db, "SELECT * FROM subscriptions")
    assert len(subs) == 1  # no duplicate subscription rows


# ── Subscription lifecycle ────────────────────────────────────────────────


def test_subscription_created_upserts_row_and_user(client, db):
    user_id = _make_user(db, "skipper@example.com", "cus_Pay0109")
    event = make_subscription_event("evt_created_1", "customer.subscription.created")
    res = post_event(client, event)
    assert res.status_code == 200

    subs = _rows(db, "SELECT * FROM subscriptions")
    assert len(subs) == 1
    sub = subs[0]
    assert sub["stripe_subscription_id"] == "sub_1Pay0109Premium"
    assert sub["user_id"] == user_id
    assert sub["stripe_customer_id"] == "cus_Pay0109"
    assert sub["status"] == "active"
    # plan = items[0].price.lookup_key prefix
    assert sub["lookup_key"] == "premium_annual"
    assert sub["plan"] == "premium"
    assert sub["cancel_at_period_end"] is False
    assert sub["current_period_start"] is not None
    assert sub["current_period_end"] is not None

    user = _rows(db, "SELECT * FROM users WHERE id = :id", id=user_id)[0]
    assert user["subscription_status"] == "premium"


def test_portal_cancellation_sets_cancel_at_period_end_in_one_delivery(client, db):
    """AC 2: a portal cancellation shows cancel_at_period_end=true within one
    delivery (no second event needed)."""
    _make_user(db, "skipper@example.com", "cus_Pay0109")
    created = make_subscription_event("evt_cancel_seq_1", "customer.subscription.created")
    assert post_event(client, created).status_code == 200

    cancelled = make_subscription_event(
        "evt_cancel_seq_2",
        "customer.subscription.updated",
        subscription_overrides={
            "cancel_at_period_end": True,
            "canceled_at": 1780000200,
        },
    )
    res = post_event(client, cancelled)
    assert res.status_code == 200

    subs = _rows(db, "SELECT * FROM subscriptions")
    assert len(subs) == 1  # upsert, not insert
    assert subs[0]["cancel_at_period_end"] is True
    assert subs[0]["canceled_at"] is not None


def test_subscription_deleted_downgrades_user(client, db):
    user_id = _make_user(db, "skipper@example.com", "cus_Pay0109")
    created = make_subscription_event("evt_del_seq_1", "customer.subscription.created")
    post_event(client, created)

    deleted = make_subscription_event(
        "evt_del_seq_2",
        "customer.subscription.deleted",
        subscription_overrides={"status": "canceled", "ended_at": 1780000300},
    )
    res = post_event(client, deleted)
    assert res.status_code == 200

    sub = _rows(db, "SELECT * FROM subscriptions")[0]
    assert sub["status"] == "canceled"
    assert sub["ended_at"] is not None

    user = _rows(db, "SELECT * FROM users WHERE id = :id", id=user_id)[0]
    assert user["subscription_status"] == "none"


def test_subscription_paused_and_resumed(client, db):
    _make_user(db, "skipper@example.com", "cus_Pay0109")
    post_event(
        client,
        make_subscription_event("evt_pause_seq_1", "customer.subscription.created"),
    )
    paused = make_subscription_event(
        "evt_pause_seq_2",
        "customer.subscription.paused",
        subscription_overrides={"status": "paused"},
    )
    assert post_event(client, paused).status_code == 200
    assert _rows(db, "SELECT status FROM subscriptions")[0]["status"] == "paused"

    resumed = make_subscription_event(
        "evt_pause_seq_3",
        "customer.subscription.resumed",
        subscription_overrides={"status": "active"},
    )
    assert post_event(client, resumed).status_code == 200
    assert _rows(db, "SELECT status FROM subscriptions")[0]["status"] == "active"


def test_user_resolution_by_email_fallback(client, db):
    """No stripe_customer_id match → fall back to email, then backfill."""
    user_id = _make_user(db, "skipper@example.com")  # no stripe_customer_id yet
    event = make_subscription_event("evt_email_1", "customer.subscription.created")
    assert post_event(client, event).status_code == 200

    sub = _rows(db, "SELECT * FROM subscriptions")[0]
    assert sub["user_id"] == user_id
    user = _rows(db, "SELECT * FROM users WHERE id = :id", id=user_id)[0]
    assert user["stripe_customer_id"] == "cus_Pay0109"  # back-filled


# ── Parked events (AC 3) ──────────────────────────────────────────────────


def test_unmatched_customer_is_parked_and_visible_in_admin(client, db, monkeypatch):
    event = make_subscription_event("evt_parked_1", "customer.subscription.created")
    res = post_event(client, event)
    assert res.status_code == 200
    assert res.json()["status"] == "parked"

    parked = _rows(db, "SELECT * FROM stripe_events WHERE event_id = 'evt_parked_1'")
    assert len(parked) == 1
    assert parked[0]["error"].startswith("parked: no user")
    assert _rows(db, "SELECT * FROM subscriptions") == []

    # Admin endpoint surfaces parked events
    monkeypatch.setenv("ADMIN_PASSWORD", "testadmin")
    import importlib

    import irc_data.api.routers.admin as admin_mod

    importlib.reload(admin_mod)
    res = client.get(
        "/v1/admin/stripe-events?parked_only=true",
        headers={"Authorization": "Bearer testadmin"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["parked"] == 1
    assert body["events"][0]["event_id"] == "evt_parked_1"

    # And the unfiltered listing requires auth
    assert client.get("/v1/admin/stripe-events").status_code == 401


# ── Failure → 500 for Stripe retry ────────────────────────────────────────


def test_handler_failure_records_error_and_returns_500(client, db, monkeypatch):
    import irc_data.api.routers.checkout as checkout_mod

    def boom(engine, event_type, sub):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(checkout_mod, "_handle_subscription_event", boom)
    _make_user(db, "skipper@example.com", "cus_Pay0109")

    event = make_subscription_event("evt_fail_1", "customer.subscription.created")
    res = post_event(client, event)
    assert res.status_code == 500

    row = _rows(db, "SELECT * FROM stripe_events WHERE event_id = 'evt_fail_1'")[0]
    assert "db exploded" in row["error"]
    assert row["processed_at"] is None


# ── checkout.session.completed enrichment ────────────────────────────────


def test_checkout_completed_sets_user_and_payment_status(client, db, monkeypatch):
    # The background report task touches AI/email services — stub it out.
    import irc_data.api.routers.checkout as checkout_mod

    monkeypatch.setattr(
        checkout_mod, "_generate_and_deliver", lambda engine, order_id: None
    )

    user_id = _make_user(db, "buyer@example.com", "cus_Buyer1")

    # Grab any boat for the FK
    with db.connect() as conn:
        boat = conn.execute(text("SELECT id FROM boats LIMIT 1")).first()
    if not boat:
        pytest.skip("no boats in dev database")
    token = str(uuid.uuid4())
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO orders (order_token, boat_id, amount_cents, currency, "
                "stripe_session_id, status) "
                "VALUES (:t, :b, 9900, 'usd', 'cs_test_pay0109', 'pending')"
            ),
            {"t": token, "b": boat.id},
        )

    event = {
        "id": "evt_checkout_1",
        "object": "event",
        "api_version": "2025-02-24.acacia",
        "created": 1780000400,
        "livemode": False,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_pay0109",
                "object": "checkout.session",
                "customer": "cus_Buyer1",
                "customer_details": {"email": "buyer@example.com"},
                "payment_intent": "pi_pay0109",
                "payment_status": "paid",
                "amount_total": 9900,
                "currency": "usd",
                "metadata": {"order_token": token, "boat_id": str(boat.id)},
            }
        },
    }
    res = post_event(client, event)
    assert res.status_code == 200

    order = _rows(db, "SELECT * FROM orders WHERE order_token = :t", t=token)[0]
    assert order["status"] == "paid"
    assert order["user_id"] == user_id
    assert order["stripe_payment_status"] == "paid"


# ── Unknown event types are acked and logged, not retried ────────────────


def test_unknown_event_type_recorded_and_acked(client, db):
    event = {
        "id": "evt_unknown_1",
        "object": "event",
        "api_version": "2025-02-24.acacia",
        "created": 1780000500,
        "livemode": False,
        "type": "invoice.payment_succeeded",
        "data": {"object": {"id": "in_123", "object": "invoice"}},
    }
    res = post_event(client, event)
    assert res.status_code == 200
    row = _rows(db, "SELECT * FROM stripe_events WHERE event_id = 'evt_unknown_1'")[0]
    assert row["processed_at"] is not None
    assert row["error"] is None


def test_signing_helper_roundtrip(client, db):
    """Sanity: our signed fixture passes Stripe's real verification."""
    event = make_subscription_event("evt_sig_ok", "customer.subscription.created")
    body, header = sign(event, TEST_WEBHOOK_SECRET)
    import stripe

    parsed = stripe.Webhook.construct_event(body, header, TEST_WEBHOOK_SECRET)
    assert parsed["id"] == "evt_sig_ok"
