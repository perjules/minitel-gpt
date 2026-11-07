"""One-shot gateway pairing and branch-scoped terminal maintenance."""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_airzone_demo import envelope
from test_terminal_updates import checked, finished_worker

from services import airzone_hack as hack
from services import minitel_chatgpt as chat


@pytest.fixture
def receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(hack, "ROOT", tmp_path)
    monkeypatch.setenv("AIRZONE_GATEWAY", "")
    source = SimpleNamespace(airzone_run_id="a" * 32, airzone_evidence_dir=tmp_path,
                             closed=True, cancelled=False, process=SimpleNamespace(returncode=0))
    report = {"run_id": source.airzone_run_id, "status": "complete", "demo": False,
              "diagnostics": [{"exit_code": 0, "result": envelope(real=True)}]}
    path = tmp_path / "minitel-last-real.json"
    path.write_text(json.dumps(report))
    return source, report, path


@pytest.mark.parametrize("reason", ["stale_run", "failed", "cancelled", "schema", "injected_host", "malformed"])
def test_invalid_old_or_cancelled_receipts_never_offer_configuration(receipt, monkeypatch, reason):
    source, report, path = receipt
    if reason == "stale_run":
        report["run_id"] = "b" * 32
    elif reason == "failed":
        report["status"] = "failed"
    elif reason == "cancelled":
        source.cancelled = True
    elif reason == "schema":
        report["diagnostics"][0]["result"]["schema_version"] = 2
    elif reason == "injected_host":
        report["diagnostics"][0]["result"]["target"]["host"] = "192.168.1.50; whoami"
    else:
        report = []
    path.write_text(json.dumps(report))
    read = Mock()
    monkeypatch.setattr(chat, "read_question", read)
    assert hack.verified_state(source) is None
    read.assert_not_called()
    assert not (path.parent / ".env").exists()


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repositories(tmp_path):
    project, remote, checkout = tmp_path / "minitel-gpt", tmp_path / "remote", tmp_path / "airzone"
    for path, branch in ((project, hack.UPDATE_BRANCH), (remote, "develop")):
        path.mkdir()
        git(path, "init", "-b", branch)
        git(path, "config", "user.name", "Synthetic")
        git(path, "config", "user.email", "test@example.invalid")
        (path / "tracked.txt").write_text("old")
        git(path, "add", ".")
        git(path, "commit", "-m", "Initial")
    git(tmp_path, "clone", str(remote), str(checkout))
    git(checkout, "config", "user.name", "Synthetic")
    git(checkout, "config", "user.email", "test@example.invalid")
    (remote / "tracked.txt").write_text("new")
    git(remote, "commit", "-am", "New diagnostics")
    return project, remote, checkout


def test_sibling_checkout_updates_even_with_feature_disabled_and_repeats_when_current(repositories, monkeypatch):
    project, remote, checkout = repositories
    monkeypatch.setenv("AIRZONE_DEMO_ENABLED", "0")
    for _ in range(2):
        assert hack.update_checkout(project, hack.UPDATE_BRANCH)["ok"]
        assert git(checkout, "rev-parse", "HEAD") == git(remote, "rev-parse", "HEAD")
    assert (checkout / "tracked.txt").read_text() == "new"
    assert git(project, "branch", "--show-current") == hack.UPDATE_BRANCH


@pytest.mark.parametrize("local", ["dirty", "diverged", "wrong_airzone_branch", "wrong_minitel_branch"])
def test_sibling_git_problems_are_reported_without_discarding_work(repositories, local):
    project, _, checkout = repositories
    if local in {"dirty", "diverged"}:
        (checkout / "tracked.txt").write_text("preserve local work")
        if local == "diverged":
            git(checkout, "commit", "-am", "Local work")
    elif local == "wrong_airzone_branch":
        git(checkout, "switch", "-c", "other")
    else:
        git(project, "switch", "-c", "develop")
    before = git(checkout, "rev-parse", "HEAD")
    assert not hack.update_checkout(project, hack.UPDATE_BRANCH)["ok"]
    assert git(checkout, "rev-parse", "HEAD") == before
    if local in {"dirty", "diverged"}:
        assert (checkout / "tracked.txt").read_text() == "preserve local work"


@pytest.mark.parametrize("branch", ["develop", "main", "feature/other"])
def test_other_minitel_branches_never_touch_airzone(tmp_path, monkeypatch, branch):
    run = Mock()
    monkeypatch.setattr(hack.deployment, "git", run)
    assert hack.update_checkout(tmp_path, branch) == {"ok": True, "skipped": True}
    run.assert_not_called()


@pytest.mark.parametrize("behind", [0, 2])
@pytest.mark.parametrize("confirmed", [False, True])
def test_maj_branch_hook_runs_after_confirmation_even_when_minitel_is_current(monkeypatch, behind, confirmed):
    preview = {**checked(behind), "branch": hack.UPDATE_BRANCH}
    def request(language, current=None):
        return finished_worker({"display": "Module Airzone inclus.", "preview": preview} if current is None else
                               {"rows": ["Procedure locale."]})
    monkeypatch.setattr(chat, "start_update_request", request)
    monkeypatch.setattr(chat, "show_response", Mock(return_value="done"))
    monkeypatch.setattr(chat, "read_question", Mock(return_value=("OUI" if confirmed else "NON", "envoi")))
    monkeypatch.setattr(chat.deployment, "unchanged", Mock(return_value=True))
    sibling = Mock(side_effect=lambda *args: finished_worker({"ok": True, "display": "Airzone synchronisé."}))
    monkeypatch.setattr(chat, "start_airzone_update", sibling)
    queue = Mock(return_value=(True, "queued"))
    monkeypatch.setattr(chat.deployment, "queue_deployment", queue)
    monkeypatch.setattr(chat.mainframe_update, "arm_resume", Mock())
    monkeypatch.setattr(chat, "play_update", Mock(return_value=("done", "")))
    chat.terminal_update(Mock(), "fr")
    assert sibling.call_count == int(confirmed)
    assert queue.call_count == int(confirmed and behind > 0)


def test_failed_airzone_sync_does_not_claim_or_queue_minitel_success(monkeypatch):
    preview = {**checked(), "branch": hack.UPDATE_BRANCH}
    monkeypatch.setattr(chat, "start_update_request", lambda language: finished_worker({"display": "Preview", "preview": preview}))
    show = Mock(return_value="done")
    monkeypatch.setattr(chat, "show_response", show)
    monkeypatch.setattr(chat, "read_question", Mock(return_value=("OUI", "envoi")))
    monkeypatch.setattr(chat.deployment, "unchanged", Mock(return_value=True))
    monkeypatch.setattr(chat, "start_airzone_update", lambda *args: finished_worker({"ok": False, "display": "Git Airzone échoué. SSH."}))
    queue = Mock()
    monkeypatch.setattr(chat.deployment, "queue_deployment", queue)
    chat.terminal_update(Mock(), "fr")
    queue.assert_not_called()
    assert "SSH" in show.call_args.args[1]
