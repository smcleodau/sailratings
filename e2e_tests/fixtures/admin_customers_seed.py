"""Shared seed fixture for the PAY-01-10 Admin Customers zone E2E rig.

Builds a temporary file-backed SQLite database with the exact shape the
admin-customers Playwright spec asserts against:

    5 users   — alice.waters / bob.north / carol.drift / dave.helm / erin.tide
    6 claims  — alice verified + pending, bob verified, carol pending,
                dave verified, erin pending
   47 orders  — 5 paid (alice) / 5 error (bob) / 37 abandoned (no Stripe
                session ever existed → honest 'abandoned' status)

The schema mirrors the production tables the Customers zone reads
(``users`` / ``boats`` / ``boat_claims`` / ``orders``) plus the
``v_admin_users`` read model from migration 0032.  It is intentionally
self-contained: the E2E API server (``admin_customers_api.py``) only mounts
the ``admin_customers`` router, so no other tables are needed.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

ADMIN_PASSWORD = "sailfast2026"

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

# (email, name, role, plan, stripe_customer_id, joined_at, last_seen_at)
USERS = [
    ("alice.waters@example.com", "Alice Waters", "customer", "pro",
     "cus_demoAlice", "2026-05-06T10:00:00", "2026-09-03T04:42:00"),
    ("bob.north@example.com", "Bob North", "customer", "skipper",
     "cus_demoBob", "2026-06-11T09:30:00", "2026-09-01T18:05:00"),
    ("carol.drift@example.com", "Carol Drift", "customer", "free",
     None, "2026-07-02T14:15:00", "2026-08-28T08:20:00"),
    ("dave.helm@example.com", "Dave Helm", "staff", "pro",
     "cus_demoDave", "2026-04-20T11:45:00", "2026-09-02T21:12:00"),
    ("erin.tide@example.com", "Erin Tide", "customer", "free",
     None, "2026-08-15T16:00:00", None),
]

# (user_email, boat_id, status) — 6 claims total, 3 pending
CLAIMS = [
    ("alice.waters@example.com", 1, "verified"),
    ("alice.waters@example.com", 2, "pending"),
    ("bob.north@example.com", 3, "verified"),
    ("carol.drift@example.com", 4, "pending"),
    ("dave.helm@example.com", 1, "verified"),
    ("erin.tide@example.com", 5, "pending"),
]

BOATS = [
    (1, "Morning Light", "GBR 8314", "J/109", "GBR"),
    (2, "Second Wind", "GBR 7201", "Sunfast 3300", "GBR"),
    (3, "Northerly", "AUS 442", "Sydney 38", "AUS"),
    (4, "Driftwood", "USA 6110", "J/105", "USA"),
    (5, "Tidal Dance", "FRA 339", "Figaro 3", "FRA"),
]


def seed_admin_customers(engine: Engine) -> dict:
    """Create the schema and insert the deterministic verification dataset."""
    with engine.begin() as conn:
        for stmt in SCHEMA_SQL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))

        for boat in BOATS:
            conn.execute(
                text(
                    "INSERT INTO boats (id, boat_name, sail_number, design, country) "
                    "VALUES (:id, :name, :sail, :design, :country)"
                ),
                {"id": boat[0], "name": boat[1], "sail": boat[2],
                 "design": boat[3], "country": boat[4]},
            )

        user_ids: dict[str, int] = {}
        for email, name, role, plan, cus, joined, seen in USERS:
            cur = conn.execute(
                text(
                    "INSERT INTO users (email, full_name, role, plan, "
                    "subscription_status, stripe_customer_id, created_at, last_seen_at) "
                    "VALUES (:email, :name, :role, :plan, :sub, :cus, :joined, :seen)"
                ),
                {
                    "email": email,
                    "name": name,
                    "role": role,
                    "plan": plan,
                    "sub": "premium" if plan != "free" else "none",
                    "cus": cus,
                    "joined": joined,
                    "seen": seen,
                },
            )
            user_ids[email] = int(cur.lastrowid)

        for email, boat_id, status in CLAIMS:
            conn.execute(
                text(
                    "INSERT INTO boat_claims (user_id, boat_id, status, evidence) "
                    "VALUES (:uid, :boat, :status, 'sail number matches club records')"
                ),
                {"uid": user_ids[email], "boat": boat_id, "status": status},
            )

        def add_order(user_id, email, boat_id, status, with_session, amount=9900,
                      currency="usd"):
            tok = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO orders (order_token, boat_id, email, user_id, "
                    "amount_cents, currency, stripe_session_id, stripe_payment_intent, "
                    "status, search_query, paid_at, report_generated_at) "
                    "VALUES (:tok, :boat, :email, :uid, :amount, :currency, "
                    ":sess, :pi, :status, 'morning light', :paid_at, :gen_at)"
                ),
                {
                    "tok": tok,
                    "boat": boat_id,
                    "email": email,
                    "uid": user_id,
                    "amount": amount,
                    "currency": currency,
                    "sess": f"cs_test_{tok[:8]}" if with_session else None,
                    "pi": f"pi_{tok[:8]}" if with_session else None,
                    "status": status,
                    "paid_at": "2026-08-20T12:00:00" if status in ("paid", "generated") else None,
                    "gen_at": "2026-08-20T12:05:00" if status == "generated" else None,
                },
            )

        alice = user_ids["alice.waters@example.com"]
        bob = user_ids["bob.north@example.com"]

        # Alice: 5 reports bought — total spend $455 (3×$99 paid + 2×$79 generated).
        for _ in range(3):
            add_order(alice, "alice.waters@example.com", 1, "paid", True, 9900)
        for _ in range(2):
            add_order(alice, "alice.waters@example.com", 1, "generated", True, 7900)
        # Bob: 5 orders that errored after payment.
        for _ in range(5):
            add_order(bob, "bob.north@example.com", 3, "error", True, 9900)
        # 37 abandoned checkouts — no Stripe session was ever created.
        for i in range(37):
            add_order(None, f"abandoned{i:02d}@example.com", 1, "pending", False, 9900)

    return {"users": user_ids, "n_orders": 47}
