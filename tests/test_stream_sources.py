"""Owned process cleanup and SSE contracts: synthetic scripts/HTTP only."""

import copy
import json
import signal
import sys
import time
from pathlib import Path
from queue import Empty
from unittest.mock import Mock

import pytest
import requests

from services import llm_streaming as api
from services import mistral_answers as engine
from services.stream_sources import ProcessSource


def collect(source, *, until=None):
    output = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            chunk = source.get_nowait()
        except Empty:
            time.sleep(.005)
            continue
        if chunk is None:
            return "".join(output)
        assert isinstance(chunk, str), chunk
        output.append(chunk)
        if until is not None and until in "".join(output):
            return "".join(output)
    pytest.fail("Synthetic source did not progress")


def script(code):
    return ProcessSource([sys.executable, "-u", "-c", code])


def running(pid):
    try:
        status = Path(f"/proc/{pid}/stat").read_text().split()[2]
        return status not in ("Z", "X")
    except FileNotFoundError:
        return False


def test_command_is_lazy_shell_free_and_literal():
    with pytest.raises(TypeError):
        ProcessSource("echo wrong")
    source = ProcessSource([sys.executable, "-c", "import sys; print(sys.argv[1])", "$(echo not-a-shell); *"])
    assert source.process is None
    with source:
        assert collect(source) == "$(echo not-a-shell); *\n"
        assert source.closed and source.process.poll() == 0
    assert source.process.stdout.closed


def test_python_output_split_utf8_carriage_stderr_and_environment(tmp_path):
    path = tmp_path / "fixture.py"
    path.write_text("import os,sys,time\nos.write(1,b'caf\\xc3')\ntime.sleep(.03)\nos.write(1,b'\\xa9\\r')\n"
                    "time.sleep(.03)\nos.write(1,b'\\nnext\\rfinal')\nprint(os.getcwd(),file=sys.stderr)\n")
    with ProcessSource.python(path, cwd=tmp_path) as source:
        text = collect(source)
    assert text.startswith("café\nnext\nfinal")
    assert str(tmp_path) in text and "\r" not in text


def test_nonzero_exit_keeps_partial_output_then_reports_failure():
    with script("import sys; print('available',flush=True); sys.exit(7)") as source:
        text = collect(source, until="available")
        assert "available" in text
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                result = source.get_nowait()
            except Empty:
                time.sleep(.005)
                continue
            assert isinstance(result, RuntimeError)
            break
        else:
            pytest.fail("No process failure")
    assert source.closed and source.process.returncode == 7


