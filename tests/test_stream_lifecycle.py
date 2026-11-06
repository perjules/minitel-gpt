"""Actual local HTTP worker and UART lifecycle, without external API requests."""

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import test_terminal_startup as startup
from test_rose_echo import submitted
from test_stream_sources import collect, done, job, message, script
from test_terminal_display import VisibleUART, assert_gray_input

from services import llm_streaming, stream_sources
from services import minitel_chatgpt as chat


@contextmanager
def endpoint(*, blocked=False, broken=False):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    calls = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.flush()
            entered.set()
            if blocked:
                self.connection.settimeout(3)
                try:
                    while self.connection.recv(1):
                        pass
                    closed.set()
                except OSError:
                    pass
                return
            def send(data):
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
            try:
                send(message("École " * 12 + "\n"))
                release.wait(3)
                if not broken:
                    send(done())
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                closed.set()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state = SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}/stream", entered=entered,
                            release=release, closed=closed, calls=calls)
    try:
        yield state
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(1)


def test_real_http_child_emits_public_text_before_completion_and_reaps():
    with endpoint() as server:
        payload = job()
        payload["url"] = server.url
        with stream_sources.ProcessSource.api(payload) as source:
            text = collect(source, until="École " * 12)
            assert source.process.poll() is None and source.result is None
            assert "École" in text
            assert source._job is None
            assert "synthetic-secret" not in " ".join(source.argv)
            server.release.set()
            text += collect(source)
            assert source.closed and source.process.returncode == 0
            assert source.result["text"] == "École " * 12 + "\n"
            assert "Fin du flux validée" in text
        assert len(server.calls) == 1 and server.calls[0]["store"] is False


def test_real_http_disconnection_never_commits_a_final_result():
    with endpoint(broken=True) as server:
        payload = job()
        payload["url"] = server.url
        with stream_sources.ProcessSource.api(payload) as source:
            text = collect(source, until="École " * 12)
            server.release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    result = source.get_nowait()
                except stream_sources.Empty:
                    time.sleep(.005)
                    continue
                if isinstance(result, str):
                    text += result
                    continue
                assert isinstance(result, RuntimeError)
                break
            else:
                pytest.fail("Missing truncated-stream failure")
            assert source.result is None and "Fin du flux validée" not in text
        assert len(server.calls) == 1


def test_annulation_kills_blocked_api_worker_closes_socket_and_restores_uart():
    with endpoint(blocked=True) as server:
        payload = job()
        payload["url"] = server.url
        source = stream_sources.ProcessSource.api(payload)
        uart = VisibleUART()
        term = chat.Term(uart)
        routes = dict(uart.routes)
        term.poll_key = lambda: ("fn", chat.K_ANNUL) if server.entered.is_set() else ("timeout", None)
        assert stream_sources.show(term, source) == "done"
        assert source.closed and source.process.poll() is not None
        assert server.closed.wait(2)
        assert source.result is None and len(server.calls) == 1
        assert "connexion API fermée" in term.view.text
        assert not uart.screen.scrolling and uart.screen.cursor_visible
        assert uart.routes == routes
        assert_gray_input(uart)


def test_annulation_kills_real_python_monitor_and_normal_input_recovers():
    source = script("import time; print('partial école',flush=True); time.sleep(120)")
    uart = VisibleUART()
    term = chat.Term(uart)
    def key():
        return ("fn", chat.K_ANNUL) if "partial école" in startup.screen_text(uart.output) else ("timeout", None)
    term.poll_key = key
    assert stream_sources.show(term, source) == "done"
    assert source.closed and source.process.poll() is not None
    assert "partial école" in term.view.text and "[Processus arrêté.]" in term.view.text
    assert "120" not in term.view.text
    assert_gray_input(uart)
    uart.keys.extend(submitted("FOLLOW UP"))
    assert chat.read_question(term) == ("FOLLOW UP", "envoi")


def test_failed_echo_gate_never_launches_a_producer():
    source = script("raise RuntimeError('must not run')")
    uart = VisibleUART()
    uart.ignore_off = True
    term = chat.Term(uart)
    assert stream_sources.show(term, source) == "done"
    assert source.process is None and source.closed


def test_main_loop_stream_request_is_isolated_and_uses_configured_adapter(monkeypatch):
    source = Mock()
    factory = Mock(return_value=source)
    def display(term, current, language):
        assert current is source
        chat.show_response(term, "Synthetic streamed answer", language)
        return "done"
    monkeypatch.setattr(chat.llm_streaming, "mistral", factory)
    monkeypatch.setattr(chat.stream_sources, "show", display)
    helper = startup.StartupTests()
    helper.run_session(startup.login_keys("fr") + submitted("STREAM Explain Jacquard") + submitted("FOLLOW UP"),
                       boot_text="BOOT")
    assert factory.call_count == 1
    assert factory.call_args.args[0] == [{"role": "user", "content": "Explain Jacquard"}]
    assert factory.call_args.kwargs == {"api": "chat_completions", "web_search": False}
    assert "STREAM" not in str(helper.memories[0].record.mock_calls)


def test_main_loop_process_diagnostic_is_local_and_has_no_history(monkeypatch):
    source = Mock()
    factory = Mock(return_value=source)
    def display(term, current, language):
        assert current is source
        chat.show_response(term, "Synthetic monitor transcript", language)
        return "done"
    monkeypatch.setattr(chat.stream_sources, "ProcessSource", factory)
    monkeypatch.setattr(chat.stream_sources, "show", display)
    helper = startup.StartupTests()
    _, calls = helper.run_session(startup.login_keys("fr") + submitted("TEST STREAM") + submitted("FOLLOW UP"))
    assert factory.call_count == 1 and "--stream-demo" in factory.call_args.args[0]
    assert [call[1][-1]["content"] for call in calls] == ["FOLLOW UP"]
    assert "TEST STREAM" not in str(helper.memories[0].record.mock_calls)


@pytest.mark.parametrize("text,expected", [
    ("STREAM bonjour", {"prompt": "bonjour", "web": False}),
    (" stream   WEB  recherche météo ", {"prompt": "recherche météo", "web": True}),
    ("stream", {"prompt": "", "web": False}),
    ("STREAM WEB", {"prompt": "", "web": True}),
    ("Explain a stream", None),
    ("A: STREAM hello", None),
])
def test_explicit_stream_command(text, expected):
    assert llm_streaming.command(text) == expected
