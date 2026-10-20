"""NetworkManager and HTTP contracts; no host Wi-Fi mutations."""
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import wifi_manager as wifi


@pytest.fixture
def control(monkeypatch):
    monkeypatch.setattr(wifi, "NETWORK_LOCK", __import__('threading').Lock())
    monkeypatch.setattr(wifi, "run", Mock(return_value=SimpleNamespace(stdout="", returncode=0)))
    wifi.app.config["TESTING"] = True
    return wifi.app.test_client()


def test_nmcli_names_decode_escaped_colons_and_backslashes():
    assert wifi.fields(r"uuid:Julian\: home\\room:wlan0") == ["uuid", "Julian: home\\room", "wlan0"]


def test_commands_have_deadlines_and_no_user_environment_or_password_logging(monkeypatch, caplog):
    run = Mock(return_value=SimpleNamespace(stdout=""))
    monkeypatch.setattr(wifi.subprocess, "run", run)
    wifi.run(["nmcli", "con", "add", "wifi-sec.psk", "SECRET-PASSWORD"])
    args = run.call_args.args[0]
    assert args[0] == "/usr/bin/nmcli" and args[1:3] == ["--wait", "15"]
    assert run.call_args.kwargs["timeout"] == 20
    assert "SECRET-PASSWORD" not in caplog.text
    assert run.call_args.kwargs["env"] == wifi.COMMAND_ENV


def test_hotspot_never_disables_or_deletes_client_profiles(control):
    wifi.create_hotspot()
    calls = [c.args[0] for c in wifi.run.call_args_list]
    assert not any("modify" in c for c in calls)
    assert all(c[3] == wifi.AP_CON_NAME for c in calls if c[:3] == ["nmcli", "con", "delete"])


def test_failed_candidate_keeps_existing_credentials_and_restores_hotspot(control, monkeypatch):
    monkeypatch.setattr(wifi.uuid, "uuid4", lambda: "candidate-uuid")
    create = Mock()
    monkeypatch.setattr(wifi, "create_hotspot", create)

    def run(args, **kwargs):
        if args[:3] == ["nmcli", "con", "up"]:
            raise subprocess.TimeoutExpired(args, 20)
        return SimpleNamespace(stdout="")

    wifi.run.side_effect = run
    assert wifi.connect_wifi("Home", "wrong password")[0] is False
    deletes = [c.args[0] for c in wifi.run.call_args_list if "delete" in c.args[0]]
    assert deletes == [["nmcli", "con", "delete", "uuid", "candidate-uuid"]]
    create.assert_called_once()


def test_successful_candidate_only_enables_its_own_autoconnect(control, monkeypatch):
    monkeypatch.setattr(wifi.uuid, "uuid4", lambda: "candidate")
    monkeypatch.setattr(wifi, "client_connected", lambda: "candidate")
    monkeypatch.setattr(wifi, "is_connected", lambda: "10.0.0.2")
    assert wifi.connect_wifi("Home", "  secret  ") == (True, "10.0.0.2")
    calls = [c.args[0] for c in wifi.run.call_args_list]
    assert calls[0][-1] == "  secret  "
    assert not any("delete" in c for c in calls)
    assert calls[-1] == ["nmcli", "con", "modify", "uuid", "candidate", "connection.autoconnect", "yes"]


def test_monitor_returns_from_hotspot_without_manual_configuration(control, monkeypatch):
    connected = [False]
    monkeypatch.setattr(wifi, "client_connected", lambda: "home-uuid" if connected[0] else None)
    monkeypatch.setattr(wifi, "hotspot_active", lambda: True)
    def recover():
        connected[0] = True
    retry = Mock(side_effect=recover)
    monkeypatch.setattr(wifi, "try_known_networks", retry)
    monitor = wifi.Monitor()
    monitor.step(300)
    monitor.step(330)
    retry.assert_called_once()
    assert monitor.disconnected == 0


def test_failed_retry_keeps_a_five_minute_provisioning_window(control, monkeypatch):
    monkeypatch.setattr(wifi, "client_connected", lambda: None)
    monkeypatch.setattr(wifi, "hotspot_active", lambda: True)
    retry = Mock(return_value=False)
    monkeypatch.setattr(wifi, "try_known_networks", retry)
    monitor = wifi.Monitor()
    for now in (0, 30, 299, 300):
        monitor.step(now)
    assert retry.call_count == 2


