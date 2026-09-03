"""API-level tests for the AD-01-15 data-health facts endpoints.

End-to-end over FastAPI TestClient with the DB dependency overridden to an
in-memory SQLite fixture.  Proves:

* both endpoints are behind the admin credential;
* ``/v1/admin/health/completeness`` renders the nightly ``admin_metrics``
  stream (and honestly reports unavailable before the first run);
* ``/v1/admin/health/tables`` degrades honestly off PostgreSQL (the
  pg_stat census requires PG — asserted in the PG-backed test in
  ``tests/test_admin_metrics.py``);
* each response returns well inside the page's 200 ms budget.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from irc_data.ops import admin_metrics as adm


@pytest.fixture()
def engine():
    """StaticPool-backed in-memory SQLite so the TestClient shares one DB."""
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE boats (
                    id INTEGER PRIMARY KEY,
                    boat_name TEXT,
                    design TEXT,
                    design_canonical TEXT,
                    country TEXT,
                    year_built INTEGER,
                    builder TEXT,
                    designer TEXT,
                    loa REAL,
                    lwl REAL,
                    beam_max REAL,
                    displacement_kg REAL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    venue TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO boats (boat_name, design, country) "
                "VALUES ('Alpha', 'J/109', 'GBR'), ('Beta', 'J/109', NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO events (name, venue) "
                "VALUES ('Cowes Week 2026', 'Cowes'), ('RORC  Fastnet', NULL)"
            )
        )
    return eng


@pytest.fixture()
def client(engine, monkeypatch):
    from fastapi.testclient import TestClient

    from irc_data.api import app as app_module
    from irc_data.api.deps import get_db
    from irc_data.api.routers import admin as admin_module

    monkeypatch.setattr(admin_module, "ADMIN_PASSWORD", "test-secret")

    app_module.app.dependency_overrides[get_db] = lambda: engine
    try:
        yield TestClient(app_module.app)
    finally:
        app_module.app.dependency_overrides.pop(get_db, None)


def _auth():
    return {"Authorization": "Bearer test-secret"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAuth:
    def test_completeness_requires_admin(self, client):
        res = client.get("/v1/admin/health/completeness")
        assert res.status_code == 401

    def test_tables_requires_admin(self, client):
        res = client.get("/v1/admin/health/tables")
        assert res.status_code == 401


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


class TestCompletenessEndpoint:
    def test_unavailable_before_first_run(self, client):
        res = client.get("/v1/admin/health/completeness", headers=_auth())
        assert res.status_code == 200
        body = res.json()
        assert body["available"] is False
        assert body["meters"] == []
        assert body["buoy_threshold_pct"] == 40.0

    def test_renders_from_admin_metrics_after_nightly(self, client, engine):
        adm.compute_nightly_metrics(engine)
        res = client.get("/v1/admin/health/completeness", headers=_auth())
        assert res.status_code == 200
        body = res.json()
        assert body["available"] is True
        by_col = {m["column"]: m for m in body["meters"]}
        # 2 boats, both with design, 1 with country.
        assert by_col["design"]["pct_non_null"] == 100.0
        assert by_col["country"]["pct_non_null"] == 50.0
        assert by_col["design_canonical"]["pct_non_null"] == 0.0
        assert by_col["design_canonical"]["buoy"] is True
        # events facts present.
        assert body["events"]["venue_null_rate"] == 50.0
        assert set(body["events"]["raw_name_sample"]) >= {"Cowes Week 2026"}

    def test_response_under_200ms(self, client, engine):
        """The page's budget: no query on the page may exceed 200 ms."""
        adm.compute_nightly_metrics(engine)
        start = time.perf_counter()
        res = client.get("/v1/admin/health/completeness", headers=_auth())
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert res.status_code == 200
        assert elapsed_ms < 200


# ---------------------------------------------------------------------------
# Tables census
# ---------------------------------------------------------------------------


class TestTablesEndpoint:
    def test_degrades_honestly_off_postgres(self, client):
        res = client.get("/v1/admin/health/tables", headers=_auth())
        assert res.status_code == 200
        body = res.json()
        assert body["available"] is False
        assert "PostgreSQL" in body["reason"]
        assert body["tables"] == []
        assert body["empty_tables"] == []

    def test_response_under_200ms(self, client):
        start = time.perf_counter()
        res = client.get("/v1/admin/health/tables", headers=_auth())
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert res.status_code == 200
        assert elapsed_ms < 200
