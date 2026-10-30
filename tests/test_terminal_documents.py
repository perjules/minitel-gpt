"""Command entry and review through bidirectional physical document paging."""

from queue import Queue
from unittest.mock import Mock

import pytest
from test_gmail import draft, make_session
from test_rose_echo import RoutingSerial, submitted
from test_terminal_startup import EndOfInput, FakeSerial, fn, screen_text

from services import memory_session, terminal_documents
from services import minitel_chatgpt as chat


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)

    def make(keys):
        serial = RoutingSerial(keys)
        return chat.Term(serial), serial

    return make


def document(pages=3):
    return "\n".join(f"ROW {i:02d}" for i in range(chat.CONTENT_ROWS * pages))


def test_command_can_be_entered_on_first_page_without_approving_unseen_text(terminal):
    term, serial = terminal(submitted("L: 1"))
    reviewed = Mock()
    assert terminal_documents.read(term, document(), reviewed=reviewed) == (
        "L: 1",
        "envoi",
    )
    assert "Page 1/3" in screen_text(serial.output)
    assert "Page 2/3" not in screen_text(serial.output)
    reviewed.assert_not_called()


def test_previous_from_last_page_preserves_typed_input_and_review_runs_once(terminal):
    keys = (
        fn(chat.K_RETOUR)
        + b"M: "
        + fn(chat.K_SUITE) * 2
        + fn(chat.K_RETOUR) * 2
        + submitted("raccourcis")
    )
    term, serial = terminal(keys)
    reviewed = Mock()
    assert terminal_documents.read(term, document(), reviewed=reviewed) == (
        "M: raccourcis",
        "envoi",
    )
    reviewed.assert_called_once_with()
    shown = screen_text(serial.output)
    assert shown.count("Page 1/3") == 3
    assert shown.count("Page 2/3") == 2
    assert shown.count("Page 3/3") == 1
    assert "> M: " in shown  # Redrawn on navigation without resubmitting input.


def test_early_send_returns_to_pending_draft_then_all_pages_enable_send(terminal):
    session, api, model = make_session(output="Long outgoing reply. " * 180)
    ticket = draft(session)
    original = session.draft["raw"]
    response = session.handle("E:", ticket)
    assert response["review_ticket"] == ticket
    assert "review_current_draft_first" in response["display"]
    assert session.draft["raw"] == original
    api.send.assert_not_called()
    page_count = (
        len(chat.wrap(chat.safe_display(response["display"]))) + chat.CONTENT_ROWS - 1
    ) // chat.CONTENT_ROWS
    term, _ = terminal(
        fn(chat.K_SUITE) * (page_count - 1) + fn(chat.K_RETOUR) + submitted("E:")
    )
    model.reset_mock()
    text, action = terminal_documents.read(
        term, response["display"], reviewed=lambda: session.acknowledge(ticket)
    )
    assert action == "envoi" and session.review_valid(ticket)
    session.handle(text, ticket)
    api.send.assert_called_once()
    assert model.method_calls == []


def test_paging_and_early_read_do_not_refetch_mail_or_call_ai(terminal):
    session, api, model = make_session()
    response = session.handle("MAIL")
    # Use a long synthetic list without changing the selected mailbox state.
    text = response["display"] + "\n" + document()
    api.reset_mock()
    model.reset_mock()
    term, _ = terminal(fn(chat.K_SUITE) + fn(chat.K_RETOUR) + submitted("L: 1"))
    command, _ = terminal_documents.read(term, text)
    assert api.method_calls == [] and model.method_calls == []
    session.handle(command)
    api.read.assert_called_once()
    assert model.method_calls == []


def test_incomplete_review_can_be_cancelled_without_acknowledgement(terminal):
    term, _ = terminal(fn(chat.K_SUITE) + fn(chat.K_SOMMAIRE))
    reviewed = Mock()
    assert terminal_documents.read(term, document(), reviewed=reviewed) == (
        None,
        "sommaire",
    )
    reviewed.assert_not_called()


def test_annulation_and_overflow_never_submit_truncated_commands(terminal):
    term, _ = terminal(
        b"x" * 2001 + fn(chat.K_ENVOI) + b"discard" + fn(chat.K_ANNUL) + submitted("Q:")
    )
    assert terminal_documents.read(term, document()) == ("Q:", "envoi")


def test_real_mail_loop_accepts_read_and_reply_from_first_pages(tmp_path, monkeypatch):
    session, api, model = make_session(text="Incoming message line.\n" * 80)
    api.list.return_value = api.list.return_value * 15
    selected = memory_session.MemorySession("Alice", root=tmp_path / "users")
    selected.remember_language("fr")
    uart = FakeSerial(
        b"".join(submitted(q) for q in ("MAIL", "L: 1", "REPONDRE oui"))
        + fn(chat.K_SOMMAIRE)
    )
    term = chat.Term(uart)
    monkeypatch.setenv("GMAIL_ENABLED", "1")
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    monkeypatch.setattr(chat, "wait_connection", lambda _: None)
    monkeypatch.setattr(chat, "prepare_boot", lambda _: [])
    monkeypatch.setattr(chat, "show_boot", lambda *args: "done")
    monkeypatch.setattr(
        chat, "select_memory_user", lambda *args, **kwargs: (selected, "envoi")
    )
    monkeypatch.setattr(chat.gmail_mail, "MailSession", lambda **kwargs: session)

    def synchronous(system, history, language, options, knowledge, **kwargs):
        result = chat.call_answer(
            system, history, language, options, knowledge, **kwargs
        )
        output = Queue()
        output.put((result, None))
        return Mock(is_alive=lambda: False), output, chat.time.monotonic()

    monkeypatch.setattr(chat, "start_answer", synchronous)
    monkeypatch.setattr(
        chat, "wait_answer", lambda t, p, *args, **kwargs: (p[1].get()[0], "done")
    )
    with pytest.raises(EndOfInput):
        chat.run(term)
    api.list.assert_called_once()
    api.read.assert_called_once()
    model.complete.assert_called_once()  # Reply drafted without paging through either document.
    api.send.assert_not_called()
    assert "Page 2/" not in screen_text(uart.output)
