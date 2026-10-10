"""AUTH-01-01: Clerk -> Postgres webhook sync + ``irc-data sync-clerk-users``.

Runs against in-memory SQLite (all SQL in the handler / service is
dialect-neutral) with real svix signatures.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from svix.webhooks import Webhook

from irc_data.api.deps import get_db
from irc_data.api.routers import webhooks_clerk

SECRET = "whsec_" + base64.b64encode(b"unit-test-secret-key-0123456789").decode()


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clerk_id TEXT UNIQUE,
                email TEXT UNIQUE,
                full_name TEXT,
                last_seen_at TEXT,
                deleted_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE clerk_events (
                id TEXT PRIMARY KEY,
                type TEXT,
                received_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
    yield eng
    eng.dispose()


@pytest.fixture()
def client(engine, monkeypatch):
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", SECRET)
    app = FastAPI()
    app.include_router(webhooks_clerk.router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: engine
    return TestClient(app)


def _post(client, event: dict, msg_id="msg_1", secret=SECRET):
    body = json.dumps(event)
    ts = datetime.now(timezone.utc)
    sig = Webhook(secret).sign(msg_id, ts, body)
    return client.post(
        "/v1/webhooks/clerk",
        content=body,
        headers={
            "svix-id": msg_id,
            "svix-timestamp": str(int(ts.timestamp())),
            "svix-signature": sig,
            "content-type": "application/json",
        },
    )


def _clerk_user(clerk_id="user_1", email="a@example.com", first="Ada", last="Lovelace"):
    return {
        "id": clerk_id,
        "first_name": first,
        "last_name": last,
        "primary_email_address_id": "idn_primary",
        "email_addresses": [
            {"id": "idn_other", "email_address": "other@example.com"},
            {"id": "idn_primary", "email_address": email},
        ],
    }


def _row(engine, clerk_id="user_1"):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT * FROM users WHERE clerk_id = :c"), {"c": clerk_id}
        ).mappings().first()


def test_bad_signature_returns_400(client, engine):
    body = json.dumps({"type": "user.created", "data": _clerk_user()})
    resp = client.post(
        "/v1/webhooks/clerk",
        content=body,
        headers={
            "svix-id": "msg_bad",
            "svix-timestamp": str(int(datetime.now(timezone.utc).timestamp())),
            "svix-signature": "v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        },
    )
    assert resp.status_code == 400
    assert _row(engine) is None


def test_user_created_inserts_primary_email_and_full_name(client, engine):
    resp = _post(client, {"type": "user.created", "data": _clerk_user()})
    assert resp.status_code == 200
    row = _row(engine)
    assert row["email"] == "a@example.com"
    assert row["full_name"] == "Ada Lovelace"


def test_user_updated_updates_email_and_full_name(client, engine):
    _post(client, {"type": "user.created", "data": _clerk_user()}, "msg_1")
    resp = _post(
        client,
        {"type": "user.updated",
         "data": _clerk_user(email="new@example.com", first="Ada", last="King")},
        "msg_2",
    )
    assert resp.status_code == 200
    row = _row(engine)
    assert row["email"] == "new@example.com"
    assert row["full_name"] == "Ada King"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1


def test_user_deleted_sets_deleted_at(client, engine):
    _post(client, {"type": "user.created", "data": _clerk_user()}, "msg_1")
    assert _row(engine)["deleted_at"] is None
    resp = _post(
        client,
        {"type": "user.deleted", "data": {"id": "user_1", "deleted": True}},
        "msg_2",
    )
    assert resp.status_code == 200
    assert _row(engine)["deleted_at"] is not None


def test_session_created_touches_last_seen_at(client, engine):
    _post(client, {"type": "user.created", "data": _clerk_user()}, "msg_1")
    assert _row(engine)["last_seen_at"] is None
    resp = _post(
        client,
        {"type": "session.created", "data": {"id": "sess_1", "user_id": "user_1"}},
        "msg_2",
    )
    assert resp.status_code == 200
    assert _row(engine)["last_seen_at"] is not None


def test_duplicate_svix_id_is_a_noop(client, engine):
    event = {"type": "user.created", "data": _clerk_user()}
    assert _post(client, event, "msg_dup").status_code == 200
    # Redelivery of the same svix-id with changed content must not apply.
    changed = {"type": "user.created", "data": _clerk_user(first="Changed")}
    resp = _post(client, changed, "msg_dup")
    assert resp.status_code == 200
    assert resp.json().get("replay") is True
    assert _row(engine)["full_name"] == "Ada Lovelace"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM clerk_events")).scalar() == 1


def test_unknown_event_type_is_acknowledged(client, engine):
    resp = _post(client, {"type": "organization.created", "data": {"id": "org_1"}})
    assert resp.status_code == 200
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 0


def test_missing_secret_returns_503(client, monkeypatch):
    monkeypatch.delenv("CLERK_WEBHOOK_SECRET", raising=False)
    resp = client.post("/v1/webhooks/clerk", content="{}")
    assert resp.status_code == 503


def test_sync_clerk_users_upserts_mocked_three_user_page(engine, monkeypatch):
    from irc_data import cli as cli_mod

    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_dummy")
    # user_2 already exists -> counted as updated; user_1/user_3 are created.
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (clerk_id, email) VALUES ('user_2', 'old@example.com')")
        )
    page = [
        _clerk_user("user_1", "one@example.com", "One", "A"),
        _clerk_user("user_2", "two@example.com", "Two", "B"),
        _clerk_user("user_3", "three@example.com", "Three", "C"),
    ]
    with patch.object(cli_mod, "get_engine", return_value=engine), patch.object(
        cli_mod, "_fetch_clerk_users_page", side_effect=[page]
    ) as fetch:
        result = CliRunner().invoke(cli_mod.cli, ["sync-clerk-users"])

    assert result.exit_code == 0, result.output
    assert "created 2" in result.output and "updated 1" in result.output
    assert fetch.call_count == 1
    assert _row(engine, "user_1")["email"] == "one@example.com"
    assert _row(engine, "user_2")["email"] == "two@example.com"
    assert _row(engine, "user_2")["full_name"] == "Two B"
    assert _row(engine, "user_3")["full_name"] == "Three C"
