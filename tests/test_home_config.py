import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_airzone import ORIGIN, state

from services import admin_ui as admin
from services import airzone_answers as answers
from services import airzone_client as api
from services import airzone_terminal as panel
from services import config_store, home_config


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.setattr(home_config, "CONFIG", tmp_path / "homes.json")
    monkeypatch.setenv("AIRZONE_URL", "")
    return home_config.CONFIG


def inventory():
    return {"version": 1, "revision": 0, "enabled": True, "homes": {
        "drome": {"name": "Drôme", "aliases": ["Bourdeaux"], "enabled": True, "targets": {
            "clim": {"kind": "airzone", "name": "Climatisation", "url": ORIGIN, "enabled": True}}},
        "lyon": {"name": "Lyon", "aliases": ["Croix-Rousse"], "enabled": True, "targets": {
            "clim": {"kind": "airzone", "name": "Climatisation", "url": "https://lyon.example", "enabled": True}}},
    }}


def save(path, data):
    path.write_text(json.dumps(data))


def test_env_fallback_and_authoritative_disable(homes, monkeypatch):
    monkeypatch.setenv("AIRZONE_URL", ORIGIN)
    old = home_config.load()
    assert old["homes"]["legacy-home"]["targets"]["legacy-airzone"]["url"] == ORIGIN
    assert home_config.enabled()
    home_config.update(lambda data: data.update(enabled=False), revision=0)
    assert not home_config.enabled()
    assert home_config.load()["homes"] == old["homes"]
    assert homes.stat().st_mode & 0o777 == 0o600


def test_home_alias_selection_is_accent_insensitive_and_bounded(homes):
    save(homes, inventory())
    assert home_config.resolve("Quelle température à Bourdeaux ?")["home_id"] == "drome"
    assert home_config.resolve("Mets le salon de DROME à 20 degrés")["home_id"] == "drome"
    assert home_config.resolve("Humidité à Croix-Rousse ?")["home_id"] == "lyon"
    assert answers.interested("Quelle température à Bourdeaux ?")
    with pytest.raises(api.AirzoneError, match="Précisez la maison"):
        home_config.resolve("Température salon ?")
    with pytest.raises(api.AirzoneError, match="seule maison"):
        home_config.resolve("Compare Lyon et Drôme")


def test_disabled_named_home_cannot_fall_back_to_another(homes):
    data = inventory()
    data["homes"]["drome"]["enabled"] = False
    save(homes, data)
    with pytest.raises(api.AirzoneError, match="désactivée"):
        home_config.resolve("Température à Bourdeaux ?")
    assert home_config.resolve("Température salon ?")["home_id"] == "lyon"


def test_multiple_targets_require_disambiguation(homes):
    data = inventory()
    data["homes"]["drome"]["targets"]["upstairs"] = {
        "kind": "airzone", "name": "Étage", "url": "https://upstairs.example", "enabled": True}
    save(homes, data)
    with pytest.raises(api.AirzoneError, match="contrôleur"):
        home_config.resolve("Température à Drôme ?")
    assert home_config.resolve("Température étage à Drôme ?")["target_id"] == "upstairs"


@pytest.mark.parametrize("corrupt", [
    lambda data: data["homes"]["lyon"].update(aliases=["drome"]),
    lambda data: data["homes"]["lyon"]["targets"]["clim"].update(url="http://example.com"),
    lambda data: data["homes"]["lyon"]["targets"]["clim"].update(kind="shell"),
])
def test_invalid_config_cannot_overwrite_working_settings(homes, corrupt):
    save(homes, inventory())
    before = homes.read_bytes()
    with pytest.raises(config_store.InvalidConfig):
        home_config.update(corrupt, revision=0)
    assert homes.read_bytes() == before


def test_malformed_file_never_falls_back_to_env(homes, monkeypatch):
    monkeypatch.setenv("AIRZONE_URL", ORIGIN)
    homes.write_text("{")
    with pytest.raises(config_store.InvalidConfig):
        home_config.load()
    assert not home_config.enabled()


def test_stale_admin_revision_is_rejected(homes):
    save(homes, inventory())
    home_config.update(lambda data: data.update(enabled=False), revision=0)
    with pytest.raises(config_store.InvalidConfig, match="modifiée ailleurs"):
        home_config.update(lambda data: data.update(enabled=True), revision=0)
    assert not home_config.enabled()


def test_url_change_or_disable_invalidates_inflight_proposal(homes):
    data = inventory()
    save(homes, data)
    assert home_config.authorize("drome", "clim", ORIGIN)["home"] == "Drôme"
    data["homes"]["drome"]["targets"]["clim"]["url"] = "https://new.example"
    save(homes, data)
    with pytest.raises(api.AirzoneError, match="modifié"):
        home_config.authorize("drome", "clim", ORIGIN)


