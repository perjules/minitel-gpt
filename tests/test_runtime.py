"""Deployment transactions in temporary directories; no root/systemd/network required."""
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import runtime
from services import admin_ui as admin


@pytest.fixture
def manager(tmp_path, monkeypatch):
    instance = runtime.Runtime(tmp_path / "checkout", tmp_path / "opt", tmp_path / "state",
                               tmp_path / "bin/uv", tmp_path / "system")
    instance.project.mkdir()
    instance.root.mkdir()
    instance.state.mkdir()
    monkeypatch.setattr(instance, "command", Mock(return_value=SimpleNamespace(stdout="")))
    monkeypatch.setattr(instance, "git", Mock(return_value="old-commit"))
    monkeypatch.setattr(instance, "running", lambda: ["minitel-chatgpt", "admin-ui"])
    monkeypatch.setattr(runtime.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    return instance


def release(manager, name, extras=()):
    path = manager.root / "releases" / name
    (path / "venv/bin").mkdir(parents=True)
    (path / "venv/bin/python").touch()
    (path / "root").mkdir()
    (path / "project").mkdir()
    (path / "project/pyproject.toml").write_text("test release metadata")
    (path / "project/uv.lock").write_text("test release lock")
    for service in runtime.SERVICES:
        (path / f"root/{service}.service").write_text(f"[Service]\n# {name}\n")
    source = Path(__file__).resolve().parents[1]
    for asset in runtime.SYSTEM_FILES:
        (path / "root" / asset).write_bytes((source / "config" / asset).read_bytes())
    (path / "root/journal_reader.py").write_bytes((source / "scripts/journal_reader.py").read_bytes())
    return {"release": str(path), "commit": name, "extras": list(extras), "python": ""}


def fail_once(manager, predicate):
    failed = False

    def command(args, **kwargs):
        nonlocal failed
        if not failed and predicate(args):
            failed = True
            raise subprocess.CalledProcessError(1, args)
        return SimpleNamespace(stdout="")

    manager.command.side_effect = command


def test_feature_installs_preserve_union_and_reject_unknown(manager):
    old = {"active": {"extras": ["gmail", "pronote"]}}
    assert manager.selected_extras(["printer"], old) == ["gmail", "printer", "pronote"]
    assert manager.selected_extras([], old) == ["gmail", "pronote"]
    with pytest.raises(ValueError):
        manager.selected_extras(["unknown"], old)


def test_legacy_detection_runs_as_unprivileged_user(manager):
    legacy = manager.project / ".venv/bin/python"
    legacy.parent.mkdir(parents=True)
    legacy.touch()
    manager.command.return_value.stdout = '["gmail", "printer"]'
    assert manager.selected_extras(["pronote"], {}) == ["gmail", "printer", "pronote"]
    assert manager.command.call_args.args[0][:5] == ["runuser", "-u", "minitel", "--", legacy]


def test_preparation_failure_cleans_only_candidate_without_switching(manager, monkeypatch):
    source = Path(__file__).resolve().parents[1]
    before = release(manager, "old")
    manager.activate(before)
    monkeypatch.setenv("UV_INDEX_URL", "https://untrusted.invalid")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    fail_once(manager, lambda args: args[0] == manager.uv)
    with pytest.raises(subprocess.CalledProcessError):
        manager.prepare(source, ["gmail"], "new")
    assert (manager.root / "current").resolve() == Path(before["release"])
    assert [path.name for path in (manager.root / "releases").iterdir()] == ["old"]
    args, = manager.command.call_args.args
    assert "--locked" in args and "--no-dev" in args and "--no-sources" in args
    assert args[-2:] == ["--extra", "gmail"]
    env = manager.command.call_args.kwargs["env"]
    assert "PYTHONPATH" not in env and "UV_INDEX_URL" not in env
    assert env["UV_PYTHON_DOWNLOADS"] == "never"


def test_preparation_copies_protected_code_and_checks_optional_imports(manager):
    source = Path(__file__).resolve().parents[1]
    candidate = manager.prepare(source, ["gmail", "printer"], "new")
    protected = Path(candidate["release"]) / "root"
    assert (protected / "wifi_manager.py").read_bytes() == (source / "services/wifi_manager.py").read_bytes()
    probe = manager.command.call_args.args[0]
    assert probe[1:3] == ["-I", "-c"]
    assert "cryptography" in probe[-1] and "PIL" in probe[-1]
    assert "pronotepy" not in probe[-1]


def test_successful_switch_records_matching_pair_and_preserves_credentials(manager):
    before, after = release(manager, "old", ["gmail"]), release(manager, "new", ["gmail", "printer"])
    manager.activate(before)
    folder = manager.system_path("/etc/systemd/system/admin-ui.service.d")
    folder.mkdir(parents=True)
    (folder / "gmail.conf").write_text("[Service]\nExecStart=old-python\nLoadCredentialEncrypted=gmail:secret\n")
    (folder / "pronote.conf").write_text("[Service]\nExecStart=other-python\n")
    old = {"active": before, "branch": "develop"}
    manager.switch(after, old, branch="develop", target="new")
    state = manager.read()
    assert state["active"] == after and state["previous"] == before
    assert (manager.root / "venv").resolve() == Path(after["release"]) / "venv"
    assert (manager.project / ".venv").resolve() == Path(after["release"]) / "venv"
    text = (folder / "gmail.conf").read_text()
    assert "LoadCredentialEncrypted=gmail:/etc/credstore.encrypted/minitel-gmail" in text
    assert "ExecStart" not in text and not (folder / "pronote.conf").exists()
    wrapper = manager.system_path("/usr/local/libexec/minitel-gmail-credential").read_text()
    assert "/opt/minitel-gpt/venv/bin/python -I" in wrapper


def test_failed_service_start_restores_code_environment_units_and_state(manager):
    before, after = release(manager, "old"), release(manager, "new")
    manager.activate(before)
    old = {"active": before, "branch": "develop", "previous": None}
    runtime.atomic_json(manager.state / "runtime.json", old)
    unit = manager.system_path("/etc/systemd/system/admin-ui.service")
    unit.parent.mkdir(parents=True)
    unit.write_text("original unit\n")
    fail_once(manager, lambda args: args[:2] == ["systemctl", "start"])
    with pytest.raises(subprocess.CalledProcessError):
        manager.switch(after, old, branch="develop", target="new")
    assert manager.read() == old
    assert (manager.root / "current").resolve() == Path(before["release"])
    assert unit.read_text() == "original unit\n"
    assert manager.git.call_args.args == ("reset", "--hard", "old")
    assert any(call.args[0][:2] == ["systemctl", "restart"] for call in manager.command.call_args_list)


def test_failed_first_migration_restores_original_venv_and_helper(manager):
    legacy = manager.project / ".venv"
    legacy.mkdir()
    (legacy / "marker").write_text("original")
    helper = manager.system_path("/usr/local/libexec/minitel-gmail-credential")
    helper.parent.mkdir(parents=True)
    helper.write_text("old helper")
    fail_once(manager, lambda args: args[:2] == ["systemctl", "start"])
    with pytest.raises(subprocess.CalledProcessError):
        manager.switch(release(manager, "new"), {}, branch="develop")
    assert not legacy.is_symlink() and (legacy / "marker").read_text() == "original"
    assert not (manager.root / "current").exists()
    assert helper.read_text() == "old helper"
    assert not (manager.state / "runtime.json").exists()


def test_one_service_crashing_after_start_does_not_commit_the_release(manager):
    before, after = release(manager, "old"), release(manager, "new")
    old = {"active": before, "branch": "develop"}
    manager.activate(before)
    runtime.atomic_json(manager.state / "runtime.json", old)
    fail_once(manager, lambda args: args == ["systemctl", "is-active", "--quiet", "admin-ui"])
    with pytest.raises(subprocess.CalledProcessError):
        manager.switch(after, old, branch="develop", target="new")
    assert manager.read() == old
    assert (manager.root / "current").resolve() == Path(before["release"])


def test_repeated_install_preserves_previous_pair_without_rebuilding(manager, monkeypatch):
    before, active = release(manager, "old"), release(manager, "active", ["gmail", "printer"])
    old = {"active": active, "previous": before, "branch": "develop"}
    runtime.atomic_json(manager.state / "runtime.json", old)
    manager.activate(active)
    manager.git.side_effect = lambda *args: "" if args[0] in {"status", "check-ref-format"} else "develop" if args[0] == "branch" else "active"
    prepare = Mock()
    monkeypatch.setattr(manager, "prepare", prepare)
    manager.install([], None)
    assert manager.read() == old
    prepare.assert_not_called()
    assert any(call.args[0] == ["apt-get", "install", "-y", "bluez", "fonts-dejavu-core", "libjpeg-dev", "zlib1g-dev"]
               for call in manager.command.call_args_list)


@pytest.mark.parametrize("cause", ["python_upgrade", "broken_import"])
def test_install_rebuilds_after_python_upgrade_or_failed_import(manager, monkeypatch, cause):
    active = release(manager, "active", ["gmail"])
    old = {"active": active, "previous": None, "branch": "develop"}
    runtime.atomic_json(manager.state / "runtime.json", old)
    manager.git.side_effect = lambda *args: "" if args[0] in {"status", "check-ref-format"} else "develop" if args[0] == "branch" else "active"
    monkeypatch.setattr(manager, "snapshot", Mock())
    candidate = release(manager, "replacement", ["gmail"])
    prepare, switch = Mock(return_value=candidate), Mock()
    monkeypatch.setattr(manager, "prepare", prepare)
    monkeypatch.setattr(manager, "switch", switch)
    if cause == "python_upgrade":
        monkeypatch.setattr(manager, "python_version", lambda: "new interpreter")
    else:
        monkeypatch.setattr(manager, "probe", Mock(side_effect=subprocess.CalledProcessError(1, ["python"])))
    manager.install([], None)
    prepare.assert_called_once()
    switch.assert_called_once_with(candidate, old, branch="develop")


def test_update_preparation_failure_never_resets_code_or_stops_services(manager, monkeypatch):
    before = release(manager, "old", ["pronote"])
    old = {"active": before, "branch": "develop"}
    runtime.atomic_json(manager.state / "runtime.json", old)

    def git(*args):
        if args == ("branch", "--show-current"):
            return "develop"
        if args[0] == "status":
            return ""
        return "new"

    manager.git.side_effect = git
    monkeypatch.setattr(manager, "snapshot", Mock())
    monkeypatch.setattr(manager, "prepare", Mock(side_effect=RuntimeError("synthetic install failure")))
    with pytest.raises(RuntimeError):
        manager.update()
    assert manager.read() == old
    assert not any(call.args[0] == "reset" for call in manager.git.call_args_list)
    manager.command.assert_not_called()


@pytest.mark.parametrize("dirty,branch", [(True, "develop"), (False, "other")])
def test_update_refuses_dirty_or_wrong_branch(manager, dirty, branch):
    runtime.atomic_json(manager.state / "runtime.json", {"active": release(manager, "old"), "branch": "develop"})
    manager.git.side_effect = lambda *args: branch if args[0] == "branch" else " M source.py" if dirty else ""
    with pytest.raises(RuntimeError):
        manager.update()
    assert not any(call.args[0] == "fetch" for call in manager.git.call_args_list)


def test_rollback_uses_retained_pair_with_its_feature_selection(manager, monkeypatch):
    before, after = release(manager, "old", ["gmail"]), release(manager, "new", ["gmail", "printer"])
    state = {"active": after, "previous": before, "branch": "develop"}
    runtime.atomic_json(manager.state / "runtime.json", state)
    manager.git.return_value = ""
    switch = Mock()
    monkeypatch.setattr(manager, "switch", switch)
    manager.rollback()
    switch.assert_called_once_with(before, state, branch="develop", target="old")


def test_missing_rollback_and_concurrent_operation_fail_without_mutation(manager):
    with pytest.raises(RuntimeError):
        manager.rollback()
    with manager.locked(), pytest.raises(BlockingIOError), manager.locked():
        pass


def test_snapshot_reads_only_required_committed_files(manager, tmp_path):
    manager.command.return_value.stdout = b"tracked content"
    destination = tmp_path / "snapshot"
    manager.snapshot("abc123", destination)
    assert (destination / "scripts/runtime.py").read_bytes() == b"tracked content"
    assert not (destination / ".env").exists()
    assert all("show" in call.args[0] for call in manager.command.call_args_list)


def test_admin_queues_without_claiming_completion_or_resetting_checkout(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(admin.subprocess, "run", run)
    ok, message = admin.do_update()
    assert ok and "planifiée" in message
    assert run.call_args.args[0] == ["sudo", "-n", "/usr/local/sbin/minitel-runtime", "update"]
    run.return_value.returncode = 1
    assert admin.do_rollback()[0] is False


def test_admin_branch_and_deployment_status_use_installed_state(tmp_path, monkeypatch):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"branch": "develop"}))
    monkeypatch.setattr(admin, "RUNTIME_STATE", path)
    monkeypatch.setattr(admin, "DEPLOYMENT_STATUS", tmp_path / "status.json")
    assert admin.runtime_branch() == "develop"
    client = admin.app.test_client()
    assert client.get("/deployment-status").status_code == 302
    with client.session_transaction() as session:
        session["logged_in"] = True
    assert client.get("/deployment-status").json == {"phase": "not_installed"}


