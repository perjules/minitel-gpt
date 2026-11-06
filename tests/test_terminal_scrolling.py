"""Scrolling lifecycle, cancellation, pagination and natural-rate serial output."""

import curses
from queue import Empty, Queue
from unittest.mock import Mock

import pytest
import test_terminal_startup as startup
from test_rose_echo import submitted
from test_terminal_display import VisibleUART, assert_gray_input
from test_terminal_serial_timing import BufferedUART, Clock
from test_terminal_startup import fn

from services import minitel_chatgpt as chat
from services import terminal_diagnostics, terminal_scrolling
from services.console_terminal import ConsoleSerial


@pytest.fixture
def hardware(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(chat.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(chat.time, "sleep", clock.sleep)
    def make(keys=b""):
        uart = VisibleUART(keys)
        term = chat.Term(uart)
        term.poll_key = Mock(return_value=("timeout", None))
        return term, uart, clock
    return make


def document():
    return terminal_diagnostics.test_scroll()["display"]


def assert_restored(term, uart):
    assert not uart.screen.scrolling
    assert uart.screen.cursor_visible
    assert (uart.screen.row, uart.screen.column) == (21, 2)
    assert "*** 3615 IA ***" in uart.lines()[0]
    assert_gray_input(uart)
    assert term.view.page == len(term.view.pages) - 1


def test_static_eof_needs_no_key_and_rebuilds_only_last_page(hardware, monkeypatch):
    term, uart, clock = hardware()
    chat.show_response(term, "Old answer")
    before = dict(uart.routes)
    offset = len(uart.output)
    installs = []
    install = chat.terminal_display.View.install
    monkeypatch.setattr(chat.terminal_display.View, "install", lambda view: (installs.append(view.page), install(view))[1])
    assert terminal_scrolling.show(term, document()) == "done"
    wire = bytes(uart.output[offset:])
    assert wire.index(b"\x0c") < wire.index(chat.SCROLL_ON) < wire.index(b"Journal 01")
    assert wire.index(b"Journal 54") < wire.rindex(chat.SCROLL_OFF) < wire.rindex(b"\x0c")
    assert wire.count(b"\x0c") == 2
    assert installs == [2]  # Never paint page one while restoring.
    assert term.view.text == document() and len(term.view.pages) == 3
    assert "Journal 37" in uart.lines()[1] and "Journal 54" in uart.lines()[18]
    assert uart.routes == before
    assert_restored(term, uart)
    assert clock.now == pytest.approx(.6)  # Three clear() settles only; no line pauses.


@pytest.mark.parametrize("after", [0, 4, 53])
def test_annulation_keeps_complete_static_text_and_supports_normal_paging(hardware, after):
    term, uart, _ = hardware()
    def key():
        count = bytes(uart.output).count(b"Journal ")
        return ("fn", chat.K_ANNUL) if count >= after else ("timeout", None)
    term.poll_key = key
    assert terminal_scrolling.show(term, document()) == "done"
    scroll_wire = bytes(uart.output).split(chat.SCROLL_ON)[1].split(chat.SCROLL_OFF)[0]
    assert scroll_wire.count(b"Journal ") == after
    assert term.view.text == document()
    assert "arrêté" in term.view.notice
    assert_restored(term, uart)
    uart.keys.extend(fn(chat.K_RETOUR) * 2 + fn(chat.K_SUITE) + submitted("FOLLOW UP"))
    assert chat.read_question(term) == ("FOLLOW UP", "envoi")
    assert term.view.page == 1
    assert "Journal 19" in uart.lines()[1]


@pytest.mark.parametrize("exit_key,action", [(chat.K_SOMMAIRE, "sommaire"), (chat.K_GUIDE, "guide")])
def test_global_exit_keys_restore_page_mode_and_frame(hardware, exit_key, action):
    term, uart, _ = hardware()
    term.poll_key = Mock(return_value=("fn", exit_key))
    assert terminal_scrolling.show(term, document()) == action
    assert_restored(term, uart)


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 41, 4096])
def test_stream_chunks_accents_spaces_and_eof_retain_exact_text(hardware, chunk_size):
    term, uart, _ = hardware()
    text = ("éte\u0301 au central : " + "mot long avec espaces " * 6 + "\n\n") * 6 + "FIN"
    source = Queue()
    for index in range(0, len(text), chunk_size):
        source.put_nowait(text[index:index + chunk_size])
    source.put_nowait(None)
    cancel = Mock()
    assert terminal_scrolling.show(term, source, cancel=cancel) == "done"
    cancel.assert_not_called()
    assert term.view.text == text
    assert_restored(term, uart)
    # Scroll rows are the ordinary wrapping, even with accent/word boundaries split.
    start = bytes(uart.output).index(chat.SCROLL_ON) + len(chat.SCROLL_ON)
    stop = bytes(uart.output).rindex(chat.SCROLL_OFF)
    transcript = startup.screen_text(uart.output[start:stop])
    expected = "\r\n".join(chat.wrap(chat.safe_display(text))) + "\r\n"
    assert transcript.endswith(expected)


