"""Dedicated UART loop: no ordinary chat, memory, printing or transcript hooks."""

import threading
import time
from queue import Empty, Queue

from . import (
    journal,
    journal_store,
    persistent_memory,
    rose_terminal,
    terminal_documents,
    terminal_link,
)


def read_field(term, session):
    chat = rose_terminal.api()
    secret = session.secret
    buffer = []
    overflow = False
    term.w("" if secret else "> ")
    while True:
        with session.lock:
            session.check()
        kind, code = term.read_key(0.2)
        if kind == "timeout":
            continue
        with session.lock:
            session.check()
            session.touch()
        if kind == "fn" and code == chat.K_ANNUL:
            if not secret:
                term.w(b"\x08 \x08" * len(buffer))
            buffer.clear()
            overflow = False
            continue
        if kind == "fn" and code in (chat.K_SOMMAIRE, chat.K_RETOUR):
            return None, "sommaire" if code == chat.K_SOMMAIRE else "retour"
        if kind == "fn" and code == chat.K_GUIDE and not secret:
            return "AIDE", "submit"
        if (kind == "fn" and code == chat.K_ENVOI) or (
            kind == "char" and code in (chat.CR, chat.LF)
        ):
            term.w(b"\r\n")
            if overflow:
                return None, "overflow"
            return "".join(buffer), "submit"
        if (kind == "fn" and code == chat.K_CORR) or (
            kind == "char" and code in (chat.BS, 0x7F)
        ):
            if buffer:
                buffer.pop()
                if not secret:
                    term.w(b"\x08 \x08")
        elif kind == "char" and 0x20 <= code <= 0x7E:
            if len(buffer) < 2000 and not overflow:
                buffer.append(chr(code))
                if not secret:
                    term.w(bytes([code]))
            else:
                buffer.clear()
                overflow = True


def display(term, session, result):
    """Acknowledge a draft only after every page has actually been displayed."""
    chat = rose_terminal.api()
    lines = chat.wrap(chat.safe_display(result["display"]))
    pages = [
        lines[i : i + chat.CONTENT_ROWS]
        for i in range(0, len(lines), chat.CONTENT_ROWS)
    ] or [[""]]
    for index, page in enumerate(pages):
        with session.lock:
            session.check()
        term.clear()
        term.w(chat.FG_WHITE)
        for line in page:
            term.line(line)
        if index + 1 < len(pages):
            term.line("SUITE : lire / SOMMAIRE : verrouiller")
            while True:
                with session.lock:
                    session.check()
                kind, code = term.read_key(0.2)
                if kind == "timeout":
                    continue
                with session.lock:
                    session.check()
                    session.touch()
                if kind == "fn" and code in (
                    chat.K_SOMMAIRE,
                    chat.K_RETOUR,
                    chat.K_ANNUL,
                ):
                    return "sommaire" if code == chat.K_SOMMAIRE else "retour"
                if kind == "fn" and code == chat.K_SUITE:
                    break
    if result.get("review"):
        session.acknowledge(result["review"])
    return "done"


def start(session, text):
    output = Queue(maxsize=1)

    def request():
        try:
            result = session.handle(text)
        except Exception:  # noqa: BLE001 - Private failures must never expose arbitrary payloads.
            session.close()
            result = session.result("Journal indisponible. Session verrouillée.")
        output.put(result)

    worker = threading.Thread(target=request, daemon=True)
    started = time.monotonic()
    worker.start()
    return worker, output, started


def wait(term, session, pending):
    chat = rose_terminal.api()
    while time.monotonic() - pending[2] < 45:
        # Navigation takes priority over a completed response.
        kind, code = term.read_key(0.05)
        if kind == "fn" and code in (chat.K_SOMMAIRE, chat.K_RETOUR, chat.K_ANNUL):
            session.close()
            return None, "sommaire" if code == chat.K_SOMMAIRE else "retour"
        with session.lock:
            session.check()
        try:
            result = pending[1].get_nowait()
        except Empty:
            continue
        # Discard all typeahead, particularly repeated password submissions.
        term.s.reset_input_buffer()
        return result, "done"
    session.close()
    return None, "timeout"


def run(term, user, registry, model=None):
    session = None
    pending = None
    try:
        session = journal.Session(journal_store.Vault(user, registry), model)
        with rose_terminal.hidden_echo(term):
            return interact(term, session)
    except (
        journal.JournalError,
        persistent_memory.MemoryProblem,
        rose_terminal.EchoUnavailable,
    ) as error:
        return "error:" + str(error), pending
    finally:
        if session is not None:
            session.close()
        try:
            term.clear()
            term.line("Journal verrouillé.")
        except terminal_link.Unavailable:
            pass


def interact(term, session):
    # Echo stays disabled from before the first password prompt to final clear.
    # Only nonsecret fields are echoed explicitly, once, by this adapter.
    pending = None

    def check():
        with session.lock:
            session.check()

    def touch():
        with session.lock:
            session.check()
            session.touch()

    try:
        result = session.open()
        while True:
            if session.secret:
                action = display(term, session, result)
                if action != "done":
                    return action, pending
                text, action = read_field(term, session)
            else:
                token = result.get("review")
                text, action = terminal_documents.read(
                    term, result["display"], echo=True, check=check, touch=touch,
                    reviewed=(lambda token=token: session.acknowledge(token)) if token else None,
                )
                if action == "guide":
                    text, action = "AIDE", "submit"
                elif action == "envoi":
                    action = "submit"
            if action == "overflow":
                result = session.result(
                    "Saisie trop longue. Recommencez (2000 caractères maximum)."
                )
                continue
            if action != "submit":
                return action, pending
            if session.secret:
                # Password processing is local and bounded; never leave a secret
                # referenced by a background thread or enable echo during input.
                try:
                    result = session.handle(text)
                finally:
                    text = None
                term.s.reset_input_buffer()
            else:
                pending = start(session, text)
                term._pending = pending
                text = None
                term.clear()
                term.line("Journal intime — traitement en cours...")
                result, action = wait(term, session, pending)
                if action != "done":
                    return action, pending
            if result.get("closed"):
                return "logout" if result.get("logout") else "retour", pending
    except (journal.JournalError, persistent_memory.MemoryProblem):
        return "retour", pending
    finally:
        session.close()
        try:
            term.clear()
        except terminal_link.Unavailable:
            pass
