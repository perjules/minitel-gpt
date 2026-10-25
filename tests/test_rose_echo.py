"""Model actual Minitel routing, not the console's software echo switch."""
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import minitel_chatgpt as chat
from services import rose_terminal as rose
from services import terminal_link


def submitted(text):
    return text.encode() + bytes([chat.SEP, chat.K_ENVOI])


class RoutingSerial:
    """Local modem loop plus optional direct echo; UART receives each key once."""

    def __init__(self, keys=b"", *, direct=False, modem=True):
        self.routes = {0x58: 0x4D | (0x02 if direct else 0),
                       0x5A: 0x44 | (0x02 if modem else 0)}
        self.keys = deque(keys)  # Future human keystrokes, not buffered UART data.
        self.incoming = deque()
        self.output = bytearray()
        self.echo = []
        self.commands = []
        self.respond = True
        self.ignore_off = False
        self.before_restore = None
        self.function = False

    def reset_input_buffer(self):
        self.incoming.clear()

    def write(self, data):
        self.output.extend(data)
        if data[:3] == b"\x1b\x3a\x62":
            receiver = data[3]
        elif data[:2] == b"\x1b\x3b" and data[2] in (0x60, 0x61):
            self.commands.append(data)
            receiver = data[3]
            assert data[4] == 0x51  # Only keyboard routes may be changed.
            if data[2] == 0x61 and self.before_restore:
                self.before_restore(receiver)
            if data[2] == 0x61:
                self.routes[receiver] |= 0x02
            elif not self.ignore_off:
                self.routes[receiver] &= ~0x02
        else:
            return len(data)
        if self.respond:
            self.incoming.extend(b"\x1b\x3b\x63" + bytes([receiver, self.routes[receiver]]))
        return len(data)

    def read(self, count):
        if self.incoming:
            return bytes([self.incoming.popleft()])
        if not self.keys:
            return b""
        code = self.keys.popleft()
        if isinstance(code, BaseException):
            raise code
        if self.function:
            self.function = False
        elif code == chat.SEP:
            self.function = True
        elif 0x20 <= code <= 0x7E:
            copies = int(bool(self.routes[0x58] & 0x02))
            copies += int(bool(self.routes[0x5A] & 0x02) and bool(self.routes[0x58] & 0x04))
            self.echo.append((chr(code), copies))
        return bytes([code])


@pytest.fixture
def hardware(monkeypatch):
    monkeypatch.setenv("ROSE_PASSWORD", "fixture")
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    def make(keys=b"", **kwargs):
        serial = RoutingSerial(keys, **kwargs)
        return chat.Term(serial), serial
    return make


@pytest.mark.parametrize("direct,modem", [(False, True), (True, False), (False, False), (True, True)])
def test_password_is_hidden_and_exact_original_routes_are_restored(hardware, direct, modem):
    term, serial = hardware(submitted("fixture"), direct=direct, modem=modem)
    before = dict(serial.routes)
    assert rose.gate(term) == (True, "submit")
    assert serial.echo == [(c, 0) for c in "fixture"]
    assert b"fixture" not in serial.output
    assert serial.routes == before
    serial.keys.extend(submitted("A"))
    assert rose.read_field(term) == ("A", "submit")
    assert serial.echo[-1] == ("A", int(direct) + int(modem))


def test_selection_exit_reentry_and_ordinary_input_keep_one_echo(hardware):
    # Exercise the real byte decoder, gate and selection loop, without a provider.
    keys = submitted("fixture") + submitted("F") + bytes([chat.SEP, chat.K_RETOUR])
    term, serial = hardware(keys)
    before = dict(serial.routes)
    model = Mock()
    assert rose.run(term, "", model) == ("retour", None)
    model.ready.assert_not_called()  # Left the character list before a model call.
    assert serial.echo == [(c, 0) for c in "fixture"] + [("F", 1)]
    assert serial.routes == before
    serial.keys.extend(submitted("ABC"))
    assert chat.read_question(term) == ("ABC", "envoi")
    assert serial.echo[-3:] == [(c, 1) for c in "ABC"]
    # Re-enter and exercise actual character-name input too.
    serial.keys.extend(submitted("fixture") + submitted("F") + submitted("Jacqueline"))
    assert rose.gate(term)[0]
    assert rose.welcome(term) == ("femme", "submit")
    assert rose.roster(term, "femme") == ("Jacqueline", "submit")
    assert serial.echo[-len("FJacqueline"):] == [(c, 1) for c in "FJacqueline"]
    assert serial.routes == before


