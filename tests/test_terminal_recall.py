"""Recall is editable, bounded, session-local and excluded from private/features."""

import curses
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import test_terminal_startup as startup
from test_rose_echo import submitted
from test_terminal_display import VisibleUART, long_answer
from test_terminal_startup import fn, login_keys

from services import minitel_chatgpt as chat
from services.console_terminal import key_bytes
from services.terminal_display import PromptHistory


@pytest.fixture
def hardware(monkeypatch):
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    def make(keys):
        uart = VisibleUART(keys)
        return chat.Term(uart), uart
    return make


def test_recall_cycles_exact_prompts_then_saved_draft_and_bounds_duplicates():
    history = PromptHistory()
    assert history.recall("draft") is None
    for index in range(25):
        history.add(f"Prompt {index}")
    history.add("Prompt 24")
    assert len(history.items) == 20
    assert [history.recall("draft") for _ in range(20)] == [f"Prompt {i}" for i in range(24, 4, -1)]
    assert history.recall("ignored") == "draft"
    assert history.recall("draft") == "Prompt 24"
    history.edited()
    assert history.recall("edited draft") == "Prompt 24"
    history.clear()
    assert history.items == [] and history.recall("secret") is None


def test_recall_editing_paging_and_long_saved_viewport(hardware):
    draft = "Q" * 200
    keys = (draft.encode() + fn(chat.K_REPET) + fn(chat.K_SUITE) + fn(chat.K_RETOUR)
            + fn(chat.K_REPET) + fn(chat.K_REPET) + submitted("Z"))
    term, uart = hardware(keys)
    term.prompt_history.add("OLD")
    term.prompt_history.add("NEW")
    chat.show_response(term, long_answer())
    assert chat.read_question(term, recall=True) == (draft + "Z", "envoi")
    assert term.view.page == 0 and uart.lines()[21].startswith("> " + "Q" * 10)
    assert term.prompt_history.index is None


def test_edit_exits_traversal_and_annulation_discards_saved_draft(hardware):
    keys = (b"draft" + fn(chat.K_REPET) + b"X" + fn(chat.K_REPET)
            + fn(chat.K_REPET) + fn(chat.K_ANNUL) + fn(chat.K_REPET) + submitted("!"))
    term, _ = hardware(keys)
    term.prompt_history.add("old")
    chat.show_response(term, "Answer")
    assert chat.read_question(term, recall=True) == ("old!", "envoi")
    assert term.prompt_history.items == ["old"]  # Editor never records or submits recalled text itself.


@pytest.mark.parametrize("recall", [False, True])
def test_empty_or_disabled_recall_does_not_submit_or_change_draft(hardware, recall):
    term, _ = hardware(b"draft" + fn(chat.K_REPET) + submitted("!"))
    if not recall:
        term.prompt_history.add("private-in-this-context")
    chat.show_response(term, "Answer")
    assert chat.read_question(term, recall=recall) == ("draft!", "envoi")


def test_recall_key_is_real_activity_and_console_binding(hardware, monkeypatch):
    term, _ = hardware(fn(chat.K_REPET))
    activity = Mock()
    monkeypatch.setattr(term, "keyboard_activity", activity)
    assert term.read_key(1) == ("fn", chat.K_REPET)
    activity.assert_called_once()
    assert key_bytes("\x02") == key_bytes(curses.KEY_F6) == fn(chat.K_REPET)


@pytest.mark.parametrize("feature", ["calculator", "school", "printer", "memory", "mail"])
def test_recall_eligibility_excludes_feature_contexts(feature):
    memory = SimpleNamespace(pending=False, welcome=None)
    mail = SimpleNamespace(items=[], selected=None, draft=None, composition=None, recipient_choices=[])
    calc, school, printer = SimpleNamespace(mode=False), SimpleNamespace(mode=False), SimpleNamespace(approval=None)
    assert chat.prompt_recall_allowed(memory, mail, calc, school, printer)
    assert not chat.prompt_recall_allowed(memory, mail, calc, school, printer, onboarding_active=True)
    if feature in {"calculator", "school"}:
        (calc if feature == "calculator" else school).mode = True
    elif feature == "printer":
        printer.approval = object()
    elif feature == "memory":
        memory.pending = object()
    else:
        mail.draft = object()
    assert not chat.prompt_recall_allowed(memory, mail, calc, school, printer)


def test_real_session_recalls_without_extra_call_and_resets_on_sommaire():
    calls = []
    def answer(history, *args, **kwargs):
        calls.append([dict(turn) for turn in history])
        return {"status": "ordinary", "text": "Answer", "chunks": [], "references": []}
    helper = startup.StartupTests()
    helper.run_session(login_keys("fr") + submitted("FIRST") + fn(chat.K_REPET) + submitted(" AGAIN")
                       + fn(chat.K_SOMMAIRE) + fn(chat.K_REPET) + submitted("FRESH"), factual_reply=answer)
    assert [turns[-1]["content"] for turns in calls] == ["FIRST", "FIRST AGAIN", "FRESH"]
