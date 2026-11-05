"""Offline intent/recovery checks. No UART, systemd, accounts or live APIs."""

import json
from unittest.mock import Mock

import pytest

from scripts import display_probe as probe


def harness(monkeypatch, tmp_path):
    p = probe.InteractionProbe(Mock(), tmp_path / "report.json")
    monkeypatch.setattr(p, "send", Mock())
    monkeypatch.setattr(p, "receive", Mock(return_value=b""))
    monkeypatch.setattr(p, "exchange", Mock(return_value=b""))
    monkeypatch.setattr(p, "observation", Mock(return_value=b""))
    return p


@pytest.mark.parametrize("count,encoded", [(1, b"X\x12@"), (2, b"X\x12A"),
                                         (39, b"X\x12f"), (64, b"X\x12\x7f")])
def test_repeat_counts_are_additional_not_total(count, encoded):
    assert probe.repeat(b"X", count) == encoded


@pytest.mark.parametrize("count", [0, -1, 65])
def test_repeat_rejects_out_of_range(count):
    with pytest.raises(ValueError):
        probe.repeat(b"X", count)


@pytest.mark.parametrize("reply,expected", [(b"", None), (b"\x1b:s", None),
                                           (b"\x1b:s\x00", None),
                                           (b"noise\x1b:sH\x13A", 0x48)])
def test_status_requires_complete_bounded_frame(reply, expected):
    assert probe.mode_status(reply) == expected


def test_report_collision_never_truncates_existing_file(tmp_path):
    report = tmp_path / "report.json"
    report.write_text("previous run")
    uart = Mock()
    with pytest.raises(FileExistsError):
        probe.InteractionProbe(uart, report)
    assert report.read_text() == "previous run"
    uart.write.assert_not_called()


def test_can_order_attributes_boundaries_and_cursor_proofs(monkeypatch, tmp_path):
    p = harness(monkeypatch, tmp_path)
    p.clear_tests()
    can = [c.args[0] for c in p.send.call_args_list if c.args[0].endswith(b"\x18")]
    assert can == [probe.position(row, col, False) + probe.NORMAL +
                   (probe.GRAY if gray else b"") + b"\x18"
                   for row, col, gray in ((24, 1, False), (14, 11, True), (12, 11, False),
                                          (10, 1, True), (8, 1, False), (16, 1, False))]
    assert all(c[1]["result"] == "unknown" for c in p.result["checks"].items())
    assert p.observation.call_args_list[-1].args[0] == "C3-redraw"


@pytest.mark.parametrize("reply,result,actual", [(b"\x1fVR", "fail", [22, 18]),
                                                (b"junk\x1fVC", "pass", [22, 3]),
                                                (b"", "unknown", None)])
def test_cursor_result_not_inferred_from_successful_write(monkeypatch, tmp_path, reply, result, actual):
    p = harness(monkeypatch, tmp_path)
    p.exchange.return_value = reply
    p.cursor_check("caret", 22, 3)
    assert p.result["checks"]["caret"] == {"expected": [22, 3], "actual": actual, "result": result}


def test_repeat_has_literal_controls_and_gray_accent_cases(monkeypatch, tmp_path):
    p = harness(monkeypatch, tmp_path)
    p.repeat_tests()
    commands = [c.args[0] for c in p.send.call_args_list]
    assert any(c.endswith(b"-" * 39) for c in commands)
    assert any(c.endswith(b"-\x12f") for c in commands)
    assert any(c.endswith(probe.GRAY + b" \x12f") for c in commands)
    assert any(c.endswith(b"\x19Be\x12G") for c in commands)
    assert p.result["checks"]["REP-gray-39-cells"]["expected"] == [7, 40]


def test_case_without_snapshot_never_mutates_keyboard(monkeypatch, tmp_path):
    p = harness(monkeypatch, tmp_path)
    p.lowercase_tests()
    assert [c.args[1] for c in p.exchange.call_args_list] == [probe.MODE_STATUS]
    assert not p.lower_changed
    assert p.result["observations"]["K2-lowercase"]["result"] == "unknown"


@pytest.mark.parametrize("original", [False, True])
def test_case_exact_original_restored_and_symbols_captured_in_both_modes(monkeypatch, tmp_path, original):
    p = harness(monkeypatch, tmp_path)
    initial = b"\x1b:s" + bytes([0x48 if original else 0x40])
    p.exchange.side_effect = [initial, b"\x1b:s@", b"\x1b:sH", initial]
    p.lowercase_tests()
    assert [c.args[1] for c in p.exchange.call_args_list] == [
        probe.MODE_STATUS, probe.LOWER_OFF, probe.LOWER_ON,
        probe.LOWER_ON if original else probe.LOWER_OFF]
    assert not p.lower_changed
    assert len(p.observation.call_args_list) == 3
    for call in p.observation.call_args_list:
        assert "accent" in call.args[1] and "Local echo" in call.args[1]


def test_partial_case_write_is_still_restored(monkeypatch, tmp_path):
    p = harness(monkeypatch, tmp_path)
    p.exchange.side_effect = [b"\x1b:sH", OSError("partial write"), b"\x1b:sH"]
    with pytest.raises(OSError):
        p.lowercase_tests()
    assert p.lower_changed
    p.restore_case()
    assert p.exchange.call_args.args[1] == probe.LOWER_ON
    assert not p.lower_changed


