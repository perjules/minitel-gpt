import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_airzone import ORIGIN, house, state

from services import airzone_answers as answers
from services import airzone_client as api
from services import airzone_terminal as panel


@pytest.mark.parametrize("question", [
    "Quelle température fait-il dans le salon ?", "Quelle humidité dans les chambres ?",
    "Mets le salon à 20,5 degrés", "Éteins la clim", "Allume toutes les zones",
    "Arrête la climatisation", "Quel mode pour la clim ?", "Passe en chauffage", "Baisse la chambre à 19 degrés", "Affiche les températures Airzone",
])
def test_local_airzone_requests(question):
    assert answers.interested(question)


@pytest.mark.parametrize("question", [
    "Quelles sont les nouvelles ?", "Comment fonctionne une climatisation ?",
    "Explique le chauffage par pompe à chaleur", "Quelle température à Marseille ?",
    "Pourquoi l'humidité augmente dans la chambre ?", "Retiens ce numéro de téléphone",
])
def test_general_topics_do_not_offer_airzone_tools(question):
    assert not answers.interested(question)


def setup_request(monkeypatch, name="airzone_read", arguments=None):
    monkeypatch.setenv("AIRZONE_URL", ORIGIN)
    client = Mock(origin=ORIGIN)
    client.state.side_effect = lambda **kwargs: house()
    monkeypatch.setattr(api, "Client", Mock(return_value=client))
    reply = Mock()
    reply.json.return_value = {"outputs": [
        {"type": "function.call", "name": name, "arguments": arguments or {"zone": "Salon"},
         "tool_call_id": "Az0001"}]}
    post = Mock(return_value=reply)
    monkeypatch.setattr(answers.requests, "post", post)
    return client, post


def test_read_is_real_controller_data_without_writes(monkeypatch):
    client, post = setup_request(monkeypatch)
    result = answers.answer([{"role": "user", "content": "Température du salon ?"}], "fr", "key", "model", 30)
    assert "20,2 °C" in result["display"] and "48 %" in result["display"]
    assert result["turn"] is None and "airzone_proposal" not in result
    client.apply.assert_not_called()
    client.write.assert_not_called()
    request = post.call_args.kwargs["json"]
    assert all(tool["type"] == "function" for tool in request["tools"])
    assert "web_search" not in str(request["tools"])
    assert post.call_args.args[0] == answers.engine.URL


@pytest.mark.parametrize(("name", "arguments", "selected", "mode", "command"), [
    ("airzone_setpoint", {"zone": "Salon", "temperature": 20.5}, 5, False, "C:20.5"),
    ("airzone_power", {"zone": "tous", "on": False}, 0, False, "ARRET"),
    ("airzone_mode", {"mode": 2}, None, True, "2"),
])
def test_tool_only_prepares_local_confirmation(monkeypatch, name, arguments, selected, mode, command):
    client, _ = setup_request(monkeypatch, name, arguments)
    result = answers.answer([{"role": "user", "content": "Change la clim"}], "fr", "key", "model", 30)
    assert result["airzone_proposal"] == {"selected": selected, "mode": mode, "command": command}
    assert result["airzone_origin"] == ORIGIN
    client.apply.assert_not_called()
    client.apply_all.assert_not_called()
    client.write.assert_not_called()


@pytest.mark.parametrize(("name", "arguments"), [
    ("airzone_setpoint", {"zone": "Salon", "temperature": 100}),
    ("airzone_setpoint", {"zone": "Salon", "temperature": True}),
    ("airzone_power", {"zone": "Salon", "on": "true"}),
    ("airzone_power", {"zone": "Salon", "on": True, "host": "example.com"}),
    ("airzone_power", {"zone": "chambre inconnue", "on": True}),
    ("airzone_mode", {"mode": 7}), ("airzone_mode", {"mode": True}),
    ("run_shell", {"command": "anything"}),
])
def test_untrusted_or_unsupported_tool_parameters_are_rejected(name, arguments):
    with pytest.raises(api.AirzoneError):
        answers.execute(house(), name, arguments)


