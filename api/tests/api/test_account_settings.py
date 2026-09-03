"""AUTH-01-03: account settings, data export and deletion.

Covers the acceptance criteria's verification hooks:

* **Deletion cascade test** — deleting an account removes personal data
  (settings, notification preferences, boat claims, subscriptions), detaches
  retained financial records (orders) from the identity, and anonymises the
  ``users`` row into a tombstone audit stub. Other users' data is untouched
  and deletion is idempotent.
* **Export completeness test** — the export document contains every row we
  hold on the member: profile, settings + notification preferences, boat
  claims, orders and subscriptions, with schema version + generated-at stamp.

The service SQL is deliberately dialect-neutral (``CURRENT_TIMESTAMP``,
no Postgres-only casts) so the suite runs on in-memory SQLite. Endpoint
tests mount the router on a throwaway FastAPI app with the auth + DB
dependencies overridden, the same pattern as the PAY-01-08 checkout suite.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from irc_data.api.deps import CallerIdentity, get_db, get_optional_identity
from irc_data.api.routers import users as users_router
from irc_data.api.services import account_service


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def engine():
    """In-memory SQLite with the account-schema surface created directly.

    Mirrors the Postgres shape from migrations 0027/0034/0035 with SQLite
    affinities (INTEGER PK users, TEXT booleans-as-ints).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
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
                    role TEXT DEFAULT 'member',
                    plan TEXT DEFAULT 'free',
                    last_seen_at TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    deletion_requested_at TEXT,
                    deletion_completed_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE boats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    boat_name TEXT NOT NULL,
                    sail_number TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE user_settings (
                    user_id INTEGER PRIMARY KEY
                        REFERENCES users(id) ON DELETE CASCADE,
                    display_name TEXT,
                    home_club TEXT,
                    country TEXT,
                    notify_product_updates INTEGER NOT NULL DEFAULT 0,
                    notify_rating_changes INTEGER NOT NULL DEFAULT 0,
                    notify_event_reminders INTEGER NOT NULL DEFAULT 0,
                    notify_marketing INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE boat_claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    boat_id INTEGER NOT NULL REFERENCES boats(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    evidence TEXT,
                    created_at TEXT,
                    verified_at TEXT
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
                    status TEXT DEFAULT 'pending',
                    paid_at TEXT,
                    user_id INTEGER,
                    stripe_customer_id TEXT,
                    created_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                -- Mirrors the canonical 0027 Postgres schema exactly
                -- (lookup_key / price_id / raw, and NO cancel_at column) so
                -- the SQLite double can no longer mask a service query that
                -- references a column the live table does not have.
                CREATE TABLE subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    stripe_subscription_id TEXT,
                    stripe_customer_id TEXT,
                    status TEXT,
                    plan TEXT,
                    lookup_key TEXT,
                    price_id TEXT,
                    current_period_start TEXT,
                    current_period_end TEXT,
                    cancel_at_period_end INTEGER DEFAULT 0,
                    canceled_at TEXT,
                    ended_at TEXT,
                    raw TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
        )
    yield engine
    engine.dispose()


def _insert_user(engine, clerk_id: str, email: str, full_name: str = None) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO users (clerk_id, email, full_name, created_at)"
                " VALUES (:c, :e, :f, CURRENT_TIMESTAMP)"
            ),
            {"c": clerk_id, "e": email, "f": full_name},
        ).lastrowid


def _insert_boat(engine, name: str, sail: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO boats (boat_name, sail_number) VALUES (:n, :s)"),
            {"n": name, "s": sail},
        ).lastrowid


def _insert_claim(engine, user_id: int, boat_id: int, status: str = "verified") -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO boat_claims (user_id, boat_id, status, created_at)"
                " VALUES (:u, :b, :s, CURRENT_TIMESTAMP)"
            ),
            {"u": user_id, "b": boat_id, "s": status},
        ).lastrowid


def _insert_order(engine, user_id: int, token: str, email: str, amount: int) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO orders (order_token, email, amount_cents, currency,
                                    status, user_id, stripe_customer_id, created_at)
                VALUES (:t, :e, :a, 'gbp', 'paid', :u, 'cus_test', CURRENT_TIMESTAMP)
                """
            ),
            {"t": token, "e": email, "a": amount, "u": user_id},
        ).lastrowid


def _insert_subscription(engine, user_id: int, sub_id: str, plan: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO subscriptions (user_id, stripe_subscription_id,
                                           stripe_customer_id, plan, status,
                                           created_at)
                VALUES (:u, :s, 'cus_test', :p, 'active', CURRENT_TIMESTAMP)
                """
            ),
            {"u": user_id, "s": sub_id, "p": plan},
        ).lastrowid