@pytest.mark.parametrize("keys,expected", [
    (submitted("wrong"), (False, "submit")),
    (submitted("RETOUR"), (False, "retour")),
    (bytes([chat.SEP, chat.K_SOMMAIRE]), (False, "sommaire")),
    (submitted("x" * 150), (False, "overflow")),
])
def test_rejected_or_cancelled_gate_restores_echo(hardware, keys, expected):
    term, serial = hardware(keys)
    before = dict(serial.routes)
    assert rose.gate(term) == expected
    assert all(copies == 0 for _, copies in serial.echo)
    assert serial.routes == before


def test_gate_timeout_restores_echo(hardware, monkeypatch):
    term, serial = hardware()
    before = dict(serial.routes)
    monkeypatch.setattr(term, "read_key", lambda _: ("timeout", None))
    assert rose.gate(term) == (False, "timeout")
    assert serial.routes == before


def test_disconnect_and_failed_screen_clear_still_attempt_both_route_restorations(hardware, monkeypatch):
    term, serial = hardware([OSError("disconnected")], direct=True)
    clear = Mock(side_effect=[None, terminal_link.Unavailable("clear failed")])
    monkeypatch.setattr(term, "clear", clear)
    with pytest.raises(terminal_link.Unavailable):
        rose.gate(term)
    assert serial.commands[-2:] == [b"\x1b\x3b\x61\x58\x51", b"\x1b\x3b\x61\x5a\x51"]


def test_restoration_attempts_other_route_even_when_first_write_fails(hardware):
    term, serial = hardware(direct=True)
    before = dict(serial.routes)
    def fail_screen(receiver):
        if receiver == 0x58:
            raise OSError("disconnected")
    serial.before_restore = fail_screen
    with pytest.raises(terminal_link.Unavailable), rose.hidden_echo(term):
        assert not any(serial.routes[r] & 0x02 for r in before)
    assert serial.routes[0x5A] == before[0x5A]
    assert serial.commands[-1] == b"\x1b\x3b\x61\x5a\x51"


def test_unacknowledged_routing_never_requests_or_reads_password(hardware, monkeypatch):
    term, serial = hardware(submitted("fixture"))
    before = dict(serial.routes)
    serial.respond = False
    clock = iter(i * .1 for i in range(100))
    monkeypatch.setattr(rose, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    # No keyboard input may be consumed by the status query in this fixture.
    monkeypatch.setattr(term, "read_byte", lambda: None)
    assert rose.gate(term) == (False, "retour")
    assert b"Mot de passe" not in serial.output
    assert serial.routes == before and not serial.echo


def test_terminal_refusing_echo_off_does_not_request_password(hardware):
    term, serial = hardware(submitted("fixture"))
    before = dict(serial.routes)
    serial.ignore_off = True
    assert rose.gate(term) == (False, "retour")
    assert b"Mot de passe" not in serial.output and not serial.echo
    assert serial.routes == before


def test_wrong_module_or_malformed_status_cannot_be_used_as_snapshot(hardware, monkeypatch):
    term, _serial = hardware()
    codes = deque(b"\x1b\x3b\x63\x5a\x42\x1b\x3b\x63\x58\x7f")
    monkeypatch.setattr(term, "read_byte", lambda: codes.popleft() if codes else None)
    clock = iter(i * .01 for i in range(200))
    monkeypatch.setattr(rose, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises(rose.EchoUnavailable):
        rose.routing_status(term, 0x58)


def test_console_preserves_preexisting_disabled_echo(hardware):
    term, serial = hardware(submitted("fixture"))
    serial.local_echo = False
    serial.set_local_echo = Mock()
    assert rose.gate(term)[0]
    assert [c.args for c in serial.set_local_echo.call_args_list] == [(False,), (False,)]
    assert serial.commands == []
