"""A deterministic local tool exercises the real AI-answer display pipeline."""

import json

import pytest
import test_terminal_startup as startup
from test_rose_echo import submitted
from test_terminal_display import VisibleUART

from services import minitel_chatgpt as chat
from services import mistral_answers, terminal_diagnostics


@pytest.mark.parametrize("text", ["test Unicode", "TEST UNICODE", "  test   unicode  "])
def test_local_command_is_case_insensitive(text):
    assert terminal_diagnostics.unicode_command(text)


@pytest.mark.parametrize("text", ["test", "Unicode", "Please test Unicode", "A: test Unicode"])
def test_only_exact_diagnostic_command_is_intercepted(text):
    assert not terminal_diagnostics.unicode_command(text)


@pytest.mark.parametrize("escaped", [False, True])
def test_fixture_survives_provider_json_and_uses_real_answer_view(monkeypatch, escaped):
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    fixture = terminal_diagnostics.test_unicode()
    raw = {"outputs": [{"type": "message.output", "role": "assistant", "content": [
        {"type": "text", "text": fixture["display"]}]}]}
    decoded = json.loads(json.dumps(raw, ensure_ascii=escaped))
    response = mistral_answers.parse_response(decoded)
    assert response["text"] == fixture["display"]
    uart = VisibleUART(submitted("NEXT"))
    term = chat.Term(uart)
    assert chat.show_response(term, response["text"]) == "done"
    assert len(term.view.pages) == 1 and len(term.view.pages[0]) == 18
    assert uart.lines()[3].startswith("02 FR: é è ê ë à â ù û ü î ï ô ç")
    assert "?" not in uart.lines()[3]  # Ordinary French letters are not unknown glyphs.
    assert uart.lines()[5].startswith("04 NFD: é à ü ç")
    assert uart.lines()[6].startswith("05 ISOLES: ? ? ? ?")
    assert "Page 1/1" in uart.lines()[19]
    assert chat.read_question(term) == ("NEXT", "envoi")


def test_real_terminal_command_bypasses_model_and_does_not_enter_history_or_memory():
    calls = []
    def answer(history, *args, **kwargs):
        calls.append([dict(turn) for turn in history])
        return {"status": "ordinary", "text": "Normal answer", "chunks": [], "references": []}
    helper = startup.StartupTests()
    helper.run_session(startup.login_keys("fr") + submitted("test Unicode")
                       + startup.fn(chat.K_REPET) + submitted("FOLLOW UP"), factual_reply=answer)
    assert [turns[-1]["content"] for turns in calls] == ["FOLLOW UP"]
    assert calls[0] == [{"role": "user", "content": "FOLLOW UP"}]
    assert any("TEST UNICODE - sortie applicative" in screen for screen in helper.result_screens)
    assert "TEST UNICODE" not in str(helper.memories[0].record.mock_calls)
    assert "test Unicode" not in str(helper.memories[0].record.mock_calls)