def _count(engine, sql: str, params: dict) -> int:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def _member(engine):
    """A fully-populated member: settings, claim, order, subscription."""
    uid = _insert_user(engine, "user_member", "member@example.com", "Sam Sailor")
    boat = _insert_boat(engine, "Mild Peril", "GBR1234")
    _insert_claim(engine, uid, boat)
    _insert_order(engine, uid, "tok-m1", "member@example.com", 4900)
    _insert_subscription(engine, uid, "sub_1", "skipper")
    with engine.begin() as conn:
        account_service.update_profile(
            conn, "user_member",
            display_name="Sammy", home_club="Royal Solent YC", country="GBR",
        )
        account_service.update_notifications(
            conn, "user_member",
            {"notify_product_updates": True, "notify_marketing": True},
        )
    return uid, boat


# ── Settings ─────────────────────────────────────────────────────────────


class TestSettings:
    def test_settings_row_created_with_privacy_first_defaults(self, engine):
        uid = _insert_user(engine, "user_new", "new@example.com")
        with engine.begin() as conn:
            settings = account_service.get_settings(conn, "user_new")
        assert settings["user_id"] == uid
        # Every non-essential notification defaults OFF (privacy policy:
        # "only essential transactional email unless you opt in").
        for field in account_service.NOTIFICATION_FIELDS:
            assert settings[field] is False, field
        assert settings["display_name"] is None

    def test_update_profile_full_name_on_users_row(self, engine):
        uid = _insert_user(engine, "user_p", "p@example.com")
        with engine.begin() as conn:
            result = account_service.update_profile(
                conn, "user_p",
                full_name="Pat Sailor", home_club="  Hamble SC  ",
            )
        assert result["user"]["full_name"] == "Pat Sailor"
        assert result["settings"]["home_club"] == "Hamble SC"
        with engine.connect() as conn:
            name = conn.execute(
                text("SELECT full_name FROM users WHERE id = :i"), {"i": uid}
            ).scalar()
        assert name == "Pat Sailor"

    def test_update_notifications_partial_and_allowlisted(self, engine):
        _insert_user(engine, "user_n", "n@example.com")
        with engine.begin() as conn:
            settings = account_service.update_notifications(
                conn, "user_n",
                {"notify_marketing": True, "not_a_real_field": True},
            )
        assert settings["notify_marketing"] is True
        assert settings["notify_product_updates"] is False
        assert "not_a_real_field" not in settings


# ── Export completeness ──────────────────────────────────────────────────


class TestExportCompleteness:
    def test_export_contains_every_held_row(self, engine):
        uid, boat = _member(engine)
        with engine.begin() as conn:
            export = account_service.build_account_export(conn, "user_member")

        # Envelope
        assert export["schema_version"] == account_service.EXPORT_SCHEMA_VERSION
        assert export["generated_at"]

        # Profile (identity row incl. audit timestamps)
        profile = export["profile"]
        assert profile["email"] == "member@example.com"
        assert profile["full_name"] == "Sam Sailor"
        assert profile["clerk_id"] == "user_member"
        assert profile["stripe_customer_id"] is None
        assert profile["created_at"]

        # Settings + notification preferences
        assert export["settings"] == {
            "display_name": "Sammy",
            "home_club": "Royal Solent YC",
            "country": "GBR",
        }
        prefs = export["notification_preferences"]
        assert prefs["notify_product_updates"] is True
        assert prefs["notify_marketing"] is True
        assert prefs["notify_rating_changes"] is False
        assert set(prefs) == set(account_service.NOTIFICATION_FIELDS)

        # Boats (claim joined to boat name/sail)
        assert len(export["boats"]) == 1
        claim = export["boats"][0]
        assert claim["boat_id"] == boat
        assert claim["boat_name"] == "Mild Peril"
        assert claim["sail_number"] == "GBR1234"
        assert claim["status"] == "verified"

        # Orders
        assert len(export["orders"]) == 1
        order = export["orders"][0]
        assert order["order_token"] == "tok-m1"
        assert order["amount_cents"] == 4900
        assert order["email"] == "member@example.com"

        # Subscriptions
        assert len(export["subscriptions"]) == 1
        sub = export["subscriptions"][0]
        assert sub["stripe_subscription_id"] == "sub_1"
        assert sub["plan"] == "skipper"
        assert sub["status"] == "active"

        # Audit block present
        assert export["audit"]["deletion_completed_at"] is None

    def test_export_is_json_serialisable_document(self, engine):
        _member(engine)
        with engine.begin() as conn:
            export = account_service.build_account_export(conn, "user_member")
        body = account_service.export_as_json_bytes(export)
        parsed = json.loads(body.decode("utf-8"))
        assert parsed["profile"]["email"] == "member@example.com"

    def test_export_isolates_members(self, engine):
        _member(engine)
        other = _insert_user(engine, "user_other", "other@example.com")
        boat2 = _insert_boat(engine, "Second Wind", "GBR9999")
        _insert_claim(engine, other, boat2)
        _insert_order(engine, other, "tok-o1", "other@example.com", 9900)

        with engine.begin() as conn:
            export = account_service.build_account_export(conn, "user_member")

        assert {b["sail_number"] for b in export["boats"]} == {"GBR1234"}
        assert {o["order_token"] for o in export["orders"]} == {"tok-m1"}


