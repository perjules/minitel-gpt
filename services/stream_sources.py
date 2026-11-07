"""Killable text sources for the UART owner; no shell or background continuation."""

import codecs
import json
import os
import signal
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from queue import Empty

ROOT = Path(__file__).resolve().parents[1]
MAX_FRAME = 1024 * 1024


class ProcessSource:
    """Lazy process, bounded pipe, nonblocking reads and bounded TERM/KILL cleanup.

    Arguments are application-owned, never shell/model-generated command text.
    There is no operation deadline. A source belongs to one main-thread with
    block; its producer never receives the terminal or owns the UART.
    """

    def __init__(self, argv, *, cwd=None, env=None, api_job=None):
        if isinstance(argv, (str, bytes)) or not argv or not all(isinstance(arg, str) for arg in argv):
            raise TypeError("Expected an explicit argument list")
        self.argv = list(argv)
        self.cwd, self.env, self._job = cwd, env, api_job
        self.api = api_job is not None
        self.process = self.result = None
        self.closed = self.eof = self.cancelled = False
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict" if self.api else "replace")
        self._buffer = self._carriage = ""
        self._complete = False
        self._candidate = None

    @classmethod
    def python(cls, script, *arguments, cwd=None, env=None):
        return cls([sys.executable, "-u", str(script), *arguments], cwd=cwd, env=env)

    @classmethod
    def api(cls, job):
        return cls([sys.executable, "-u", "-m", "services.stream_sources", "--api"], cwd=ROOT, api_job=job)

    def __enter__(self):
        return self

    def __exit__(self, *_error):
        self.close()

    def _start(self):
        environment = {**os.environ, **(self.env or {}), "PYTHONUNBUFFERED": "1"}
        try:
            with tempfile.TemporaryFile() if self.api else nullcontext(None) as stdin:
                if self.api:
                    payload = json.dumps(self._job, ensure_ascii=False).encode("utf-8")
                    if len(payload) > MAX_FRAME:
                        raise ValueError("API stream request too large")
                    stdin.write(payload)
                    stdin.seek(0)
                self.process = subprocess.Popen(
                    self.argv, cwd=self.cwd, env=environment, stdin=stdin or subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL if self.api else subprocess.STDOUT,
                    start_new_session=True, close_fds=True,
                )
                os.set_blocking(self.process.stdout.fileno(), False)
        finally:
            self._job = None

    @property
    def stop_message(self):
        return "[Flux arrêté; connexion API fermée.]" if self.api else "[Processus arrêté.]"

    def _group_signal(self, sig):
        if self.process is not None:
            try:
                os.killpg(self.process.pid, sig)
            except ProcessLookupError:
                pass

    def cancel(self):
        """Stop/reap locally. A closed HTTP connection cannot prove remote cancellation."""
        self.cancelled = True
        if self.closed:
            return True
        if self.process is not None:
            self._group_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                pass
            # Kill remaining descendants even if their leader exited on TERM.
            self._group_signal(signal.SIGKILL)
            try:
                self.process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                return False
            self.process.stdout.close()
        self.closed = True
        self._job = None
        self._buffer = ""
        self._candidate = self.result = None
        return True

    def close(self):
        if not self.closed and not self.cancel():
            raise RuntimeError("Stream producer could not be reaped")

    def _frame(self):
        line, self._buffer = self._buffer.split("\n", 1)
        frame = json.loads(line)
        if not isinstance(frame, dict):
            raise TypeError("Invalid stream worker frame")
        if set(frame) == {"text"} and isinstance(frame["text"], str) and not self._complete:
            return frame["text"]
        if set(frame) == {"result"} and isinstance(frame["result"], dict) and not self._complete:
            self._candidate, self._complete = frame["result"], True
            raise Empty
        raise ValueError("Invalid stream worker frame")

    def get_nowait(self):
        if self.closed:
            return None
        try:
            if self.api and "\n" in self._buffer:
                return self._frame()
            if self.process is None:
                self._start()
            try:
                data = os.read(self.process.stdout.fileno(), 4096)
            except BlockingIOError:
                if self.process.poll() is not None:
                    self._group_signal(signal.SIGKILL)
                raise Empty from None
            if data:
                text = self._decoder.decode(data)
                if self.api:
                    self._buffer += text
                    if len(self._buffer) > MAX_FRAME:
                        raise ValueError("Stream worker frame too large")
                    if "\n" in self._buffer:
                        return self._frame()
                    raise Empty
                text = self._carriage + text
                self._carriage = "\r" if text.endswith("\r") else ""
                text = text[:-1] if self._carriage else text
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                if text:
                    return text
                raise Empty
            if not self.eof:
                self.eof = True
                tail = self._decoder.decode(b"", final=True) + ("\n" if self._carriage else "")
                self._carriage = ""
                if not self.api and tail:
                    return tail
            code = self.process.poll()
            if code is None:
                raise Empty
            self._group_signal(signal.SIGKILL)
            self.process.wait()
            self.process.stdout.close()
            self.closed = True
            if code != 0 or self.api and (not self._complete or self._buffer):
                return RuntimeError("Stream producer failed")
            self.result = self._candidate
            return None
        except (OSError, ValueError, UnicodeError, TypeError):
            self.cancel()
            return RuntimeError("Stream producer failed")


def show(term, source, language="fr"):
    """No producer survives return, user exit, serial failure or an exception."""
    from . import terminal_scrolling
    with source:
        outcome = terminal_scrolling.show(term, source, language, cancel=source.cancel)
        if outcome == "done" and source.result is not None and term.view is not None:
            term.view.references = tuple(source.result.get("display_references", ()))
        return outcome


def _api_worker():
    from . import llm_streaming
    try:
        job = json.load(sys.stdin)
        def emit(text):
            print(json.dumps({"text": text}, ensure_ascii=False), flush=True)
        result = llm_streaming.run(job, emit)
        print(json.dumps({"result": result}, ensure_ascii=False), flush=True)
    except Exception:  # noqa: BLE001 - Never expose provider bodies, credentials or tracebacks.
        return 1
    return 0


if __name__ == "__main__" and sys.argv[1:] == ["--api"]:
    raise SystemExit(_api_worker())
