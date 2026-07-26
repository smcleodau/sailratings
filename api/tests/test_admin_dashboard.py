"""Integration tests for the admin dashboard endpoints.

These hit the real FastAPI app over ``TestClient`` against the dev PostgreSQL
database (no mocks), so they exercise the actual SQL the router runs. They
assert on *structure* (status codes, presence of keys, types) rather than
specific data values, which would drift as the live data changes.
"""

from __future__ import annotations

import os

# The admin routers read ADMIN_PASSWORD at import time, so it must be set
# before the app module is imported.
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-secret")

from fastapi.testclient import TestClient  # noqa: E402

from irc_data.api.app import app  # noqa: E402

ADMIN_TOKEN = "test-admin-secret"
AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

client = TestClient(app)

OVERVIEW_KEYS = {"generated_at", "counts", "revenue", "moderation", "ingestion", "alerts"}
COUNT_KEYS = {"boats", "race_results", "matched_results", "irc_certificates",
              "orc_certificates", "tcc_snapshots", "design_classes", "orders"}
REVENUE_KEYS = {"orders_by_status", "revenue_by_currency", "paid_orders", "revenue_cents"}
MODERATION_KEYS = {"pending_corrections", "corrections_by_status",
                   "total_admin_edits", "conversations"}
INGESTION_KEYS = {"sources", "freshness"}


# ── Auth gate ────────────────────────────────────────────────────────────


def test_overview_requires_auth():
    assert client.get("/v1/admin/dashboard").status_code == 401


def test_overview_rejects_bad_token():
    r = client.get("/v1/admin/dashboard",
                   headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_revenue_requires_auth():
    assert client.get("/v1/admin/dashboard/revenue").status_code == 401


def test_recent_orders_requires_auth():
    assert client.get("/v1/admin/dashboard/recent-orders").status_code == 401


# ── Overview ─────────────────────────────────────────────────────────────


def test_overview_structure():
    r = client.get("/v1/admin/dashboard", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == OVERVIEW_KEYS
    assert isinstance(body["generated_at"], str)

    counts = body["counts"]
    assert set(counts) == COUNT_KEYS
    for k in COUNT_KEYS:
        assert isinstance(counts[k], int) and counts[k] >= 0
    # matched can never exceed total results
    assert counts["matched_results"] <= counts["race_results"]

    revenue = body["revenue"]
    assert set(revenue) == REVENUE_KEYS
    assert isinstance(revenue["revenue_cents"], int) and revenue["revenue_cents"] >= 0
    assert isinstance(revenue["paid_orders"], int) and revenue["paid_orders"] >= 0
    assert isinstance(revenue["orders_by_status"], dict)
    for status, v in revenue["orders_by_status"].items():
        assert "count" in v and "revenue_cents" in v

    moderation = body["moderation"]
    assert set(moderation) == MODERATION_KEYS
    assert isinstance(moderation["pending_corrections"], int)
    assert isinstance(moderation["corrections_by_status"], dict)

    ingestion = body["ingestion"]
    assert set(ingestion) == INGESTION_KEYS
    assert isinstance(ingestion["sources"], list)
    assert isinstance(ingestion["freshness"], dict)

    assert isinstance(body["alerts"], list)
    for a in body["alerts"]:
        assert "type" in a and "source" in a


def test_overview_ingestion_sources_have_expected_fields():
    r = client.get("/v1/admin/dashboard", headers=AUTH)
    sources = r.json()["ingestion"]["sources"]
    assert sources, "expected at least one ingestion source"
    for s in sources:
        assert {"source", "started_at", "status", "records_new"} <= set(s)


# ── Revenue ───────────────────────────────────────────────────────────────


def test_revenue_structure():
    r = client.get("/v1/admin/dashboard/revenue?days=30", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["days"] == 30
    assert REVENUE_KEYS <= set(body)
    assert isinstance(body["daily_series"], list)
    for point in body["daily_series"]:
        assert {"day", "orders", "revenue_cents"} == set(point)
        assert isinstance(point["orders"], int)
        assert isinstance(point["revenue_cents"], int)


def test_revenue_days_bounds():
    # below minimum
    assert client.get("/v1/admin/dashboard/revenue?days=0", headers=AUTH).status_code == 422
    # above maximum
    assert client.get("/v1/admin/dashboard/revenue?days=999", headers=AUTH).status_code == 422


def test_revenue_default_window():
    r = client.get("/v1/admin/dashboard/revenue", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["days"] == 30


# ── Recent orders ─────────────────────────────────────────────────────────


def test_recent_orders_structure():
    r = client.get("/v1/admin/dashboard/recent-orders?limit=5", headers=AUTH)
    assert r.status_code == 200
    orders = r.json()["orders"]
    assert isinstance(orders, list)
    assert len(orders) <= 5
    for o in orders:
        assert {"id", "order_token", "boat_id", "boat_name", "amount_cents",
                "currency", "status", "created_at"} <= set(o)
    # newest-first ordering
    created = [o["created_at"] for o in orders if o["created_at"]]
    assert created == sorted(created, reverse=True)


def test_recent_orders_limit_bounds():
    assert client.get("/v1/admin/dashboard/recent-orders?limit=0", headers=AUTH).status_code == 422
    assert client.get("/v1/admin/dashboard/recent-orders?limit=999", headers=AUTH).status_code == 422
