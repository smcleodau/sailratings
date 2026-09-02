"""API-level tests for the DP-05-04 data-health router.

End-to-end over FastAPI TestClient with the DB dependency overridden to
an in-memory SQLite engine.  Proves the admin endpoints expose the
aggregated dashboard and the incident workflow (create → alert →
acknowledge → mitigate → resolve) behind the admin credential, and that
the reconcile endpoint checks incident evidence against the quality
events.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from irc_data.diagnostics.reconciliation import (
    PipelineCountsV1,
    init_reconciliation_tables,
    reconcile_run,
)
from irc_data.diagnostics.source_monitor import init_monitor_tables
from irc_data.quality import gate_store, health


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
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE ingestion_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    status TEXT DEFAULT 'running',
                    records_found INTEGER,
                    records_new INTEGER,
                    records_updated INTEGER,
                    error_message TEXT,
                    metadata TEXT
                )
                """
            )
        )
    init_monitor_tables(eng)
    init_reconciliation_tables(eng)
    gate_store.init_quality_tables(eng)
    health.init_data_incident_tables(eng)
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


def test_requires_admin_auth(client):
    assert client.get("/v1/admin/data-health/dashboard").status_code == 401
    assert client.get("/v1/admin/data-health/incidents").status_code == 401
    assert (
        client.post(
            "/v1/admin/data-health/incidents",
            json={"kind": "manual", "title": "t"},
        ).status_code
        == 401
    )
    assert (
        client.get("/v1/admin/data-health/incidents/reconcile").status_code
        == 401
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def test_dashboard_empty(client):
    resp = client.get("/v1/admin/data-health/dashboard", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == health.SCHEMA_VERSION
    assert body["overview"]["open_data_incidents"] == 0
    assert body["sources"] == []


def test_dashboard_aggregates_quality_events(client, engine):
    """Seed a blocking reconciliation (silent loss) and confirm the
    dashboard surfaces the yield block, the quarantine and the SLO
    breach."""
    report = reconcile_run(
        engine,
        PipelineCountsV1(
            run_id=777, source_id="sailsys",
            discovered=100, fetched=100, parsed=60, transformed=60,
            published=60,
        ),
    )
    assert report.decision == "block"

    resp = client.get("/v1/admin/data-health/dashboard", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    by_source = {s["source"]: s for s in body["sources"]}
    assert by_source["sailsys"]["latest_yield"]["decision"] == "block"
    assert by_source["sailsys"]["active_quarantine"] is True
    assert body["overview"]["sources_quarantined"] == 1
    assert body["overview"]["blocking_reconciliations_in_window"] == 1
    assert body["overview"]["slo_breaches"] >= 1


# ---------------------------------------------------------------------------
# Incident workflow over HTTP (synthetic incident)
# ---------------------------------------------------------------------------


def test_incident_workflow_end_to_end(client, monkeypatch):
    """Synthetic incident: create (alert captured) → acknowledge →
    mitigate → resolve, with ownership and recommended action set."""
    captured = []

    def _transport(url, payload):
        captured.append(payload)
        return True

    monkeypatch.setattr(health, "_post_webhook", _transport)
    monkeypatch.setenv(health.INCIDENT_WEBHOOK_ENV, "https://hooks.test/inc")

    # Create (synthetic).
    resp = client.post(
        "/v1/admin/data-health/incidents",
        headers=_auth(),
        json={
            "kind": "silent_loss",
            "severity": "critical",
            "title": "Synthetic silent loss on sailsys",
            "source_slug": "sailsys",
            "evidence": {"run_id": 1, "variance": 5},
        },
    )
    assert resp.status_code == 201
    inc = resp.json()
    iid = inc["incident_id"]
    assert inc["status"] == "open"
    assert inc["owner"]["handle"], "owner must be assigned"
    assert inc["recommended_action"]["kind"] == "replay"
    assert inc["alert_sent_at"] is not None
    assert captured and iid in str(captured[0]), "alert carries incident id"

    # Detail.
    resp = client.get(
        f"/v1/admin/data-health/incidents/{iid}", headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json()["incident_id"] == iid

    # Acknowledge.
    resp = client.post(
        f"/v1/admin/data-health/incidents/{iid}/acknowledge",
        headers=_auth(),
        json={"actor": "ingestion-ops@sailratings.com", "note": "ack"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged"
    assert resp.json()["acknowledged_by"] == "ingestion-ops@sailratings.com"

    # Mitigate.
    resp = client.post(
        f"/v1/admin/data-health/incidents/{iid}/mitigate",
        headers=_auth(),
        json={"actor": "ingestion-ops@sailratings.com",
              "note": "replaying parser fix"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "mitigating"

    # Resolve requires a resolution note.
    resp = client.post(
        f"/v1/admin/data-health/incidents/{iid}/resolve",
        headers=_auth(),
        json={"actor": "ingestion-ops@sailratings.com", "resolution": ""},
    )
    assert resp.status_code == 409

    resp = client.post(
        f"/v1/admin/data-health/incidents/{iid}/resolve",
        headers=_auth(),
        json={"actor": "ingestion-ops@sailratings.com",
              "resolution": "replay promoted; counts reconciled"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    assert resp.json()["resolved_at"] is not None

    # Terminal: acknowledge after resolve is rejected.
    resp = client.post(
        f"/v1/admin/data-health/incidents/{iid}/acknowledge",
        headers=_auth(), json={"actor": "someone@sailratings.com"},
    )
    assert resp.status_code == 409


def test_unknown_incident_404(client):
    resp = client.get(
        "/v1/admin/data-health/incidents/inc-nope", headers=_auth()
    )
    assert resp.status_code == 404
    resp = client.post(
        "/v1/admin/data-health/incidents/inc-nope/acknowledge",
        headers=_auth(), json={"actor": "x"},
    )
    assert resp.status_code == 404


def test_create_validation(client):
    resp = client.post(
        "/v1/admin/data-health/incidents",
        headers=_auth(),
        json={"kind": "not-a-kind", "title": "t"},
    )
    assert resp.status_code == 422
    resp = client.post(
        "/v1/admin/data-health/incidents",
        headers=_auth(),
        json={"kind": "manual", "severity": "apocalyptic", "title": "t"},
    )
    assert resp.status_code == 422


def test_list_filters(client):
    client.post(
        "/v1/admin/data-health/incidents", headers=_auth(),
        json={"kind": "freshness_breach", "title": "a",
              "source_slug": "tcc", "alert": False},
    )
    client.post(
        "/v1/admin/data-health/incidents", headers=_auth(),
        json={"kind": "silent_loss", "title": "b",
              "source_slug": "sailsys", "alert": False},
    )
    resp = client.get("/v1/admin/data-health/incidents", headers=_auth())
    assert resp.json()["count"] == 2
    resp = client.get(
        "/v1/admin/data-health/incidents?source=tcc", headers=_auth()
    )
    assert resp.json()["count"] == 1
    resp = client.get(
        "/v1/admin/data-health/incidents?kind=silent_loss", headers=_auth()
    )
    assert resp.json()["count"] == 1
    resp = client.get(
        "/v1/admin/data-health/incidents?status=resolved", headers=_auth()
    )
    assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# Reconcile endpoint
# ---------------------------------------------------------------------------


def test_reconcile_endpoint(client, engine):
    """A detector incident reconciles against its quality events; a
    ghost-evidence incident is flagged."""
    report = reconcile_run(
        engine,
        PipelineCountsV1(
            run_id=888, source_id="sailsys",
            discovered=10, fetched=10, parsed=4, transformed=4,
            published=4,
        ),
    )
    health.create_incident_from_reconciliation(engine, report, alert=False)
    health.create_incident(
        engine, kind=health.KIND_SILENT_LOSS, title="ghost",
        source_slug="orc",
        evidence={"reconciliation_report_ids": ["recon-ghost-9"]},
        alert=False,
    )
    resp = client.get(
        "/v1/admin/data-health/incidents/reconcile", headers=_auth()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["incidents_checked"] == 2
    assert body["reconciled"] == 1
    assert len(body["unreconciled"]) == 1
    assert body["unreconciled"][0]["reconciliation"] == "unreconciled"