def test_admin_imports_env_and_edits_homes_and_targets(console, homes):
    client, headers, _ = console
    config_store.update_env(admin.ENV_FILE, {"AIRZONE_URL": ORIGIN})
    page = client.get("/services/smart-home")
    assert ORIGIN in page.text and "AIRZONE_URL" in page.text
    assert "tailscale" not in page.text.casefold()
    assert not homes.exists()  # Reading the form has no write side effect.
    assert client.post("/services/smart-home", data={"action": "toggle", "revision": "0"}).status_code == 403

    def post(**data):
        result = client.post("/services/smart-home", data={
            "revision": str(home_config.load()["revision"]), **data}, headers=headers)
        assert result.status_code == 303, result.text

    post(action="save_home", home_id="legacy-home", name="Drôme", aliases="Bourdeaux", enabled="1")
    current = home_config.load()
    assert current["homes"]["legacy-home"]["targets"]["legacy-airzone"]["url"] == ORIGIN
    assert current["homes"]["legacy-home"]["name"] == "Drôme"
    post(action="add_home", name="Lyon", aliases="Croix-Rousse", enabled="1")
    home_id = next(key for key in home_config.load()["homes"] if key != "legacy-home")
    post(action="add_target", home_id=home_id, name="Climatisation", kind="airzone",
         url="https://lyon.example", enabled="1")
    target_id = next(iter(home_config.load()["homes"][home_id]["targets"]))
    post(action="save_target", home_id=home_id, target_id=target_id, name="Étage", kind="airzone",
         url="https://lyon.example", enabled="1")
    assert home_config.resolve("Température à Lyon ?")["name"] == "Étage"
    post(action="delete_target", home_id=home_id, target_id=target_id)
    post(action="delete_home", home_id=home_id)
    client.post("/services/smart-home/toggle", headers=headers)
    assert not home_config.enabled()
    assert home_config.load()["homes"]
    admin.restart_terminal.assert_not_called()


def test_admin_invalid_url_and_xss(console, homes):
    client, headers, _ = console
    save(homes, inventory())
    before = homes.read_bytes()
    result = client.post("/services/smart-home", headers=headers, data={
        "action": "save_target", "revision": "0", "home_id": "drome", "target_id": "clim",
        "kind": "airzone", "name": "Clim", "url": "javascript:alert(1)", "enabled": "1"})
    assert result.status_code == 400 and homes.read_bytes() == before
    data = inventory()
    data["homes"]["drome"]["name"] = "<script>alert(1)</script>"
    save(homes, data)
    page = client.get("/services/smart-home").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_planner_uses_named_home_and_only_that_controller(homes, monkeypatch):
    save(homes, inventory())
    clients = []
    real_client = api.Client

    def factory(url):
        instance = real_client(url)
        instance.state = Mock(return_value=state())
        clients.append(instance)
        return instance

    monkeypatch.setattr(api, "Client", factory)
    response = Mock()
    response.json.return_value = {"outputs": [{
        "type": "function.call", "name": "airzone_power", "tool_call_id": "az1",
        "arguments": {"zone": "Salon", "on": False}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(answers.requests, "post", post)
    result = answers.answer([{"role": "user", "content": "Éteins le salon à Bourdeaux"}],
                            "fr", "key", "model", 30)
    assert result["airzone_target"]["home_id"] == "drome"
    assert result["airzone_origin"] == ORIGIN
    assert [client.origin for client in clients if client.state.called] == [ORIGIN]
    assert "Drôme" in post.call_args.kwargs["json"]["instructions"]
    clients.clear()
    result = answers.answer([{"role": "user", "content": "Éteins le salon"}],
                            "fr", "key", "model", 30)
    assert "Précisez la maison" in result["display"]
    assert not any(client.state.called for client in clients)


def test_direct_panel_controller_picker(homes, monkeypatch):
    save(homes, inventory())
    chat = SimpleNamespace(show_response=Mock(), read_question=Mock(return_value=("2", "envoi")))
    monkeypatch.setattr(panel.rose_terminal, "api", lambda: chat)
    selected, action = panel.choose_controller(Mock(), "fr")
    assert action == "done" and selected["home_id"] == "lyon"
    assert "Drôme" in chat.show_response.call_args.args[1]


def test_revoked_access_during_confirmation_sends_nothing(homes, monkeypatch):
    save(homes, inventory())

    def wait(_term, pending, _timeout):
        return pending[1].get(timeout=2)[0], "done"

    def reply(*args, **kwargs):
        home_config.update(lambda data: data.update(enabled=False))
        return "OUI", "envoi"

    chat = SimpleNamespace(show_response=Mock(), wait_answer=wait, read_question=reply)
    monkeypatch.setattr(panel.rose_terminal, "api", lambda: chat)
    client = Mock()
    client.state.side_effect = state
    initial = {"selected": 5, "mode": False, "command": "C:20.5"}
    panel.run(Mock(), client, "fr", initial=initial,
              access=lambda: home_config.authorize("drome", "clim", ORIGIN), location="Drôme")
    client.apply.assert_not_called()
