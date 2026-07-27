"""Tests for the ``create_pull_request`` Temporal activity.

These tests exercise the real activity code path but control the subprocess
boundary: actually running ``git push`` / ``gh pr create`` would push to a real
remote and open a real pull request, so we substitute a fake subprocess that
records the shell commands the activity issues and returns scripted exit codes.
The activity under test is *not* mocked -- only ``asyncio.create_subprocess_shell``.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from temporalio.exceptions import ApplicationError

from irc_data.temporal.orchestrator import activities


class _FakeProc:
    """A minimal stand-in for the object returned by create_subprocess_shell."""

    def __init__(self, shell_cmd: str, returncode: int = 0,
                 stdout: bytes = b"", stderr: bytes = b""):
        self._shell_cmd = shell_cmd
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


def _install_fake_subprocess(monkeypatch, calls, scripts):
    """Patch ``asyncio.create_subprocess_shell`` to record commands and replay.

    ``scripts`` maps a substring -> (returncode, stdout, stderr). The first
    matching script is used so the order of commands is deterministic.
    """
    real = asyncio.create_subprocess_shell

    async def fake(cmd, **kwargs):
        calls.append(cmd)
        for needle, (rc, out, err) in scripts.items():
            if needle in cmd:
                return _FakeProc(cmd, returncode=rc, stdout=out, stderr=err)
        # Default to success with empty output.
        return _FakeProc(cmd, returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake)
    return real


@pytest.mark.asyncio
async def test_push_and_pr_commands_match_spec(monkeypatch):
    """The activity must run exactly the two commands from the spec, in order."""
    worktree_path = "/tmp/worktrees/issue-03"
    calls = []
    _install_fake_subprocess(
        monkeypatch,
        calls,
        {
            "git -C": (0, b"", b""),
            "gh pr create": (0, b"https://github.com/irc-data/sailratings/pull/1\n", b""),
        },
    )

    result = await activities.create_pull_request(worktree_path)

    assert result is None
    assert len(calls) == 2
    assert calls[0] == f"git -C {worktree_path} push -u origin HEAD"
    assert calls[1] == (
        'gh pr create --title "Automated PR" --body "Evidence verified." '
        "--head feature/issue-03 --repo irc-data/sailratings"
    )


@pytest.mark.asyncio
async def test_branch_name_derived_from_worktree_basename(monkeypatch):
    """The PR --head must be feature/{basename(worktree_path)}."""
    worktree_path = os.path.join("/srv/worktrees", "abc-123")
    calls = []
    _install_fake_subprocess(
        monkeypatch,
        calls,
        {
            "git -C": (0, b"", b""),
            "gh pr create": (0, b"", b""),
        },
    )

    await activities.create_pull_request(worktree_path)

    pr_cmd = [c for c in calls if c.startswith("gh pr create")][0]
    assert "--head feature/abc-123 " in pr_cmd + " "


@pytest.mark.asyncio
async def test_push_failure_raises_application_error(monkeypatch):
    """A non-zero push exit code must surface as ApplicationError."""
    calls = []
    _install_fake_subprocess(
        monkeypatch,
        calls,
        {
            "git -C": (1, b"", b"permission denied"),
            "gh pr create": (0, b"", b""),
        },
    )

    with pytest.raises(ApplicationError, match="Failed to push branch to origin"):
        await activities.create_pull_request("/tmp/worktrees/issue-03")

    # PR creation must not be attempted when the push failed.
    assert not any(c.startswith("gh pr create") for c in calls)


@pytest.mark.asyncio
async def test_gh_create_failure_raises_application_error(monkeypatch):
    """A non-zero ``gh pr create`` exit code must surface as ApplicationError."""
    calls = []
    _install_fake_subprocess(
        monkeypatch,
        calls,
        {
            "git -C": (0, b"", b""),
            "gh pr create": (1, b"", b"already exists"),
        },
    )

    with pytest.raises(ApplicationError, match="Failed to create pull request"):
        await activities.create_pull_request("/tmp/worktrees/issue-03")