def test_actual_conversations_json_string_arguments():
    result = answers.execute(state(), "airzone_read", json.dumps({"zone": "Salon"}))
    assert "20,2 °C" in result["display"]


def test_multiple_tool_calls_cannot_schedule_multiple_writes(monkeypatch):
    client, post = setup_request(monkeypatch)
    call = {"type": "function.call", "name": "airzone_power", "arguments": {"zone": "tous", "on": False},
            "tool_call_id": "Az1"}
    post.return_value.json.return_value = {"outputs": [call, {**call, "tool_call_id": "Az2"}]}
    result = answers.answer([{"role": "user", "content": "Éteins tout"}], "fr", "key", "model", 30)
    assert "airzone_proposal" not in result
    client.write.assert_not_called()


@pytest.mark.parametrize("accept", [False, True])
def test_prepared_action_revalidates_before_confirmation(monkeypatch, accept):
    def wait(_term, pending, _timeout):
        return pending[1].get(timeout=2)[0], "done"

    chat = SimpleNamespace(wait_answer=wait, show_response=Mock(),
                           read_question=Mock(side_effect=[("OUI" if accept else "NON", "envoi"), ("RETOUR", "envoi"),
                                                           ("RETOUR", "envoi")]))
    monkeypatch.setattr(panel.rose_terminal, "api", lambda: chat)
    controller = Mock()
    controller.state.side_effect = state
    controller.apply.return_value = {"outcome": "confirmed"}
    prepared = {"selected": 5, "mode": False, "command": "C:20.5"}
    assert panel.run(Mock(), controller, "fr", initial=prepared)[0] == "done"
    assert "Confirmer" in chat.show_response.call_args_list[0].args[1]
    assert chat.read_question.call_count == 1
    if accept:
        controller.apply.assert_called_once_with(5, "setpoint", 20.5)
    else:
        controller.apply.assert_not_called()


@pytest.mark.parametrize("configured", [True, False])
def test_normal_chat_dispatch_keeps_tools_opt_in(monkeypatch, configured):
    from services import minitel_chatgpt as chat

    monkeypatch.setenv("AIRZONE_URL", ORIGIN if configured else "")
    monkeypatch.setattr(chat, "PROVIDER", "mistral")
    monkeypatch.setattr(chat.pronote, "backend", lambda: None)
    monkeypatch.setattr(chat.mistral_answers, "answer", Mock(return_value={
        "status": "ok", "text": "Ordinary answer", "chunks": [], "references": []}))
    local = Mock(return_value={"display": "LOCAL AIRZONE", "turn": None, "airzone": True})
    monkeypatch.setattr(answers, "answer", local)
    result = chat.call_answer("", [{"role": "user", "content": "Température du salon ?"}],
                              "fr", ("conversations", False, 30), "")
    assert local.called is configured
    if configured:
        assert result["display"] == "LOCAL AIRZONE"
    else:
        assert result["turn"]["content"] == "Ordinary answer"


def test_terminal_proposal_cannot_execute_from_model_prose(monkeypatch):
    from test_terminal_startup import StartupTests, fn, login_keys

    from services import minitel_chatgpt as chat

    monkeypatch.setenv("AIRZONE_URL", ORIGIN)
    prepared = {"selected": 5, "mode": False, "command": "C:20.5"}
    result = {"display": "PROPOSAL", "turn": None, "airzone": True,
              "airzone_proposal": prepared, "airzone_origin": ORIGIN}
    original = chat.call_answer

    def answer_worker(system, history, language, options, knowledge, **kwargs):
        if history[-1]["content"] == "Mets le salon a 20,5":
            return result
        return original(system, history, language, options, knowledge, **kwargs)

    monkeypatch.setattr(chat, "call_answer", answer_worker)
    controller = Mock()
    monkeypatch.setattr(api, "Client", Mock(return_value=controller))
    panel_run = Mock(return_value=("done", None))
    monkeypatch.setattr(panel, "run", panel_run)
    send = fn(chat.K_ENVOI)
    startup = StartupTests()
    _, calls = startup.run_session(login_keys("fr") + b"Mets le salon a 20,5" + send + b"FOLLOW UP" + send)
    assert panel_run.call_args.kwargs["initial"] == prepared
    controller.apply.assert_not_called()
    assert [call[1][-1]["content"] for call in calls] == ["FOLLOW UP"]


