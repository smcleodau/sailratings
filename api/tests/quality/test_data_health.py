"""Tests for the data-health dashboard & incident workflow (DP-05-04).

Verification approach: **synthetic incidents** drive the full workflow —
detector ingestion (source-monitor health events + blocking
reconciliation reports), alerting, ownership assignment,
acknowledgement, mitigation and resolution — and the dashboard is then
checked to **reconcile to the quality events** those incidents cite.

These tests run against an in-memory SQLite engine with the hand-rolled
schema mirrors (``init_monitor_tables`` + ``init_reconciliation_tables``
+ ``init_quality_tables`` + ``init_data_incident_tables``), so no
Postgres or Alembic state is required.  The data layer deliberately uses
portable SQL so behaviour is identical on Postgres in production.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from irc_data.diagnostics import reconciliation as recon
from irc_data.diagnostics import source_monitor
from irc_data.diagnostics.source_monitor import (
    SourceHealthEventV1,
    init_monitor_tables,
)
from irc_data.diagnostics.reconciliation import (
    PipelineCountsV1,
    init_reconciliation_tables,
    reconcile_run,
)
from irc_data.quality import dimensions as dq
from irc_data.quality import gate_store, gates, health
from irc_data.quality.contracts import GateKind
from irc_data.quality.health import (
    STATUS_ACKNOWLEDGED,
    STATUS_MITIGATING,
    STATUS_OPEN,
    STATUS_RESOLVED,
    DataIncidentV1,
    IncidentWorkflowError,
)

from . import fixtures as fx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
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


class AlertRecorder:
    """Captures webhook alerts without any network calls."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, url: str, payload: dict) -> bool:
        self.payloads.append({"url": url, "payload": payload})
        return True


@pytest.fixture()
def alerts():
    return AlertRecorder()


# ---------------------------------------------------------------------------
# Contract: DataIncidentV1 round-trips
# ---------------------------------------------------------------------------


class TestContract:
    def test_round_trip(self):
        inc = DataIncidentV1(
            incident_id="inc-abc123",
            kind=health.KIND_SILENT_LOSS,
            severity=health.SEVERITY_CRITICAL,
            status=STATUS_OPEN,
            source_slug="sailsys",
            dataset="race_results",
            title="Silent loss on sailsys",
            owner=dq.OWNER_DATA_PLATFORM.to_dict(),
            affected_batches=["extraction:sailsys:v3"],
            affected_consumers=["canonical_view:race_results", "public:results"],
            evidence={"reconciliation_report_ids": ["recon-sailsys-42"]},
            recommended_action={"kind": "replay", "summary": "replay it"},
        )
        d = inc.to_dict()
        assert d["schema_version"] == health.SCHEMA_VERSION
        inc2 = DataIncidentV1.from_dict(d)
        assert inc2 == inc
        # JSON round-trip too.
        inc3 = DataIncidentV1.from_json(inc.to_json())
        assert inc3 == inc

    def test_transition_table(self):
        inc = DataIncidentV1(
            incident_id="inc-x", kind="manual", severity="warning",
            status=STATUS_OPEN,
        )
        assert inc.can_transition(STATUS_ACKNOWLEDGED)
        assert inc.can_transition(STATUS_RESOLVED)
        inc.status = STATUS_RESOLVED
        assert not inc.can_transition(STATUS_OPEN)
        assert not inc.can_transition(STATUS_ACKNOWLEDGED)


# ---------------------------------------------------------------------------
# Synthetic incident: creation → alert → ownership → ack → resolve
# ---------------------------------------------------------------------------


