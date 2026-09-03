"""Stripe test-mode e2e for PAY-01-08 (customer per user).

Runs against the dev Postgres (DATABASE_URL) and the real Stripe TEST-mode
API (STRIPE_SECRET_KEY=sk_test_...). All rows/customers created here use a
unique run tag and are cleaned up at the end.

Acceptance criteria exercised:

1. Repeat test-mode purchases by one signed-in user create EXACTLY ONE
   Stripe customer (first checkout creates it with metadata.clerk_id;
   second reuses it via ``customer=`` — no ``customer_creation``).
2. A guest purchase creates a session with ``customer_creation=always``;
   the completed checkout links the session's customer to the user
   (by email) and sets ``orders.user_id`` — the "next sign-in" claim.

Usage:  python api/scripts/e2e_stripe_customer_per_user.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import stripe
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from irc_data.api.deps import CallerIdentity, get_db, get_optional_identity  # noqa: E402
from irc_data.api.routers import checkout as checkout_router  # noqa: E402

RUN_TAG = uuid.uuid4().hex[:8]
EMAIL = f"pay0108-e2e-{RUN_TAG}@example.com"
CLERK_ID = f"user_e2e_{RUN_TAG}"

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def sign(payload: bytes, secret: str) -> str:
    ts = int(time.time())
    signed = f"{ts}.{payload.decode()}".encode()
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def main() -> int:
    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    db_url = os.environ.get("DATABASE_URL", "")
    if not stripe_key.startswith("sk_test_"):
        print("ERROR: STRIPE_SECRET_KEY must be a test-mode key")
        return 2
    if not webhook_secret or not db_url:
        print("ERROR: STRIPE_WEBHOOK_SECRET and DATABASE_URL are required")
        return 2
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    stripe.api_key = stripe_key
    engine = create_engine(db_url)

    with engine.connect() as conn:
        boat = conn.execute(
            text("SELECT id, boat_name FROM boats ORDER BY id LIMIT 1")
        ).first()
    if not boat:
        print("ERROR: no boats in DB")
        return 2
    boat_id, boat_name = boat[0], boat[1]
    print(f"run tag: {RUN_TAG}  boat: {boat_id} {boat_name!r}  email: {EMAIL}")

    app = FastAPI()
    app.include_router(checkout_router.router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: engine

    created_customers: list[str] = []
    order_tokens: list[str] = []
    try:
        # ── Scenario 1: signed-in repeat purchases → one customer ──────
        identity = CallerIdentity(clerk_user_id=CLERK_ID, email=EMAIL)
        app.dependency_overrides[get_optional_identity] = lambda: identity
        client = TestClient(app, raise_server_exceptions=True)

        customers_before = {
            c.id for c in stripe.Customer.list(email=EMAIL, limit=10)
        }

        r1 = client.post("/v1/checkout/create-session", json={
            "boat_id": boat_id, "boat_name": boat_name, "currency": "usd",
        })
        check("signed-in checkout #1 returns 200", r1.status_code == 200, r1.text[:200])
        order_tokens.append(r1.json()["order_token"])
        s1 = stripe.checkout.Session.retrieve(_session_id(engine, order_tokens[-1]))
        check("session #1 has customer= set", bool(s1.get("customer")),
              str(s1.get("customer")))
        cust1 = s1.get("customer")
        created_customers.append(cust1)

        stripe_customer = stripe.Customer.retrieve(cust1)
        check("customer email matches", stripe_customer.get("email") == EMAIL,
              str(stripe_customer.get("email")))
        check("customer metadata.clerk_id matches",
              (stripe_customer.get("metadata") or {}).get("clerk_id") == CLERK_ID)

        r2 = client.post("/v1/checkout/create-session", json={
            "boat_id": boat_id, "boat_name": boat_name, "currency": "gbp",
        })
        check("signed-in checkout #2 returns 200", r2.status_code == 200, r2.text[:200])
        order_tokens.append(r2.json()["order_token"])
        s2 = stripe.checkout.Session.retrieve(_session_id(engine, order_tokens[-1]))
        check("session #2 reuses the same customer", s2.get("customer") == cust1,
              f"{s2.get('customer')} vs {cust1}")

        customers_after = {
            c.id for c in stripe.Customer.list(email=EMAIL, limit=10)
        }
        new_customers = customers_after - customers_before
        check("exactly one Stripe customer for the signed-in user",
              len(new_customers) == 1, f"new={new_customers}")

        with engine.connect() as conn:
            u = conn.execute(
                text("SELECT id, stripe_customer_id FROM users WHERE clerk_id = :c"),
                {"c": CLERK_ID},
            ).mappings().first()
        check("users.stripe_customer_id populated", bool(u) and u["stripe_customer_id"] == cust1,
              str(u))
        user_id = u["id"]

        with engine.connect() as conn:
            linked = conn.execute(
                text("SELECT COUNT(*) FROM orders WHERE user_id = :u"),
                {"u": user_id},
            ).scalar()
        check("both signed-in orders carry user_id at creation", linked == 2,
              f"linked={linked}")

        # ── Scenario 2: guest purchase → customer_creation=always ──────
        app.dependency_overrides[get_optional_identity] = lambda: None
        guest_client = TestClient(app, raise_server_exceptions=True)
        rg = guest_client.post("/v1/checkout/create-session", json={
            "boat_id": boat_id, "boat_name": boat_name, "currency": "usd",
        })
        check("guest checkout returns 200", rg.status_code == 200, rg.text[:200])
        order_tokens.append(rg.json()["order_token"])
        sg = stripe.checkout.Session.retrieve(_session_id(engine, order_tokens[-1]))
        check("guest session has customer_creation=always",
              sg.get("customer_creation") == "always", str(sg.get("customer_creation")))
        check("guest session has no pre-set customer", not sg.get("customer"))

        # ── Scenario 3: completed guest checkout links to the user ─────
        # Simulate the user's next sign-in having already created the user
        # row (scenario 1 did); fire a signed webhook for the guest session
        # whose customer_details.email matches the user.
        guest_cust = f"cus_e2e_guest_{RUN_TAG}"
        payload = json.dumps({
            "id": f"evt_e2e_{RUN_TAG}",
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": sg["id"],
                "object": "checkout.session",
                "customer": guest_cust,
                "customer_details": {"email": EMAIL},
                "payment_intent": f"pi_e2e_{RUN_TAG}",
                "metadata": {"order_token": order_tokens[-1], "boat_id": str(boat_id)},
                "amount_total": 9900,
                "currency": "usd",
            }},
        }).encode()
        rw = guest_client.post(
            "/v1/checkout/webhook", content=payload,
            headers={"stripe-signature": sign(payload, webhook_secret),
                     "content-type": "application/json"},
        )
        check("webhook accepted (200)", rw.status_code == 200, rw.text[:200])

        # The webhook kicks off background report generation which later
        # flips status paid → generated; wait for the settled state.
        o = None
        for _ in range(30):
            with engine.connect() as conn:
                o = conn.execute(
                    text(
                        "SELECT status, user_id, stripe_customer_id, email, "
                        "paid_at FROM orders WHERE stripe_session_id = :s"
                    ),
                    {"s": sg["id"]},
                ).mappings().first()
            if o and o["status"] in ("generated", "error", "delivered"):
                break
            time.sleep(1)
        check("guest order payment recorded (status + paid_at)",
              o and o["status"] in ("paid", "generated", "delivered")
              and o["paid_at"] is not None, str(o))
        check("guest order linked to user by email", o and o["user_id"] == user_id,
              f"user_id={o and o['user_id']}")
        check("guest order records stripe customer",
              o and o["stripe_customer_id"] == guest_cust)

        with engine.connect() as conn:
            u2 = conn.execute(
                text("SELECT stripe_customer_id FROM users WHERE id = :i"),
                {"i": user_id},
            ).scalar()
        check("existing user customer not overwritten by guest link",
              u2 == cust1, str(u2))

        # ── Scenario 4: webhook matches by stripe_customer_id first ────
        r3 = client.post("/v1/checkout/create-session", json={
            "boat_id": boat_id, "boat_name": boat_name, "currency": "usd",
        })
        order_tokens.append(r3.json()["order_token"])
        sid3 = _session_id(engine, order_tokens[-1])
        payload3 = json.dumps({
            "id": f"evt_e2e_b_{RUN_TAG}",
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": sid3,
                "object": "checkout.session",
                "customer": cust1,
                "customer_details": {"email": "someone-else@example.org"},
                "payment_intent": f"pi_e2e_b_{RUN_TAG}",
                "metadata": {"order_token": order_tokens[-1], "boat_id": str(boat_id)},
                "amount_total": 9900,
                "currency": "usd",
            }},
        }).encode()
        rw3 = client.post(
            "/v1/checkout/webhook", content=payload3,
            headers={"stripe-signature": sign(payload3, webhook_secret),
                     "content-type": "application/json"},
        )
        check("webhook #2 accepted (200)", rw3.status_code == 200, rw3.text[:200])
        time.sleep(2)  # let the synchronous part of the webhook settle
        with engine.connect() as conn:
            o3 = conn.execute(
                text("SELECT user_id FROM orders WHERE stripe_session_id = :s"),
                {"s": sid3},
            ).mappings().first()
        check("customer-id match wins over foreign email",
              o3 and o3["user_id"] == user_id, str(o3))

    finally:
        # ── Cleanup: DB rows + Stripe customers ───────────────────────
        # Orders first (orders.user_id → users FK), then the user row.
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM orders WHERE order_token = ANY(:t)"),
                {"t": list(order_tokens)},
            )
            # Guest orders may have been linked by email with tokens we
            # didn't track; sweep by email too.
            conn.execute(
                text("DELETE FROM orders WHERE lower(email) = :e"),
                {"e": EMAIL},
            )
            conn.execute(text("DELETE FROM users WHERE clerk_id = :c"), {"c": CLERK_ID})
        for cid in created_customers:
            try:
                stripe.Customer.delete(cid)
            except stripe.StripeError as e:
                print(f"cleanup: could not delete customer {cid}: {e}")
        print(f"cleaned up {len(order_tokens)} orders, user {CLERK_ID}, "
              f"{len(created_customers)} stripe customer(s)")

    print()
    if FAILURES:
        print(f"E2E FAILED: {len(FAILURES)} check(s) failed: {FAILURES}")
        return 1
    print("E2E PASSED: all checks green")
    return 0


def _session_id(engine, order_token: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT stripe_session_id FROM orders WHERE order_token = :t"),
            {"t": order_token},
        ).scalar()


if __name__ == "__main__":
    raise SystemExit(main())