def test_annulation_escalates_for_group_leader_and_children_ignoring_term(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    child = ("import signal,time,pathlib\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
             f"path=pathlib.Path({str(heartbeat)!r})\n"
             "for i in range(10000):\n path.write_text(str(i)); time.sleep(.01)")
    code = ("import subprocess,sys,time,signal\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            f"child=subprocess.Popen([sys.executable,'-u','-c',{child!r}])\n"
            "time.sleep(.1)\nprint(child.pid,flush=True)\ntime.sleep(120)")
    with script(code) as source:
        collect(source, until="\n")
        assert heartbeat.exists() and source.process.poll() is None
        began = time.monotonic()
        assert source.cancel()
        assert time.monotonic() - began < 1.5
        assert source.process.returncode == -signal.SIGKILL
        time.sleep(.05)
        last = heartbeat.read_text()
        time.sleep(.05)
        assert heartbeat.read_text() == last
    assert source.closed and source.process.stdout.closed


def test_context_exit_stops_quiet_and_pipe_blocked_producers():
    for code in ("import time; time.sleep(120)", "import os; os.write(1,b'x'*10000000)"):
        with script(code) as source:
            try:
                source.get_nowait()
            except Empty:
                pass
            assert source.process.poll() is None
        assert source.process.poll() is not None and source.closed


def test_leader_exit_does_not_leave_a_background_child_running(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    child = ("import time,pathlib\n"
             f"path=pathlib.Path({str(heartbeat)!r})\n"
             "for i in range(10000):\n path.write_text(str(i)); time.sleep(.01)")
    code = ("import subprocess,sys,time\n"
            f"child=subprocess.Popen([sys.executable,'-u','-c',{child!r}])\n"
            "time.sleep(.1)\nprint(child.pid,flush=True)")
    with script(code) as source:
        collect(source)
        time.sleep(.05)
        last = heartbeat.read_text()
        time.sleep(.05)
        assert heartbeat.read_text() == last
    assert source.closed


def frame(kind, **data):
    return (f"event: {kind}\ndata: " + json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n\n").encode()


def message(text, *, output=0, content=0, identity="m0"):
    return frame("message.output.delta", id=identity, output_index=output, content_index=content, content=text)


def done():
    return frame("conversation.response.done", usage={"prompt_tokens": 1, "completion_tokens": 2})


def job(protocol="conversations"):
    return {"url": engine.URL, "headers": {"Authorization": "Bearer synthetic-secret"},
            "payload": {"stream": True, "store": False}, "protocol": protocol,
            "history": [{"role": "user", "content": "Explain Jacquard"}], "language": "fr", "web_search": True}


def response(monkeypatch, body, *, status=200, content_type="text/event-stream", fragment=3):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.status_code = status
    result.headers = {"Content-Type": content_type}
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError("private provider body")
    result.iter_content.return_value = (body[index:index + fragment] for index in range(0, len(body), fragment))
    post = Mock(return_value=result)
    monkeypatch.setattr(requests, "post", post)
    return post, result


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("size", [1, 2, 7, 4096])
def test_sse_fragmentation_comments_multiline_utf8(separator, size):
    raw = ("\ufeff: keepalive" + separator + "event: example" + separator
           + 'data: {"text":' + separator + 'data: "été"}' + separator * 2).encode()
    parsed = list(api.events(raw[index:index + size] for index in range(0, len(raw), size)))
    assert parsed == [("example", '{"text":\n"été"}')]


def test_sse_rejects_incomplete_utf8_or_unterminated_or_oversized_frames(monkeypatch):
    monkeypatch.setattr(api, "MAX_FRAME", 24)
    for raw in (b"data: \xff\n\n", b"data: unfinished", b"data: " + b"x" * 25 + b"\n\n",
                b"data:\n" * 30):
        with pytest.raises((ValueError, UnicodeError)):
            list(api.events([raw]))


def test_plain_conversations_stream_suppresses_reasoning_and_validates_end(monkeypatch):
    raw = message({"type": "thinking", "thinking": "never public"}, content=0)
    raw += message("Bon", content=1) + message("jour é", content=1) + done()
    post, res = response(monkeypatch, raw)
    output = []
    result = api.run(job(), output.append)
    assert result["text"] == "Bonjour é"
    assert "never public" not in "".join(output)
    assert "Bonjour é" in "".join(output) and "Fin du flux validée" in "".join(output)
    assert post.call_count == 1
    assert post.call_args.kwargs["stream"]
    assert post.call_args.kwargs["timeout"] == (10, None)
    res.__exit__.assert_called_once()


def test_hosted_search_intermediate_entries_and_final_citations(monkeypatch):
    data = json.loads((Path(__file__).parent / "fixtures" / "conversations_searched.json").read_text())
    frames = []
    for output_index, entry in enumerate(data["outputs"]):
        identity = str(output_index)
        if entry["type"] == "tool.execution":
            frames.append(frame("tool.execution.started", output_index=output_index, id=identity,
                                name=entry["name"], arguments="private tool arguments"))
            frames.append(frame("tool.execution.done", output_index=output_index, id=identity,
                                name=entry["name"], info=entry.get("info") or {}))
        else:
            content = entry["content"]
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for index, chunk in enumerate(content):
                frames.append(message(chunk, output=output_index, content=index, identity=identity))
    frames.append(done())
    response(monkeypatch, b"".join(frames))
    text = []
    actual = api.run(job(), text.append)
    expected = engine.parse_response(data)
    assert actual["text"] == expected["text"] and actual["references"] == expected["references"]
    transcript = "".join(text)
    assert "private tool arguments" not in transcript and "Private" not in transcript
    assert "Recherche web démarrée" in transcript and "Sources:" in transcript


@pytest.mark.parametrize("raw", [
    message("Partial"),  # No completion.
    message("x") + frame("conversation.response.error", message="secret", code=500),
    message("x") + frame("function.call.delta", id="f", name="send_email", arguments="private"),
    message("x") + message("bad id", identity="other") + done(),
    message({"type": "tool_reference", "tool": "web_search", "url": "https://example.org", "title": "unverified"}) + done(),
    message("x") + frame("tool.execution.done", id="t", output_index=1, name="web_search") + done(),
    message("x") + b"data: [DONE]\n\n",
])
def test_bad_provider_stream_is_not_success_and_never_retries(monkeypatch, raw):
    post, res = response(monkeypatch, raw)
    output = []
    with pytest.raises((ValueError, TypeError)):
        api.run(job(), output.append)
    assert "Fin du flux validée" not in "".join(output)
    assert "private" not in "".join(output) and "secret" not in "".join(output)
    assert post.call_count == 1 and res.__exit__.called


@pytest.mark.parametrize("status,ctype", [(429, "text/event-stream"), (200, "application/json"), (302, "text/event-stream")])
def test_http_error_or_wrong_media_type_is_not_an_answer(monkeypatch, status, ctype):
    response(monkeypatch, b"private provider body", status=status, content_type=ctype)
    output = []
    with pytest.raises((ValueError, requests.HTTPError)):
        api.run(job(), output.append)
    assert output == []


def test_chat_completions_stream_discards_reasoning_and_requires_stop_plus_done(monkeypatch):
    frames = [{"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "private"}}]},
              {"choices": [{"index": 0, "delta": {"content": "École"}}]},
              {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
    raw = b"".join(("data: " + json.dumps(item) + "\n\n").encode() for item in frames)
    response(monkeypatch, raw + b"data: [DONE]\n\n")
    text = []
    assert api.run(job("chat_completions"), text.append)["text"] == "École"
    assert "private" not in "".join(text)
    for ending in (b"", b"data: [DONE]\n\n"):
        bad = copy.deepcopy(frames)
        bad[-1]["choices"][0]["finish_reason"] = "length"
        response(monkeypatch, b"".join(("data: " + json.dumps(item) + "\n\n").encode() for item in bad) + ending)
        with pytest.raises(ValueError):
            api.run(job("chat_completions"), lambda _: None)


def test_source_factory_preserves_store_false_and_removes_private_pairs():
    history = [{"role": "user", "content": "private question"},
               {"role": "assistant", "content": "private answer", "private": True},
               {"role": "user", "content": "public question"}]
    with api.mistral(history, "fr", "synthetic-secret", "configured") as source:
        assert source.process is None
        assert source._job["payload"]["store"] is False
        assert source._job["payload"]["stream"] is True
        assert "private question" not in json.dumps(source._job)
        assert "private answer" not in json.dumps(source._job)
        assert "synthetic-secret" not in " ".join(source.argv)
    with api.mistral(history, "fr", "synthetic-secret", "configured", api="chat_completions") as source:
        assert source._job["payload"]["stream"] is True
        assert source._job["payload"]["messages"][-1]["content"] == "public question"
