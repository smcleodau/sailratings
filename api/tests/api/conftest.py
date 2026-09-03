"""SQLite-backed fixtures for checkout/user-linkage unit tests (PAY-01-08).

PostgreSQL ``ON CONFLICT`` syntax in ``users_service.get_or_create_user``
requires a real Postgres, so those paths are covered with mocks; all
link/claim SQL here is dialect-neutral and runs against in-memory SQLite.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def sqlite_engine():
    # StaticPool keeps one shared in-memory database across connections.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        # Canonical users shape per the PAY-01-07/09 schema (migration 0032).
        conn.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clerk_id TEXT UNIQUE,
                    email TEXT UNIQUE,
                    full_name TEXT,
                    subscription_status TEXT DEFAULT 'none',
                    stripe_customer_id TEXT UNIQUE,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_token TEXT,
                    boat_id INTEGER,
                    email TEXT,
                    amount_cents INTEGER,
                    currency TEXT,
                    stripe_session_id TEXT,
                    stripe_payment_intent TEXT,
                    status TEXT DEFAULT 'pending',
                    paid_at TEXT,
                    user_id INTEGER,
                    stripe_customer_id TEXT
                )
                """
            )
        )
    yield engine
    engine.dispose()


def insert_user(engine, clerk_id: str, email=None, customer=None) -> int:
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "INSERT INTO users (clerk_id, email, stripe_customer_id) "
                "VALUES (:c, :e, :s)"
            ),
            {"c": clerk_id, "e": email, "s": customer},
        )
        return res.lastrowid


def insert_order(engine, token: str, email=None, customer=None, session=None,
                 user_id=None, status="pending") -> int:
    with engine.begin() as conn:
        res = conn.execute(
            text(
                """
                INSERT INTO orders (order_token, email, stripe_customer_id,
                                    stripe_session_id, user_id, status)
                VALUES (:t, :e, :c, :s, :u, :st)
                """
            ),
            {"t": token, "e": email, "c": customer, "s": session,
             "u": user_id, "st": status},
        )
        return res.lastrowid