@pytest.mark.parametrize("confirmed,marker", [(True, "[Flux arrêté.]"), (False, "source non confirmée")])
def test_stream_cancel_marks_received_text_and_requests_source_stop(hardware, confirmed, marker):
    term, uart, _ = hardware()
    source = Queue()
    source.put_nowait("Reçu : école\n")
    source.put_nowait("Must remain unread")
    cancel = Mock(return_value=confirmed)
    term.poll_key = lambda: ("fn", chat.K_ANNUL) if "Reçu" in startup.screen_text(uart.output) else ("timeout", None)
    assert terminal_scrolling.show(term, source, cancel=cancel) == "done"
    cancel.assert_called_once_with()
    assert term.view.text.startswith("Reçu : école\n") and marker in term.view.text
    assert "Must remain unread" not in term.view.text
    assert source.get_nowait() == "Must remain unread"
    assert_restored(term, uart)


def test_quiet_stream_has_no_idle_or_worker_duration_limit(hardware, monkeypatch):
    term, uart, clock = hardware()
    monkeypatch.setattr(chat, "IDLE_TIMEOUT", .01)
    class SlowSource:
        sent = False
        def get_nowait(self):
            if clock.now < 720:
                raise Empty
            if not self.sent:
                self.sent = True
                return "Monitoring resumed after twelve minutes"
            return None
    calls = []
    def wait(_timeout):
        calls.append(_timeout)
        clock.sleep(120)
        return "timeout", None
    term.read_key = wait
    assert terminal_scrolling.show(term, SlowSource()) == "done"
    assert clock.now >= 720 and calls
    assert "twelve minutes" in term.view.text
    assert_restored(term, uart)


@pytest.mark.parametrize("payload", [RuntimeError("secret provider details"), 123])
def test_source_failure_keeps_partial_text_without_exception_payload(hardware, payload):
    term, uart, _ = hardware()
    source = Queue()
    source.put_nowait("Available output")
    source.put_nowait(payload)
    cancel = Mock(side_effect=RuntimeError("private cancellation details"))
    assert terminal_scrolling.show(term, source, cancel=cancel) == "done"
    assert term.view.text == "Available output\n\n[Flux interrompu : erreur de source.]"
    assert b"secret" not in uart.output and b"private" not in uart.output
    cancel.assert_called_once_with()
    assert_restored(term, uart)


def test_keyboard_interrupt_requests_stop_and_restores_echo_frame_and_page_mode(hardware):
    term, uart, _ = hardware()
    before = dict(uart.routes)
    term.poll_key = Mock(side_effect=KeyboardInterrupt)
    cancel = Mock(return_value=True)
    with pytest.raises(KeyboardInterrupt):
        terminal_scrolling.show(term, Queue(), cancel=cancel)
    cancel.assert_called_once_with()
    assert uart.routes == before
    assert_restored(term, uart)


def test_stream_receiver_exception_still_cancels_and_restores(hardware):
    term, uart, _ = hardware()
    source = Mock()
    source.get_nowait.side_effect = RuntimeError("adapter bug")
    cancel = Mock(return_value=True)
    with pytest.raises(RuntimeError, match="adapter bug"):
        terminal_scrolling.show(term, source, cancel=cancel)
    cancel.assert_called_once_with()
    assert_restored(term, uart)