class TestIncidentWorkflow:
    def test_create_assigns_owner_and_recommended_replay(
        self, engine: Engine, alerts: AlertRecorder
    ):
        """A synthetic silent-loss incident is created with the dataset
        owner, affected consumers and a *replay* recommended action, and
        the alert fires in the same call."""
        inc = health.create_incident(
            engine,
            kind=health.KIND_SILENT_LOSS,
            title="Silent loss on sailsys",
            severity=health.SEVERITY_CRITICAL,
            source_slug="sailsys",
            evidence={"run_id": 42, "variance": 17},
            alert_transport=alerts,
            webhook_url="https://hooks.slack.test/alert",
        )
        # Ownership: race_results' completeness blocking rule is owned by
        # the ingestion on-call (DP-05-01 registry).
        assert inc.owner["handle"], "owner must be assigned"
        assert inc.owner["escalation"], "owner must carry an escalation"
        # Affected consumers recorded.
        assert "canonical_view:race_results" in inc.affected_consumers
        # Recommended action: replay with a ready ReplayPlanV1.
        assert inc.recommended_action["kind"] == "replay"
        plan = inc.recommended_action["replay_plan"]
        assert plan["source_slug"] == "sailsys"
        assert plan["plan_id"], "replay plan must carry an idempotency key"
        # Alert fired with the incident id and owner.
        assert inc.alert_sent_at is not None
        assert len(alerts.payloads) == 1
        text_blob = str(alerts.payloads[0]["payload"])
        assert inc.incident_id in text_blob
        assert inc.owner["handle"] in text_blob

    def test_alert_not_sent_without_webhook(self, engine: Engine, monkeypatch):
        monkeypatch.delenv(health.INCIDENT_WEBHOOK_ENV, raising=False)
        inc = health.create_incident(
            engine, kind=health.KIND_MANUAL, title="manual", alert=True
        )
        assert inc.alert_sent_at is None  # no webhook configured

    def test_acknowledge_mitigate_resolve(self, engine: Engine):
        inc = health.create_incident(
            engine, kind=health.KIND_FRESHNESS, title="tcc stale",
            source_slug="tcc", alert=False,
        )
        assert inc.status == STATUS_OPEN

        # Acknowledge: stamps the actor and timestamp.
        inc = health.acknowledge_incident(
            engine, inc.incident_id, actor="stuart@sailratings.com",
            note="on it",
        )
        assert inc.status == STATUS_ACKNOWLEDGED
        assert inc.acknowledged_at is not None
        assert inc.acknowledged_by == "stuart@sailratings.com"
        assert any("on it" in n["note"] for n in inc.notes)

        # Mitigate.
        inc = health.start_mitigation(
            engine, inc.incident_id, actor="stuart@sailratings.com",
            note="upstream down; extending budget",
        )
        assert inc.status == STATUS_MITIGATING

        # Resolve requires a resolution note.
        with pytest.raises(IncidentWorkflowError):
            health.resolve_incident(
                engine, inc.incident_id, actor="stuart@sailratings.com",
                resolution="",
            )
        inc = health.resolve_incident(
            engine, inc.incident_id, actor="stuart@sailratings.com",
            resolution="upstream recovered; freshness back inside budget",
        )
        assert inc.status == STATUS_RESOLVED
        assert inc.resolved_at is not None

        # Terminal: no further transitions.
        with pytest.raises(IncidentWorkflowError):
            health.acknowledge_incident(
                engine, inc.incident_id, actor="someone@sailratings.com"
            )

    def test_illegal_transition_rejected(self, engine: Engine):
        inc = health.create_incident(
            engine, kind=health.KIND_MANUAL, title="t", alert=False
        )
        with pytest.raises(IncidentWorkflowError):
            health._transition(
                engine, inc.incident_id, "flying", actor="x"
            )
        # open → open is not a transition.
        with pytest.raises(IncidentWorkflowError):
            health._transition(
                engine, inc.incident_id, STATUS_OPEN, actor="x"
            )

    def test_unknown_incident_raises(self, engine: Engine):
        with pytest.raises(IncidentWorkflowError):
            health.acknowledge_incident(engine, "inc-nope", actor="x")
        assert health.get_incident(engine, "inc-nope") is None

    def test_notes_append(self, engine: Engine):
        inc = health.create_incident(
            engine, kind=health.KIND_MANUAL, title="t", alert=False
        )
        inc = health.add_incident_note(
            engine, inc.incident_id, actor="a", note="first"
        )
        inc = health.add_incident_note(
            engine, inc.incident_id, actor="b", note="second"
        )
        assert [n["note"] for n in inc.notes] == ["first", "second"]
        # Notes persist across reads.
        fetched = health.get_incident(engine, inc.incident_id)
        assert fetched is not None
        assert [n["note"] for n in fetched.notes] == ["first", "second"]

    def test_list_filters(self, engine: Engine):
        a = health.create_incident(
            engine, kind=health.KIND_FRESHNESS, title="a",
            source_slug="tcc", alert=False,
        )
        health.create_incident(
            engine, kind=health.KIND_SILENT_LOSS, title="b",
            source_slug="sailsys", alert=False,
        )
        assert len(health.list_incidents(engine)) == 2
        assert len(health.list_incidents(engine, status="active")) == 2
        assert len(health.list_incidents(engine, source_slug="tcc")) == 1
        assert len(health.list_incidents(engine, kind=health.KIND_SILENT_LOSS)) == 1
        health.resolve_incident(
            engine, a.incident_id, actor="x", resolution="done"
        )
        assert len(health.list_incidents(engine, status="active")) == 1
        assert len(health.list_incidents(engine, status=STATUS_RESOLVED)) == 1


