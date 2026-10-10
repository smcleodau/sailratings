"""AD-01-19 — Factory telemetry API.

Lists every ``EpicExecutionWorkflow`` run tracked by the Temporal server the
factory orchestrator talks to, so the admin can see workflow state, duration
and failure reason from one endpoint without opening the Temporal UI.

  GET /admin/factory/runs              — list runs (optional ``state`` filter)
  GET /admin/factory/runs/{workflow_id} — last 50 history events, summarised
                                          by activity

Auth is the shared admin bearer credential (``_verify_admin``). The screen
that consumes this is AD-01-19b (gated on a prototype) — this card is API
only.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from irc_data.api.routers.admin import _verify_admin

router = APIRouter(prefix="/admin/factory", tags=["Admin"])

WORKFLOW_TYPE = "EpicExecutionWorkflow"
CARD_PAGE_ID_PREFIX = "agent-task-"

# Activity-related Temporal event type names we summarise in run detail.
_SCHEDULED = "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"
_COMPLETED = "EVENT_TYPE_ACTIVITY_TASK_COMPLETED"
_FAILED = "EVENT_TYPE_ACTIVITY_TASK_FAILED"
_TIMED_OUT = "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT"
_CANCELED = "EVENT_TYPE_ACTIVITY_TASK_CANCELED"


async def get_temporal_client(authorization: str = Header(None)):
    """FastAPI dependency returning a connected Temporal client.

    Isolated as its own dependency (rather than called inline) so tests can
    override it with a fake client via ``app.dependency_overrides`` instead
    of talking to a real Temporal server.

    Auth is checked here, before connecting: dependencies run before the
    handler body, so an unauthenticated request used to reach Temporal first,
    and where none is reachable (Railway prod) every caller got a bare 500.
    """
    from temporalio.client import Client

    _verify_admin(authorization)
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "sailratings")
    try:
        return await Client.connect(address, namespace=namespace)
    except Exception:
        raise HTTPException(status_code=503, detail=f"Temporal not reachable at {address}")


def _parse_card_page_id(workflow_id: str) -> str | None:
    """Parse the Notion card page id out of a ``agent-task-<id>`` workflow id."""
    if workflow_id and workflow_id.startswith(CARD_PAGE_ID_PREFIX):
        return workflow_id[len(CARD_PAGE_ID_PREFIX):]
    return None


def _status_name(status: Any) -> str | None:
    if status is None:
        return None
    # temporalio.client.WorkflowExecutionStatus is an IntEnum; ``.name`` gives
    # e.g. "RUNNING" / "COMPLETED" / "FAILED".
    return getattr(status, "name", str(status))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _duration_s(start: datetime | None, close: datetime | None) -> float | None:
    if start is None or close is None:
        return None
    return (close - start).total_seconds()


def _event_time(event: Any) -> datetime | None:
    ts = getattr(event, "event_time", None)
    if ts is None:
        return None
    if hasattr(ts, "ToDatetime"):
        return ts.ToDatetime(tzinfo=timezone.utc)
    return ts


def _event_type_name(event: Any) -> str:
    et = getattr(event, "event_type", None)
    name = getattr(et, "name", None)
    if name:
        return name
    # Real protobuf enums resolve via the module-level EventType descriptor.
    try:
        from temporalio.api.enums.v1 import EventType

        return EventType.Name(et)
    except Exception:
        return str(et)


def _summarize_activities(events: list[Any]) -> list[dict[str, Any]]:
    """Summarise the activity-related events in a slice of workflow history."""
    scheduled: dict[int, dict[str, Any]] = {}
    order: list[int] = []

    for event in events:
        event_type = _event_type_name(event)
        event_id = getattr(event, "event_id", None)

        if event_type == _SCHEDULED:
            attrs = event.activity_task_scheduled_event_attributes
            scheduled[event_id] = {
                "activity": attrs.activity_type.name,
                "status": "scheduled",
                "_start": _event_time(event),
                "duration_s": None,
                "failure_message": None,
            }
            order.append(event_id)
            continue

        if event_type == _COMPLETED:
            attrs = event.activity_task_completed_event_attributes
            rec = scheduled.get(attrs.scheduled_event_id)
            if rec is not None:
                rec["status"] = "completed"
                rec["duration_s"] = _duration_s(rec["_start"], _event_time(event))
            continue

        if event_type == _FAILED:
            attrs = event.activity_task_failed_event_attributes
            rec = scheduled.get(attrs.scheduled_event_id)
            if rec is not None:
                rec["status"] = "failed"
                rec["duration_s"] = _duration_s(rec["_start"], _event_time(event))
                rec["failure_message"] = attrs.failure.message
            continue

        if event_type == _TIMED_OUT:
            attrs = event.activity_task_timed_out_event_attributes
            rec = scheduled.get(attrs.scheduled_event_id)
            if rec is not None:
                rec["status"] = "timed_out"
                rec["duration_s"] = _duration_s(rec["_start"], _event_time(event))
            continue

        if event_type == _CANCELED:
            attrs = event.activity_task_canceled_event_attributes
            rec = scheduled.get(attrs.scheduled_event_id)
            if rec is not None:
                rec["status"] = "canceled"
                rec["duration_s"] = _duration_s(rec["_start"], _event_time(event))
            continue

    summary = []
    for event_id in order:
        rec = dict(scheduled[event_id])
        rec.pop("_start", None)
        summary.append(rec)
    return summary


@router.get("/runs")
async def list_runs(
    state: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    authorization: str = Header(None),
    client: Any = Depends(get_temporal_client),
):
    """List ``EpicExecutionWorkflow`` runs, newest first per Temporal's default.

    ``state`` is an optional Temporal ``ExecutionStatus`` filter value (e.g.
    ``Running``, ``Completed``, ``Failed``).
    """
    _verify_admin(authorization)

    query = f"WorkflowType='{WORKFLOW_TYPE}'"
    if state:
        query += f" AND ExecutionStatus='{state}'"

    items: list[dict[str, Any]] = []
    counts_by_status: dict[str, int] = {}

    async for wf in client.list_workflows(query, limit=limit):
        status = _status_name(wf.status)
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        items.append(
            {
                "workflow_id": wf.id,
                "card_page_id": _parse_card_page_id(wf.id),
                "status": status,
                "start_time": _iso(wf.start_time),
                "close_time": _iso(wf.close_time),
                "duration_s": _duration_s(wf.start_time, wf.close_time),
            }
        )

    return {"items": items, "counts_by_status": counts_by_status}


@router.get("/runs/{workflow_id}")
async def run_detail(
    workflow_id: str,
    authorization: str = Header(None),
    client: Any = Depends(get_temporal_client),
):
    """Last 50 history events for one run, summarised by activity."""
    _verify_admin(authorization)

    handle = client.get_workflow_handle(workflow_id)
    history = await handle.fetch_history()
    events = list(history.events)[-50:]

    return {
        "workflow_id": workflow_id,
        "activities": _summarize_activities(events),
    }