@pytest.mark.parametrize("reply", [b"", b"\x1b:sH"])
def test_case_restore_missing_or_wrong_ack_keeps_recovery_pending(monkeypatch, tmp_path, reply):
    p = harness(monkeypatch, tmp_path)
    p.lower_changed, p.saved_lower = True, False
    p.exchange.return_value = reply
    with pytest.raises(RuntimeError, match="Case restoration"):
        p.restore_case()
    assert p.lower_changed


def test_echo_restore_checks_both_routes_and_keeps_failures(monkeypatch, tmp_path):
    p = harness(monkeypatch, tmp_path)
    p.saved_routes = {0x58: True, 0x5A: False}
    p.exchange.side_effect = [b"\x1b;cXO", b"\x1b;cZF"]  # modem stayed ON
    with pytest.raises(RuntimeError, match="Echo restoration"):
        p.restore_routes()
    assert p.saved_routes == {0x5A: False}
    assert len(p.exchange.call_args_list) == 2


def test_recovery_attempts_every_independent_action_and_records_failure(monkeypatch, tmp_path):
    p = harness(monkeypatch, tmp_path)
    p.restore_case = Mock(side_effect=RuntimeError("case failed"))
    p.restore_routes = Mock(side_effect=RuntimeError("routes failed"))
    with pytest.raises(RuntimeError, match="case failed; routes failed"):
        p.restore()
    p.exchange.assert_called_once_with("restore page mode", probe.PAGE)
    p.send.assert_called_once()
    report = json.loads(p.report.read_text())
    assert report["recovery"] == "unconfirmed"
    assert report["recovery_errors"] == ["case failed", "routes failed"]


@pytest.mark.parametrize("error", [KeyboardInterrupt, OSError, TimeoutError])
def test_all_interrupt_paths_restore_without_80_column_or_reset_commands(monkeypatch, tmp_path, error):
    p = harness(monkeypatch, tmp_path)
    p.keyboard_test = Mock(side_effect=error())
    p.restore_case = Mock()
    p.restore_routes = Mock()
    with pytest.raises(error):
        p.run()
    p.restore_case.assert_called_once()
    p.restore_routes.assert_called_once()
    commands = [c.args[1] for c in p.exchange.call_args_list]
    assert probe.MIXED not in commands and probe.VIDEO not in commands
    assert b"\x1b\x39\x7f" not in commands
    assert p.result["recovery"] == "commands_completed_verify_visually"
    assert "Completed: False" in probe.report_summary(p.result)


def test_keyboard_observation_keeps_all_bytes_without_software_echo(monkeypatch, tmp_path):
    # Use actual receive/event/report methods; no real transport or blocking time.
    uart = Mock()
    uart.read.side_effect = [b"\x19", b"\x42", b"e", b"\x13", b"C"]
    p = probe.InteractionProbe(uart, tmp_path / "raw.json")
    p.receive = Mock(return_value=b"")
    responses = iter([([], [], [])] * 4 + [([probe.sys.stdin], [], [])])
    monkeypatch.setattr(probe.select, "select", lambda *args: next(responses))
    monkeypatch.setattr(probe.sys.stdin, "readline", lambda: "p\n")
    assert p.observation("raw", "Type") == bytes.fromhex("19 42 65 13 43")
    uart.write.assert_not_called()
    assert p.result["observations"]["raw"]["keyboard_hex"] == "19 42 65 13 43"
    assert "KEYS raw: 19 42 65 13 43" in probe.report_summary(p.result)


@pytest.mark.parametrize("answer,error", [("q\n", KeyboardInterrupt), ("", KeyboardInterrupt)])
def test_quit_and_eof_save_raw_evidence(monkeypatch, tmp_path, answer, error):
    p = probe.InteractionProbe(Mock(), tmp_path / "report.json")
    p.uart.read.return_value = b"A"
    monkeypatch.setattr(probe.select, "select", lambda *args: ([probe.sys.stdin], [], []))
    monkeypatch.setattr(probe.sys.stdin, "readline", lambda: answer)
    with pytest.raises(error):
        p.observation("quit", "Type")
    assert json.loads(p.report.read_text())["events"][-1]["hex"] == "41"
    p.uart.write.assert_not_called()


def test_observation_timeout_is_bounded_and_saves_report(tmp_path):
    p = probe.InteractionProbe(Mock(), tmp_path / "report.json")
    with pytest.raises(TimeoutError, match="at deadline"):
        p.observation("deadline", "Type", seconds=0)
    assert json.loads(p.report.read_text())["recovery"] == "not_attempted"


def test_complete_offline_procedure_does_not_claim_hardware_acceptance(monkeypatch, tmp_path):
    p = harness(monkeypatch, tmp_path)
    # Snapshot absent: case experiment must remain unknown, not silently enabled.
    p.routing = Mock(side_effect=[True, True, False, False, True, True])
    p.run()
    assert p.result["completed"]
    assert p.result["observations"]["K2-lowercase"]["result"] == "unknown"
    labels = [c.args[0] for c in p.observation.call_args_list]
    assert {"C2-after", "R1-pairs", "R2-spaces", "T1-output", "Q1-typeahead", "Q2-caret", "E2-restored"} <= set(labels)
    commands = [c.args[0] for c in p.send.call_args_list]
    assert all(b < 128 for command in commands for b in command)
    assert not any(probe.VIDEO in c or probe.MIXED in c for c in commands)
    assert "Unknown/skipped is NOT a pass" in probe.report_summary(p.result)
