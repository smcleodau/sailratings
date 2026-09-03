#!/usr/bin/env python3
"""Test-mode e2e driver for PAY-01-09.

Exercises the live HTTP webhook endpoint (no TestClient, no mocks — the
request goes over TCP to a running uvicorn, is signed with the real Stripe
signature scheme, and is verified with the real SDK):

  1. Seed a user row for the fixture customer.
  2. POST customer.subscription.created           → subscription row appears.
  3. POST the SAME event again (Stripe 'resend')  → 200, no duplicate rows.
  4. POST customer.subscription.updated with      → cancel_at_period_end=true
     cancel_at_period_end=true (portal cancel)       visible immediately.
  5. POST customer.subscription.created for an    → parked, visible via the
     unknown customer                                  admin endpoint.
  6. POST with a bad signature                    → 400, nothing recorded.

Usage:
    BASE=http://127.0.0.1:4199 \
    DATABASE_URL=postgresql+psycopg://irc:irc@localhost:5433/pay0109_test \
    STRIPE_WEBHOOK_SECRET=whsec_... ADMIN_PASSWORD=... \
    python api/scripts/e2e_stripe_webhook.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

from sqlalchemy import create_engine, text

BASE = os.environ.get("BASE", "http://127.0.0.1:4199")
DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://irc:irc@localhost:5433/pay0109_test"
).replace("postgresql://", "postgresql+psycopg://", 1)
SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]
ADMIN = os.environ.get("ADMIN_PASSWORD", "")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
failures = 0


def check(name: str, ok: bool, detail: str = ""):
    global failures
    print(f"{'✔' if ok else '✘'} {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures += 1


def post_event(payload: dict, secret: str = SECRET) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    ts = int(time.time())
    sig = hmac.new(
        secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256
    ).hexdigest()
    req = urllib.request.Request(
        f"{BASE}/v1/checkout/webhook",
        data=body,
        headers={"stripe-signature": f"t={ts},v1={sig}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def subscription_event(event_id, event_type, **overrides):
    sub = {
        "id": "sub_e2ePay0109",
        "object": "subscription",
        "customer": "cus_e2ePay0109",
        "customer_email": "e2e-skipper@example.com",
        "status": "active",
        "cancel_at_period_end": False,
        "canceled_at": None,
        "ended_at": None,
        "current_period_start": 1780000000,
        "current_period_end": 1810000000,
        "metadata": {},
        "items": {
            "object": "list",
            "data": [
                {
                    "id": "si_e2e",
                    "object": "subscription_item",
                    "current_period_start": 1780000000,
                    "current_period_end": 1810000000,
                    "price": {
                        "id": "price_e2e_annual",
                        "object": "price",
                        "lookup_key": "premium_annual",
                        "unit_amount": 29000,
                        "currency": "usd",
                    },
                }
            ],
        },
    }
    sub.update(overrides)
    return {
        "id": event_id,
        "object": "event",
        "api_version": "2025-02-24.acacia",
        "created": 1780000100,
        "livemode": False,
        "type": event_type,
        "data": {"object": sub},
    }


def main() -> int:
    engine = create_engine(DB_URL)

    # Reset fixtures
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM subscriptions WHERE stripe_subscription_id LIKE 'sub_e2e%'"))
        conn.execute(text("DELETE FROM users WHERE email LIKE 'e2e-%@example.com'"))
        conn.execute(text("DELETE FROM stripe_events WHERE event_id LIKE 'evt_e2e%'"))
        conn.execute(
            text("INSERT INTO users (email, stripe_customer_id) VALUES (:e, :c)"),
            {"e": "e2e-skipper@example.com", "c": "cus_e2ePay0109"},
        )

    # 1. created
    code, body = post_event(subscription_event("evt_e2e_created", "customer.subscription.created"))
    with engine.connect() as conn:
        subs = conn.execute(text("SELECT * FROM subscriptions")).mappings().all()
    check("subscription.created → 200 + row upserted",
          code == 200 and len(subs) == 1 and subs[0]["plan"] == "premium",
          f"status={code} plan={subs[0]['plan'] if subs else None}")

    # 2. replay (Stripe 'resend' of the same id)
    code, body = post_event(subscription_event("evt_e2e_created", "customer.subscription.created"))
    with engine.connect() as conn:
        n_events = conn.execute(text("SELECT count(*) FROM stripe_events WHERE event_id='evt_e2e_created'")).scalar()
        n_subs = conn.execute(text("SELECT count(*) FROM subscriptions")).scalar()
    check("replay same id → 200, no duplicate rows",
          code == 200 and body.get("replay") is True and n_events == 1 and n_subs == 1,
          f"replay={body.get('replay')} events={n_events} subs={n_subs}")

    # 3. portal cancellation
    code, _ = post_event(subscription_event(
        "evt_e2e_cancel", "customer.subscription.updated",
        cancel_at_period_end=True, canceled_at=1780000200,
    ))
    with engine.connect() as conn:
        sub = conn.execute(text("SELECT * FROM subscriptions")).mappings().first()
    check("portal cancel → cancel_at_period_end=true within one delivery",
          code == 200 and sub["cancel_at_period_end"] is True,
          f"cancel_at_period_end={sub['cancel_at_period_end']}")

    # 4. parked (unknown customer)
    parked = subscription_event("evt_e2e_parked", "customer.subscription.created")
    parked["data"]["object"]["id"] = "sub_e2eParked"
    parked["data"]["object"]["customer"] = "cus_unknown"
    parked["data"]["object"]["customer_email"] = "nobody@example.com"
    code, body = post_event(parked)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT error FROM stripe_events WHERE event_id='evt_e2e_parked'")
        ).mappings().first()
    check("unknown customer → parked (200, error recorded)",
          code == 200 and body.get("status") == "parked" and row and row["error"].startswith("parked:"),
          f"error={row['error'] if row else None}")

    # 5. parked visible in admin
    req = urllib.request.Request(
        f"{BASE}/v1/admin/stripe-events?parked_only=true",
        headers={"Authorization": f"Bearer {ADMIN}"},
    )
    with urllib.request.urlopen(req) as res:
        admin_body = json.loads(res.read())
    parked_ids = [e["event_id"] for e in admin_body["events"]]
    check("parked event visible via GET /v1/admin/stripe-events",
          "evt_e2e_parked" in parked_ids and admin_body["counts"]["parked"] >= 1,
          f"parked={parked_ids}")

    # 6. bad signature rejected
    code, _ = post_event(subscription_event("evt_e2e_badsig", "customer.subscription.created"),
                         secret="whsec_wrong")
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM stripe_events WHERE event_id='evt_e2e_badsig'")).scalar()
    check("bad signature → 400 and nothing recorded", code == 400 and n == 0)

    # Cleanup fixtures so the e2e is re-runnable and leaves no residue.
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM subscriptions WHERE stripe_subscription_id LIKE 'sub_e2e%'"))
        conn.execute(text("DELETE FROM users WHERE email LIKE 'e2e-%@example.com'"))
        conn.execute(text("DELETE FROM stripe_events WHERE event_id LIKE 'evt_e2e%'"))

    print()
    print("E2E RESULT:", PASS if failures == 0 else f"{FAIL} ({failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
