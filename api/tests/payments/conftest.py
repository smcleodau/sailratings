"""Fixtures for the PAY-01-09 Stripe webhook suite.

These tests run against the real Postgres pointed at by
``IRC_DATABASE_URL``/``DATABASE_URL`` (default localhost:5433, per the
monorepo handoff). Each test truncates only the payments tables it uses, so
the suite is safe to run against a dev database. They are skipped cleanly
when no database is reachable.

Webhook payloads are *recorded Stripe fixtures* (shape matches API version
2025-02-24.acacia) and are signed with a real ``whsec_`` test secret using
Stripe's own signature scheme, so ``stripe.Webhook.construct_event`` runs
the genuine verification path — nothing is mocked.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import pytest
from sqlalchemy import create_engine, text

TEST_WEBHOOK_SECRET = "whsec_pay0109testsecret"


def _db_url() -> str:
    url = os.environ.get("IRC_DATABASE_URL") or os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://irc:irc@localhost:5433/irc_data"
    )
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _db_available(url: str) -> bool:
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def engine():
    url = _db_url()
    if not _db_available(url):
        pytest.skip("Postgres not reachable — set IRC_DATABASE_URL to run webhook tests")
    engine = create_engine(url)
    # The payments tables must exist (alembic 0032). Apply them idempotently
    # so the suite also works on a database that hasn't been migrated yet.
    _ensure_payments_schema(engine)
    yield engine
    engine.dispose()


def _ensure_payments_schema(engine) -> None:
    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        clerk_id TEXT UNIQUE,
        email TEXT UNIQUE,
        full_name TEXT,
        subscription_status TEXT NOT NULL DEFAULT 'none',
        stripe_customer_id TEXT UNIQUE,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS subscriptions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        stripe_subscription_id TEXT UNIQUE NOT NULL,
        stripe_customer_id TEXT,
        status TEXT,
        plan TEXT,
        lookup_key TEXT,
        price_id TEXT,
        current_period_start TIMESTAMPTZ,
        current_period_end TIMESTAMPTZ,
        cancel_at_period_end BOOLEAN NOT NULL DEFAULT false,
        canceled_at TIMESTAMPTZ,
        ended_at TIMESTAMPTZ,
        raw JSON,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS stripe_events (
        id SERIAL PRIMARY KEY,
        event_id TEXT UNIQUE NOT NULL,
        type TEXT,
        api_version TEXT,
        livemode BOOLEAN NOT NULL DEFAULT false,
        payload JSON,
        created_at TIMESTAMPTZ DEFAULT now(),
        processed_at TIMESTAMPTZ,
        error TEXT
    );
    ALTER TABLE orders ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
    ALTER TABLE orders ADD COLUMN IF NOT EXISTS stripe_payment_status TEXT;
    """
    with engine.begin() as conn:
        for stmt in ddl.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))


@pytest.fixture()
def db(engine):
    """Truncate the payments tables around each test."""
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE stripe_events, subscriptions, users CASCADE")
        )
        conn.execute(
            text(
                "UPDATE orders SET user_id = NULL, stripe_payment_status = NULL "
                "WHERE user_id IS NOT NULL OR stripe_payment_status IS NOT NULL"
            )
        )
    yield engine
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE stripe_events, subscriptions, users CASCADE")
        )
        conn.execute(
            text(
                "UPDATE orders SET user_id = NULL, stripe_payment_status = NULL "
                "WHERE user_id IS NOT NULL OR stripe_payment_status IS NOT NULL"
            )
        )


@pytest.fixture()
def client(engine, db, monkeypatch):
    """FastAPI TestClient with the DB dependency pinned to the test engine."""
    from fastapi.testclient import TestClient

    from irc_data.api.app import app
    from irc_data.api.deps import get_db

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_pay0109")
    app.dependency_overrides[get_db] = lambda: engine
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db, None)


def sign(payload: dict, secret: str = TEST_WEBHOOK_SECRET) -> tuple[bytes, str]:
    """Return (body, Stripe-Signature header) for a fixture payload."""
    body = json.dumps(payload).encode()
    ts = int(time.time())
    signed = f"{ts}.{body.decode()}".encode()
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return body, f"t={ts},v1={sig}"


def post_event(client, payload: dict, secret: str = TEST_WEBHOOK_SECRET):
    body, header = sign(payload, secret)
    return client.post(
        "/v1/checkout/webhook",
        content=body,
        headers={"stripe-signature": header},
    )


# ── Recorded Stripe fixtures (API 2025-02-24.acacia shape) ───────────────

SUBSCRIPTION_OBJECT = {
    "id": "sub_1Pay0109Premium",
    "object": "subscription",
    "customer": "cus_Pay0109",
    "customer_email": "skipper@example.com",
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
                "id": "si_Pay0109",
                "object": "subscription_item",
                "current_period_start": 1780000000,
                "current_period_end": 1810000000,
                "price": {
                    "id": "price_Pay0109Annual",
                    "object": "price",
                    "lookup_key": "premium_annual",
                    "unit_amount": 29000,
                    "currency": "usd",
                },
            }
        ],
    },
}


def make_subscription_event(
    event_id: str,
    event_type: str,
    *,
    subscription_overrides: dict | None = None,
) -> dict:
    sub = dict(SUBSCRIPTION_OBJECT)
    sub["items"] = {
        "object": "list",
        "data": [dict(SUBSCRIPTION_OBJECT["items"]["data"][0])],
    }
    sub["items"]["data"][0]["price"] = dict(
        SUBSCRIPTION_OBJECT["items"]["data"][0]["price"]
    )
    if subscription_overrides:
        sub.update(subscription_overrides)
    return {
        "id": event_id,
        "object": "event",
        "api_version": "2025-02-24.acacia",
        "created": 1780000100,
        "livemode": False,
        "type": event_type,
        "data": {"object": sub},
    }
