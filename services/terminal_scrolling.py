"""UART-owned rouleau view; a queue is a producer seam, not a task runner."""

import logging
import time
import unicodedata
from collections import deque
from queue import Empty

from . import rose_terminal, terminal_display

log = logging.getLogger(__name__)


class _Rows:
    """Incremental display wrapping, retaining a possible split accent cluster."""

    def __init__(self, columns):
        self.columns = columns
        self.pending = self.tail = ""

    def feed(self, text, *, final=False):
        chat = rose_terminal.api()
        raw = self.tail + text
        end = len(raw)
        if not final and raw:
            end -= 1
            while end and unicodedata.combining(raw[end]):
                end -= 1
            if not raw[end].isalpha():
                end = len(raw)
        self.tail = raw[end:]
        self.pending += chat.safe_display(raw[:end])
        paragraphs = self.pending.split("\n")
        rows = [row for paragraph in paragraphs[:-1] for row in chat.wrap(paragraph, self.columns)]
        last = paragraphs[-1]
        wrapped = chat.wrap(last, self.columns)
        rows.extend(wrapped[:-1])
        trailing = last[len(last.rstrip(" \t")):]
        self.pending = wrapped[-1] + trailing if len(wrapped) > 1 else last
        if final:
            rows.append(self.pending)
            self.pending = ""
        return rows


def _cancel(callback):
    """A caller must supply a nonblocking stop request, never a blocking join."""
    if callback is None:
        return False
    try:
        return callback() is True
    except Exception as error:  # noqa: BLE001 - A failed stop must not strand the terminal.
        log.warning("scrolling cancellation=unconfirmed error=%s", type(error).__name__)
        return False


def show(term, source, language="fr", *, cancel=None):
    """Scroll a str, or a caller-owned Queue of str chunks / None EOF / Exception.

    Only this thread touches the UART. Empty queues have no idle/duration limit.
    ANNULATION stops locally; a nonblocking cancel callback may confirm that the
    producer stopped too. Queues/producers do not use the bounded API _pending
    worker slot. Every received chunk is retained for ordinary local pagination.
    """
    try:
        return _show(term, source, language, cancel=cancel)
    except rose_terminal.EchoUnavailable:
        # Do not begin a full-screen log with uncontrolled local keyboard echo.
        # Public static text remains available through the normal reader fallback.
        if isinstance(source, str):
            text = source
        else:
            _cancel(cancel)
            text = "[Flux non ouvert : écho clavier non maîtrisé.]"
        _restore(term, text, language, "Défilement indisponible - clavier")
        return "done"


def _restore(term, text, language, notice):
    chat = rose_terminal.api()
    term.w(chat.SCROLL_OFF + terminal_display.NORMAL + b"\x1b\x50")
    term.view = None
    view = terminal_display.View(term, text, language)
    view.page = len(view.pages) - 1
    view.notice = notice
    view.install()
    term.w(b"\x11")


def _show(term, source, language, *, cancel):
    chat = rose_terminal.api()
    static = isinstance(source, str)
    if not static and not callable(getattr(source, "get_nowait", None)):
        raise TypeError("Scrolling source must be text or a chunk queue")
    parts = [source] if static else []
    rows = deque(chat.wrap(chat.safe_display(source), term.geometry.columns) if static else ())
    wrapper = _Rows(term.geometry.columns)
    ended, stopped, failed = static, False, False
    action = "done"

    def key(wait=False):
        nonlocal stopped, action
        kind, code = term.read_key(.05) if wait else term.poll_key()
        if kind == "fn" and code in (chat.K_ANNUL, chat.K_SOMMAIRE, chat.K_GUIDE):
            stopped = True
            action = {chat.K_SOMMAIRE: "sommaire", chat.K_GUIDE: "guide"}.get(code, "done")
        # All other input during scrolling is discarded, with local echo off.

    with rose_terminal.hidden_echo(term):
        try:
            term.w(chat.SCROLL_OFF)
            term.clear()  # Invalidate the fixed view before entering rouleau.
            term.w(terminal_display.NORMAL + b"\x1b\x50\x14")
            term.w(chat.SCROLL_ON)
            term.line("MF-83 / JOURNAL CONTINU OUVERT")
            term.line("ANNULATION : fermer le robinet")
            term.line()
            while not stopped:
                key()
                if stopped:
                    break
                # Flow control, not an animation delay: at most about one row
                # remains in the adapter. Keep polling while the real wire drains.
                baud = getattr(term.s, "baudrate", None)
                budget = 120 * 10 / baud if isinstance(baud, int) and baud > 0 else 0
                if budget and term._tx_until - time.monotonic() > budget:
                    key(wait=True)
                    continue
                if rows:
                    term.w(terminal_display.encode_runs(rows.popleft()) + b"\r\n")
                elif ended:
                    # Let the last row reach the CRT before clearing it. No key
                    # wait or minimum display duration after physical delivery.
                    if budget and time.monotonic() < term._tx_until:
                        key(wait=True)
                    else:
                        break
                else:
                    try:
                        chunk = source.get_nowait()
                    except Empty:
                        key(wait=True)
                        continue
                    if chunk is None:
                        rows.extend(wrapper.feed("", final=True))
                        ended = True
                    elif isinstance(chunk, Exception) or not isinstance(chunk, str):
                        failed = ended = True
                        break
                    else:
                        parts.append(chunk)
                        rows.extend(wrapper.feed(chunk))
        finally:
            interrupted = stopped or failed or not ended
            confirmed = _cancel(cancel) if not static and interrupted else False
            text = "".join(parts)
            notice = "Journal terminé - dernière page"
            if not static and interrupted:
                marker = ("[Flux interrompu : erreur de source.]" if failed else
                          "[Flux arrêté.]" if confirmed else "[Arrêt demandé; source non confirmée.]")
                text += "\n\n" + marker
                notice = "Journal interrompu - dernière page"
            elif stopped:
                notice = "Défilement arrêté - texte complet"
            # A fresh View prevents stale cache/gray fields or page-one flashes.
            # Its seen set starts with the last page only; this grants no review
            # acknowledgement for unseen pages and never changes feature state.
            _restore(term, text, language, notice)
    return action