# ---------------------------------------------------------------------------
# Detector ingestion
# ---------------------------------------------------------------------------


class TestDetectorIngestion:
    def test_health_event_ingestion(
        self, engine: Engine, alerts: AlertRecorder
    ):
        """A material source-monitor event becomes a critical incident
        with the health-event evidence attached."""
        event = SourceHealthEventV1(
            source_id="tcc",
            url="https://tcc.example/listing",
            checked_at="2026-09-05T00:00:00+00:00",
            status=source_monitor.STATUS_MATERIAL,
            material=True,
            deviations=[source_monitor.DEV_STRUCTURE],
            diff_ratio=0.42,
            incident_id=7,
            quarantined=True,
        )
        inc = health.create_incident_from_health_event(
            engine, event,
            alert_transport=alerts, webhook_url="https://hooks.test/x",
        )
        assert inc is not None
        assert inc.kind == health.KIND_SOURCE_DEVIATION
        assert inc.severity == health.SEVERITY_CRITICAL
        assert inc.evidence["source_incident_id"] == 7
        assert inc.evidence["deviations"] == [source_monitor.DEV_STRUCTURE]
        # Structure change → policy action (rebaseline / release).
        assert inc.recommended_action["kind"] == "policy"
        assert inc.recommended_action["policy"] == "quarantine_release"

    def test_non_material_health_event_skipped(self, engine: Engine):
        event = SourceHealthEventV1(
            source_id="tcc", material=False,
            status=source_monitor.STATUS_CHANGED,
        )
        assert (
            health.create_incident_from_health_event(engine, event) is None
        )

    def test_reconciliation_ingestion(
        self, engine: Engine, alerts: AlertRecorder
    ):
        """A blocking reconciliation report becomes a silent-loss
        incident with a replay recommendation."""
        report = reconcile_run(
            engine,
            PipelineCountsV1(
                run_id=99, source_id="sailsys",
                discovered=100, fetched=100, parsed=60, transformed=60,
                published=60,  # 40 records vanished, unexplained
            ),
            alert_transport=alerts,
        )
        assert report.decision == "block"

        inc = health.create_incident_from_reconciliation(
            engine, report,
            alert_transport=alerts, webhook_url="https://hooks.test/y",
        )
        assert inc is not None
        assert inc.kind == health.KIND_SILENT_LOSS
        assert inc.severity == health.SEVERITY_CRITICAL
        assert inc.evidence["reconciliation_report_ids"] == [report.report_id]
        assert inc.evidence["run_id"] == 99
        assert inc.evidence["variance"] == 40
        assert inc.recommended_action["kind"] == "replay"
        assert inc.alert_sent_at is not None

    def test_allow_reconciliation_skipped(self, engine: Engine):
        report = reconcile_run(
            engine,
            PipelineCountsV1(
                run_id=100, source_id="sailsys",
                discovered=10, fetched=10, parsed=10, transformed=10,
                published=10,
            ),
        )
        assert report.decision == "allow"
        assert (
            health.create_incident_from_reconciliation(engine, report) is None
        )


