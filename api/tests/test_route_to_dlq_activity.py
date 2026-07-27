"""Tests for the ``route_to_dlq`` Temporal activity.

These tests exercise the real activity code path but control the filesystem
boundary: writing to the production DLQ path would pollute the real dead-letter
queue with test data, so we redirect the module-level ``DLQ_PATH`` constant to a
temporary file. The activity under test is *not* mocked -- only the destination
path is swapped.
"""
from __future__ import annotations

import json
import os

import pytest

from irc_data.temporal.orchestrator import activities


PROD_DLQ_PATH = "/home/irc-data/code/sailratings/worktrees/dlq.jsonl"


def test_dlq_path_constant_matches_contract():
    """The production DLQ path must be the one required by the spec."""
    assert activities.DLQ_PATH == PROD_DLQ_PATH


@pytest.mark.asyncio
async def test_failed_task_payload_appended_as_one_json_line(monkeypatch, tmp_path):
    """A failed task payload is appended as a single JSON line to the DLQ file."""
    dlq_file = tmp_path / "dlq.jsonl"
    monkeypatch.setattr(activities, "DLQ_PATH", str(dlq_file))

    payload = {"error": "budget limit reached", "task": {"id": "T-42", "desc": "fix bug"}}

    await activities.route_to_dlq(payload)

    assert dlq_file.exists()
    lines = dlq_file.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == payload


@pytest.mark.asyncio
async def test_multiple_failures_append_not_overwrite(monkeypatch, tmp_path):
    """Repeated failures append; they must not clobber earlier entries."""
    dlq_file = tmp_path / "dlq.jsonl"
    monkeypatch.setattr(activities, "DLQ_PATH", str(dlq_file))

    first = {"error": "boom", "task": {"id": "T-1"}}
    second = {"error": "kaboom", "task": {"id": "T-2"}}

    await activities.route_to_dlq(first)
    await activities.route_to_dlq(second)

    lines = dlq_file.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == first
    assert json.loads(lines[1]) == second


@pytest.mark.asyncio
async def test_existing_dlq_file_is_preserved(monkeypatch, tmp_path):
    """A pre-existing DLQ file is extended, not truncated."""
    dlq_file = tmp_path / "dlq.jsonl"
    preexisting = {"error": "old", "task": {"id": "T-0"}}
    dlq_file.write_text(json.dumps(preexisting) + "\n")
    monkeypatch.setattr(activities, "DLQ_PATH", str(dlq_file))

    new_payload = {"error": "new", "task": {"id": "T-1"}}
    await activities.route_to_dlq(new_payload)

    lines = dlq_file.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == preexisting
    assert json.loads(lines[1]) == new_payload


@pytest.mark.asyncio
async def test_unwritable_dlq_path_raises(monkeypatch, tmp_path):
    """An unwritable DLQ path surfaces as a raised exception (no silent drop)."""
    # Point at a path inside a non-existent directory with no parent to create it.
    monkeypatch.setattr(activities, "DLQ_PATH", str(tmp_path / "no_such_dir" / "dlq.jsonl"))

    with pytest.raises(Exception):
        await activities.route_to_dlq({"error": "x", "task": {"id": "T-9"}})
