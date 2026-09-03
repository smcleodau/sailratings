"""Contract tests for the Admin Customers zone (PAY-01-10).

Covers:
  GET  /v1/admin/users            — q / plan / role / claims=pending / cursor
  GET  /v1/admin/users/{id}       — dossier with boats, orders, claims
  POST /v1/admin/users/{id}/role  — role changes validated + persisted
  POST /v1/admin/claims/{id}/verify|reject — pending-only transitions
  GET  /v1/admin/orders           — all 47 rows, honest 'abandoned' status
  POST /v1/admin/orders/{id}/regenerate — paid-only, kicks background regen
  GET  /v1/admin/billing          — catalogue by lookup_key, promo codes,
                                    balance, last charges (60 s cache)

In-memory SQLite mirrors of ``users`` / ``boat_claims`` / ``orders`` /
``boats`` + the ``v_admin_users`` view (SQLite dialect). Stripe is patched.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient

from irc_data.api.deps import get_db
from irc_data.api.routers import admin_customers

ADMIN_PW = "test-admin-pw"
AUTH = {"Authorization": f"Bearer {ADMIN_PW}"}

N_ORDERS = 47  # matches the production expectation in the acceptance criteria

SCHEMA_SQL = """
CREATE TABLE boats (
    id INTEGER PRIMARY KEY,
    boat_name TEXT,
    sail_number TEXT,
    design TEXT,
    design_canonical TEXT,
    country TEXT
);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clerk_id TEXT UNIQUE,
    email TEXT NOT NULL UNIQUE,
    full_name TEXT,
    role TEXT NOT NULL DEFAULT 'customer',
    plan TEXT NOT NULL DEFAULT 'free',
    subscription_status TEXT NOT NULL DEFAULT 'none',
    stripe_customer_id TEXT,
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE boat_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    boat_id INTEGER NOT NULL REFERENCES boats(id),
    status TEXT NOT NULL DEFAULT 'pending',
    evidence TEXT,
    verified_by TEXT,
    verified_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_token TEXT NOT NULL UNIQUE,
    boat_id INTEGER NOT NULL REFERENCES boats(id),
    email TEXT,
    user_id TEXT REFERENCES users(id),
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'usd',
    stripe_session_id TEXT UNIQUE,
    stripe_payment_intent TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    report_markdown TEXT,
    report_analytics TEXT,
    pdf_path TEXT,
    search_query TEXT,
    teaser_text TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at TIMESTAMP,
    report_generated_at TIMESTAMP,
    email_sent_at TIMESTAMP
);
CREATE VIEW v_admin_users AS
SELECT
    u.id,
    u.email,
    u.full_name,
    u.role,
    u.plan,
    u.subscription_status,
    u.clerk_id,
    u.stripe_customer_id,
    u.created_at AS joined_at,
    u.last_seen_at,
    COALESCE(bc.boats_claimed, 0)  AS boats_claimed,
    COALESCE(bc.pending_claims, 0) AS pending_claims,
    COALESCE(oc.reports_bought, 0) AS reports_bought,
    oc.total_spend_cents,
    oc.last_order_currency
