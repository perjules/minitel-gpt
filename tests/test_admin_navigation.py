"""Offline navigation, health fallbacks and operational authorization."""
import json
import re
import subprocess
from unittest.mock import Mock

import pytest

from services import admin_ui as admin
from services import config_store, system_health


@pytest.fixture
def console(tmp_path, monkeypatch):
    prompts = tmp_path / "prompts.json"
    config_store.save_prompts(prompts, {"active": "main", "presets": {
        "main": {"label": "Main", "system": "synthetic prompt"},
        "other": {"label": "Other", "system": "another synthetic prompt"}}})
    for name, path in (("PROMPTS_FILE", prompts), ("ENV_FILE", tmp_path / ".env"),
                       ("KNOWLEDGE_DIR", tmp_path / "knowledge"),
                       ("DEPLOYMENT_STATUS", tmp_path / "deployment.json")):
        monkeypatch.setattr(admin, name, path)
    monkeypatch.setattr(admin, "svc_status", lambda _: "active")
    monkeypatch.setattr(admin, "ip_address", lambda: "192.0.2.1")
    monkeypatch.setattr(admin, "log_tail", lambda _: "\n".join(f"line {n}" for n in range(40)))
    monkeypatch.setattr(admin, "current_version", lambda: "test-version")
    monkeypatch.setattr(system_health, "command", lambda *args: "")
    monkeypatch.setattr(admin, "restart_terminal", Mock(return_value=True))
    client = admin.app.test_client()
    with client.session_transaction() as session:
        session.update(logged_in=True, csrf_token="test-token")
    return client, {"X-CSRF-Token": "test-token"}, prompts


def test_four_sections_and_dashboard_are_read_only(console):
    client, _, _ = console
    for section in ("dash", "ai", "services", "maintenance", "invalid"):
        response = client.get("/", query_string={"section": section})
        assert response.status_code == 200
        page = response.text
        nav = re.search(r'<nav class=tabs>(.*?)</nav>', page, re.DOTALL)[1]
        assert re.findall(r'data-tab="[^"]+"[^>]*>(.*?)</a>', nav) == [
            "Tableau de bord", "Configuration IA", "Services", "Maintenance"]
        assert re.search(r'<section class="panel active" id=([^>]+)>', page)[1] == (
            "dash" if section == "invalid" else section)
        dashboard = re.search(r'id=dash>(.*?)</section>', page, re.DOTALL)[1]
        maintenance = re.search(r'id=maintenance>(.*?)</section>', page, re.DOTALL)[1]
        services = re.search(r'id=services>(.*?)</section>', page, re.DOTALL)[1]
        assert "<form" not in dashboard
        assert "line 34" not in dashboard and "line 35" in dashboard
        for route in ("/restart", "/check-update", "/rollback"):
            assert f"action={route}" in maintenance
        assert 'href="/services/gmail"' in services and 'href="/pronote"' in services
    admin.restart_terminal.assert_not_called()


def test_operational_controls_stay_authenticated_csrf_protected_and_queue_only(console, monkeypatch):
    client, headers, _ = console
    queue = Mock(return_value=(True, "planifiée"))
    monkeypatch.setattr(admin, "queue_deployment", queue)
    monkeypatch.setattr(admin, "check_update", Mock(return_value={"ok": True, "behind": 1, "log": "change"}))
    anonymous = admin.app.test_client()
    for route in ("/restart", "/update", "/rollback", "/check-update"):
        assert anonymous.post(route).status_code in (302, 403)
        assert client.post(route).status_code == 403
    queue.assert_not_called()
    for route in ("/check-update", "/restart", "/update", "/rollback"):
        response = client.post(route, headers=headers)
        assert response.status_code == 302 and response.location.endswith("?section=maintenance")
    assert [call.args for call in queue.call_args_list] == [("update",), ("rollback",)]
    for route in ("/storage-status", "/deployment-status"):
        assert anonymous.get(route).status_code == 302


def test_health_command_failures(monkeypatch):
    for error in (FileNotFoundError(), subprocess.TimeoutExpired("probe", 3),
                  UnicodeDecodeError("utf8", b"\xff", 0, 1, "invalid")):
        monkeypatch.setattr(system_health.subprocess, "run", Mock(side_effect=error))
        assert system_health.command("nmcli") == ""
        assert system_health.wifi() == "indisponible"
        assert system_health.tailscale() == "indisponible"
    monkeypatch.setattr(system_health.shutil, "disk_usage", Mock(side_effect=OSError))
    assert system_health.disk(".") is None


def test_health_parsing_and_malformed_optional_state(tmp_path, monkeypatch):
    monkeypatch.setattr(system_health, "command", lambda *args: (
        r"wlan0:wifi:connected:Home\: network" if args[0] == "nmcli" else
        json.dumps({"BackendState": "Running", "TailscaleIPs": ["100.64.0.1"]})))
    assert system_health.wifi() == "Home: network (wlan0, connected)"
    assert system_health.tailscale() == "Running · 100.64.0.1"
    for value in ("not json", "[]", "null", '{"phase": 3}'):
        state = tmp_path / "state.json"
        state.write_text(value)
        assert system_health.deployment(state) == {"phase": "indisponible"}
    state.write_text('{"phase":"failed","error_type":"OSError"}')
    assert system_health.deployment(state)["error_type"] == "OSError"


