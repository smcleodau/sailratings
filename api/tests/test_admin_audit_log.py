from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from irc_data.api.app import app
from irc_data.api.audit import log_admin_action
from irc_data.api.deps import get_db

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield eng
    eng.dispose()

@pytest.fixture()
def client(engine, monkeypatch):
    from fastapi.testclient import TestClient

    from irc_data.api import app as app_module
    from irc_data.api.deps import get_db
    from irc_data.api.routers import admin as admin_module

    monkeypatch.setattr(admin_module, "ADMIN_PASSWORD", "local")

    app_module.app.dependency_overrides[get_db] = lambda: engine
    
    with TestClient(app_module.app) as c:
        yield c

@pytest.fixture(autouse=True)
def setup_audit_data(engine):
    # Ensure some data exists
    log_admin_action(engine, who="admin1", action="CREATE", entity="boats", pk="100", before=None, after={"name": "Boat 1"})
    log_admin_action(engine, who="admin2", action="UPDATE", entity="designs", pk="50", before={"draft": 1.0}, after={"draft": 1.5})
    log_admin_action(engine, who="admin1", action="DELETE", entity="events", pk="200", before={"status": "active"}, after=None)
    log_admin_action(engine, who="admin3", action="CREATE", entity="boats", pk="101", before=None, after={"name": "Boat 2", "field": "some json text inside"})
    
    yield

def test_unauthorized(client):
    res = client.get("/v1/admin/audit-log")
    assert res.status_code == 401
    
def test_empty_filters_returns_le_limit(client):
    res = client.get("/v1/admin/audit-log?limit=2", headers={"Authorization": "Bearer local"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) <= 2
    assert "next_cursor" in data

def test_actor_filter(client):
    res = client.get("/v1/admin/audit-log?actor=admin1", headers={"Authorization": "Bearer local"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 2
    assert all(item["actor"] == "admin1" for item in data["items"])

def test_table_filter(client):
    res = client.get("/v1/admin/audit-log?table=designs", headers={"Authorization": "Bearer local"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["table"] == "designs"

def test_since_until_window(client):
    # Get one of the items to find its time
    res = client.get("/v1/admin/audit-log", headers={"Authorization": "Bearer local"})
    created_at = res.json()["items"][0]["created_at"]
    
    # query since this exact time
    res = client.get(f"/v1/admin/audit-log?since={created_at}&until={created_at}", headers={"Authorization": "Bearer local"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) >= 1

def test_q_matches_json_text(client):
    res = client.get("/v1/admin/audit-log?q=json text inside", headers={"Authorization": "Bearer local"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["actor"] == "admin3"

def test_cursor_pagination_returns_disjoint_pages(client):
    res1 = client.get("/v1/admin/audit-log?limit=2", headers={"Authorization": "Bearer local"})
    data1 = res1.json()
    assert len(data1["items"]) == 2
    cursor = data1["next_cursor"]
    assert cursor is not None
    
    res2 = client.get(f"/v1/admin/audit-log?limit=2&cursor={cursor}", headers={"Authorization": "Bearer local"})
    data2 = res2.json()
    
    ids1 = {item["id"] for item in data1["items"]}
    ids2 = {item["id"] for item in data2["items"]}
    assert ids1.isdisjoint(ids2)

def test_export_content_type_csv(client):
    res = client.get("/v1/admin/audit-log/export", headers={"Authorization": "Bearer local"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "text/csv; charset=utf-8"
    assert res.text.replace('\r\n', '\n').startswith("id,created_at,actor,action,table,pk,before,after,source\n")