def test_low_space_refuses_staging_before_creating_a_candidate(manager, monkeypatch):
    monkeypatch.setattr(runtime.shutil, "disk_usage",
                        lambda _: SimpleNamespace(free=runtime.MIN_STAGE_FREE - 1))
    with pytest.raises(RuntimeError, match="1 GiB"):
        manager.prepare(Path(__file__).resolve().parents[1], [], "new")
    assert not (manager.root / "releases").exists()
    manager.command.assert_not_called()


def test_housekeeping_retains_both_pairs_and_never_touches_user_logs(manager):
    before = release(manager, "1" * 32)
    active = release(manager, "2" * 32)
    stale = release(manager, "3" * 32)
    unexpected = release(manager, "manual-backup")
    manager.activate(active)
    runtime.atomic_json(manager.state / "runtime.json",
                        {"active": active, "previous": before})
    logs = manager.project / "logs"
    logs.mkdir()
    (logs / "chatgpt.log").write_text("old evidence")
    legacy = manager.project / ".venv.before-uv"
    legacy.mkdir()
    external = manager.project / "do-not-delete"
    external.mkdir()
    symlink = manager.root / "releases" / ("4" * 32)
    symlink.symlink_to(external, target_is_directory=True)
    (manager.state / "uv-cache").mkdir()
    manager.housekeeping()
    assert Path(active["release"]).is_dir() and Path(before["release"]).is_dir()
    assert not Path(stale["release"]).exists()
    assert Path(unexpected["release"]).exists() and external.exists()
    assert symlink.is_symlink() and legacy.exists()
    assert (logs / "chatgpt.log").read_text() == "old evidence"
    assert manager.command.call_args.args[0] == [
        manager.uv, "cache", "prune", "--cache-dir", manager.state / "uv-cache"]