def test_natural_rate_scrolling_bounds_uart_backlog_and_keeps_presence(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(chat.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(chat.time, "sleep", clock.sleep)
    uart = BufferedUART(clock, capacity=256)
    term = chat.Term(uart)
    term._presence_enabled = term._presence_seen = True
    term.start_presence()
    backlogs, scroll_screens = [], []
    write = uart.write
    scrolling = False
    def observe(data):
        nonlocal scrolling
        if data.startswith(chat.SCROLL_OFF):
            scrolling = False
        elif data == chat.SCROLL_ON:
            scrolling = True
        value = write(data)
        if scrolling:
            backlogs.append(uart.tx_until - clock.now)
        return value
    uart.write = observe
    deliver = uart.deliver
    def delivered(data):
        deliver(data)
        if uart.screen.scrolling and b"\r\n" in data:
            scroll_screens.append("\n".join("".join(row) for row in uart.screen.rows))
    uart.deliver = delivered
    text = "\n".join(f"{number:02d} été 0123456789abcdefghijklmnopqr" for number in range(60))
    assert terminal_scrolling.show(term, text) == "done"
    uart.finish()
    assert backlogs and max(backlogs) < 2
    assert any("30 été" in screen and "00 été" not in screen for screen in scroll_screens)
    assert "59 été" in "".join(uart.screen.rows[6])
    assert not uart.timeouts
    assert term._presence_seen and term._presence_misses == 0
    assert not uart.screen.scrolling and uart.screen.cursor_visible
    assert all(copies == 0 for _, copies in uart.echo)


def test_console_nonblocking_poll_and_scrolling_have_no_fake_line_delay(monkeypatch):
    class Window:
        def __init__(self):
            self.timeouts = []
        def timeout(self, value):
            self.timeouts.append(value)
        def get_wch(self):
            raise curses.error()
        def getmaxyx(self):
            return 26, 80
        def __getattr__(self, name):
            return lambda *args: None
    clock = Clock()
    monkeypatch.setattr(chat.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(chat.time, "sleep", clock.sleep)
    window = Window()
    uart = ConsoleSerial(window)
    term = chat.Term(uart)
    assert terminal_scrolling.show(term, document()) == "done"
    assert clock.now == pytest.approx(.4)
    assert window.timeouts[-2:] == [0, 100]
    assert not uart.screen.scrolling


def test_cancel_during_actual_wire_backlog_stops_within_one_poll(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(chat.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(chat.time, "sleep", clock.sleep)
    class CancelUART(BufferedUART):
        sent = False
        def read(self, count):
            if clock.now >= 6 and self.screen.scrolling and not self.sent:
                self.sent = True
                self.cancel_at = clock.now
                self.incoming.extend(b"ZKZX" + fn(chat.K_ANNUL))
            return super().read(count)
    uart = CancelUART(clock, capacity=256)
    term = chat.Term(uart)
    before = dict(uart.routes)
    text = "\n".join(f"{number:03d}: é abcdefghijklmnopqrstuvwxyz" for number in range(180))
    assert terminal_scrolling.show(term, text) == "done"
    assert uart.sent and uart.cancel_at < 6.15
    wire = bytes(uart.output).split(chat.SCROLL_ON)[1].split(chat.SCROLL_OFF)[0]
    assert wire.count(b"\r\n") < 30
    assert b"ZKZX" not in uart.output and not term.view.buffer
    assert term.view.text == text
    uart.finish()
    assert not uart.screen.scrolling and uart.routes == before and not uart.timeouts


@pytest.mark.parametrize("stream", [False, True])
def test_unconfirmed_echo_never_starts_scrolling(hardware, stream):
    term, uart, _ = hardware()
    uart.ignore_off = True
    cancel = Mock(return_value=False)
    source = Queue() if stream else document()
    assert terminal_scrolling.show(term, source, cancel=cancel) == "done"
    assert chat.SCROLL_ON not in uart.output
    assert "indisponible" in term.view.notice
    assert_restored(term, uart)
    if stream:
        cancel.assert_called_once_with()
    else:
        assert term.view.text == document()
        cancel.assert_not_called()


def test_transient_write_failure_restores_mode_and_keeps_result(hardware, monkeypatch):
    term, uart, _ = hardware()
    write = uart.write
    def fail_once(data):
        if data.startswith(b"Journal 02"):
            monkeypatch.setattr(uart, "write", write)
            raise OSError("transport failure")
        return write(data)
    monkeypatch.setattr(uart, "write", fail_once)
    with pytest.raises(chat.terminal_link.Unavailable):
        terminal_scrolling.show(term, document())
    assert term.view.text == document()
    assert_restored(term, uart)


def test_test_scroll_main_loop_is_local_and_does_not_change_history(monkeypatch):
    monkeypatch.setattr(chat.Term, "poll_key", Mock(return_value=("timeout", None)))
    helper = startup.StartupTests()
    _, calls = helper.run_session(startup.login_keys("fr") + submitted("TEST SCROLL") + submitted("FOLLOW UP"))
    assert [call[1][-1]["content"] for call in calls] == ["FOLLOW UP"]
    assert "TEST SCROLL" not in str(helper.memories[0].record.mock_calls)
    assert any("Page 3/3" in screen and "Journal 54" in screen for screen in helper.result_screens)


def test_main_loop_honors_explicit_scrolling_result_flag(monkeypatch):
    monkeypatch.setattr(chat.Term, "poll_key", Mock(return_value=("timeout", None)))
    result = {"display": document(), "turn": None, "scrolling": True}
    monkeypatch.setattr(chat, "call_answer", lambda *args, **kwargs: result)
    helper = startup.StartupTests()
    helper.run_session(startup.login_keys("fr") + submitted("Run the journal"))
    assert chat.SCROLL_ON in helper.uart.output
    assert any("Page 3/3" in screen and "Journal 54" in screen for screen in helper.result_screens)
