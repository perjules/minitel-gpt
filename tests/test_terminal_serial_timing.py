"""1200/7E1 transport: write() queues bytes; routing ACKs arrive on the wire.

The console and older UART fakes acknowledge writes immediately. This fake
advances a virtual clock so display backlogs exercise real protocol deadlines.
"""

import heapq
from collections import deque

import pytest
from test_rose_echo import submitted

from services import minitel_chatgpt as chat
from services import rose_terminal, terminal_documents
from services.console_terminal import Screen


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0, seconds)


class QueuedUART:
    baudrate = 1200
    write_timeout = 2

    def __init__(self, clock, keys=b"", *, direct=False, modem=True, replies=True):
        self.clock = clock
        self.tx_until = clock.now
        self.routes = {0x58: 0x4D | (2 if direct else 0), 0x5A: 0x44 | (2 if modem else 0)}
        self.input = deque(keys)
        self.incoming = deque()
        self.events = []
        self.sequence = 0
        self.replies = replies
        self.output = bytearray()
        self.screen = Screen()
        self.echo = []
        self.input_reads = 0

    @property
    def out_waiting(self):
        # Drivers may report zero after transferring bytes to the adapter FIFO.
        return 0

    def later(self, when, callback):
        self.sequence += 1
        heapq.heappush(self.events, (when, self.sequence, callback))

    def pump(self):
        while self.events and self.events[0][0] <= self.clock.now:
            _, _, callback = heapq.heappop(self.events)
            callback()

    def write(self, data):
        self.output.extend(data)
        self.tx_until = max(self.tx_until, self.clock.now) + len(data) * 10 / self.baudrate
        self.later(self.tx_until, lambda: self.deliver(data))
        return len(data)

    def deliver(self, data):
        self.screen.write(data)
        receiver = None
        if data[:3] == b"\x1b\x3a\x62":
            receiver = data[3]
        elif data[:2] == b"\x1b\x3b" and data[2] in (0x60, 0x61):
            receiver = data[3]
            if data[2] == 0x61:
                self.routes[receiver] |= 2
            else:
                self.routes[receiver] &= ~2
        reply = None
        if receiver is not None and self.replies:
            reply = b"\x1b\x3b\x63" + bytes([receiver, self.routes[receiver]])
        elif data == b"\x1b\x39\x70" and self.replies:
            reply = b"\x1b\x3a\x71\x40"
        if reply is not None:
            for index, code in enumerate(reply, 1):
                self.later(self.clock.now + .02 + index * 10 / self.baudrate,
                           lambda code=code: self.incoming.append(code))

    def read(self, count):
        self.clock.sleep(.02)
        self.pump()
        if self.clock.now > 120:
            raise AssertionError("Unbounded serial wait")
        if self.incoming:
            return bytes([self.incoming.popleft()])
        # Future human input is supplied only after echo suppression completes.
        if self.input and not self.events and not any(value & 2 for value in self.routes.values()):
            code = self.input.popleft()
            self.input_reads += 1
            if code == 0x13 and self.input:
                # A function key is one atomic two-byte message; an unrelated
                # protocol reply cannot split its SEP from its key code.
                self.incoming.append(self.input.popleft())
                self.input_reads += 1
            elif 0x20 <= code <= 0x7E:
                self.echo.append((code, 0))
            return bytes([code])
        return b""

    def reset_input_buffer(self):
        self.pump()
        self.incoming.clear()

    def finish(self):
        while self.events:
            self.clock.now = max(self.clock.now, self.events[0][0])
            self.pump()


