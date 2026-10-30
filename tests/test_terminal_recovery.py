"""Offline transport failures, quiet retries, session retirement and UART recovery."""
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import serial

from services import minitel_chatgpt as chat
from services import terminal_link


class StopSimulation(Exception):
    pass


def test_missing_terminal_backs_off_and_summarizes_instead_of_flooding(caplog):
    now = [0]
    delays = []

    def sleep(seconds):
        delays.append(seconds)
        now[0] += seconds
        if len(delays) == 100:
            raise StopSimulation

    def missing():
        raise terminal_link.Unavailable("SerialException")

    with caplog.at_level(logging.INFO), pytest.raises(StopSimulation):
        terminal_link.supervise(missing, Mock(), sleep=sleep, clock=lambda: now[0])
    assert delays[:5] == [5, 10, 30, 60, 60]
    assert all(value == 60 for value in delays[4:])
    assert len(caplog.records) <= 1 + int(now[0] / 300)
    assert "Traceback" not in caplog.text


def test_flapping_open_or_brief_success_does_not_report_recovery(caplog):
    now = [0]
    opened = []

    def factory():
        terminal = SimpleNamespace(close=Mock())
        opened.append(terminal)
        return terminal

    def session(terminal):
        terminal.link_activity()
        raise terminal_link.Unavailable("SerialException")

    def sleep(seconds):
        now[0] += seconds
        if len(opened) == 20:
            raise StopSimulation

    with caplog.at_level(logging.INFO), pytest.raises(StopSimulation):
        terminal_link.supervise(factory, session, sleep=sleep, clock=lambda: now[0])
    assert "stable again" not in caplog.text
    assert all(t.close.call_count == 1 for t in opened)
    assert sum("link unavailable" in r.message for r in caplog.records) == 1


def test_successful_recovery_requires_stable_io_and_closes_old_transport(caplog):
    now = [0]
    old, new = SimpleNamespace(close=Mock()), SimpleNamespace(close=Mock())
    opened = iter([old, new])

    def session(terminal):
        if terminal is old:
            raise terminal_link.Unavailable("SerialException")
        now[0] += 31
        terminal.link_activity()
        terminal.link_activity()

    with caplog.at_level(logging.INFO):
        terminal_link.supervise(lambda: next(opened), session,
                                sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
                                clock=lambda: now[0])
    assert sum("stable again" in r.message for r in caplog.records) == 1
    old.close.assert_called_once()
    new.close.assert_called_once()


def test_recovery_waits_for_discarded_worker_before_starting_another_session():
    worker = Mock()
    worker.is_alive.side_effect = [True, True, False]
    old = SimpleNamespace(close=Mock(), _pending=(worker, Mock(), __import__("time").monotonic()))
    new = SimpleNamespace(close=Mock())
    opened = iter([old, new])
    waits = []

    def session(term):
        if term is old:
            raise terminal_link.Unavailable("SerialException")
        assert waits == [5, 1, 1]

    terminal_link.supervise(lambda: next(opened), session, sleep=waits.append)


@pytest.mark.parametrize("failure", [serial.SerialException("device payload"),
                                     serial.SerialTimeoutException("device payload"), OSError("device payload")])
def test_serial_failures_are_classified_without_payloads(failure):
    term = chat.Term(transport=SimpleNamespace(write=Mock(side_effect=failure)))
    with pytest.raises(terminal_link.Unavailable) as caught:
        term.w("hello")
    assert "device payload" not in str(caught.value)


def test_serial_reads_can_be_empty_without_reporting_an_error(monkeypatch):
    pause = Mock()
    monkeypatch.setattr(chat.time, "sleep", pause)
    term = chat.Term(transport=SimpleNamespace(read=Mock(return_value=b"")))
    assert term.read_byte() is None
    pause.assert_called_once_with(0.01)