# ---------------------------------------------------------------------------
# Dashboard aggregation
# ---------------------------------------------------------------------------


def _seed_ledger(engine: Engine):
    """Two sources: one fresh+healthy, one stale+failing."""
    from datetime import datetime, timedelta, timezone

    from irc_data.db import run_ledger

    now = datetime.now(timezone.utc)
    # Fresh source: ran an hour ago, ingested new rows.
    rid = run_ledger.record_run_start(
        engine, "sailsys", started_at=now - timedelta(hours=1)
    )
    run_ledger.record_run_end(
        engine, rid, status=run_ledger.STATUS_COMPLETED,
        records_found=50, records_new=5, records_updated=45,
        completed_at=now - timedelta(hours=1),
    )
    # Stale source: last success 5 days ago, then a failure.
    rid2 = run_ledger.record_run_start(
        engine, "tcc", started_at=now - timedelta(days=5)
    )
    run_ledger.record_run_end(
        engine, rid2, status=run_ledger.STATUS_COMPLETED,
        records_found=10, records_new=10,
        completed_at=now - timedelta(days=5),
    )
    rid3 = run_ledger.record_run_start(
        engine, "tcc", started_at=now - timedelta(hours=2)
    )
    run_ledger.record_run_end(
        engine, rid3, status=run_ledger.STATUS_FAILED,
        error_message="boom", completed_at=now - timedelta(hours=2),
    )
    return {"fresh_run_id": rid, "stale_failed_run_id": rid3}


def _seed_blocking_recon(engine: Engine, source: str = "sailsys"):
    """A blocking reconciliation report (silent loss) for ``source``."""
    return reconcile_run(
        engine,
        PipelineCountsV1(
            run_id=555, source_id=source,
            discovered=100, fetched=100, parsed=70, transformed=70,
            published=70,  # 30 unexplained
        ),
    )


