"""Tests for the factory telemetry API (AD-01-19).

``GET /v1/admin/factory/runs`` and ``GET /v1/admin/factory/runs/{id}`` talk
to a Temporal client — here a fake one, injected via
``app.dependency_overrides[get_temporal_client]``, so no real Temporal
server is required.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from irc_data.api import app as app_module
from irc_data.api.routers import admin as admin_module
from irc_data.api.routers import factory_telemetry

ADMIN_HEADERS = {"Authorization": "Bearer test-secret"}


class FakeWorkflowExecution:
    def __init__(self, id, status, start_time, close_time=None):
        self.id = id
        self.status = status
        self.start_time = start_time
        self.close_time = close_time


class _FakeStatus:
    """Duck-types ``temporalio.client.WorkflowExecutionStatus``'s ``.name``."""

    def __init__(self, name):
        self.name = name


RUNNING = _FakeStatus("RUNNING")
COMPLETED = _FakeStatus("COMPLETED")
FAILED = _FakeStatus("FAILED")


class FakeWorkflowHandle:
    def __init__(self, events):
        self._events = events

    async def fetch_history(self):
        class _History:
            events = self._events

        return _History()


class FakeTemporalClient:
    """Records the query it was called with and returns canned executions."""

    def __init__(self, executions=None, history_events=None):
        self._executions = executions or []
        self._history_events = history_events or []
        self.last_query = None
        self.last_limit = None

    def list_workflows(self, query, limit=None):
        self.last_query = query
        self.last_limit = limit
        executions = self._executions

        async def _gen():
            for wf in executions:
                yield wf

        return _gen()

    def get_workflow_handle(self, workflow_id):
        return FakeWorkflowHandle(self._history_events)


def _dt(minutes_offset: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes_offset)


@pytest.fixture()
def fake_client():
    return FakeTemporalClient(
        executions=[
            FakeWorkflowExecution(
                "agent-task-3d137ffe-abcd",
                COMPLETED,
                _dt(0),
                _dt(5),
            ),
            FakeWorkflowExecution(
                "agent-task-3d137ffe-ef01",
                FAILED,
                _dt(10),
                _dt(12),
            ),
            FakeWorkflowExecution(
                "agent-task-3d137ffe-9999",
                RUNNING,
                _dt(20),
                None,
            ),
        ]
    )


@pytest.fixture()
def client(fake_client, monkeypatch):
    monkeypatch.setattr(admin_module, "ADMIN_PASSWORD", "test-secret")

    app_module.app.dependency_overrides[factory_telemetry.get_temporal_client] = (
        lambda: fake_client
    )
    try:
        yield TestClient(app_module.app)
    finally:
        app_module.app.dependency_overrides.pop(
            factory_telemetry.get_temporal_client, None
        )


# ---------------------------------------------------------------------------
# GET /v1/admin/factory/runs
# ---------------------------------------------------------------------------


def test_runs_requires_admin_auth(client):
    resp = client.get("/v1/admin/factory/runs")
    assert resp.status_code == 401


