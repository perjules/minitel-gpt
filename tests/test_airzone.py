import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
import requests

from services import airzone_client as api
from services import airzone_terminal as panel

ORIGIN = "https://controller.example:8443"


def state(*, age=0, pending=None, setpoint=19.0):
    return {
        "demo": False, "connection": "online", "commands_enabled": True,
        "updated_at": (datetime.now(UTC) - timedelta(seconds=age)).isoformat(),
        "supported_modes": [2, 3], "master_zone_id": 5, "mode_enabled": True,
        "zones": [{
            "id": 5, "name": "Salon", "available": True, "temperature": 20.2,
            "setpoint": setpoint, "humidity": 48, "air_demand": 12, "on": True,
            "mode": 3, "min": 15, "max": 30, "step": 0.5, "pending_setpoint": pending,
            "controls": {"setpoint": pending is None, "on": True},
        }],
    }


@pytest.mark.parametrize("origin", [
    "", "http://controller.example", ORIGIN + "/", ORIGIN + "?",
    "https://user@controller.example", "https://controller.example:0",
])
def test_invalid_origins(origin):
    with pytest.raises(api.AirzoneError):
        api.Client(origin)


def response(status, data):
    reply = Mock(status_code=status)
    reply.iter_content.return_value = iter([json.dumps(data).encode()])
    context = Mock()
    context.__enter__ = Mock(return_value=reply)
    context.__exit__ = Mock(return_value=False)
    return context


@pytest.mark.parametrize(("zone", "field", "value", "path"), [
    (5, "setpoint", 20.5, "/api/zones/5"),
    (5, "on", False, "/api/zones/5"),
    (None, "mode", 2, "/api/mode"),
])
def test_write_contract(monkeypatch, zone, field, value, path):
    request = Mock(return_value=response(200, {"outcome": "confirmed"}))
    monkeypatch.setattr(api.requests, "request", request)
    assert api.Client(ORIGIN).apply(zone, field, value)["outcome"] == "confirmed"
    request.assert_called_once()
    assert request.call_args.args == ("POST", ORIGIN + path)
    options = request.call_args.kwargs
    assert options["json"] == {field: value}
    assert options["headers"]["Origin"] == ORIGIN
    UUID(options["headers"]["X-Command-ID"])
    assert options["allow_redirects"] is False


def test_timeout_does_not_resend(monkeypatch):
    request = Mock(side_effect=requests.Timeout)
    monkeypatch.setattr(api.requests, "request", request)
    assert api.Client(ORIGIN).apply(5, "on", False) == {"outcome": "unknown"}
    request.assert_called_once()


@pytest.mark.parametrize(("new_state", "expected"), [
    (state(age=1, setpoint=20.5), "confirmed"),
    (state(age=1, pending=20.5, setpoint=20.5), "sent"),
    (state(age=60, setpoint=20.5), "sent"),
    (state(age=1, setpoint=19), "sent"),
    ({**state(age=1, setpoint=20.5), "demo": True}, "sent"),
])
def test_pending_setpoint_reads_only(monkeypatch, new_state, expected):
    sent = {**state(age=30), "outcome": "sent"}
    request = Mock(side_effect=[response(202, sent),
                                response(200, new_state), response(200, new_state)])
    monkeypatch.setattr(api.requests, "request", request)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    assert api.Client(ORIGIN).apply(5, "setpoint", 20.5)["outcome"] == expected
    assert [call.args[0] for call in request.call_args_list] == (
        ["POST", "GET"] if expected == "confirmed" else ["POST", "GET", "GET"])


def test_metrics_and_readiness():
    current = state()
    zone = current["zones"][0]
    assert "48 %" in panel.render(current, zone, False, "")
    assert "Demande air : 12" in panel.render(current, zone, False, "")
    assert panel.proposal(current, zone, False, "C:20,5")[:3] == (5, "setpoint", 20.5)
    with pytest.raises(api.AirzoneError):
        panel.proposal(current, zone, False, "C:20,1")
    with pytest.raises(api.AirzoneError):
        panel.proposal(current, None, True, "7")
    for unavailable in ({**current, "demo": True}, state(age=120), state(pending=20.5)):
        with pytest.raises(api.AirzoneError):
            panel.proposal(unavailable, unavailable["zones"][0], False, "C:20,5")


@pytest.mark.parametrize(("commands", "expected"), [
    (["5", "C:20,5", "OUI", "RETOUR", "RETOUR"], (5, "setpoint", 20.5)),
    (["5", "ARRET", "OUI", "RETOUR", "RETOUR"], (5, "on", False)),
    (["MODE", "2", "OUI", "RETOUR", "RETOUR"], (None, "mode", 2)),
    (["5", "C:20,5", "NON", "RETOUR", "RETOUR"], None),
    (["5", "C:20,5", "RETOUR", "RETOUR", "RETOUR"], None),
    (["5", "C:20,5", "SOMMAIRE"], None),
])
def test_explicit_confirmation(monkeypatch, commands, expected):
    def wait(_term, pending, _timeout):
        return pending[1].get(timeout=2)[0], "done"

    chat = SimpleNamespace(wait_answer=wait, show_response=Mock(),
                           read_question=Mock(side_effect=[(command, "envoi") for command in commands]))
    monkeypatch.setattr(panel.rose_terminal, "api", lambda: chat)
    controller = Mock()
    controller.state.side_effect = state
    controller.apply.return_value = {"outcome": "confirmed"}
    action, _ = panel.run(Mock(), controller, "fr")
    assert action == ("sommaire" if commands[-1] == "SOMMAIRE" else "done")
    if expected is None:
        controller.apply.assert_not_called()
    else:
        controller.apply.assert_called_once_with(*expected)
        assert any("Réglage confirmé" in call.args[1] for call in chat.show_response.call_args_list)