# ── Deletion cascade ─────────────────────────────────────────────────────


class TestDeletionCascade:
    def test_cascade_removes_personal_data_and_anonymises_identity(self, engine):
        uid, boat = _member(engine)

        with engine.begin() as conn:
            summary = account_service.delete_account(
                conn, "user_member", reason="closing account"
            )

        assert summary["deleted"] is True
        assert summary["already_deleted"] is False
        cascade = summary["cascade"]
        assert cascade["user_settings_deleted"] == 1
        assert cascade["boat_claims_deleted"] == 1
        assert cascade["subscriptions_deleted"] == 1
        assert cascade["orders_detached"] == 1
        assert cascade["identity_anonymised"] is True

        with engine.connect() as conn:
            # Personal data tables empty for this user.
            assert conn.execute(
                text("SELECT COUNT(*) FROM user_settings WHERE user_id = :u"),
                {"u": uid},
            ).scalar() == 0
            assert conn.execute(
                text("SELECT COUNT(*) FROM boat_claims WHERE user_id = :u"),
                {"u": uid},
            ).scalar() == 0
            assert conn.execute(
                text("SELECT COUNT(*) FROM subscriptions WHERE user_id = :u"),
                {"u": uid},
            ).scalar() == 0

            # Orders kept (financial retention) but fully detached of PII.
            order = conn.execute(
                text(
                    "SELECT user_id, email, stripe_customer_id FROM orders"
                    " WHERE order_token = 'tok-m1'"
                )
            ).mappings().first()
            assert order["user_id"] is None
            assert order["email"] is None
            assert order["stripe_customer_id"] is None

            # Identity row survives only as an anonymised audit stub.
            # clerk_id is kept so deletion stays idempotent and a lingering
            # Clerk session gets 410 Gone instead of a silent resurrection.
            user = conn.execute(
                text("SELECT * FROM users WHERE id = :u"), {"u": uid}
            ).mappings().first()
            assert user is not None  # referential integrity preserved
            assert user["email"].endswith("@deleted.invalid")
            assert "member@example.com" not in (user["email"] or "")
            assert user["full_name"] is None
            assert user["stripe_customer_id"] is None
            assert user["clerk_id"] == "user_member"
            assert user["deletion_requested_at"] is not None
            assert user["deletion_completed_at"] is not None

            # The boat itself is catalogue data — it must NOT be deleted.
            assert conn.execute(
                text("SELECT COUNT(*) FROM boats WHERE id = :b"), {"b": boat}
            ).scalar() == 1

    def test_cascade_leaves_other_members_untouched(self, engine):
        uid, _ = _member(engine)
        other = _insert_user(engine, "user_other", "other@example.com")
        boat2 = _insert_boat(engine, "Second Wind", "GBR9999")
        _insert_claim(engine, other, boat2)
        _insert_order(engine, other, "tok-o1", "other@example.com", 9900)
        _insert_subscription(engine, other, "sub_2", "programme")

        with engine.begin() as conn:
            account_service.delete_account(conn, "user_member")

        with engine.connect() as conn:
            other_user = conn.execute(
                text("SELECT email, full_name FROM users WHERE id = :u"),
                {"u": other},
            ).mappings().first()
            assert other_user["email"] == "other@example.com"
            assert conn.execute(
                text("SELECT COUNT(*) FROM boat_claims WHERE user_id = :u"),
                {"u": other},
            ).scalar() == 1
            assert conn.execute(
                text(
                    "SELECT email FROM orders WHERE order_token = 'tok-o1'"
                )
            ).scalar() == "other@example.com"
            assert conn.execute(
                text("SELECT COUNT(*) FROM subscriptions WHERE user_id = :u"),
                {"u": other},
            ).scalar() == 1

    def test_deletion_is_idempotent(self, engine):
        uid, _ = _member(engine)
        with engine.begin() as conn:
            first = account_service.delete_account(conn, "user_member")
            second = account_service.delete_account(conn, "user_member")
        assert first["already_deleted"] is False
        assert second["deleted"] is True
        assert second["already_deleted"] is True

        # Nothing personal can be exported after deletion.
        with engine.begin() as conn:
            export = account_service.build_account_export(conn, "user_member")
        assert export["profile"]["email"].endswith("@deleted.invalid")
        assert export["boats"] == []
        assert export["orders"] == []


