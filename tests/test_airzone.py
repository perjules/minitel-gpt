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
    assert any(("Humidité" if configured else "AIRZONE_URL") in screen for screen in startup.result_screens)


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