def test_housekeeping_refuses_inconsistent_state_without_deleting_releases(manager):
    before = release(manager, "1" * 32)
    active = release(manager, "2" * 32)
    manager.activate(before)
    runtime.atomic_json(manager.state / "runtime.json", {"active": active})
    with pytest.raises(RuntimeError, match="pointer"):
        manager.housekeeping()
    assert Path(before["release"]).is_dir()
    manager.command.assert_not_called()


def test_failed_activation_restores_journal_policy_sudoers_and_helper(manager):
    before, after = release(manager, "old"), release(manager, "new")
    manager.activate(before)
    paths = [manager.system_path(path) for path in runtime.SYSTEM_FILES.values()]
    paths.append(manager.system_path("/usr/local/libexec/minitel-journal"))
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
        path.chmod(0o440)
    fail_once(manager, lambda args: args[:2] == ["systemctl", "start"])
    with pytest.raises(subprocess.CalledProcessError):
        manager.switch(after, {"active": before}, branch="develop")
    assert all(path.read_text() == "original" for path in paths)
    assert all(path.stat().st_mode & 0o777 == 0o440 for path in paths)


def test_rollback_to_pre_journal_release_removes_new_integration(manager):
    old, new = release(manager, "old"), release(manager, "new")
    manager.activate(new)
    manager.wrappers()
    manager.units()
    for name in (*runtime.SYSTEM_FILES, "journal_reader.py"):
        (Path(old["release"]) / "root" / name).unlink()
    manager.switch(old, {"active": new}, branch="develop")
    assert not manager.system_path("/usr/local/libexec/minitel-journal").exists()
    assert all(not manager.system_path(path).exists() for path in runtime.SYSTEM_FILES.values())


def test_maintenance_failure_does_not_turn_a_successful_deployment_into_failure(manager, monkeypatch):
    runtime.atomic_json(manager.state / "deployment.json", {"phase": "ready"})
    monkeypatch.setattr(manager, "housekeeping", Mock(side_effect=OSError("full")))
    manager.maintain()
    assert json.loads((manager.state / "deployment.json").read_text())["phase"] == "ready"