def test_capability_change_after_planning_blocks_confirmation(monkeypatch):
    def wait(_term, pending, _timeout):
        return pending[1].get(timeout=2)[0], "done"

    chat = SimpleNamespace(wait_answer=wait, show_response=Mock(),
                           read_question=Mock(side_effect=[("RETOUR", "envoi"), ("RETOUR", "envoi")]))
    monkeypatch.setattr(panel.rose_terminal, "api", lambda: chat)
    current = state()
    current["zones"][0]["controls"]["setpoint"] = False
    controller = Mock()
    controller.state.return_value = current
    prepared = {"selected": 5, "mode": False, "command": "C:20.5"}
    assert panel.run(Mock(), controller, "fr", initial=prepared)[0] == "done"
    assert not any("Confirmer ?" in call.args[1] for call in chat.show_response.call_args_list)
    controller.apply.assert_not_called()


@pytest.mark.parametrize(("reply", "outcome"), [
    ("OUI", "confirmed"), ("OUI", "sent"), ("OUI", "unknown"), ("NON", "confirmed"),
])
def test_chat_change_returns_directly_to_normal_chat(monkeypatch, reply, outcome):
    from test_terminal_startup import StartupTests, fn, login_keys

    from services import minitel_chatgpt as chat

    monkeypatch.setenv("AIRZONE_URL", ORIGIN)
    prepared = {"selected": 5, "mode": False, "command": "C:20.5"}
    original_answer = chat.call_answer

    def answer_worker(system, history, language, options, knowledge, **kwargs):
        if history[-1]["content"] == "Mets le salon a 20,5":
            return {"display": "PROPOSAL", "turn": None, "airzone": True,
                    "airzone_proposal": prepared, "airzone_origin": ORIGIN}
        return original_answer(system, history, language, options, knowledge, **kwargs)

    monkeypatch.setattr(chat, "call_answer", answer_worker)
    controller = Mock()
    controller.state.side_effect = state
    controller.apply.return_value = {"outcome": outcome}
    monkeypatch.setattr(api, "Client", Mock(return_value=controller))
    original_wait = chat.wait_answer

    def wait_for_panel(term, pending, timeout, **options):
        if timeout in (api.REQUEST_TIMEOUT + 1, api.REQUEST_TIMEOUT + 8):
            result, error = pending[1].get(timeout=2)
            assert error is None
            return result, "done"
        return original_wait(term, pending, timeout, **options)

    monkeypatch.setattr(chat, "wait_answer", wait_for_panel)
    submit = fn(chat.K_ENVOI)
    helper = StartupTests()
    _, calls = helper.run_session(login_keys("fr") + b"Mets le salon a 20,5" + submit
                                  + reply.encode() + submit + b"FOLLOW UP" + submit)
    assert [call[1][-1]["content"] for call in calls] == ["FOLLOW UP"]
    assert not any("AIRZONE /" in screen for screen in helper.result_screens)
    if reply == "OUI":
        controller.apply.assert_called_once_with(5, "setpoint", 20.5)
    else:
        controller.apply.assert_not_called()


def test_unavailable_chat_target_returns_without_entering_panel(monkeypatch):
    def wait(_term, pending, _timeout):
        return pending[1].get(timeout=2)[0], "done"

    chat = SimpleNamespace(wait_answer=wait, show_response=Mock(), read_question=Mock())
    monkeypatch.setattr(panel.rose_terminal, "api", lambda: chat)
    client = Mock()
    client.state.side_effect = api.AirzoneError("Lecture Aizone indisponible.")
    prepared = {"selected": 5, "mode": False, "command": "C:20.5"}
    assert panel.run(Mock(), client, "fr", initial=prepared)[0] == "done"
    chat.read_question.assert_not_called()
    client.apply.assert_not_called()
