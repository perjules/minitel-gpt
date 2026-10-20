"""Finite worker/command lifetimes, readiness windows and input budgets."""
import io
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from web_client import csrf_client

from scripts import runtime
from services import admin_ui as admin
from services import io_limits, printing_session, terminal_link
from services import minitel_chatgpt as chat


def test_expired_worker_forces_controlled_restart_without_replay(monkeypatch):
    expired = terminal_link.WorkerExpired("timeout")
    supervise = Mock(side_effect=expired)
    monkeypatch.setattr(terminal_link, "supervise", supervise)
    stop = Mock()
    terminal_link.serve(Mock(), Mock(), exit_process=stop)
    stop.assert_called_once_with(1)
    assert supervise.call_count == 1


def test_stuck_retired_worker_has_a_deadline():
    pending = (SimpleNamespace(is_alive=lambda: True), None, 0)
    with pytest.raises(terminal_link.WorkerExpired):
        terminal_link.check_worker(pending, clock=lambda: terminal_link.MAX_WORKER_AGE + 1)
    pending[0].is_alive = lambda: False
    terminal_link.check_worker(pending, clock=lambda: 100000)


def test_serial_loop_checks_worker_age_even_when_keyboard_is_idle():
    term = chat.Term(transport=SimpleNamespace(read=Mock(return_value=b"")))
    term._pending = (SimpleNamespace(is_alive=lambda: True), None,
                     time.monotonic() - terminal_link.MAX_WORKER_AGE - 1)
    with pytest.raises(terminal_link.WorkerExpired):
        term.read_key(1)
    term.s.read.assert_not_called()


def test_oversized_serial_input_is_discarded_instead_of_submitted():
    keys = iter([*(('char', ord('x')) for _ in range(io_limits.MAX_QUESTION_CHARS + 500)),
                 ('fn', chat.K_ENVOI), ('char', ord('O')), ('char', ord('K')), ('fn', chat.K_ENVOI)])
    term = SimpleNamespace(w=Mock(), line=Mock(), read_key=lambda _: next(keys))
    assert chat.read_question(term) == ("OK", "envoi")
    assert "trop longue" in term.line.call_args.args[0]


def test_knowledge_read_is_bounded_even_for_large_existing_files(tmp_path):
    path = tmp_path / "large.txt"
    path.write_bytes(b"x" * (io_limits.KNOWLEDGE_READ_BYTES + 500))
    assert len(io_limits.read_knowledge(path)) == io_limits.KNOWLEDGE_READ_BYTES


def test_oversized_upload_is_rejected_without_overwriting_old_knowledge(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "KNOWLEDGE_DIR", tmp_path)
    monkeypatch.setattr(admin, "load_prompts", lambda: {"presets": {"test": {}}, "active": "test"})
    folder = tmp_path / "test"
    folder.mkdir()
    (folder / "old.txt").write_text("original")
    client = csrf_client(admin.app)
    with client.session_transaction() as session:
        session["logged_in"] = True
    response = client.post("/upload-knowledge", data={"preset_key": "test", "files": [
        (io.BytesIO(b"replacement"), "old.txt"),
        (io.BytesIO(b"x" * (io_limits.KNOWLEDGE_FILE_BYTES + 1)), "large.txt")]})
    assert response.status_code == 413
    assert (folder / "old.txt").read_text() == "original"
    assert not (folder / "large.txt").exists()


def test_http_request_body_is_bounded_before_file_parsing():
    client = csrf_client(admin.app)
    with client.session_transaction() as session:
        session["logged_in"] = True
    response = client.post("/preview-title", data=b"x" * (io_limits.MAX_REQUEST_BYTES + 1),
                           content_type="application/json")
    assert response.status_code == 413


def test_root_command_timeout_kills_transport_children_without_hanging_on_pipes(tmp_path):
    manager = runtime.Runtime()
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); time.sleep(60)"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        manager.command([sys.executable, "-c", code], capture_output=True, text=True, timeout=.3)
    assert time.monotonic() - started < 5
    result = manager.command([sys.executable, "-c", "print('ready')"], capture_output=True, text=True)
    assert result.stdout.strip() == "ready"


def clock_runtime(monkeypatch):
    now = [0]
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: now[0],
                                                       sleep=lambda n: now.__setitem__(0, now[0] + n)))
    return now


def test_readiness_requires_stable_pid_and_http_for_a_full_window(monkeypatch):
    now = clock_runtime(monkeypatch)
    manager = runtime.Runtime()
    manager.command = Mock(return_value=SimpleNamespace(stdout="MainPID=12\nNRestarts=0"))
    manager.http_ready = Mock(return_value=True)
    manager.wait_ready(["admin-ui"], timeout=10, stable_for=5)
    assert now[0] >= 5
    assert manager.http_ready.call_count >= 6


def test_late_service_restart_is_rejected(monkeypatch):
    clock_runtime(monkeypatch)
    manager = runtime.Runtime()
    def command(args, **kwargs):
        pid = "13" if manager.command.call_count > 4 else "12"
        return SimpleNamespace(stdout=f"MainPID={pid}\nNRestarts=0")
    manager.command = Mock(side_effect=command)
    manager.http_ready = Mock(return_value=True)
    with pytest.raises(RuntimeError, match="restarted"):
        manager.wait_ready(["admin-ui"])


def test_readiness_timeout_and_legacy_rollback_without_health_route(monkeypatch):
    now = clock_runtime(monkeypatch)
    manager = runtime.Runtime()
    manager.command = Mock(return_value=SimpleNamespace(stdout="MainPID=12\nNRestarts=0"))
    manager.http_ready = Mock(return_value=False)
    with pytest.raises(RuntimeError, match="deadline"):
        manager.wait_ready(["wifi-manager"], timeout=3)
    assert now[0] == 3
    manager.http_ready.reset_mock()
    manager.wait_ready(["admin-ui"], healthchecks=False, timeout=10, stable_for=2)
    manager.http_ready.assert_not_called()


def test_health_endpoints_work_without_login_or_hotspot(monkeypatch):
    from services import wifi_manager as wifi
    query = Mock(side_effect=AssertionError("Health probe must not query NetworkManager"))
    monkeypatch.setattr(wifi, "hotspot_active", query)
    assert csrf_client(admin.app).get("/healthz").json == {"ok": True}
    assert wifi.app.test_client().get("/healthz").json == {"ok": True}


def test_serial_loss_during_printing_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(printing_session, "print_with_progress",
                        Mock(side_effect=terminal_link.Unavailable("SerialException")))
    with pytest.raises(terminal_link.Unavailable):
        chat.execute_print(SimpleNamespace(clear=Mock(), w=Mock(), line=Mock()), object(), "fr")


def test_stuck_printer_worker_is_bounded_and_registered(monkeypatch):
    from printing import PrintDocument, TextBlock
    fake_time = SimpleNamespace(monotonic=Mock(side_effect=[0, 10]))
    monkeypatch.setattr(printing_session, "time", fake_time)
    monkeypatch.setattr(printing_session.threading, "Thread", Mock())
    monkeypatch.setattr(printing_session.threading, "Event", lambda: SimpleNamespace(wait=lambda _: False))
    track = Mock()
    with pytest.raises(terminal_link.WorkerExpired, match="Printer worker"):
        printing_session.print_with_progress(PrintDocument("test", (TextBlock("test"),)),
                                             Mock(), track=track, timeout=1)
    track.assert_called_once()
