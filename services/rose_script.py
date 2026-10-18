"""Opt-in scripted playback and private diagnostics, without host theatre."""
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty

from . import rose


def load(path, test_only=False):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("persona"), str):
        raise TypeError("Le script doit préciser une persona nommée.")
    prompts = data.get("prompts")
    if not isinstance(prompts, list) or any(not isinstance(p, str) or len(p) > rose.MAX_INPUT for p in prompts):
        raise ValueError(f"prompts doit contenir des textes de {rose.MAX_INPUT} caractères maximum.")
    matches = []
    for test in ((True,) if test_only else (False, True)):
        for person in rose.catalogue(test=test)["personas"]:
            names = [person["name"], *person["aliases"]]
            if rose.normalized(data["persona"]) in map(rose.normalized, names):
                matches.append((test, person["name"]))
    if len(matches) != 1:
        raise ValueError("Persona inconnue ou ambiguë dans les catalogues.")
    test, name = matches[0]
    return {"persona": name, "test": test, "prompts": prompts}


class Transcript:
    """One flushed JSON object per event; no auth headers or provider error bodies."""
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.stream = os.fdopen(descriptor, "w", encoding="utf-8")
        self.lock = threading.Lock()
        self.turn = None
        self.closed = False
        self.ended = False
        self.failed = False

    def write(self, event, **values):
        with self.lock:
            if self.closed or (self.ended and event == "provider_response"):
                return  # A cancelled HTTP worker cannot append after session_end/close.
            if self.failed:
                raise OSError("Journal indisponible")
            record = {"event": event, "time": datetime.now(UTC).isoformat(), "turn": self.turn, **values}
            try:
                self.stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                self.stream.flush()
            except OSError:
                self.failed = True
                raise
            if event == "session_end":
                self.ended = True

    def provider_response(self, status, data, error_type=None):
        data = data if isinstance(data, dict) else {}
        choices = data.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        self.write("provider_response", http_status=status, error_type=error_type,
                   text=content if isinstance(content, str) else None,
                   finish_reason=choice.get("finish_reason"),
                   usage={k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                          if type(usage.get(k)) is int})

    def close(self):
        with self.lock:
            self.closed = True
            self.stream.close()


def play(data, model, transcript, term=None, timeout=30):
    """Reuse Session's exact payload/history contracts; a terminal is optional."""
    from . import rose_terminal

    session = None
    reason = "error"
    failed = False

    def show(text):
        if term is not None:
            chat = rose_terminal.api()
            for row in chat.wrap(chat.safe_display(text)):
                term.line(row)
            term.line()

    def traced_model(prompt, history):
        transcript.write("request", messages=[{"role": "system", "content": prompt}, *history])
        try:
            return model(prompt, history)
        except Exception as error:
            transcript.write("adapter_error", error_type=type(error).__name__)
            raise

    def turn(text=None):
        nonlocal failed
        transcript.turn = (transcript.turn or 0) + 1
        job = session.start(traced_model, text)
        if term is not None:
            reply, action = rose_terminal.wait(term, job, session.safeword, timeout)
        else:
            job.thread.join(timeout)
            try:
                reply, action = (None, "deadline") if job.thread.is_alive() else (job.output.get_nowait(), "done")
            except Empty:
                reply, action = None, "error"
        if action != "done":
            job.cancel()
            transcript.write("turn_result", status=action)
            failed = failed or action in ("deadline", "error")
            return action
        if reply.status == "done":
            session.receive(reply, text)  # Keep normal history and limits; discard the host banner.
            transcript.write("assistant", text=reply.text)
            show(reply.text)
            return None
        failed = True
        transcript.write("turn_result", status=reply.status)
        show("Service momentanément indisponible. RETOUR : quitter.")
        return "moderation" if reply.status == "moderation" else None

    def submit(text, source):
        transcript.write("user", text=text, source=source)
        stop = rose.stop_reason(text, session.safeword)
        if stop:
            return stop
        if not text.strip() or rose.normalized(text) == "AIDE":
            transcript.write("local_input", action="help" if text.strip() else "empty")
            if text.strip():
                show("RETOUR / SOMMAIRE : quitter.\nMot d'arrêt : " + session.safeword)
            return None
        if source == "script":
            show("Vous : " + text)
        return turn(text)

    try:
        configured_model = getattr(model, "model", None)
        transcript.write("script", **data, mock=isinstance(model, rose.MockModel),
                         model=configured_model if isinstance(configured_model, str) else type(model).__name__,
                         max_tokens=180, temperature=0.8)
        if not model.ready():
            raise ValueError("Clé Mistral non configurée.")
        session = rose.Session(data["persona"], test=data["test"])
        transcript.write("session", persona=session.profile["id"], safeword=session.safeword)
        reason = turn()
        for text in data["prompts"]:
            if reason is not None:
                break
            reason = submit(text, "script")
        if term is not None and reason is None:
            transcript.write("interactive_start")
            while reason is None:
                text, action = rose_terminal.read_field(term, safeword=session.safeword)
                if text is not None:
                    reason = submit(text, "interactive")
                elif action == "help":
                    transcript.write("local_input", action=action)
                    show("RETOUR / SOMMAIRE : quitter.\nMot d'arrêt : " + session.safeword)
                elif action == "overflow":
                    transcript.write("local_input", action=action)
                    show("Message trop long. Réessayez ou RETOUR.")
                else:
                    reason = action
        reason = reason or "completed"
        return int(failed)
    except KeyboardInterrupt:
        reason = "interrupt"
        return 130
    finally:
        reason = reason or "error"
        messages = session.messages if session else 0
        if session:
            session.close(reason, billing=False)
        transcript.write("session_end", reason=reason, messages=messages, failed=failed or reason == "error")
