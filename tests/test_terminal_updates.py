"""Offline update preflight, theatrical boundaries and physical confirmation."""
import json
import subprocess
from unittest.mock import Mock

import pytest

from services import deployment


def test_real_local_remote_preview_uses_installed_branch_and_preserves_checkout(tmp_path):
    remote, checkout = tmp_path / "remote", tmp_path / "checkout"

    def git(path, *args):
        return subprocess.run(["git", "-C", str(path), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    remote.mkdir()
    git(remote, "init", "-b", "develop")
    git(remote, "config", "user.name", "Synthetic")
    git(remote, "config", "user.email", "test@example.invalid")
    (remote / "test.txt").write_text("first")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "Initial")
    git(tmp_path, "clone", str(remote), str(checkout))
    head = git(checkout, "rev-parse", "HEAD")
    runtime, state = tmp_path / "runtime.json", tmp_path / "deployment.json"
    runtime.write_text(json.dumps({"branch": "develop", "active": {"commit": head}}))
    state.write_text('{"phase":"ready"}')
    (remote / "test.txt").write_text("second")
    git(remote, "commit", "-am", "Better messages")
    preview = deployment.preview(checkout, runtime, state)
    assert preview == {"ok": True, "behind": 1, "log": "Better messages", "head": head,
                       "branch": "develop", "target": git(remote, "rev-parse", "HEAD")}
    assert git(checkout, "rev-parse", "HEAD") == head
    assert (checkout / "test.txt").read_text() == "first"
    # A real tracked edit is refused; no stash/reset/checkout is attempted.
    (checkout / "test.txt").write_text("owner edit")
    assert not deployment.preview(checkout, runtime, state)["ok"]
    assert (checkout / "test.txt").read_text() == "owner edit"


@pytest.mark.parametrize("failure", ["missing", "branch", "head", "dirty", "fetch", "diverged", "busy"])
def test_failed_preflight_never_returns_changes_to_the_model(tmp_path, monkeypatch, failure):
    head, target = "a" * 40, "b" * 40
    runtime, status = tmp_path / "runtime.json", tmp_path / "status.json"
    if failure != "missing":
        runtime.write_text(json.dumps({"branch": "develop", "active": {"commit": head}}))
    status.write_text(json.dumps({"phase": "preparing" if failure == "busy" else "ready"}))

    def git(project, *args, **kwargs):
        if args[0] == "branch":
            return 0, "main" if failure == "branch" else "develop", ""
        if args[:2] == ("rev-parse", "HEAD"):
            return 0, target if failure == "head" else head, ""
        if args[0] == "status":
            return 0, " M test" if failure == "dirty" else "", ""
        if args[0] == "fetch":
            return (1 if failure == "fetch" else 0), "", "private remote diagnostics"
        if args[0] == "rev-list":
            return 0, "1", ""
        if args[0] == "log":
            return 0, "Synthetic change", ""
        if args[0] == "rev-parse":
            return 0, target, ""
        return (1 if failure == "diverged" else 0), "", ""

    run = Mock(side_effect=git)
    monkeypatch.setattr(deployment, "git", run)
    result = deployment.preview(tmp_path, runtime, status)
    assert result["ok"] is False and "log" not in result
    if failure in ("missing", "busy"):
        run.assert_not_called()


def test_git_timeout_kills_transport_group(monkeypatch, tmp_path):
    process = Mock(pid=123, returncode=1)
    process.communicate.side_effect = [subprocess.TimeoutExpired("git", 5), ("", "")]
    spawn, kill = Mock(return_value=process), Mock()
    monkeypatch.setattr(deployment.subprocess, "Popen", spawn)
    monkeypatch.setattr(deployment.os, "killpg", kill)
    assert deployment.git(tmp_path, "fetch", "origin", "develop", timeout=5)[0] == 1
    kill.assert_called_once_with(123, deployment.signal.SIGKILL)
    assert spawn.call_args.kwargs["start_new_session"] is True


def test_shared_queue_only_launches_fixed_helper(monkeypatch):
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(deployment.subprocess, "run", run)
    assert deployment.queue_deployment("update")[0]
    assert run.call_args.args[0] == ["sudo", "-n", "/usr/local/sbin/minitel-runtime", "update"]
    assert run.call_args.kwargs["timeout"] == 15
    assert not deployment.queue_deployment("apply-update")[0]
    assert run.call_count == 1