def test_runs_lists_and_maps_fields(client):
    resp = client.get("/v1/admin/factory/runs", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 3

    completed = body["items"][0]
    assert completed["workflow_id"] == "agent-task-3d137ffe-abcd"
    assert completed["card_page_id"] == "3d137ffe-abcd"
    assert completed["status"] == "COMPLETED"
    assert completed["start_time"] is not None
    assert completed["close_time"] is not None
    assert completed["duration_s"] == pytest.approx(300.0)

    running = body["items"][2]
    assert running["status"] == "RUNNING"
    assert running["close_time"] is None
    assert running["duration_s"] is None


def test_runs_parses_card_page_id_from_workflow_id(client):
    resp = client.get("/v1/admin/factory/runs", headers=ADMIN_HEADERS)
    body = resp.json()
    by_id = {item["workflow_id"]: item["card_page_id"] for item in body["items"]}
    assert by_id["agent-task-3d137ffe-abcd"] == "3d137ffe-abcd"
    assert by_id["agent-task-3d137ffe-ef01"] == "3d137ffe-ef01"

    # A workflow id that doesn't follow the agent-task-<id> convention has no
    # parseable card page id rather than raising.
    assert factory_telemetry._parse_card_page_id("schedule-sync-loop") is None


def test_runs_state_filter_passes_execution_status_into_query(client, fake_client):
    resp = client.get(
        "/v1/admin/factory/runs", params={"state": "Failed"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    assert fake_client.last_query == (
        "WorkflowType='EpicExecutionWorkflow' AND ExecutionStatus='Failed'"
    )


def test_runs_counts_by_status_sums_to_len_items(client):
    resp = client.get("/v1/admin/factory/runs", headers=ADMIN_HEADERS)
    body = resp.json()
    assert sum(body["counts_by_status"].values()) == len(body["items"])
    assert body["counts_by_status"] == {"COMPLETED": 1, "FAILED": 1, "RUNNING": 1}


# ---------------------------------------------------------------------------
# GET /v1/admin/factory/runs/{workflow_id}
# ---------------------------------------------------------------------------


def _activity_events():
    """A scheduled+completed pair and a scheduled+failed pair."""
    from google.protobuf.timestamp_pb2 import Timestamp
    from temporalio.api.common.v1 import ActivityType
    from temporalio.api.enums.v1 import EventType
    from temporalio.api.failure.v1 import Failure
    from temporalio.api.history.v1 import (
        ActivityTaskCompletedEventAttributes,
        ActivityTaskFailedEventAttributes,
        ActivityTaskScheduledEventAttributes,
        HistoryEvent,
    )

    def ts(minutes_offset):
        t = Timestamp()
        t.FromDatetime(_dt(minutes_offset))
        return t

    return [
        HistoryEvent(
            event_id=1,
            event_time=ts(0),
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,
            activity_task_scheduled_event_attributes=ActivityTaskScheduledEventAttributes(
                activity_type=ActivityType(name="run_lane_worker_agent"),
            ),
        ),
        HistoryEvent(
            event_id=2,
            event_time=ts(2),
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,
            activity_task_completed_event_attributes=ActivityTaskCompletedEventAttributes(
                scheduled_event_id=1,
            ),
        ),
        HistoryEvent(
            event_id=3,
            event_time=ts(5),
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,
            activity_task_scheduled_event_attributes=ActivityTaskScheduledEventAttributes(
                activity_type=ActivityType(name="run_reviewer_agent"),
            ),
        ),
        HistoryEvent(
            event_id=4,
            event_time=ts(6),
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED,
            activity_task_failed_event_attributes=ActivityTaskFailedEventAttributes(
                scheduled_event_id=3,
                failure=Failure(message="lint failed"),
            ),
        ),
    ]


def test_run_detail_summarises_activities(fake_client, monkeypatch):
    fake_client._history_events = _activity_events()
    monkeypatch.setattr(admin_module, "ADMIN_PASSWORD", "test-secret")
    app_module.app.dependency_overrides[factory_telemetry.get_temporal_client] = (
        lambda: fake_client
    )
    try:
        tc = TestClient(app_module.app)
        resp = tc.get(
            "/v1/admin/factory/runs/agent-task-3d137ffe-abcd",
            headers=ADMIN_HEADERS,
        )
    finally:
        app_module.app.dependency_overrides.pop(
            factory_telemetry.get_temporal_client, None
        )

    assert resp.status_code == 200
    body = resp.json()
    activities = body["activities"]
    assert len(activities) == 2

    completed = activities[0]
    assert completed["activity"] == "run_lane_worker_agent"
    assert completed["status"] == "completed"
    assert completed["duration_s"] == pytest.approx(120.0)
    assert completed["failure_message"] is None

    failed = activities[1]
    assert failed["activity"] == "run_reviewer_agent"
    assert failed["status"] == "failed"
    assert failed["duration_s"] == pytest.approx(60.0)
    assert failed["failure_message"] == "lint failed"


# ---------------------------------------------------------------------------
# Real dependency (no override): auth before connecting, 503 when unreachable
# ---------------------------------------------------------------------------


@pytest.fixture()
def raw_client(monkeypatch):
    monkeypatch.setattr(admin_module, "ADMIN_PASSWORD", "test-secret")
    calls = []

    async def boom(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("connection refused")

    from temporalio.client import Client

    monkeypatch.setattr(Client, "connect", boom)
    return TestClient(app_module.app), calls


@pytest.mark.parametrize("path", ["/v1/admin/factory/runs", "/v1/admin/factory/runs/agent-task-x"])
def test_unauthenticated_gets_401_without_touching_temporal(raw_client, path):
    http, calls = raw_client
    assert http.get(path).status_code == 401
    assert calls == []


@pytest.mark.parametrize("path", ["/v1/admin/factory/runs", "/v1/admin/factory/runs/agent-task-x"])
def test_unreachable_temporal_is_503_not_500(raw_client, path):
    http, calls = raw_client
    resp = http.get(path, headers=ADMIN_HEADERS)
    assert resp.status_code == 503
    assert "Temporal" in resp.json()["detail"]
    assert len(calls) == 1
