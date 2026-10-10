import subprocess
import time

from irc_data.temporal.orchestrator.activities import _kill_processes_in


def _alive(proc: subprocess.Popen) -> bool:
    return proc.poll() is None


def test_kills_processes_whose_cwd_is_inside_the_worktree(tmp_path):
    worktree = tmp_path / "worktrees" / "abc"
    (worktree / "api").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    at_root = subprocess.Popen(["sleep", "300"], cwd=worktree)
    in_subdir = subprocess.Popen(["sleep", "300"], cwd=worktree / "api")
    unrelated = subprocess.Popen(["sleep", "300"], cwd=outside)
    try:
        time.sleep(0.2)
        killed = _kill_processes_in(str(worktree))
        time.sleep(0.2)
        assert set(killed) == {at_root.pid, in_subdir.pid}
        assert not _alive(at_root)
        assert not _alive(in_subdir)
        assert _alive(unrelated)
    finally:
        for p in (at_root, in_subdir, unrelated):
            p.kill()
            p.wait()


def test_does_not_match_sibling_with_shared_prefix(tmp_path):
    worktree = tmp_path / "abc"
    sibling = tmp_path / "abcdef"
    worktree.mkdir()
    sibling.mkdir()
    proc = subprocess.Popen(["sleep", "300"], cwd=sibling)
    try:
        time.sleep(0.2)
        assert _kill_processes_in(str(worktree)) == []
        assert _alive(proc)
    finally:
        proc.kill()
        proc.wait()


def test_kills_process_ignoring_sigterm(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    stubborn = subprocess.Popen(
        ["bash", "-c", "trap '' TERM HUP; sleep 300 & wait"], cwd=worktree
    )
    try:
        time.sleep(0.3)
        _kill_processes_in(str(worktree), grace_seconds=1)
        time.sleep(0.3)
        assert not _alive(stubborn)
    finally:
        stubborn.kill()
        stubborn.wait()
