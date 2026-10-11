"""Contract tests for the corrections workflow (AD-01-09).

propose -> list -> approve (applies value) -> revert (restores before value),
plus reject, and an admin_edits audit row for every transition.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

AUTH = {"Authorization": "Bearer test-secret"}

DDL = """
CREATE TABLE boats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_name TEXT,
    designer TEXT,
    builder TEXT,
    year_built INTEGER,
    design_canonical TEXT
);
CREATE TABLE design_classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name_canonical TEXT UNIQUE
);
CREATE TABLE boat_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_id INTEGER REFERENCES boats(id),
    field_name TEXT NOT NULL,
    current_value TEXT,
    proposed_value TEXT NOT NULL,
    submitted_email TEXT,
    submitted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at TIMESTAMP,
    review_notes TEXT
);
"""


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _connect(dbapi_connection, _record):
        dbapi_connection.create_function("now", 0, lambda: "2026-01-01 00:00:00")

    with eng.begin() as conn:
        for stmt in DDL.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.execute(text(
            "INSERT INTO boats (id, boat_name, designer, builder, year_built, design_canonical) "
            "VALUES (1, 'Testarossa', 'Old Designer', 'Old Builder', 1999, 'Old Class')"
        ))
    return eng


@pytest.fixture()
def client(engine, monkeypatch):
    from irc_data.api import app as app_module
    from irc_data.api.deps import get_db
    from irc_data.api.routers import corrections as corrections_module

    monkeypatch.setattr(corrections_module, "ADMIN_PASSWORD", "test-secret")
    app_module.app.dependency_overrides[get_db] = lambda: engine
    try:
        yield TestClient(app_module.app)
    finally:
        app_module.app.dependency_overrides.pop(get_db, None)


def _propose(client, field="designer", value="New Designer", boat_id=1):
    r = client.post(f"/v1/boats/{boat_id}/corrections", json={"field_name": field, "proposed_value": value})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _boat_value(engine, column="designer"):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT {column} FROM boats WHERE id = 1")).scalar()


def _audit(engine, action, pk):
    """admin_edits rows for a correction transition ([] if none logged yet)."""
    with engine.connect() as conn:
        if not conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='admin_edits'")
        ).first():
            return []
        return conn.execute(
            text(
                "SELECT table_name, pk_value, column_name, old_value, new_value FROM admin_edits "
                "WHERE table_name = 'corrections' AND pk_value = :pk AND column_name = :a"
            ),
            {"pk": str(pk), "a": action},
        ).fetchall()


def test_propose_returns_201(client):
    r = client.post("/v1/boats/1/corrections", json={"field_name": "designer", "proposed_value": "New Designer"})
    assert r.status_code == 201
    assert r.json()["status"] == "pending"


def test_list_requires_admin_token(client):
    assert client.get("/v1/admin/corrections").status_code == 401
    assert client.get("/v1/admin/corrections", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/v1/admin/corrections/1/approve").status_code == 401
    assert client.post("/v1/admin/corrections/1/reject").status_code == 401
    assert client.post("/v1/admin/corrections/1/revert").status_code == 401


def test_list_shows_pending(client):
    cid = _propose(client)
    r = client.get("/v1/admin/corrections", headers=AUTH)
    assert r.status_code == 200
    rows = r.json()
    assert [x["id"] for x in rows] == [cid]
    assert rows[0]["status"] == "pending"
    assert rows[0]["current_value"] == "Old Designer"
    assert rows[0]["proposed_value"] == "New Designer"
    assert rows[0]["boat_name"] == "Testarossa"


def test_approve_applies_value(client, engine):
    cid = _propose(client)
    r = client.post(f"/v1/admin/corrections/{cid}/approve", headers=AUTH, json={"review_notes": "ok"})
    assert r.status_code == 200
    assert r.json()["status"] == "applied"
    assert _boat_value(engine) == "New Designer"
    assert client.get("/v1/admin/corrections?status=applied", headers=AUTH).json()[0]["id"] == cid
    assert client.get("/v1/admin/corrections", headers=AUTH).json() == []


def test_approve_writes_admin_edits(client, engine):
    cid = _propose(client)
    client.post(f"/v1/admin/corrections/{cid}/approve", headers=AUTH)
    rows = _audit(engine, "approve", cid)
    assert len(rows) == 1
    assert json.loads(rows[0].old_value)["value"] == "Old Designer"
    assert json.loads(rows[0].new_value)["value"] == "New Designer"


def test_reject_writes_admin_edits(client, engine):
    cid = _propose(client)
    r = client.post(f"/v1/admin/corrections/{cid}/reject", headers=AUTH, json={"review_notes": "nope"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert _boat_value(engine) == "Old Designer"
    rows = _audit(engine, "reject", cid)
    assert len(rows) == 1
    assert json.loads(rows[0].new_value)["status"] == "rejected"


def test_revert_restores_before_value(client, engine):
    cid = _propose(client)
    client.post(f"/v1/admin/corrections/{cid}/approve", headers=AUTH)
    assert _boat_value(engine) == "New Designer"
    r = client.post(f"/v1/admin/corrections/{cid}/revert", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "reverted"
    assert _boat_value(engine) == "Old Designer"
    reverted = client.get("/v1/admin/corrections?status=reverted", headers=AUTH).json()
    assert [x["id"] for x in reverted] == [cid]


def test_revert_writes_admin_edits_action_revert(client, engine):
    cid = _propose(client)
    client.post(f"/v1/admin/corrections/{cid}/approve", headers=AUTH)
    client.post(f"/v1/admin/corrections/{cid}/revert", headers=AUTH)
    rows = _audit(engine, "revert", cid)
    assert len(rows) == 1
    assert json.loads(rows[0].old_value)["value"] == "New Designer"
    assert json.loads(rows[0].new_value)["value"] == "Old Designer"


def test_revert_of_non_applied_correction_is_409(client, engine):
    cid = _propose(client)
    r = client.post(f"/v1/admin/corrections/{cid}/revert", headers=AUTH)
    assert r.status_code == 409
    assert _boat_value(engine) == "Old Designer"
    assert _audit(engine, "revert", cid) == []