class TestDashboard:
    def test_empty_dashboard(self, engine: Engine):
        dash = health.get_health_dashboard(engine)
        assert dash["schema_version"] == health.SCHEMA_VERSION
        assert dash["overview"]["open_data_incidents"] == 0
        assert dash["sources"] == []
        # All stacks are available in this fixture (tables exist).
        assert all(dash["availability"].values())

    def test_freshness_signals(self, engine: Engine):
        _seed_ledger(engine)
        dash = health.get_health_dashboard(engine)
        by_source = {s["source"]: s for s in dash["sources"]}
        assert by_source["sailsys"]["freshness"]["stale"] is False
        # tcc's last *success* is 5 days out → stale even though a run
        # started 2 h ago (and failed).
        assert by_source["tcc"]["freshness"]["stale"] is True
        assert by_source["tcc"]["freshness"]["failed_7d"] == 1
        assert dash["overview"]["sources_stale"] == 1

    def test_pipeline_yields(self, engine: Engine):
        _seed_ledger(engine)
        report = _seed_blocking_recon(engine)
        dash = health.get_health_dashboard(engine)
        by_source = {s["source"]: s for s in dash["sources"]}
        y = by_source["sailsys"]["latest_yield"]
        assert y is not None
        assert y["run_id"] == 555
        assert y["decision"] == "block"
        assert y["variance"] == 30
        assert dash["overview"]["blocking_reconciliations_in_window"] == 1
        # The block is an SLO breach too.
        assert dash["overview"]["slo_breaches"] >= 1
        assert any(
            b["report_id"] == report.report_id for b in dash["slo_breaches"]
        )

    def test_quarantine_signals(self, engine: Engine):
        _seed_blocking_recon(engine)  # blocks → quarantines the source
        dash = health.get_health_dashboard(engine)
        by_source = {s["source"]: s for s in dash["sources"]}
        assert by_source["sailsys"]["active_quarantine"] is True
        assert dash["overview"]["sources_quarantined"] == 1
        assert dash["active_quarantines"], "quarantine detail must surface"
        assert dash["active_quarantines"][0]["source"] == "sailsys"

    def test_lineage_gaps(self, engine: Engine):
        ids = _seed_ledger(engine)
        # Two completed runs, neither reconciled → two lineage gaps.
        dash = health.get_health_dashboard(engine)
        gap_ids = {g["run_id"] for g in dash["lineage_gaps"]["runs"]}
        assert ids["fresh_run_id"] in gap_ids
        assert dash["overview"]["lineage_gap_runs_in_window"] == 2
        # Reconcile one of them and the gap closes.
        reconcile_run(
            engine,
            PipelineCountsV1(
                run_id=ids["fresh_run_id"], source_id="sailsys",
                discovered=50, fetched=50, parsed=50, transformed=50,
                published=50,
            ),
        )
        dash = health.get_health_dashboard(engine)
        gap_ids = {g["run_id"] for g in dash["lineage_gaps"]["runs"]}
        assert ids["fresh_run_id"] not in gap_ids

    def test_identity_uncertainty(self, engine: Engine):
        """An identity batch awaiting promotion and a quarantined one
        surface as identity uncertainty."""
        # A clean identity batch: validates and parks in
        # ``awaiting_promotion`` (no auto-promote).
        gates.ingest_validate_and_optionally_promote(
            engine, pipeline="identity", source_slug="irc",
            gate=GateKind.IDENTITY.value,
            payload=fx.clean_identity_batch(),
        )
        # A faulty identity batch: quarantined by the identity gate.
        gates.ingest_validate_and_optionally_promote(
            engine, pipeline="identity", source_slug="irc",
            gate=GateKind.IDENTITY.value,
            payload=fx.identity_self_merge(),
        )
        dash = health.get_health_dashboard(engine)
        iu = dash["identity_uncertainty"]
        assert iu["available"] is True
        assert iu["awaiting_review_batches"] == 1
        assert iu["quarantined_batches"] == 1
        assert dash["overview"]["identity_awaiting_review"] == 1

    def test_incidents_in_dashboard(self, engine: Engine, alerts: AlertRecorder):
        _seed_ledger(engine)
        inc = health.create_incident(
            engine, kind=health.KIND_SILENT_LOSS, title="t",
            source_slug="sailsys", alert_transport=alerts,
            webhook_url="https://hooks.test/z",
        )
        dash = health.get_health_dashboard(engine)
        assert dash["overview"]["open_data_incidents"] == 1
        assert dash["overview"]["unacknowledged_data_incidents"] == 1
        ids = [i["incident_id"] for i in dash["incidents"]]
        assert inc.incident_id in ids
        by_source = {s["source"]: s for s in dash["sources"]}
        assert by_source["sailsys"]["open_data_incidents"] == 1
        # Acknowledge it and the unacked counter drops.
        health.acknowledge_incident(engine, inc.incident_id, actor="op")
        dash = health.get_health_dashboard(engine)
        assert dash["overview"]["unacknowledged_data_incidents"] == 0
        assert dash["overview"]["open_data_incidents"] == 1

    def test_degrades_when_tables_missing(self):
        """A bare engine (no quality tables) still returns a dashboard —
        every section reports available=False instead of erroring."""
        eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
        health.init_data_incident_tables(eng)
        dash = health.get_health_dashboard(eng)
        assert dash["overview"]["sources_tracked"] == 0
        assert dash["availability"]["run_ledger"] is False
        assert dash["identity_uncertainty"]["available"] is False


# ---------------------------------------------------------------------------
# Reconciliation of the dashboard to quality events (acceptance criterion)
# ---------------------------------------------------------------------------


