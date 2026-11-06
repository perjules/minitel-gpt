"""French SS2 input/output and the white-ink gray-paper input field."""

import threading
from types import SimpleNamespace

import pytest
from test_console_terminal import Window
from test_rose_echo import RoutingSerial
from test_terminal_display import VisibleUART, assert_gray_input
from test_terminal_serial_timing import BufferedUART, Clock
from test_terminal_startup import fn

from services import journal_terminal, rose_terminal, videotex
from services import minitel_chatgpt as chat
from services.console_terminal import ConsoleSerial, Screen
from services.terminal_display import STANDARD, encode_runs


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(chat.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(chat.time, "sleep", clock.sleep)
    return clock


@pytest.mark.parametrize("char", list(videotex.GLYPHS))
def test_french_characters_are_one_cell_and_seven_bit_on_the_wire(clock, char):
    wire = videotex.encode(char)
    assert max(wire) < 128
    term = chat.Term(RoutingSerial(wire))
    assert term.read_key(1) == ("char", ord(char))
    screen = Screen()
    for code in wire:
        screen.write(bytes([code]))
    assert screen.rows[0][0] == char and (screen.row, screen.column) == (0, 1)
    assert encode_runs(char * 8) == wire * 8  # REP never splits SS2 sequences.


def test_dead_accent_waits_for_base_across_timeouts_and_protocol_replies(clock):
    uart = RoutingSerial(b"\x19\x42")
    term = chat.Term(uart)
    assert term.read_key(.05) == ("timeout", None)
    assert bytes(term._ss2) == b"\x19\x42"
    clock.sleep(2)
    uart.keys.extend(b"\x1b\x3a\x71\x40e")
    assert term.read_key(1) == ("char", ord("é"))
    assert not term._ss2


@pytest.mark.parametrize("key", [chat.K_SOMMAIRE, chat.K_RETOUR, chat.K_CORR, chat.K_ENVOI])
def test_function_key_cancels_pending_accent_without_being_swallowed(clock, key):
    term = chat.Term(RoutingSerial(b"\x19\x42" + fn(key) + b"a"))
    assert term.read_key(1) == ("fn", key)
    assert term.read_key(1) == ("char", ord("a"))


def test_unknown_and_incomplete_ss2_never_leak_selector_letters(clock):
    uart = RoutingSerial(b"\x19\x41Z" + fn(chat.K_RETOUR))
    term = chat.Term(uart)
    assert term.read_key(1) == ("char", ord("?"))
    assert term.read_key(1) == ("fn", chat.K_RETOUR)
    uart.keys.extend(b"\x19\x42")
    assert term.read_key(.05) == ("timeout", None)
    term.clear()
    uart.keys.extend(b"e")
    assert term.read_key(1) == ("char", ord("e"))


@pytest.mark.parametrize("reader", ["chat", "rose", "rose_secret", "memory_secret", "journal_secret"])
def test_readers_receive_complete_french_characters_not_protocol_bytes(clock, reader):
    text = "été à Noël, ça va ?"
    uart = VisibleUART(videotex.encode(text) + fn(chat.K_ENVOI))
    term = chat.Term(uart)
    if reader == "chat":
        chat.show_response(term, "Réponse française")
        assert chat.read_question(term) == (text, "envoi")
        assert uart.lines()[21].startswith("> " + text)
        assert_gray_input(uart)
    elif reader == "rose":
        rose_terminal.display(term, "Bonjour", framed=True, safeword="navet")
        assert rose_terminal.read_field(term, safeword="navet") == (text, "submit")
        assert uart.lines()[21].startswith("> " + text)
    else:
        with rose_terminal.hidden_echo(term):
            if reader == "rose_secret":
                result = rose_terminal.read_field(term, secret=True)
            elif reader == "memory_secret":
                result = chat.read_memory_password(term)
            else:
                session = SimpleNamespace(secret=True, lock=threading.RLock(), check=lambda: None, touch=lambda: None)
                result = journal_terminal.read_field(term, session)
        assert result == (text, "submit")
        assert b"\x19" not in uart.output  # No native glyph output from secret fields.
    assert all(byte < 128 for byte in uart.output)
    assert all(copies == 0 for _, copies in uart.echo)


def test_output_preserves_native_lowercase_accents_and_falls_back_for_capitals(clock):
    uart = VisibleUART()
    term = chat.Term(uart)
    chat.show_response(term, "Déjà prête : école, Noël, cœur. É À Ç")
    assert uart.lines()[1].startswith("Déjà prête : école, Noël, cœur. E A C")
    assert b"\x19\x42e" in uart.output
    assert all(byte < 128 for byte in uart.output)


def test_white_input_retains_c2_background_delimiters_through_editing_and_clearing(clock):
    uart = VisibleUART()
    term = chat.Term(uart)
    chat.show_response(term, "Text")
    for text in ("white text", "é " * 70, "short", "", "x" * 110, "x" * 111):
        term.view.buffer = list(text)
        term.view.input()
        assert_gray_input(uart)
        assert all(uart.screen.zones[row][1] == 7 for row in range(21, 24))
        assert 21 <= uart.screen.row <= 23 and 2 <= uart.screen.column <= 38
    term.view.busy("Wait")
    assert_gray_input(uart)
    assert all(uart.screen.zones[row][1] == 7 for row in range(21, 24))


def test_can_does_not_create_a_background_delimiter_and_keeps_existing_zone():
    screen = Screen()
    screen.write(STANDARD.position(22) + b"\x1b\x5d\x1b\x42\x1b\x50\x18>\x1b\x57 abc")
    assert screen.styles[21][2:] == [(2, True, 7)] * 38
    screen.write(STANDARD.position(22, 3) + b"\x1b\x5d\x1b\x42\x1b\x57\x18")
    assert screen.zones[21] == {1: 7}
    assert screen.styles[21][2:] == [(2, True, 7)] * 38
    # Overwriting the delimiter removes that zone, as on the real decoder.
    screen.write(STANDARD.position(22, 2) + b"X")
    assert screen.styles[21][2][2] == 0


def test_french_output_uses_encoded_byte_budget_under_usb_backpressure(clock):
    uart = BufferedUART(clock, b"OK" + fn(chat.K_ENVOI), capacity=320)
    term = chat.Term(uart)
    chat.show_response(term, "\n".join(["é" * 39] * 18))
    assert chat.read_question(term) == ("OK", "envoi")
    assert uart.screen.rows[1][:39] == list("é" * 39)
    assert uart.waits and not uart.timeouts
    assert term._tx_until >= len(uart.output) / 120


def test_console_accent_keys_round_trip_without_changing_case(clock):
    uart = ConsoleSerial(Window(["é", "\n"]))
    term = chat.Term(uart)
    chat.show_response(term, "Prête")
    assert chat.read_question(term) == ("é", "envoi")
    assert "".join(uart.screen.rows[21]).startswith("> é")
