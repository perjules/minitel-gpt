from queue import Queue
from unittest.mock import Mock

import pytest
import test_terminal_display as terminal_tests
from test_rose_echo import submitted
from test_terminal_startup import fn

from services import minitel_chatgpt as chat
from services import terminal_qr
from services import yolo_terminal as yolo

hardware = terminal_tests.hardware


@pytest.mark.parametrize(("question", "operation"), [
    ("Crée une application web de dépenses", "create"),
    ("Can you build a local web app for expenses?", "create"),
    ("YOLO, modifie l'application web depenses", "edit"),
    ("Comment créer une application web ?", None),
    ("Explique Flask et montre un exemple", None),
    ("Refactor this script", None),
])
def test_intent_is_conservative(question, operation):
    assert yolo.intent(question) == operation


@pytest.mark.parametrize("keys", [submitted(":yolo"), submitted(":") + submitted("yolo")])
def test_fixed_command_goes_to_panel_without_model(monkeypatch, keys):
    helper = terminal_tests.startup.StartupTests()
    launched = Mock(return_value="sommaire")
    model = Mock(side_effect=AssertionError("Fixed command must use no model"))
    monkeypatch.setattr(yolo, "run", launched)
    monkeypatch.setattr(chat, "call_answer", model)
    helper.run_session(terminal_tests.login_keys("fr") + keys)
    assert launched.call_count == 1
    model.assert_not_called()


def test_tool_prepares_original_requirement_only(monkeypatch):
    response = Mock()
    response.json.return_value = {"outputs": [{"type": "function.call", "name": "prepare_yolo",
                                             "arguments": '{"app_id":"depenses","operation":"edit"}'}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(yolo.requests, "post", post)
    question = "Modifie l'application web depenses pour afficher le total"
    result = yolo.proposal(question, "fr", "mock-key", "test", 2)
    assert result["yolo_proposal"] == {"app_id": "depenses", "operation": "edit", "payload": question}
    assert post.call_args.kwargs["json"]["tools"] == [yolo.TOOL]
    assert "yolo_proposal" not in yolo.proposal(
        "Modifie l'application web calculatrice", "fr", "mock-key", "test", 2
    )


def test_proposal_requires_confirmation(monkeypatch, hardware):
    client = Mock()
    monkeypatch.setattr(yolo, "client", lambda: client)
    terminal, _ = hardware(submitted("NON"))
    proposal = {"operation": "create", "app_id": "calc", "payload": "Crée une application web"}
    yolo.run(terminal, "fr", initial=proposal)
    client.submit.assert_not_called()


def test_finished_job_renders_exact_qr(monkeypatch, hardware):
    url = "http://mainframe.local:8767/yolo/depenses/"
    sent = {"status": "READY", "url": url, "name": "Depenses", "app_id": "depenses"}
    terminal, uart = hardware(fn(chat.K_SOMMAIRE))

    def synchronous(operation):
        result = Queue()
        result.put((operation(), None))
        return result

    monkeypatch.setattr(yolo, "background", synchronous)
    api = Mock()
    api.request.return_value = sent
    assert yolo.watch(chat, terminal, api, "fr", "9af798426c00") == "sommaire"
    assert terminal.view.qr == terminal_qr.make(url, terminal.geometry)
    assert any("Depenses / PRETE" in line for line in uart.lines())