class BufferedUART(QueuedUART):
    """USB batches free space only after queued bytes have drained at 7E1.

    Exercise write-side backpressure, which the unlimited QueuedUART omits.
    Enable it on RETOUR to reproduce a failure during backward navigation.
    """

    def __init__(self, *args, capacity=256, on_retour=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.capacity, self.congested = capacity, not on_retour
        self.previous_byte = None
        self.waits, self.timeouts = [], []
        self.write_calls = 0

    def read(self, count):
        data = super().read(count)
        if self.previous_byte == b"\x13" and data == b"\x42":
            self.congested = True
        if data:
            self.previous_byte = data
        return data

    def write(self, data):
        self.write_calls += 1
        backlog = max(0, self.tx_until - self.clock.now)
        wait = backlog if self.congested and backlog * self.baudrate / 10 + len(data) > self.capacity else 0
        if wait > self.write_timeout:
            self.timeouts.append(self.write_timeout)
            self.clock.sleep(self.write_timeout)
            self.pump()
            raise chat.serial.SerialTimeoutException("fixture write backpressure")
        if wait:
            self.waits.append(wait)
            self.clock.sleep(wait)
            self.pump()
        return super().write(data)


@pytest.fixture
def paced(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(chat.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(chat.time, "sleep", clock.sleep)
    def make(keys=b"", *, transport=QueuedUART, **kwargs):
        uart = transport(clock, keys, **kwargs)
        term = chat.Term(uart)
        # Match production defaults: earlier timing tests accidentally skipped
        # the real serial terminal's presence monitor.
        term._presence_enabled = True
        term._presence_seen = True
        term.start_presence()
        return term, uart, clock
    return make


@pytest.mark.parametrize("reader", ["chat", "document"])
@pytest.mark.parametrize("capacity", [320, 512])
def test_precedent_with_usb_backpressure_keeps_all_pages_and_input(paced, reader, capacity):
    keys = b"RE" + b"\x13\x48" + b"\x13\x42" + submitted("PONDRE oui")
    term, uart, _clock = paced(keys, transport=BufferedUART, capacity=capacity, on_retour=True)
    text = "\n".join([letter * 39 for letter in "AZ" for _ in range(18)])
    before = dict(uart.routes)
    if reader == "chat":
        chat.show_response(term, text)
        result = chat.read_question(term)
    else:
        result = terminal_documents.read(term, text)
    assert result == ("REPONDRE oui", "envoi")
    assert term.view.page == 0 and len(term.view.pages) == 2
    assert "Page 1/2" in "".join(uart.screen.rows[19])
    assert uart.screen.rows[1][:39] == list("A" * 39)
    assert "".join(uart.screen.rows[21]).startswith("> REPONDRE oui")
    assert 21 <= uart.screen.row <= 23
    assert uart.waits and max(uart.waits) > 2  # Healthy writes can block beyond the old budget.
    assert not uart.timeouts
    assert uart.routes == before
    assert all(copies == 0 for _, copies in uart.echo)
    assert uart.output.count(b"\x0c") == 1


@pytest.mark.parametrize("backward", [b"\x13\x42", submitted("PRECEDENT")])
def test_upward_wipe_then_long_answer_and_paging_with_usb_backpressure(paced, backward):
    term, uart, _clock = paced(submitted("NEXT"), transport=BufferedUART)
    chat.show_response(term, "Old answer")
    assert chat.read_question(term) == ("NEXT", "envoi")
    term.waiting("WAIT")
    text = "\n".join([letter * 39 for letter in "AZY" for _ in range(18)])
    chat.show_response(term, text)
    uart.input.extend(b"\x13\x48" * 2 + backward * 2 + submitted("NEW QUESTION"))
    assert chat.read_question(term) == ("NEW QUESTION", "envoi")
    assert term.view.page == 0 and len(term.view.pages) == 3
    assert "Page 1/3" in "".join(uart.screen.rows[19])
    assert "WAIT" not in "".join(uart.screen.rows[20])
    assert 21 <= uart.screen.row <= 23
    assert not uart.timeouts
    assert term._presence_enabled and term._presence_seen


def test_stalled_write_has_a_finite_budget_and_does_not_replay_partial_output(paced, monkeypatch):
    term, uart, clock = paced()
    term.w(b"A" * 120)  # One second of queued transmission.
    calls = []

    def stalled(data):
        calls.append(data)
        uart.output.extend(data[:2])  # The exception does not report how much was sent.
        clock.sleep(uart.write_timeout)
        uart.pump()
        raise chat.serial.SerialTimeoutException("fixture private payload")

    monkeypatch.setattr(uart, "write", stalled)
    before = clock.now
    with pytest.raises(chat.terminal_link.Unavailable, match="SerialTimeoutException"):
        term.w(b"NEW")
    assert 3 <= clock.now - before < 3.1  # Backlog + payload + original two-second slack.
    assert calls == [b"NEW"]
    assert uart.output.endswith(b"AANE")


def test_write_budget_uses_driver_backlog_and_returns_to_baseline_after_drain(paced, monkeypatch):
    term, uart, clock = paced()
    with monkeypatch.context() as patch:
        patch.setattr(QueuedUART, "out_waiting", property(lambda self: 600))
        term.w(b"A")
        assert 7 <= uart.write_timeout < 7.1
    uart.finish()
    for _ in range(4):
        term.w(b"A" * 120)
        assert uart.write_timeout == pytest.approx(3)  # Do not add slack to the last enlarged budget.
        uart.finish()
    assert clock.now > 4


def full_page():
    return "\n".join(f"Ligne {i:02d} " + "x" * 28 for i in range(18))


def test_long_display_does_not_timeout_echo_handshake_or_reset_document(paced):
    term, uart, clock = paced(submitted("LIRE 1"))
    before = dict(uart.routes)
    result = terminal_documents.read(term, full_page())
    assert result == ("LIRE 1", "envoi")
    assert clock.now > 5  # More than the former one-second routing deadline.
    assert term.view is not None
    assert b"Saisie indisponible" not in uart.output
    uart.finish()
    assert uart.routes == before
    assert "Ligne 00" in "".join(uart.screen.rows[1])


def test_repeated_readers_drain_restore_acks_before_next_echo_snapshot(paced):
    term, uart, _clock = paced(submitted("MAIL"), direct=True)
    before = dict(uart.routes)
    for command in ("MAIL", "LIRE 1", "REPONDRE oui"):
        if command != "MAIL":
            uart.input.extend(submitted(command))
        assert terminal_documents.read(term, full_page()) == (command, "envoi")
        # Restoration must be acknowledged, not merely queued, before returning.
        assert uart.routes == before
        assert not uart.incoming and not uart.events
    assert all(copies == 0 for _, copies in uart.echo)


def test_silent_terminal_still_times_out_after_backlog_without_reading_secret(paced):
    term, uart, clock = paced(replies=False)
    term.w(b"A" * 1200)
    with pytest.raises(rose_terminal.EchoUnavailable):
        rose_terminal.routing_status(term, 0x58)
    assert 10 < clock.now < 13  # Ten seconds transmission, then a bounded reply budget.
    assert uart.input_reads == 0


@pytest.mark.parametrize("mode,keys,expected", [
    ({}, submitted("Nouvelle question"), ("Nouvelle question", "envoi")),
    ({"calculator_mode": True}, b"\x13\x42", ("RETOUR", "envoi")),
    ({"navigation_mode": True}, b"\x13\x48", ("SUIVANT", "envoi")),
])
def test_chat_and_pronote_input_remain_on_loaded_result(paced, mode, keys, expected):
    term, uart, _clock = paced(keys)
    before = dict(uart.routes)
    chat.show_response(term, full_page())
    assert chat.read_question(term, **mode) == expected
    assert term.view is not None  # The old fallback erased the view here.
    assert uart.routes == before
    assert uart.output.count(b"\x0c") == 1


def test_paged_document_accepts_commands_and_keeps_echo_off_after_redraw(paced):
    keys = b"REP" + b"\x13\x48" + b"\x13\x42" + submitted("ONDRE oui")
    term, uart, _clock = paced(keys)
    before = dict(uart.routes)
    assert terminal_documents.read(term, full_page() + "\n" + full_page()) == (
        "REPONDRE oui", "envoi")
    assert term.view.page == 0
    assert uart.routes == before
    assert uart.output.count(b"\x0c") == 1
    assert all(copies == 0 for _, copies in uart.echo)


def test_password_after_queued_screen_is_hidden_and_routes_restore(paced, monkeypatch):
    monkeypatch.setenv("ROSE_PASSWORD", "synthetic-secret")
    term, uart, _clock = paced(submitted("synthetic-secret"))
    before = dict(uart.routes)
    chat.show_response(term, full_page())
    assert rose_terminal.gate(term) == (True, "submit")
    assert b"synthetic-secret" not in uart.output
    assert all(copies == 0 for _, copies in uart.echo)
    assert uart.routes == before


def test_unconfirmed_restoration_is_a_link_failure_not_local_echo_fallback(paced):
    term, uart, _clock = paced()
    with (
        pytest.raises(chat.terminal_link.Unavailable, match="echo_restore_unconfirmed"),
        rose_terminal.hidden_echo(term),
    ):
        uart.replies = False


@pytest.mark.parametrize("reader", ["chat", "document"])
def test_distinct_full_page_redraw_with_production_presence_polling(paced, reader):
    term, uart, _clock = paced(b"\x13\x48" + submitted("FOLLOWUP"))
    term._presence_enabled = True
    term._presence_seen = True
    term.start_presence()
    text = "\n".join(["A" * 39] * 18 + ["Z" * 39] * 18)
    if reader == "chat":
        chat.show_response(term, text)
        result = chat.read_question(term)
    else:
        result = terminal_documents.read(term, text)
    assert result == ("FOLLOWUP", "envoi")
    assert term.view.page == 1
    assert term._presence_enabled and term._presence_seen
    assert "".join(uart.screen.rows[1]).startswith("Z" * 39)
    assert b"Saisie indisponible" not in uart.output


def test_repeated_distinct_page_navigation_preserves_command_with_presence_enabled(paced):
    keys = b"RE" + b"\x13\x48" * 2 + b"\x13\x42" * 2 + submitted("PONDRE oui")
    term, uart, _clock = paced(keys)
    text = "\n".join([letter * 39 for letter in "AZY" for _ in range(18)])
    assert terminal_documents.read(term, text) == ("REPONDRE oui", "envoi")
    assert term.view.page == 0
    assert term._presence_enabled and term._presence_seen
    assert uart.screen.rows[1][:39] == list("A" * 39)
    assert uart.output.count(b"\x0c") == 1


def test_fast_api_reply_queues_behind_upward_wipe_and_keyboard_recovers(paced, monkeypatch):
    term, uart, clock = paced(submitted("NEXT"))
    chat.show_response(term, "\n".join(["X" * 39] * 18))
    assert chat.read_question(term) == ("NEXT", "envoi")
    deliver = uart.deliver
    blank_seen = []

    def observe(data):
        deliver(data)
        content = ["".join(row) for row in uart.screen.rows[1:19]]
        if not any(line.strip() for line in content):
            blank_seen.append(clock.now)
        if "SHORT" in content[0]:
            assert blank_seen  # Old text is gone before the new reply starts.
            assert not any("X" in line for line in content)

    monkeypatch.setattr(uart, "deliver", observe)
    before = clock.now
    term.waiting("WAIT")
    assert clock.now == before  # Queue erasure; no sleep/flush before launching the API.
    assert 0 < uart.tx_until - clock.now < 4  # CAN queues occupied rows without repainting the frame.
    monkeypatch.setattr(chat, "call_answer", lambda *args, **kwargs: {"display": "SHORT", "turn": None})
    pending = chat.start_answer("", [], "fr", ("conversations", False, 30), "")
    pending[0].join(1)
    assert not pending[0].is_alive()
    result, action = chat.wait_answer(term, pending, 30)
    assert action == "done"
    assert clock.now < uart.tx_until  # A quick reply arrives while the wipe is still on the wire.
    chat.show_response(term, result["display"])
    uart.input.extend(submitted("AGAIN"))
    assert chat.read_question(term) == ("AGAIN", "envoi")
    assert term._presence_enabled and term._presence_seen
    assert uart.screen.rows[1][:5] == list("SHORT")
    assert not any("".join(row).strip() for row in uart.screen.rows[2:19])
    assert uart.output.count(b"\x0c") == 1