# ── HTTP endpoints ───────────────────────────────────────────────────────


@pytest.fixture()
def client(engine):
    app = FastAPI()
    app.include_router(users_router.router, prefix="/v1")

    identity = CallerIdentity(clerk_user_id="user_member", email="member@example.com")
    app.dependency_overrides[get_db] = lambda: engine
    app.dependency_overrides[get_optional_identity] = lambda: identity
    return TestClient(app)


class TestAccountEndpoints:
    def test_settings_roundtrip(self, client, engine):
        _member(engine)

        res = client.get("/v1/users/me/settings")
        assert res.status_code == 200
        body = res.json()
        assert body["email"] == "member@example.com"
        assert body["display_name"] == "Sammy"
        assert body["notify_marketing"] is True

        res = client.patch(
            "/v1/users/me",
            json={"full_name": "Samuel Sailor", "country": "IRL"},
        )
        assert res.status_code == 200
        assert res.json()["full_name"] == "Samuel Sailor"
        assert res.json()["country"] == "IRL"
        # PATCH semantics: untouched fields are preserved.
        assert res.json()["display_name"] == "Sammy"

        res = client.patch(
            "/v1/users/me/notifications", json={"notify_rating_changes": True}
        )
        assert res.status_code == 200
        assert res.json()["notify_rating_changes"] is True
        assert res.json()["notify_marketing"] is True  # unchanged

    def test_export_endpoint_downloads_complete_document(self, client, engine):
        _member(engine)
        res = client.get("/v1/users/me/export")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("application/json")
        assert "attachment" in res.headers["content-disposition"]
        doc = res.json()
        assert doc["profile"]["email"] == "member@example.com"
        assert len(doc["boats"]) == 1
        assert len(doc["orders"]) == 1
        assert len(doc["subscriptions"]) == 1

    def test_delete_requires_confirmation_text(self, client, engine):
        _member(engine)
        res = client.request("DELETE", "/v1/users/me", json={"confirm": "yes"})
        assert res.status_code == 400
        # Nothing deleted.
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT COUNT(*) FROM user_settings")
            ).scalar() == 1

    def test_delete_endpoint_runs_cascade(self, client, engine):
        _member(engine)
        res = client.request(
            "DELETE", "/v1/users/me",
            json={"confirm": "DELETE", "reason": "test"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["deleted"] is True
        assert body["cascade"]["boat_claims_deleted"] == 1
        assert body["cascade"]["orders_detached"] == 1

        with engine.connect() as conn:
            email = conn.execute(
                text("SELECT email FROM users WHERE clerk_id = 'user_member'")
            ).scalar()
        assert email.endswith("@deleted.invalid")

    def test_deleted_account_gets_410_not_resurrection(self, client, engine):
        """A still-valid Clerk session after deletion must not recreate."""
        _member(engine)
        res = client.request("DELETE", "/v1/users/me", json={"confirm": "DELETE"})
        assert res.status_code == 200

        assert client.get("/v1/users/me/settings").status_code == 410
        assert client.get("/v1/users/me/export").status_code == 410
        assert (
            client.request("DELETE", "/v1/users/me", json={"confirm": "DELETE"})
            .status_code
            == 410
        )
        # And no new row was created by the attempts.
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT COUNT(*) FROM users WHERE clerk_id = 'user_member'")
            ).scalar() == 1

    def test_endpoints_require_auth(self, engine):
        app = FastAPI()
        app.include_router(users_router.router, prefix="/v1")
        app.dependency_overrides[get_db] = lambda: engine
        app.dependency_overrides[get_optional_identity] = lambda: None
        anon = TestClient(app)

        assert anon.get("/v1/users/me/settings").status_code == 401
        assert anon.patch("/v1/users/me", json={}).status_code == 401
        assert anon.get("/v1/users/me/export").status_code == 401
        assert (
            anon.request("DELETE", "/v1/users/me", json={"confirm": "DELETE"})
            .status_code
            == 401
        )