def test_monitor_cannot_interfere_with_an_in_progress_setup(control):
    with wifi.NETWORK_LOCK:
        wifi.Monitor().step()
    wifi.run.assert_not_called()


def test_normal_lan_and_forged_host_cannot_mutate_network(control, monkeypatch):
    monkeypatch.setattr(wifi, "hotspot_active", lambda: True)
    connection = SimpleNamespace(getsockname=lambda: ("10.0.0.2", 80))
    response = control.post("/connect", base_url="http://192.168.4.1", json={"ssid": "Home"},
                            environ_overrides={"REMOTE_ADDR": "192.168.4.2", "werkzeug.socket": connection})
    assert response.status_code == 403
    wifi.run.assert_not_called()


def test_provisioning_requires_token_and_valid_json_and_rejects_concurrency(control, monkeypatch):
    monkeypatch.setattr(wifi, "provisioning_allowed", lambda: True)
    assert control.post("/connect", json={}).status_code == 403
    with control.session_transaction() as session:
        session["csrf"] = "fixture"
    headers = {"X-CSRF-Token": "fixture"}
    for payload in ([], {"ssid": 3}, {"ssid": "x" * 33}, {"ssid": "Home", "password": "short"}):
        assert control.post("/connect", json=payload, headers=headers).status_code == 400
    with wifi.NETWORK_LOCK:
        assert control.post("/connect", json={"ssid": "Home"}, headers=headers).status_code == 409


def test_http_reports_queued_and_worker_holds_network_lock(control, monkeypatch):
    monkeypatch.setattr(wifi, "provisioning_allowed", lambda: True)
    monkeypatch.setattr(wifi, "hotspot_active", lambda: True)
    monkeypatch.setattr(wifi.time, "sleep", lambda _: None)
    thread = Mock()
    monkeypatch.setattr(wifi.threading, "Thread", thread)
    connect = Mock(return_value=(False, "unavailable"))
    monkeypatch.setattr(wifi, "connect_wifi", connect)
    with control.session_transaction() as session:
        session["csrf"] = "fixture"
    response = control.post("/connect", json={"ssid": "Home", "password": "  secret  "},
                            headers={"X-CSRF-Token": "fixture"})
    assert response.status_code == 202 and response.json["queued"]
    assert wifi.NETWORK_LOCK.locked()
    thread.call_args.kwargs["target"]()
    assert not wifi.NETWORK_LOCK.locked()
    connect.assert_called_once_with("Home", "  secret  ")


def test_server_failure_is_not_reported_as_a_healthy_monitor(control):
    with pytest.raises(RuntimeError, match="HTTP server"):
        wifi.monitor_loop(SimpleNamespace(is_alive=lambda: False))


def test_known_profile_retry_uses_uuid_and_preserves_disabled_profiles(control, monkeypatch):
    clients = iter([None, "home-uuid", "home-uuid"])
    monkeypatch.setattr(wifi, "client_connected", lambda: next(clients))
    monkeypatch.setattr(wifi, "is_connected", lambda: "10.0.0.2")
    wifi.run.return_value.stdout = "disabled:802-11-wireless:no\nhome-uuid:802-11-wireless:yes\n"
    stop = Mock()
    monkeypatch.setattr(wifi, "stop_hotspot", stop)
    assert wifi.try_known_networks()
    assert wifi.run.call_args.args[0] == ["nmcli", "con", "up", "uuid", "home-uuid"]
    assert wifi.run.call_args.kwargs["timeout"] <= 20
    stop.assert_called_once()


def test_link_without_address_restores_hotspot_after_failed_retry(control, monkeypatch):
    monkeypatch.setattr(wifi, "client_connected", lambda: "home-uuid")
    monkeypatch.setattr(wifi, "is_connected", lambda: None)
    monkeypatch.setattr(wifi, "stop_hotspot", Mock())
    create = Mock()
    monkeypatch.setattr(wifi, "create_hotspot", create)
    wifi.run.return_value.stdout = "home-uuid:802-11-wireless:yes\n"
    assert wifi.try_known_networks() is False
    create.assert_called_once()