def test_adapter_is_rediscovered_and_writes_have_a_deadline(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(chat.serial, "Serial", factory)
    monkeypatch.setattr(chat, "detect_port", Mock(side_effect=["/dev/ttyUSB0", "/dev/ttyUSB1"]))
    monkeypatch.setattr(chat.time, "sleep", Mock())
    chat.Term()
    chat.Term()
    assert [call.args[0] for call in factory.call_args_list] == ["/dev/ttyUSB0", "/dev/ttyUSB1"]
    assert all(call.kwargs["write_timeout"] == 2 for call in factory.call_args_list)


def test_session_cleanup_runs_on_disconnect_and_does_not_reuse_pending_output(monkeypatch):
    terminal = chat.Term(transport=SimpleNamespace(close=Mock()))
    invalidate_mail, close_memory = Mock(), Mock()
    abandoned = (Mock(), Mock(), 0)

    def broken_session(term):
        term._session_resources = (invalidate_mail, close_memory)
        term._pending = abandoned
        raise terminal_link.Unavailable("SerialException")

    monkeypatch.setattr(chat, "run_session", broken_session)
    with pytest.raises(terminal_link.Unavailable):
        chat.run(terminal)
    invalidate_mail.assert_called_once()
    close_memory.assert_called_once()
    assert terminal._pending is abandoned

    def fresh_session(term):
        assert term._pending is None and term._session_resources == ()

    monkeypatch.setattr(chat, "run_session", fresh_session)
    chat.run(terminal)


def test_unexpected_errors_keep_their_traceback_and_are_not_retried():
    term = SimpleNamespace(close=Mock())
    session = Mock(side_effect=ValueError("programming error"))
    sleep = Mock()
    with pytest.raises(ValueError, match="programming error"):
        terminal_link.supervise(lambda: term, session, sleep=sleep)
    sleep.assert_not_called()
    term.close.assert_called_once()


def monitored_terminal(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(chat.time, "monotonic", lambda: now[0])
    output = Mock(return_value=None)
    term = chat.Term(transport=SimpleNamespace(write=output))
    term._presence_enabled = True
    term.start_presence()
    return term, now, output


def test_status_polling_retires_a_previously_responsive_terminal(monkeypatch):
    term, now, output = monitored_terminal(monkeypatch)
    term.poll_presence()
    term.presence_reply()
    for instant in (2, 3, 4, 5, 6):
        now[0] = instant
        term.poll_presence()
    now[0] = 7
    with pytest.raises(terminal_link.Unavailable, match="terminal_status_lost"):
        term.poll_presence()
    assert all(call.args == (b"\x1b\x39\x70",) for call in output.call_args_list)


def test_unknown_status_support_never_turns_keyboard_silence_into_power_loss(monkeypatch, caplog):
    term, now, output = monitored_terminal(monkeypatch)
    with caplog.at_level(logging.INFO):
        for instant in range(20):
            now[0] = instant
            term.poll_presence()
    assert output.call_count == 3
    assert not term._presence_enabled
    assert sum("power detection disabled" in record.message for record in caplog.records) == 1


def test_idle_status_replies_and_keyboard_activity_preserve_established_link(monkeypatch):
    term, now, output = monitored_terminal(monkeypatch)
    for instant in range(0, 600, 2):
        now[0] = instant
        term.poll_presence()
        term.presence_reply()
    assert output.call_count == 300
    assert term._presence_enabled
    # One delayed response, followed by a real key, is sufficient evidence of life.
    term._presence_misses = 2
    term.keyboard_activity()
    assert term._presence_misses == 0


def test_console_and_unactivated_terminals_do_not_receive_status_probes(monkeypatch):
    term, now, output = monitored_terminal(monkeypatch)
    term._presence_due = float("inf")
    term.poll_presence()
    output.assert_not_called()
    term.start_presence()
    term._presence_enabled = False
    now[0] = 1000
    term.poll_presence()
    output.assert_not_called()


def test_status_reply_window_accounts_for_boot_bytes_still_queued_at_1200_baud(monkeypatch):
    term, now, output = monitored_terminal(monkeypatch)
    term.s.out_waiting = 1200
    term.poll_presence()
    for instant in range(1, 11):
        now[0] = instant
        term.poll_presence()
    output.assert_called_once_with(b"\x1b\x39\x70")
    assert term._presence_misses == 0
    term.presence_reply()
    assert term._presence_seen


def test_paused_probe_stays_paused_on_keys_and_restarts_with_fresh_deadline(monkeypatch):
    term, now, output = monitored_terminal(monkeypatch)
    term._presence_seen = True
    term._presence_deadline = 1
    term._presence_misses = 2
    term.pause_presence()
    now[0] = 31
    term.keyboard_activity()
    term.poll_presence()
    output.assert_not_called()
    assert term._presence_deadline is None and term._presence_misses == 0
    term.start_presence()
    term.poll_presence()
    output.assert_called_once_with(b"\x1b\x39\x70")
    assert term._presence_seen and term._presence_deadline == 32