FROM users u
LEFT JOIN (
    SELECT user_id,
           SUM(CASE WHEN status = 'verified' THEN 1 ELSE 0 END) AS boats_claimed,
           SUM(CASE WHEN status = 'pending'  THEN 1 ELSE 0 END) AS pending_claims
    FROM boat_claims GROUP BY user_id
) bc ON bc.user_id = u.id
LEFT JOIN (
    SELECT user_id,
           SUM(CASE WHEN status IN ('paid','generated') THEN 1 ELSE 0 END) AS reports_bought,
           SUM(CASE WHEN status IN ('paid','generated') THEN amount_cents END) AS total_spend_cents,
           MAX(CASE WHEN status IN ('paid','generated') THEN currency END) AS last_order_currency
    FROM orders GROUP BY user_id
) oc ON oc.user_id = u.id;
"""


def _seed(engine) -> dict:
    """Seed: 1 boat, 4 users, 5 claims, 47 orders (37 abandoned)."""
    ids = {"users": [], "claims": [], "orders": []}
    with engine.begin() as conn:
        for stmt in SCHEMA_SQL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
        conn.execute(
            text(
                "INSERT INTO boats (id, boat_name, sail_number, design, country) "
                "VALUES (1, 'Sun Fish', 'AUS 1', 'Sunfast 3300', 'AUS')"
            )
        )

        users = [
            # (email, name, role, plan, stripe_customer_id)
            ("alice@example.com", "Alice Pro", "customer", "pro", "cus_alice"),
            ("bob@example.com", "Bob Skipper", "customer", "skipper", "cus_bob"),
            ("carol@example.com", "Carol Free", "customer", "free", None),
            ("dave@example.com", "Dave Admin", "admin", "pro", "cus_dave"),
        ]
        for email, name, role, plan, cus in users:
            cur = conn.execute(
                text(
                    "INSERT INTO users (email, full_name, role, plan, "
                    "subscription_status, stripe_customer_id, created_at, last_seen_at) "
                    "VALUES (:email, :name, :role, :plan, 'premium', :cus, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"email": email, "name": name, "role": role,
                 "plan": plan, "cus": cus},
            )
            ids["users"].append(cur.lastrowid)

        alice, bob, carol, dave = ids["users"]

        # Claims: alice has a verified boat + one pending; bob pending; carol rejected.
        claims = [
            (alice, "verified"),
            (alice, "pending"),
            (bob, "pending"),
            (carol, "rejected"),
        ]
        for uid, status in claims:
            cur = conn.execute(
                text(
                    "INSERT INTO boat_claims (user_id, boat_id, status, evidence) "
                    "VALUES (:uid, 1, :status, 'sail number matches')"
                ),
                {"uid": uid, "status": status},
            )
            ids["claims"].append(cur.lastrowid)

        # Orders: alice 5 paid+generated / bob 5 error / 37 abandoned (no session).
        def add_order(user_id, email, status, with_session):
            tok = str(uuid.uuid4())
            cur = conn.execute(
                text(
                    "INSERT INTO orders (order_token, boat_id, email, user_id, "
                    "amount_cents, currency, stripe_session_id, stripe_payment_intent, "
                    "status, search_query) "
                    "VALUES (:tok, 1, :email, :uid, 9900, 'usd', :sess, :pi, :status, 'sun fish')"
                ),
                {
                    "tok": tok,
                    "email": email,
                    "uid": user_id,
                    "sess": f"cs_test_{tok[:8]}" if with_session else None,
                    "pi": f"pi_{tok[:8]}" if with_session else None,
                    "status": status,
                },
            )
            ids["orders"].append(cur.lastrowid)

        for _ in range(3):
            add_order(alice, "alice@example.com", "paid", True)
        for _ in range(2):
            add_order(alice, "alice@example.com", "generated", True)
        for _ in range(5):
            add_order(bob, "bob@example.com", "error", True)
        for _ in range(37):
            add_order(None, None, "pending", False)  # abandoned

    return ids


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ids = _seed(engine)

    app = FastAPI()
    app.include_router(admin_customers.router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: engine

    with patch.object(admin_customers, "ADMIN_PASSWORD", ADMIN_PW):
        yield TestClient(app), ids, engine


# ── Auth ──────────────────────────────────────────────────────────────────


def test_auth_required(client):
    c, _, _ = client
    assert c.get("/v1/admin/users").status_code == 401
    assert c.get("/v1/admin/orders").status_code == 401
    assert c.get("/v1/admin/billing").status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert c.get("/v1/admin/users", headers=bad).status_code == 401


# ── Users list ────────────────────────────────────────────────────────────


def test_users_list_shape(client):
    c, _, _ = client
    r = c.get("/v1/admin/users", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4
    assert body["next_cursor"] is None
    assert len(body["users"]) == 4
    u = next(u for u in body["users"] if u["email"] == "alice@example.com")
    assert u["plan"] == "pro"
    assert u["boats_claimed"] == 1
    assert u["pending_claims"] == 1
    assert u["reports_bought"] == 5
    assert u["total_spend"] == {"amount_cents": 49500, "currency": "usd"}
    assert u["joined_at"]
    assert u["last_seen_at"]
    assert u["stripe_dashboard_url"] == "https://dashboard.stripe.com/customers/cus_alice"


def test_users_filter_plan_role_claims(client):
    c, _, _ = client
    pro = c.get("/v1/admin/users?plan=pro", headers=AUTH).json()
    assert {u["email"] for u in pro["users"]} == {"alice@example.com", "dave@example.com"}

    admins = c.get("/v1/admin/users?role=admin", headers=AUTH).json()
    assert [u["email"] for u in admins["users"]] == ["dave@example.com"]

    pending = c.get("/v1/admin/users?claims=pending", headers=AUTH).json()
    assert {u["email"] for u in pending["users"]} == {
        "alice@example.com",
        "bob@example.com",
    }

    assert c.get("/v1/admin/users?plan=gold", headers=AUTH).status_code == 422
    assert c.get("/v1/admin/users?role=superuser", headers=AUTH).status_code == 422


def test_users_search_q(client):
    c, _, _ = client
    r = c.get("/v1/admin/users?q=alice", headers=AUTH).json()
    assert r["total"] == 1
    assert r["users"][0]["email"] == "alice@example.com"

    # search by claimed boat name
    r = c.get("/v1/admin/users?q=sun+fish", headers=AUTH).json()
    assert {u["email"] for u in r["users"]} >= {"alice@example.com", "bob@example.com"}


def test_users_cursor_pagination(client):
    c, _, _ = client
    page1 = c.get("/v1/admin/users?limit=3&cursor=0", headers=AUTH).json()
    assert len(page1["users"]) == 3
    assert page1["next_cursor"] == 3
    page2 = c.get("/v1/admin/users?limit=3&cursor=3", headers=AUTH).json()
    assert len(page2["users"]) == 1
    assert page2["next_cursor"] is None
    seen = {u["id"] for u in page1["users"]} | {u["id"] for u in page2["users"]}
    assert len(seen) == 4


# ── User detail ───────────────────────────────────────────────────────────


def test_user_detail(client):
    c, ids, _ = client
    alice = ids["users"][0]
    r = c.get(f"/v1/admin/users/{alice}", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == "alice@example.com"
    assert len(body["boats"]) == 2
    assert {b["status"] for b in body["boats"]} == {"verified", "pending"}
    assert len(body["orders"]) == 5
    assert len(body["claims"]) == 2


def test_user_detail_404(client):
    c, _, _ = client
    r = c.get("/v1/admin/users/424242", headers=AUTH)
    assert r.status_code == 404


def test_set_role(client):
    c, ids, engine = client
    bob = ids["users"][1]
    r = c.post(f"/v1/admin/users/{bob}/role", json={"role": "staff"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["role"] == "staff"
    with engine.connect() as conn:
        role = conn.execute(
            text("SELECT role FROM users WHERE id = :id"), {"id": bob}
        ).scalar_one()
    assert role == "staff"

    assert (
        c.post(f"/v1/admin/users/{bob}/role", json={"role": "nope"}, headers=AUTH).status_code
        == 422
    )
    assert (
        c.post("/v1/admin/users/424242/role", json={"role": "staff"}, headers=AUTH).status_code
        == 404
    )


# ── Claims ────────────────────────────────────────────────────────────────


def test_claim_verify_and_reject(client):
    c, ids, engine = client
    pending_claim = ids["claims"][1]  # alice's pending claim
    r = c.post(
        f"/v1/admin/claims/{pending_claim}/verify",
        json={"reviewer": "justin"},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["claim"]["status"] == "verified"
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, verified_by, verified_at FROM boat_claims WHERE id = :id"),
            {"id": pending_claim},
        ).mappings().first()
    assert row["status"] == "verified"
    assert row["verified_by"] == "justin"
    assert row["verified_at"] is not None

    # Second transition must 409 (no longer pending)
    assert (
        c.post(f"/v1/admin/claims/{pending_claim}/reject", json={}, headers=AUTH).status_code
        == 409
    )

    bob_claim = ids["claims"][2]
    r = c.post(
        f"/v1/admin/claims/{bob_claim}/reject",
        json={"reason": "no proof of ownership"},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["claim"]["status"] == "rejected"

    assert c.post("/v1/admin/claims/99999/verify", json={}, headers=AUTH).status_code == 404


# ── Orders ────────────────────────────────────────────────────────────────


def test_orders_all_47_honest_status(client):
    c, _, _ = client
    r = c.get("/v1/admin/orders", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == N_ORDERS
    assert len(body["orders"]) == N_ORDERS
    counts = body["status_counts"]
    assert counts["abandoned"] == 37
    assert counts["paid"] == 3
    assert counts["generated"] == 2
    assert counts["error"] == 5
    abandoned = [o for o in body["orders"] if o["status"] == "abandoned"]
    assert all(o["stripe_session_id"] is None for o in abandoned)
    assert all(o["stored_status"] == "pending" for o in abandoned)


def test_orders_status_filter_and_search(client):
    c, _, _ = client
    r = c.get("/v1/admin/orders?status=abandoned", headers=AUTH).json()
    assert r["total"] == 37
    assert all(o["status"] == "abandoned" for o in r["orders"])

    r = c.get("/v1/admin/orders?q=alice", headers=AUTH).json()
    assert r["total"] == 5

    assert c.get("/v1/admin/orders?status=weird", headers=AUTH).status_code == 422


def test_orders_cursor(client):
    c, _, _ = client
    page1 = c.get("/v1/admin/orders?limit=40", headers=AUTH).json()
    assert len(page1["orders"]) == 40
    assert page1["next_cursor"] == 40
    page2 = c.get("/v1/admin/orders?limit=40&cursor=40", headers=AUTH).json()
    assert len(page2["orders"]) == 7
    assert page2["next_cursor"] is None


def test_order_regenerate(client):
    c, ids, engine = client
    generated_order = ids["orders"][4]  # a 'generated' alice order
    abandoned_order = ids["orders"][-1]

    with patch.object(admin_customers.threading, "Thread") as mock_thread:
        r = c.post(f"/v1/admin/orders/{generated_order}/regenerate", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    mock_thread.assert_called_once()
    mock_thread.return_value.start.assert_called_once()

    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM orders WHERE id = :id"), {"id": generated_order}
        ).scalar_one()
    assert status == "paid"  # reset for regeneration

    # Abandoned orders can never be regenerated
    assert (
        c.post(f"/v1/admin/orders/{abandoned_order}/regenerate", headers=AUTH).status_code
        == 409
    )
    assert c.post("/v1/admin/orders/99999/regenerate", headers=AUTH).status_code == 404


# ── Billing ───────────────────────────────────────────────────────────────


def _fake_stripe_payload():
    pro_monthly = SimpleNamespace(
        id="price_pro_m",
        product="prod_pro",
        currency="gbp",
        unit_amount=2900,
        recurring=SimpleNamespace(interval="month", interval_count=1),
        active=True,
        metadata={},
        lookup_key="pro_monthly_gbp",
    )
    pro_annual = SimpleNamespace(
        id="price_pro_y",
        product="prod_pro",
        currency="gbp",
        unit_amount=29000,
        recurring=SimpleNamespace(interval="year", interval_count=1),
        active=True,
        metadata={},
        lookup_key="pro_annual_gbp",
    )
    report_oneoff = SimpleNamespace(
        id="price_report",
        product="prod_report",
        currency="usd",
        unit_amount=9900,
        recurring=None,
        active=True,
        metadata={"lookup_key": "report_usd"},
        lookup_key="report_usd",
    )
    product_pro = SimpleNamespace(id="prod_pro", name="SailRatings Pro", metadata={})
    product_report = SimpleNamespace(id="prod_report", name="Boat Report", metadata={})
    promo = SimpleNamespace(
        code="LAUNCH20",
        active=True,
        times_redeemed=3,
        expires_at=None,
        coupon=SimpleNamespace(percent_off=20.0, amount_off=None, currency=None),
    )
    charge = SimpleNamespace(
        id="ch_123",
        amount=29000,
        currency="gbp",
        status="succeeded",
        paid=True,
        refunded=False,
        description="Subscription",
        receipt_email="alice@example.com",
        customer="cus_alice",
        created=1756900000,
    )
    balance = {"available": [{"amount": 123456, "currency": "gbp"}], "pending": []}
    return {
        "products": [product_pro, product_report],
        "prices": [pro_monthly, pro_annual, report_oneoff],
        "promo": [promo],
        "balance": balance,
        "charges": [charge],
    }


def test_billing_catalogue_by_lookup_key(client, monkeypatch):
    c, _, _ = client
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    fake = _fake_stripe_payload()

    class _List:
        def __init__(self, items):
            self._items = items

        def auto_paging_iter(self):
            return iter(self._items)

    with (
        patch.object(admin_customers.stripe.Product, "list", return_value=_List(fake["products"])),
        patch.object(admin_customers.stripe.Price, "list", return_value=_List(fake["prices"])),
        patch.object(admin_customers.stripe.PromotionCode, "list", return_value=_List(fake["promo"])),
        patch.object(admin_customers.stripe.Balance, "retrieve", return_value=fake["balance"]),
        patch.object(admin_customers.stripe.Charge, "list", return_value=_List(fake["charges"])),
    ):
        with patch.object(admin_customers, "_billing_cache", {"fetched_at": 0.0, "payload": None}):
            r = c.get("/v1/admin/billing", headers=AUTH)
            assert r.status_code == 200
            body = r.json()
            assert body["configured"] is True
            assert body["cached"] is False

            by_key = {p["lookup_key"]: p for p in body["catalogue"]}
            assert "pro_monthly_gbp" in by_key
            assert "pro_annual_gbp" in by_key
            assert by_key.get("report_usd")["unit_amount"]["amount_cents"] == 9900
            annual = by_key["pro_annual_gbp"]
            assert annual["recurring"]["interval"] == "year"

            assert body["promo_codes"][0]["code"] == "LAUNCH20"
            assert body["promo_codes"][0]["percent_off"] == 20.0
            assert body["balance"]["available"][0]["amount_cents"] == 123456
            assert len(body["last_charges"]) == 1
            assert body["last_charges"][0]["id"] == "ch_123"

            # Second call within the TTL must be served from the cache.
            r2 = c.get("/v1/admin/billing", headers=AUTH)
            assert r2.json()["cached"] is True


def test_billing_not_configured(client, monkeypatch):
    c, _, _ = client
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    r = c.get("/v1/admin/billing", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["catalogue"] == []