@pytest.mark.parametrize("route,form", [
    ("/save-prompt", {"preset_key": "main", "system_prompt": "saved draft"}),
    ("/apply-preset", {"preset_key": "other"}),
    ("/save-llm", {"llm_provider": "mistral", "mistral_key": "synthetic-api-key"}),
])
def test_saved_configuration_is_distinct_from_restart_failure(console, route, form):
    client, headers, prompts = console
    admin.restart_terminal.return_value = False
    response = client.post(route, data=form, headers=headers)
    assert response.status_code == 302 and "section=ai" in response.location
    with client.session_transaction() as session:
        assert session["flash_ok"] is False
        assert "enregistrée" in session["flash"]
        assert "Redémarrage non confirmé" in session["flash"]
    data = config_store.read_prompts(prompts)
    if route == "/save-prompt":
        assert data["presets"]["main"]["system"] == "saved draft"
    elif route == "/apply-preset":
        assert data["active"] == "other"
    else:
        assert admin.read_env()["MISTRAL_KEY"] == "synthetic-api-key"


def test_inactive_edit_stays_inactive_and_keeps_editor_context(console):
    client, headers, prompts = console
    response = client.post("/save-prompt", data={"preset_key": "other", "system_prompt": "new draft"},
                           headers=headers)
    assert "section=ai" in response.location and "preset=other" in response.location
    data = config_store.read_prompts(prompts)
    assert data["active"] == "main" and data["presets"]["other"]["system"] == "new draft"
    admin.restart_terminal.assert_not_called()
    with client.session_transaction() as session:
        assert session["flash_ok"] and "activer celle-ci" in session["flash"]


def test_create_edit_activate_and_delete_keep_the_existing_schema(console):
    client, headers, prompts = console
    response = client.post("/new-preset", data={"key": "new", "label": "New"}, headers=headers)
    assert "section=ai" in response.location and "preset=new" in response.location
    data = config_store.read_prompts(prompts)
    assert data["active"] == "main" and data["presets"]["new"]["label"] == "New"
    admin.restart_terminal.assert_not_called()
    response = client.post("/apply-preset", data={"preset_key": "new", "label": "",
        "system_prompt": "Edited before activation", "title_msg": "Edited title",
        "tat_text": "HELLO", "tat_font": "small"}, headers=headers)
    assert response.status_code == 302
    data = config_store.read_prompts(prompts)
    assert data["active"] == "new"
    assert data["presets"]["new"]["system"] == "Edited before activation"
    assert data["presets"]["new"]["label"] == "new"
    assert data["presets"]["new"]["title_art"] == [{"text": "HELLO", "font": "small"}]
    response = client.post("/preview-title", json={"segments": [{"text": "HELLO", "font": "small"}]},
                           headers=headers)
    assert response.json["ok"] and response.json["lines"]
    assert client.post("/delete-preset", data={"preset_key": "new"}, headers=headers).status_code == 302
    data = config_store.read_prompts(prompts)
    assert data["active"] == "main" and "new" not in data["presets"]
    assert admin.restart_terminal.call_count == 2


def test_generation_and_preview_remain_available_without_modifying_configuration(console, monkeypatch):
    client, headers, prompts = console
    original = prompts.read_bytes()
    generate = Mock(return_value="Generated draft")
    monkeypatch.setattr(admin, "generate_prompt", generate)
    response = client.post("/generate-prompt", json={"description": "Synthetic idea"}, headers=headers)
    assert response.json == {"ok": True, "prompt": "Generated draft"}
    generate.assert_called_once_with("Synthetic idea")
    assert prompts.read_bytes() == original
    admin.restart_terminal.assert_not_called()


@pytest.mark.parametrize("comparison", [(1, "", "error"), (0, "invalid", "")])
def test_update_comparison_failure_cannot_claim_up_to_date(monkeypatch, comparison):
    monkeypatch.setattr(admin, "git_ready", lambda: True)
    monkeypatch.setattr(admin, "runtime_branch", lambda: "develop")
    run = Mock(side_effect=[(0, "", ""), comparison])
    monkeypatch.setattr(admin, "git", run)
    assert admin.check_update()["ok"] is False


def test_cached_update_details_survive_refresh_but_not_a_failed_check(console, monkeypatch):
    client, headers, _ = console
    monkeypatch.setattr(admin, "check_update", lambda: {"ok": True, "behind": 1, "log": "pending commit"})
    client.post("/check-update", headers=headers)
    for _ in range(2):
        assert "pending commit" in client.get("/?section=maintenance").text
    monkeypatch.setattr(admin, "check_update", lambda: {"ok": False, "msg": "offline"})
    client.post("/check-update", headers=headers)
    with client.session_transaction() as session:
        assert "update_log" not in session
        assert "échec" in session["update_status"]