class TestReconcileToEvents:
    def test_detector_incident_reconciles(self, engine: Engine):
        """An incident created from a real reconciliation report resolves
        its evidence refs against the persisted report."""
        report = _seed_blocking_recon(engine)
        inc = health.create_incident_from_reconciliation(
            engine, report, alert=False
        )
        assert inc is not None
        result = health.reconcile_incidents_to_events(engine)
        assert result["incidents_checked"] == 1
        assert result["reconciled"] == 1
        assert result["unreconciled"] == []
        check = result["checks"][0]
        assert check["reconciliation"] == "ok"
        assert any(
            f"reconciliation_reports:{report.report_id}" in r
            for r in check["resolved_refs"]
        )

    def test_health_event_incident_reconciles(self, engine: Engine):
        """An incident created from a persisted material health event
        resolves its source_incident ref."""
        # Drive a real material deviation through the monitor so the
        # health event + source_incident rows exist.
        source_monitor.check_source(
            engine, "tcc", "https://tcc.example/listing",
            content="<html><table><tr><th>A</th><th>B</th></tr>"
                    "<tr><td>1</td><td>2</td></tr></table></html>",
        )
        event = source_monitor.check_source(
            engine, "tcc", "https://tcc.example/listing",
            content="<html><body><p>no table any more</p></body></html>",
        )
        assert event.material is True
        assert event.incident_id is not None

        inc = health.create_incident_from_health_event(
            engine, event, alert=False
        )
        result = health.reconcile_incidents_to_events(engine)
        assert result["reconciled"] == 1
        check = result["checks"][0]
        assert check["reconciliation"] == "ok"
        assert any(
            f"source_incidents:{event.incident_id}" in r
            for r in check["resolved_refs"]
        )

    def test_missing_evidence_flagged(self, engine: Engine):
        """An incident citing a non-existent report is unreconciled;
        one with no refs at all is flagged as having no evidence."""
        bad = health.create_incident(
            engine, kind=health.KIND_SILENT_LOSS, title="bad",
            source_slug="sailsys",
            evidence={"reconciliation_report_ids": ["recon-ghost-1"]},
            alert=False,
        )
        bare = health.create_incident(
            engine, kind=health.KIND_MANUAL, title="bare", alert=False
        )
        result = health.reconcile_incidents_to_events(engine)
        assert result["reconciled"] == 0
        by_id = {c["incident_id"]: c for c in result["checks"]}
        assert by_id[bad.incident_id]["reconciliation"] == "unreconciled"
        assert by_id[bad.incident_id]["missing_refs"]
        assert by_id[bare.incident_id]["reconciliation"] == "no_evidence"
        assert len(result["unreconciled"]) == 2


# ---------------------------------------------------------------------------
# Recommended actions
# ---------------------------------------------------------------------------


class TestRecommendedActions:
    def test_replay_plan_is_valid_replay_contract(self, engine: Engine):
        from irc_data.temporal.replay.contracts import ReplayPlanV1

        inc = health.create_incident(
            engine, kind=health.KIND_SILENT_LOSS, title="t",
            source_slug="sailsys", evidence={"run_id": 1}, alert=False,
        )
        plan_dict = inc.recommended_action["replay_plan"]
        plan = ReplayPlanV1.from_dict(plan_dict)
        assert plan.source_slug == "sailsys"
        assert plan.plan_id
        assert plan.artifact_filter.source_slug == "sailsys"

    def test_policy_action_kinds(self, engine: Engine):
        for kind, policy in [
            (health.KIND_FRESHNESS, "ownership_escalation"),
            (health.KIND_IDENTITY_UNCERTAINTY, "identity_review"),
            (health.KIND_SLO_BREACH, "slo_review"),
            (health.KIND_LINEAGE_GAP, "reconciliation_backfill"),
            (health.KIND_SOURCE_DEVIATION, "quarantine_release"),
        ]:
            inc = health.create_incident(
                engine, kind=kind, title=f"t-{kind}", source_slug="tcc",
                alert=False,
            )
            assert inc.recommended_action["kind"] == "policy", kind
            assert inc.recommended_action["policy"] == policy, kind

    def test_identity_incidents_route_to_identity_owner(self, engine: Engine):
        inc = health.create_incident(
            engine, kind=health.KIND_IDENTITY_UNCERTAINTY, title="t",
            source_slug="irc", alert=False,
        )
        assert inc.owner["handle"] == dq.OWNER_IDENTITY.handle
