"""Rose shares CAN/REP/input drawing while retaining its isolated interaction rules."""

from unittest.mock import Mock

import pytest
from test_rose_echo import submitted
from test_terminal_display import VisibleUART
from test_terminal_startup import fn

from services import minitel_chatgpt as chat
from services import rose, rose_terminal


@pytest.fixture
def hardware(monkeypatch):
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    def make(keys=b""):
        uart = VisibleUART(keys)
        return chat.Term(uart), uart
    return make


def response():
    return "\n".join(f"Private row {i:02d}" for i in range(54))


def test_rose_uses_gray_fixed_input_can_clearing_and_shared_frame(hardware):
    term, uart = hardware(submitted("Q" * 150))
    rose_terminal.display(term, response(), framed=True, safeword="navet")
    assert uart.lines()[0][:39] == "*** 3615 rose ***".center(39)
    assert rose_terminal.read_field(term, safeword="navet") == ("Q" * 150, "submit")
    assert all(style == (2, True, 7) for row in uart.screen.styles[21:] for style in row[1:])
    assert uart.lines()[20].strip() == "Mot d'arret : navet"
    before = len(uart.output)
    term.view.input_notice = "Attente... Arret : navet"
    term.view.busy("Attente... Arret : navet")
    assert all(not line.strip() for line in uart.lines()[1:19])
    assert uart.lines()[21].strip() == ">"
    assert uart.lines()[20].strip() == "Attente... Arret : navet"
    rose_terminal.display(term, "Short", framed=True, safeword="navet")
    assert b"\x0c" not in uart.output[before:] and b"\x18" in uart.output[before:]
    assert uart.lines()[1].startswith("Short")
    assert all(not line.strip() for line in uart.lines()[2:19])


def test_paging_never_hides_stopword_or_loses_unsent_input(hardware):
    keys = b"draft" + fn(chat.K_SUITE) + fn(chat.K_SUITE) + submitted("!")
    term, uart = hardware(keys)
    view = rose_terminal.display(term, response(), framed=True, safeword="navet")
    assert rose_terminal.read_field(term, safeword="navet") == ("draft!", "submit")
    assert view.page == 2 and len(view.pages) == 3
    assert uart.lines()[1].startswith("Private row 36")
    assert uart.lines()[20].strip() == "Mot d'arret : navet"
    assert uart.lines()[21].startswith("> draft!")
    assert all(copies == 0 for _, copies in uart.echo)
    assert 21 <= uart.screen.row <= 23


@pytest.mark.parametrize("key,action", [(chat.K_RETOUR, "retour"), (chat.K_ANNUL, "retour"),
                                      (chat.K_SOMMAIRE, "sommaire"), (chat.K_GUIDE, "help")])
def test_private_exit_keys_work_from_any_page_and_restore_echo(hardware, key, action):
    term, uart = hardware(fn(chat.K_SUITE) + fn(key))
    before = dict(uart.routes)
    rose_terminal.display(term, response(), framed=True, safeword="navet")
    assert rose_terminal.read_field(term, safeword="navet") == (None, action)
    assert uart.routes == before


def test_stopword_and_textual_previous_do_not_leave_the_local_reader(hardware):
    term, uart = hardware(fn(chat.K_SUITE) + submitted("PRECEDENT") + submitted("navet"))
    view = rose_terminal.display(term, response(), framed=True, safeword="navet")
    assert rose_terminal.read_field(term, safeword="navet") == ("navet", "safeword")
    assert view.page == 0 and uart.lines()[20].strip() == "Mot d'arret : navet"


def test_private_reader_ignores_public_recall_and_drains_oversized_input(hardware):
    term, uart = hardware(fn(chat.K_REPET) + submitted("x" * 801) + submitted("PUBLIC"))
    term.prompt_history.add("ORDINARY CHAT")
    rose_terminal.display(term, response(), framed=True, safeword="navet")
    assert rose_terminal.read_field(term, safeword="navet") == (None, "overflow")
    assert "navet" in uart.lines()[20]
    assert term.prompt_history.items == ["ORDINARY CHAT"]
    assert b"ORDINARY CHAT" not in uart.output
    term.clear()
    assert chat.read_question(term) == ("PUBLIC", "envoi")


def test_catalogue_pages_keep_suite_semantics_after_long_biography(hardware):
    term, _ = hardware(fn(chat.K_SUITE) * 3)
    view = rose_terminal.display(term, response(), framed=True)
    assert rose_terminal.read_field(term, paging=True) == (None, "next")
    assert view.page == 2


def test_waiting_safeword_has_priority_and_no_local_echo(hardware):
    term, uart = hardware(submitted("navet"))
    job = rose.Job(lambda *args: rose.Reply("Already ready"), "", [])
    job.thread.join(1)
    before = dict(uart.routes)
    assert rose_terminal.wait(term, job, "navet", 10) == (None, "safeword")
    assert job.cancelled.is_set()
    assert all(copies == 0 for _, copies in uart.echo)
    assert uart.routes == before


def test_rose_exit_retires_private_frame_before_returning_to_public_chat(hardware, monkeypatch):
    term, uart = hardware(submitted("fixture") + fn(chat.K_RETOUR))
    monkeypatch.setenv("ROSE_PASSWORD", "fixture")
    def completed(term, job, *args):
        job.thread.join(1)
        return job.output.get_nowait(), "done"
    monkeypatch.setattr(rose_terminal, "wait", completed)
    bill = Mock(return_value="done")
    monkeypatch.setattr(rose_terminal, "show_bill", bill)
    model = Mock(ready=lambda: True, return_value=rose.Reply("PRIVATE RESPONSE"))
    assert rose_terminal.run(term, "Jacqueline", model) == ("retour", None)
    assert term.view is None and all(not line.strip() for line in uart.lines())
    assert term.prompt_history.items == []
    bill.assert_called_once()


def test_wait_echo_failure_tracks_and_cancels_worker_before_returning(hardware):
    term, uart = hardware()
    uart.ignore_off = True
    pending = (Mock(), Mock(), 0)
    job = Mock(pending=lambda: pending)
    with pytest.raises(rose_terminal.EchoUnavailable):
        rose_terminal.wait(term, job, "navet", 10)
    assert term._pending is pending
    job.cancel.assert_called_once_with()
