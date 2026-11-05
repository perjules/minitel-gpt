"""Offline byte/procedure checks only: never open UART or call systemd."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import display_probe as probe


@pytest.mark.parametrize("state,code,allowed", [
    ("inactive\n", 0, True), ("failed\n", 0, True),
    ("active\n", 0, False), ("activating\n", 0, False),
    ("deactivating\n", 0, False), ("", 1, False),
])
def test_service_must_be_confirmed_stopped(monkeypatch, state, code, allowed):
    run = Mock(side_effect=[SimpleNamespace(stdout=state, returncode=code),
                            SimpleNamespace(returncode=1)])
    monkeypatch.setattr(probe.subprocess, "run", run)
    if allowed:
        probe.check_idle("/dev/test")
        assert run.call_count == 2
    else:
        with pytest.raises(RuntimeError, match="Stop minitel"):
            probe.check_idle("/dev/test")
        assert run.call_count == 1


@pytest.mark.parametrize("code", [0, 2])
def test_busy_port_or_failed_check_refuses(monkeypatch, code):
    monkeypatch.setattr(probe.subprocess, "run", Mock(side_effect=[
        SimpleNamespace(stdout="inactive", returncode=0), SimpleNamespace(returncode=code)]))
    with pytest.raises(RuntimeError, match="UART is busy"):
        probe.check_idle("/dev/test")


def harness(monkeypatch):
    p = probe.Probe(Mock())
    monkeypatch.setattr(p, "send", Mock())
    monkeypatch.setattr(p, "exchange", Mock(return_value=b""))
    monkeypatch.setattr(p, "receive", Mock(return_value=b""))
    return p


def test_unacknowledged_mixte_never_sends_compact_page(monkeypatch):
    p = harness(monkeypatch)
    monkeypatch.setattr(p, "observe", Mock())
    p.run()
    p.observe.assert_called_once_with(False)
    assert p.exchange.call_args_list[-1].args[1] == bytes.fromhex("1b 3a 32 7e")
    assert any(c.args[1] == bytes.fromhex("1b 3a 32 7d") for c in p.exchange.call_args_list)


@pytest.mark.parametrize("error", [KeyboardInterrupt, OSError, TimeoutError])
def test_cleanup_after_compact_interruption(monkeypatch, error):
    p = harness(monkeypatch)
    p.exchange.return_value = b"\x13p"
    p.observe = Mock(side_effect=[None, error()])
    with pytest.raises(error):
        p.run()
    assert p.observe.call_args_list[-1].args == (True,)
    assert p.exchange.call_args_list[-1].args[1] == probe.VIDEO
    assert probe.PAGE in p.send.call_args.args[0]


def test_restore_only_never_runs_experiment(monkeypatch):
    p = harness(monkeypatch)
    p.observe = Mock()
    p.run(restore_only=True)
    p.observe.assert_not_called()
    assert [call.args[1] for call in p.exchange.call_args_list] == [probe.VIDEO]


def test_partial_echo_change_restores_exact_routes(monkeypatch):
    p = harness(monkeypatch)
    p.exchange.side_effect = [b"\x1b\x3b\x63\x58\x41",  # direct route off
                             b"\x1b\x3b\x63\x5a\x43",  # loop route on
                             b"\x1b\x3b\x63\x58\x41", b""]
    with pytest.raises(RuntimeError, match="routing status"), p.without_echo():
        pytest.fail("Must not enter without both acknowledgements")
    assert [c.args[0] for c in p.send.call_args_list] == [
        bytes.fromhex("1b 3b 60 58 51"), bytes.fromhex("1b 3b 61 5a 51")]
    assert p.saved_routes == {}


def test_missing_snapshot_never_mutates_echo(monkeypatch):
    p = harness(monkeypatch)
    p.exchange.return_value = b""
    with pytest.raises(RuntimeError), p.without_echo():
        pytest.fail("No snapshot")
    p.send.assert_not_called()


def test_echo_restored_on_exception_and_mode_restore_even_if_route_fails(monkeypatch):
    p = harness(monkeypatch)
    p.routing = Mock(side_effect=[True, False, False, False])
    with pytest.raises(KeyboardInterrupt), p.without_echo():
        raise KeyboardInterrupt
    assert [c.args[0] for c in p.send.call_args_list] == [
        bytes.fromhex("1b 3b 61 58 51"), bytes.fromhex("1b 3b 60 5a 51")]
    p.saved_routes = {0x58: True, 0x5A: False}
    p.send.side_effect = [None, OSError(), None]
    with pytest.raises(OSError):
        p.restore()
    assert p.exchange.call_args.args[1] == probe.VIDEO
    assert p.saved_routes == {0x58: True}
    assert p.send.call_args.args[0] == bytes.fromhex("1b 3b 60 5a 51")


def test_both_pages_address_all_rows_and_use_distinct_encodings():
    standard, compact = probe.test_page(False), probe.test_page(True)
    for row in range(1, 25):
        assert probe.position(row, 1, False) in standard
        assert probe.position(row, 1, True) in compact
    assert b"1234567890" * 4 in standard
    assert b"1234567890" * 8 in compact
    assert b"\x19\x42e" in standard  # Videotex SS2 accent
    assert b"\x0e@ [ \\ ] { | } ~\x0f" in compact  # French G1 / US G0
    assert b"\x1b[1m" in compact and b"\x1b[4m" in compact
    assert b"\x1b[1;4m" not in compact  # Matra SGR limitation
    assert b"\r\n" not in compact
    assert all(byte < 128 for byte in standard + compact)
    assert probe.position(24, 80, True) == b"\x1b[24;80H"
    with pytest.raises(ValueError):
        probe.position(25, 1, True)
    with pytest.raises(ValueError):
        probe.position(24, 80, False)


def test_transport_settings_exclusivity_and_no_bytes_on_race(monkeypatch):
    monkeypatch.setattr(probe.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0)
    monkeypatch.setattr(probe, "check_idle", Mock())
    uart = Mock()
    uart.fileno.return_value = 123
    @contextmanager
    def connection(*args, **kwargs):
        assert args == ("/dev/test", 1200)
        assert kwargs == {"bytesize": 7, "parity": "E", "stopbits": 1, "timeout": 0.05,
                              "write_timeout": 2, "exclusive": True, "xonxoff": False,
                              "rtscts": False, "dsrdtr": False}
        yield uart
    monkeypatch.setattr(probe.serial, "Serial", connection)
    ioctl = Mock()
    monkeypatch.setattr(probe.fcntl, "ioctl", ioctl)
    monkeypatch.setattr(probe.subprocess, "run", Mock(return_value=SimpleNamespace(
        returncode=0, stdout=f"{probe.os.getpid()} 99999")))
    assert probe.main(["--port", "/dev/test"]) == 1
    ioctl.assert_called_once_with(123, probe.termios.TIOCEXCL)
    uart.write.assert_not_called()


def test_paced_write_detects_partial_write(monkeypatch):
    monkeypatch.setattr(probe.time, "sleep", Mock())
    uart = Mock()
    uart.write.return_value = 1
    with pytest.raises(OSError, match="Short UART"):
        probe.Probe(uart).send(b"abc")


def test_quit_and_timeout_during_observation(monkeypatch):
    p = harness(monkeypatch)
    p.uart.read.return_value = b""
    monkeypatch.setattr(probe.select, "select", lambda *args: ([probe.sys.stdin], [], []))
    monkeypatch.setattr(probe.sys.stdin, "readline", lambda: "q\n")
    with pytest.raises(KeyboardInterrupt):
        p.pause("test")
    with pytest.raises(TimeoutError):
        p.pause("test", seconds=0)
