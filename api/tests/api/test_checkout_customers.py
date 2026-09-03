"""Unit tests for PAY-01-08: one Stripe customer per user + order linking.

Covers the acceptance criteria:

* Repeat purchases by one signed-in user reuse exactly one Stripe customer
  (``users.stripe_customer_id`` cached, passed as ``customer=``).
* A guest checkout uses ``customer_creation=always``; the resulting
  customer/order is linked to the user on their next sign-in (via the
  ``checkout.session.completed`` webhook matching ``stripe_customer_id``
  first, else email).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import stripe
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from irc_data.api.deps import get_db, get_optional_identity
from irc_data.api.routers import checkout as checkout_router
from irc_data.api.services.users_service import (
    ensure_stripe_customer,
    get_or_create_user,
    link_checkout_customer_to_user,
)

from .conftest import insert_order, insert_user


# ── users_service.get_or_create_user ──────────────────────────────────────


class TestGetOrCreateUser:
    def test_creates_user_via_postgres_upsert(self, sqlite_engine):
        """New clerk identity → INSERT ... ON CONFLICT issued, user returned."""
        conn = MagicMock(name="conn")
        row = {"id": 5, "clerk_id": "user_new",
               "email": "sailor@example.com", "stripe_customer_id": None}
        no_user = MagicMock()
        no_user.mappings.return_value.first.return_value = None
        email_free = MagicMock()
        email_free.first.return_value = None  # email not taken
        insert_result = MagicMock()
        insert_result.mappings.return_value.first.return_value = row
        no_other = MagicMock()
        no_other.mappings.return_value.first.return_value = None
        claimed_result = MagicMock(rowcount=0)
        # 1) SELECT by clerk_id → none; 2) email-taken probe → free;
        # 3) INSERT ... RETURNING → row; 4) adopt-customer SELECT → none;
        # 5) claim guest orders UPDATE.
        conn.execute.side_effect = [
            no_user, email_free, insert_result, no_other, claimed_result,
        ]

        user = get_or_create_user(conn, "user_new", "sailor@example.com")

        assert user == row
        insert_call = conn.execute.call_args_list[2]
        sql_text = insert_call.args[0].text
        assert "INSERT INTO users" in sql_text
        assert "ON CONFLICT (clerk_id) DO NOTHING" in sql_text
        assert insert_call.args[1] == {
            "clerk_id": "user_new", "email": "sailor@example.com"
        }

    def test_existing_user_is_returned_without_insert(self, sqlite_engine):
        """Known clerk identity → single SELECT, no INSERT issued."""
        conn = MagicMock(name="conn")
        row = {"id": 5, "clerk_id": "user_1", "email": "a@b.com",
               "stripe_customer_id": "cus_1"}
        select_result = MagicMock()
        select_result.mappings.return_value.first.return_value = row
        claimed_result = MagicMock(rowcount=0)
        conn.execute.side_effect = [select_result, claimed_result]

        user = get_or_create_user(conn, "user_1", "a@b.com")

        assert user == row
        assert conn.execute.call_count == 2  # SELECT + claim UPDATE only
        first_sql = conn.execute.call_args_list[0].args[0].text
        assert "FROM users WHERE clerk_id" in first_sql

    def test_email_claiming_sql_matches_guest_orders(self, sqlite_engine):
        """Guest orders with the same email (any case) get user_id set."""
        uid = insert_user(sqlite_engine, "user_1", email="sailor@example.com")
        insert_order(sqlite_engine, "tok-g1", email="Sailor@Example.com")
        insert_order(sqlite_engine, "tok-g2", email="sailor@example.com")
        insert_order(sqlite_engine, "tok-other", email="other@example.com")

        with sqlite_engine.begin() as conn:
            claimed = conn.execute(
                text(
                    "UPDATE orders SET user_id = :user_id "
                    "WHERE user_id IS NULL AND lower(email) = :email"
                ),
                {"user_id": uid, "email": "sailor@example.com"},
            )
            assert claimed.rowcount == 2

        with sqlite_engine.connect() as conn:
            linked = conn.execute(
                text("SELECT COUNT(*) FROM orders WHERE user_id = :u"),
                {"u": uid},
            ).scalar()
            assert linked == 2
            other = conn.execute(
                text("SELECT user_id FROM orders WHERE order_token = 'tok-other'")
            ).scalar()
            assert other is None

    def test_customer_adoption_sql_prefers_matching_email(self, sqlite_engine):
        """A customer created for the email on another row is adopted.

        With ``users.email`` UNIQUE, the signing-in user's row is created
        with ``email = NULL`` (the donor already owns the email); the adopt
        SELECT matches the donor by email and the customer transfers over,
        clearing the donor first so the ``stripe_customer_id`` UNIQUE
        constraint holds.
        """
        insert_user(sqlite_engine, "user_guest", email="a@b.com", customer="cus_guest")

        with sqlite_engine.begin() as conn:
            # Email is taken by the donor row → new row starts with NULL.
            uid = conn.execute(
                text(
                    "INSERT INTO users (clerk_id, email) "
                    "VALUES ('user_real', NULL)"
                )
            ).lastrowid

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
                {"email": "a@b.com", "clerk_id": "user_real"},
            ).mappings().first()
            assert other is not None
            # Transfer mirrors users_service: clear donor first so the
            # UNIQUE constraint on stripe_customer_id holds.
            conn.execute(
                text("UPDATE users SET stripe_customer_id = NULL WHERE id = :id"),
                {"id": other["id"]},
            )
            conn.execute(
                text("UPDATE users SET stripe_customer_id = :customer WHERE id = :id"),
                {"customer": other["stripe_customer_id"], "id": uid},
            )

        with sqlite_engine.connect() as conn:
            customer = conn.execute(
                text(
                    "SELECT stripe_customer_id FROM users WHERE clerk_id = 'user_real'"
                )
            ).scalar()
            assert customer == "cus_guest"
            donor = conn.execute(
                text(
                    "SELECT stripe_customer_id FROM users WHERE clerk_id = 'user_guest'"
                )
            ).scalar()
            assert donor is None


# ── users_service.ensure_stripe_customer ──────────────────────────────────


class TestEnsureStripeCustomer:
    def test_returns_cached_customer_without_stripe_call(self, sqlite_engine):
        uid = insert_user(sqlite_engine, "user_1", email="a@b.com", customer="cus_1")
        with sqlite_engine.begin() as conn:
            user = {"id": uid, "clerk_id": "user_1",
                    "email": "a@b.com", "stripe_customer_id": "cus_1"}
            with patch("irc_data.api.services.users_service.stripe") as mock_stripe:
                mock_stripe.api_key = "sk_test_x"
                result = ensure_stripe_customer(conn, user)
        assert result == "cus_1"
        mock_stripe.Customer.create.assert_not_called()

    def test_creates_customer_with_email_and_clerk_metadata(self, sqlite_engine):
        uid = insert_user(sqlite_engine, "user_1", email="a@b.com")
        with sqlite_engine.begin() as conn:
            user = {"id": uid, "clerk_id": "user_1",
                    "email": "a@b.com", "stripe_customer_id": None}
            with patch("irc_data.api.services.users_service.stripe") as mock_stripe:
                mock_stripe.api_key = "sk_test_x"
                mock_stripe.Customer.create.return_value = SimpleNamespace(id="cus_new")
                result = ensure_stripe_customer(conn, user, email="A@B.com")
            mock_stripe.Customer.create.assert_called_once_with(
                email="a@b.com", metadata={"clerk_id": "user_1"}
            )
        assert result == "cus_new"
        with sqlite_engine.connect() as conn:
            stored = conn.execute(
                text("SELECT stripe_customer_id FROM users WHERE id = :id"),
                {"id": uid},
            ).scalar()
            assert stored == "cus_new"

    def test_returns_none_without_api_key(self, sqlite_engine):
        uid = insert_user(sqlite_engine, "user_1", email="a@b.com")
        with sqlite_engine.begin() as conn:
            user = {"id": uid, "clerk_id": "user_1",
                    "email": "a@b.com", "stripe_customer_id": None}
            with patch("irc_data.api.services.users_service.stripe") as mock_stripe:
                mock_stripe.api_key = None
                result = ensure_stripe_customer(conn, user)
        assert result is None

    def test_repeat_calls_create_exactly_one_customer(self, sqlite_engine):
        """Acceptance: repeat purchases by one user → exactly one customer."""
        uid = insert_user(sqlite_engine, "user_1", email="a@b.com")
        with patch("irc_data.api.services.users_service.stripe") as mock_stripe:
            mock_stripe.api_key = "sk_test_x"
            mock_stripe.Customer.create.return_value = SimpleNamespace(id="cus_solo")

            with sqlite_engine.begin() as conn:
                user = {"id": uid, "clerk_id": "user_1",
                        "email": "a@b.com", "stripe_customer_id": None}
                first = ensure_stripe_customer(conn, user)
            with sqlite_engine.begin() as conn:
                # Second call: user row now carries the customer id.
                row = conn.execute(
                    text(
                        "SELECT id, clerk_id, email, stripe_customer_id "
                        "FROM users WHERE id = :id"
                    ),
                    {"id": uid},
                ).mappings().first()
                second = ensure_stripe_customer(conn, dict(row))

        assert first == second == "cus_solo"
        mock_stripe.Customer.create.assert_called_once()


# ── users_service.link_checkout_customer_to_user ──────────────────────────


class TestLinkCheckoutCustomerToUser:
    def test_links_by_stripe_customer_id(self, sqlite_engine):
        insert_user(sqlite_engine, "user_1", email="a@b.com", customer="cus_1")
        with sqlite_engine.begin() as conn:
            user_id = link_checkout_customer_to_user(conn, "cus_1", "unrelated@x.com")
        assert user_id == 1

    def test_links_by_email_and_backfills_customer(self, sqlite_engine):
        uid = insert_user(sqlite_engine, "user_1", email="sailor@example.com")
        with sqlite_engine.begin() as conn:
            user_id = link_checkout_customer_to_user(
                conn, "cus_guest_9", "  Sailor@Example.COM "
            )
        assert user_id == uid
        with sqlite_engine.connect() as conn:
            customer = conn.execute(
                text("SELECT stripe_customer_id FROM users WHERE id = :id"),
                {"id": uid},
            ).scalar()
            assert customer == "cus_guest_9"

    def test_customer_id_match_wins_over_email(self, sqlite_engine):
        uid1 = insert_user(sqlite_engine, "user_1", email="a@b.com", customer="cus_1")
        insert_user(sqlite_engine, "user_2", email="c@d.com")
        with sqlite_engine.begin() as conn:
            # Email belongs to user_2 but customer belongs to user_1.
            user_id = link_checkout_customer_to_user(conn, "cus_1", "c@d.com")
        assert user_id == uid1

    def test_does_not_overwrite_existing_different_customer(self, sqlite_engine):
        uid = insert_user(sqlite_engine, "user_1", email="a@b.com", customer="cus_orig")
        with sqlite_engine.begin() as conn:
            user_id = link_checkout_customer_to_user(conn, "cus_new", "a@b.com")
        assert user_id == uid
        with sqlite_engine.connect() as conn:
            customer = conn.execute(
                text("SELECT stripe_customer_id FROM users WHERE id = :id"),
                {"id": uid},
            ).scalar()
            assert customer == "cus_orig"

    def test_returns_none_when_no_match(self, sqlite_engine):
        insert_user(sqlite_engine, "user_1", email="a@b.com")
        with sqlite_engine.begin() as conn:
            assert link_checkout_customer_to_user(conn, "cus_x", "nobody@x.com") is None
            assert link_checkout_customer_to_user(conn, None, None) is None


# ── webhook handler ───────────────────────────────────────────────────────


def _make_session_event(**overrides):
    session = {
        "id": "cs_test_123",
        "customer": "cus_guest_1",
        "customer_details": {"email": "guest@example.com"},
        "payment_intent": "pi_123",
        "metadata": {"order_token": "tok-1", "boat_id": "1"},
        "amount_total": 9900,
        "currency": "usd",
    }
    session.update(overrides)
    return session


class TestWebhookLinking:
    def test_guest_checkout_links_on_next_signin(self, sqlite_engine):
        """Guest pays → webhook links by email → orders.user_id set."""
        uid = insert_user(sqlite_engine, "user_42", email="guest@example.com")
        insert_order(
            sqlite_engine, "tok-1",
            email=None, session="cs_test_123", status="pending",
        )
        background = MagicMock()
        with patch(
            "irc_data.api.routers.checkout._generate_and_deliver"
        ) as _:
            checkout_router._handle_checkout_completed(
                sqlite_engine, _make_session_event(), background
            )

        with sqlite_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status, user_id, stripe_customer_id, email "
                    "FROM orders WHERE stripe_session_id = 'cs_test_123'"
                )
            ).mappings().first()
        assert row["status"] == "paid"
        assert row["user_id"] == uid
        assert row["stripe_customer_id"] == "cus_guest_1"
        assert row["email"] == "guest@example.com"

        # The guest-created customer is cached on the user for next time.
        with sqlite_engine.connect() as conn:
            customer = conn.execute(
                text("SELECT stripe_customer_id FROM users WHERE id = :id"),
                {"id": uid},
            ).scalar()
        assert customer == "cus_guest_1"

    def test_repeat_buyer_matches_by_customer_id(self, sqlite_engine):
        uid = insert_user(sqlite_engine, "user_42", email="old@example.com",
                          customer="cus_repeat")
        insert_order(sqlite_engine, "tok-1", session="cs_test_123",
                     status="pending", user_id=uid, customer="cus_repeat")
        background = MagicMock()
        session = _make_session_event(
            customer="cus_repeat",
            customer_details={"email": "whatever@checkout.com"},
        )
        checkout_router._handle_checkout_completed(
            sqlite_engine, session, background
        )
        with sqlite_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT user_id, stripe_customer_id FROM orders "
                    "WHERE stripe_session_id = 'cs_test_123'"
                )
            ).mappings().first()
        assert row["user_id"] == uid
        assert row["stripe_customer_id"] == "cus_repeat"

    def test_no_matching_user_leaves_order_unlinked_but_paid(self, sqlite_engine):
        insert_order(sqlite_engine, "tok-1", session="cs_test_123", status="pending")
        background = MagicMock()
        checkout_router._handle_checkout_completed(
            sqlite_engine, _make_session_event(), background
        )
        with sqlite_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status, user_id, stripe_customer_id FROM orders "
                    "WHERE stripe_session_id = 'cs_test_123'"
                )
            ).mappings().first()
        assert row["status"] == "paid"
        assert row["user_id"] is None
        # Customer id is still recorded for later linking.
        assert row["stripe_customer_id"] == "cus_guest_1"

    def test_already_paid_order_is_not_reprocessed(self, sqlite_engine):
        uid = insert_user(sqlite_engine, "user_42", email="guest@example.com")
        insert_order(sqlite_engine, "tok-1", session="cs_test_123",
                     status="paid", user_id=None)
        background = MagicMock()
        checkout_router._handle_checkout_completed(
            sqlite_engine, _make_session_event(), background
        )
        background.add_task.assert_not_called()
        with sqlite_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT user_id FROM orders WHERE stripe_session_id = 'cs_test_123'"
                )
            ).mappings().first()
        assert row["user_id"] is None


# ── create-session route: customer= vs customer_creation=always ───────────


class TestCreateSessionCustomerParams:
    """The route must pass customer= for signed-in users and
    customer_creation=always for guests (Stripe rejects both together)."""

    def _build_client(self, monkeypatch, identity, engine):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
        app = FastAPI()
        app.include_router(checkout_router.router, prefix="/v1")
        app.dependency_overrides[get_db] = lambda: engine
        app.dependency_overrides[get_optional_identity] = lambda: identity
        return TestClient(app, raise_server_exceptions=False)

    def _engine(self):
        # StaticPool keeps one shared in-memory database across the
        # connections the route opens on separate threads.
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with engine.begin() as conn:
            conn.execute(
                text("CREATE TABLE boats (id INTEGER PRIMARY KEY, boat_name TEXT)")
            )
            conn.execute(
                text("INSERT INTO boats (id, boat_name) VALUES (1, 'Test Boat')")
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE orders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_token TEXT, boat_id INTEGER, email TEXT,
                        amount_cents INTEGER, currency TEXT,
                        stripe_session_id TEXT, stripe_payment_intent TEXT,
                        status TEXT, search_query TEXT, teaser_text TEXT,
                        user_id INTEGER, stripe_customer_id TEXT
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        clerk_id TEXT UNIQUE,
                        email TEXT UNIQUE, full_name TEXT,
                        subscription_status TEXT DEFAULT 'none',
                        stripe_customer_id TEXT UNIQUE,
                        created_at TEXT, updated_at TEXT
                    )
                    """
                )
            )
        return engine

    def test_guest_gets_customer_creation_always(self, monkeypatch):
        engine = self._engine()
        client = self._build_client(monkeypatch, None, engine)

        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="cs_guest", url="https://checkout.stripe.com/x")

        with patch.object(
            checkout_router.stripe.checkout.Session, "create", side_effect=fake_create
        ), patch("irc_data.api.services.analytics_service.track"):
            resp = client.post(
                "/v1/checkout/create-session",
                json={"boat_id": 1, "boat_name": "Test Boat", "currency": "usd"},
            )

        assert resp.status_code == 200, resp.text
        assert captured.get("customer_creation") == "always"
        assert "customer" not in captured

    def test_signed_in_user_gets_customer_param(self, monkeypatch):
        engine = self._engine()
        identity = SimpleNamespace(clerk_user_id="user_1", email="a@b.com")
        client = self._build_client(monkeypatch, identity, engine)

        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="cs_user", url="https://checkout.stripe.com/y")

        with patch.object(
            checkout_router, "get_or_create_user",
            return_value={"id": 7, "clerk_id": "user_1",
                          "email": "a@b.com", "stripe_customer_id": None},
        ) as mock_get_user, patch.object(
            checkout_router, "ensure_stripe_customer", return_value="cus_1"
        ) as mock_ensure, patch.object(
            checkout_router.stripe.checkout.Session, "create", side_effect=fake_create
        ), patch("irc_data.api.services.analytics_service.track"):
            resp = client.post(
                "/v1/checkout/create-session",
                json={"boat_id": 1, "boat_name": "Test Boat", "currency": "usd"},
            )

        assert resp.status_code == 200, resp.text
        mock_get_user.assert_called_once()
        mock_ensure.assert_called_once()
        assert captured.get("customer") == "cus_1"
        assert "customer_creation" not in captured
        assert captured["metadata"]["clerk_user_id"] == "user_1"

        # Order carries user_id + customer at creation time.
        with engine.connect() as conn:
            row = conn.execute(text("SELECT user_id, stripe_customer_id FROM orders")).mappings().first()
        assert row["user_id"] == 7
        assert row["stripe_customer_id"] == "cus_1"

    def test_signed_in_user_falls_back_to_guest_on_failure(self, monkeypatch):
        engine = self._engine()
        identity = SimpleNamespace(clerk_user_id="user_1", email="a@b.com")
        client = self._build_client(monkeypatch, identity, engine)

        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="cs_fb", url="https://checkout.stripe.com/z")

        with patch.object(
            checkout_router, "get_or_create_user", side_effect=RuntimeError("db down")
        ), patch.object(
            checkout_router.stripe.checkout.Session, "create", side_effect=fake_create
        ), patch("irc_data.api.services.analytics_service.track"):
            resp = client.post(
                "/v1/checkout/create-session",
                json={"boat_id": 1, "boat_name": "Test Boat", "currency": "usd"},
            )

        assert resp.status_code == 200, resp.text
        assert captured.get("customer_creation") == "always"
        assert "customer" not in captured