@pytest.mark.parametrize("configured", [True, False])
def test_airzone_is_intercepted_before_completion_and_returns_to_chat(monkeypatch, configured):
    from test_terminal_startup import StartupTests, fn, login_keys

    from services import minitel_chatgpt as chat

    monkeypatch.setenv("AIRZONE_URL", ORIGIN if configured else "")
    controller = Mock()
    controller.state.side_effect = state
    factory = Mock(return_value=controller)
    monkeypatch.setattr(api, "Client", factory)
    original_wait = chat.wait_answer

    def wait_for_panel(term, pending, timeout, **options):
        if timeout == api.REQUEST_TIMEOUT + 1:
            reply, error = pending[1].get(timeout=2)
            assert error is None
            return reply, "done"
        return original_wait(term, pending, timeout, **options)

    monkeypatch.setattr(chat, "wait_answer", wait_for_panel)
    send = fn(chat.K_ENVOI)
    commands = b"airzone" + send
    if configured:
        commands += b"5" + send + b"RETOUR" + send + b"RETOUR" + send
    startup = StartupTests()
    _, calls = startup.run_session(login_keys("fr") + commands + b"FOLLOW UP" + send)
    assert factory.called is configured
    controller.apply.assert_not_called()
    assert [call[1][-1]["content"] for call in calls] == ["FOLLOW UP"]
    assert any(("Humidité" if configured else "Climatisation désactivée") in screen for screen in startup.result_screens)


@pytest.mark.parametrize("bad", [
    {}, {**state(), "demo": "false"},
    {**state(), "zones": [{"id": "../api/mode"}]},
    {**state(), "zones": state()["zones"] * 2},
])
def test_invalid_state(monkeypatch, bad):
    monkeypatch.setattr(api.requests, "request", Mock(return_value=response(200, bad)))
    with pytest.raises(api.AirzoneError):
        api.Client(ORIGIN).state()


def test_oversized_body_and_redirect(monkeypatch):
    huge = response(200, {})
    huge.__enter__.return_value.iter_content.return_value = iter([b"x" * (api.MAX_BODY + 1)])
    request = Mock(side_effect=[huge, response(302, {})])
    monkeypatch.setattr(api.requests, "request", request)
    for _ in range(2):
        with pytest.raises(api.AirzoneError):
            api.Client(ORIGIN).state()


def test_failed_read_keeps_terminal_usable(monkeypatch):
    def wait(_term, pending, _timeout):
        return pending[1].get(timeout=2)[0], "done"

    chat = SimpleNamespace(wait_answer=wait, show_response=Mock(),
                           read_question=Mock(return_value=("RETOUR", "envoi")))
    monkeypatch.setattr(panel.rose_terminal, "api", lambda: chat)
    client = Mock()
    client.state.side_effect = api.AirzoneError("Lecture Aizone indisponible.")
    assert panel.run(Mock(), client, "fr")[0] == "done"
    client.apply.assert_not_called()
    assert "Lecture Aizone indisponible." in chat.show_response.call_args.args[1]


def house():
    from copy import deepcopy

    current = state()
    current["zones"] = [deepcopy(current["zones"][0]) for _ in range(3)]
    for zone, identifier, name in zip(current["zones"], (1, 3, 5), ("Enfants", "Chambre", "Salon")):
        zone.update(id=identifier, name=name)
    return current


@pytest.mark.parametrize("command", ["C:20,5", "MARCHE", "ARRET"])
def test_all_zone_proposal_validates_every_zone(command):
    current = house()
    targets, summary = panel.group_proposal(current, command)
    assert [target[0] for target in targets] == [1, 3, 5]
    assert all(name in summary for name in ("Enfants", "Chambre", "Salon"))
    field = targets[0][1]
    current["zones"][1]["controls"][field] = False
    with pytest.raises(api.AirzoneError, match="Chambre"):
        panel.group_proposal(current, command)


def test_group_stops_after_unknown_and_preserves_partial_results():
    client = api.Client(ORIGIN)
    client.write = Mock(side_effect=[{"outcome": "sent"}, {"outcome": "unknown"}])
    targets = [(i, "setpoint", 20.5) for i in (1, 3, 5)]
    receipt = client.apply_all(targets)
    assert [result["outcome"] for result in receipt["results"]] == ["sent", "unknown", "skipped"]
    assert client.write.call_count == 2


