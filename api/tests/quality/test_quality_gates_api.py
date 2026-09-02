"""API-level tests for the DP-05-02 quality gates router.

End-to-end over FastAPI TestClient with the DB dependency overridden to
an in-memory SQLite engine.  Proves the admin endpoints expose the
quarantine queue, batch detail, explicit promotion and the
promoted-only consumer view.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from irc_data.quality import gate_store, gates
from irc_data.quality.contracts import GateKind

from . import fixtures as fx


@pytest.fixture()
def engine():
    """StaticPool-backed in-memory SQLite so the TestClient's requests
    share one database."""
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    gate_store.init_quality_tables(eng)
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


def _ingest_bad(engine):
    return gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value,
        payload=fx.extraction_broken_provenance(),
    )


def _ingest_good(engine):
    return gates.ingest_validate_and_optionally_promote(
        engine, pipeline="extraction", source_slug=fx.SOURCE_SLUG,
        gate=GateKind.EXTRACTION.value, payload=fx.clean_extraction_batch(),
    )


def test_requires_admin_auth(client):
    assert client.get("/v1/admin/quality/batches").status_code == 401
    assert client.get("/v1/admin/quality/quarantine").status_code == 401


def test_quarantine_queue_and_detail(client, engine):
    bad = _ingest_bad(engine)
    resp = client.get("/v1/admin/quality/quarantine", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    qid = body["quarantine"][0]["quarantine_id"]

    resp = client.get(
        f"/v1/admin/quality/quarantine/{qid}", headers=_auth()
    )
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["quarantine_id"] == qid
    assert detail["failures"], "detail must carry the rule failures"
    assert detail["sample_rows"], "detail must carry sample rows"


def test_promote_endpoint_and_consumer_view(client, engine):
    """A quarantined batch 409s; a clean batch promotes; the consumer
    view then returns its rows."""
    bad = _ingest_bad(engine)
    resp = client.post(
        f"/v1/admin/quality/batches/{bad['batch']['batch_key']}/promote",
        headers=_auth(), json={"promoted_by": "tester"},
    )
    assert resp.status_code == 409  # quarantined → cannot promote

    # Consumer view is empty until something is promoted.
    resp = client.get(
        "/v1/admin/quality/consumer-view",
        params={"pipeline": "extraction", "source_slug": fx.SOURCE_SLUG},
        headers=_auth(),
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 0

    good = _ingest_good(engine)
    resp = client.post(
        f"/v1/admin/quality/batches/{good['batch']['batch_key']}/promote",
        headers=_auth(), json={"promoted_by": "tester"},
    )
    assert resp.status_code == 200
    receipt = resp.json()["receipt"]
    assert receipt["version"] == 2  # retry created a new version

    resp = client.get(
        "/v1/admin/quality/consumer-view",
        params={"pipeline": "extraction", "source_slug": fx.SOURCE_SLUG},
        headers=_auth(),
    )
    body = resp.json()
    assert body["count"] == 3
    assert body["promoted_batch"]["version"] == 2


def test_batch_detail(client, engine):
    good = _ingest_good(engine)
    resp = client.get(
        f"/v1/admin/quality/batches/{good['batch']['batch_key']}",
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["batch"]["status"] == "awaiting_promotion"
    assert body["row_count"] == 3
    assert body["verdicts"][0]["outcome"] == "passed"


def test_batch_detail_404(client):
    resp = client.get(
        "/v1/admin/quality/batches/nope:nothing:v99", headers=_auth()
    )
    assert resp.status_code == 404


def test_malformed_batch_key_400(client):
    resp = client.get("/v1/admin/quality/batches/not-a-key", headers=_auth())
    assert resp.status_code == 400