@pytest.mark.parametrize("outcome", ["confirmed", "unconfirmed", "sent"])
def test_group_applies_all_accepted_commands_once(outcome):
    client = api.Client(ORIGIN)
    client.write = Mock(return_value={"outcome": outcome})
    targets = [(i, "on", False) for i in (1, 3, 5)]
    receipt = client.apply_all(targets)
    assert [result["id"] for result in receipt["results"]] == [1, 3, 5]
    assert [call.args for call in client.write.call_args_list] == targets


@pytest.mark.parametrize("accepted", [True, False])
@pytest.mark.parametrize("selector", ["TOUS", "0"])
def test_all_zone_confirmation(monkeypatch, accepted, selector):
    def wait(_term, pending, _timeout):
        return pending[1].get(timeout=2)[0], "done"

    commands = [selector, "C:20,5", "OUI" if accepted else "NON", "RETOUR", "RETOUR"]
    chat = SimpleNamespace(wait_answer=wait, show_response=Mock(),
                           read_question=Mock(side_effect=[(command, "envoi") for command in commands]))
    monkeypatch.setattr(panel.rose_terminal, "api", lambda: chat)
    client = Mock()
    client.state.side_effect = house
    client.apply_all.return_value = {
        "outcome": "group", "results": [{"id": 1, "outcome": "confirmed"},
                                       {"id": 3, "outcome": "unknown"}, {"id": 5, "outcome": "skipped"}]}
    assert panel.run(Mock(), client, "fr")[0] == "done"
    client.apply.assert_not_called()
    if accepted:
        client.apply_all.assert_called_once_with([(1, "setpoint", 20.5),
                                                 (3, "setpoint", 20.5), (5, "setpoint", 20.5)])
        assert any("séquence interrompue" in call.args[1] for call in chat.show_response.call_args_list)
    else:
        client.apply_all.assert_not_called()


def test_delayed_mode_readback_never_repeats_post(monkeypatch):
    before = state(age=30)
    unconfirmed = {**before, "outcome": "unconfirmed"}
    after = state(age=1)
    after["zones"][0]["mode"] = 2
    request = Mock(side_effect=[response(502, unconfirmed), response(200, after)])
    monkeypatch.setattr(api.requests, "request", request)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    assert api.Client(ORIGIN).apply(None, "mode", 2)["outcome"] == "confirmed"
    assert [call.args[0] for call in request.call_args_list] == ["POST", "GET"]


def test_mode_pending_can_be_confirmed_by_manual_refresh(monkeypatch):
    def wait(_term, pending, _timeout):
        return pending[1].get(timeout=2)[0], "done"

    commands = ["MODE", "2", "OUI", "MAJ", "RETOUR", "RETOUR"]
    chat = SimpleNamespace(wait_answer=wait, show_response=Mock(),
                           read_question=Mock(side_effect=[(command, "envoi") for command in commands]))
    monkeypatch.setattr(panel.rose_terminal, "api", lambda: chat)
    initial, changed = state(age=30), state(age=1)
    changed["zones"][0]["mode"] = 2
    client = Mock()
    client.state.side_effect = [initial, initial, changed]
    client.apply.return_value = {**initial, "outcome": "unconfirmed"}
    assert panel.run(Mock(), client, "fr")[0] == "done"
    screens = [call.args[1] for call in chat.show_response.call_args_list]
    assert any("Relecture en attente" in screen and "Mode demandé : Climatisation" in screen for screen in screens)
    assert any("Réglage confirmé" in screen and "Mode relu : Climatisation" in screen for screen in screens)
    client.apply.assert_called_once_with(None, "mode", 2)


def test_physical_suite_retour_cycle_nonconsecutive_zones(monkeypatch):
    from test_terminal_startup import StartupTests, fn, login_keys

    from services import minitel_chatgpt as chat

    monkeypatch.setenv("AIRZONE_URL", ORIGIN)
    client = Mock()
    client.state.side_effect = house
    monkeypatch.setattr(api, "Client", Mock(return_value=client))
    original_wait = chat.wait_answer

    def wait(term, pending, timeout, **options):
        if timeout == api.REQUEST_TIMEOUT + 1:
            return pending[1].get(timeout=2)[0], "done"
        return original_wait(term, pending, timeout, **options)

    monkeypatch.setattr(chat, "wait_answer", wait)
    submit = fn(chat.K_ENVOI)
    # Partly typed C: must not be carried to another zone.
    keys = (login_keys("fr") + b"AIRZONE" + submit + b"1" + submit
            + b"C:" + fn(chat.K_SUITE) + fn(chat.K_RETOUR) + fn(chat.K_RETOUR)
            + b"RETOUR" + submit + b"RETOUR" + submit + b"FOLLOW UP" + submit)
    helper = StartupTests()
    _, calls = helper.run_session(keys)
    detail = [screen for screen in helper.result_screens if "Humidité" in screen]
    assert any("Chambre" in screen for screen in detail)
    assert any("Salon" in screen for screen in detail)  # RETOUR from first wraps to last.
    assert [call[1][-1]["content"] for call in calls] == ["FOLLOW UP"]
    client.apply.assert_not_called()
    client.apply_all.assert_not_called()
