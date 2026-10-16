"""Keyboard/renderer adapter for rose; no access to ordinary history or tools."""
import sys
import time
from queue import Empty

from . import rose


def api():
    # Late import avoids a circular dependency in the ordinary application.
    for name in ("services.minitel_chatgpt", "minitel_chatgpt", "__main__"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "Term") and hasattr(module, "SCROLL_ON"):
            return module
    from . import minitel_chatgpt
    return minitel_chatgpt


def display(term, text):
    chat = api()
    term.clear()
    term.w(chat.FG_WHITE)
    # Bounded screen: no pagination can hide the safeword or swallow exits.
    rows = chat.wrap(chat.safe_display(text))
    if len(rows) > 20:
        rows = rows[:19] + ["[Réponse abrégée]"]
    for row in rows:
        term.line(row)


def read_field(term, secret=False, safeword="", paging=False):
    chat = api()
    buf = []
    overflow = False
    term.w("> " if not secret else "")
    while True:
        kind, code = term.read_key(chat.IDLE_TIMEOUT)
        if kind == "timeout":
            return None, "timeout"
        if kind == "fn":
            if code in (chat.K_SOMMAIRE, chat.K_RETOUR, chat.K_ANNUL):
                return None, "sommaire" if code == chat.K_SOMMAIRE else "retour"
            if not secret and code == chat.K_GUIDE:
                return None, "help"
            if paging and code == chat.K_SUITE:
                return None, "next"
            if code == chat.K_CORR:
                if buf:
                    buf.pop()
                    if not secret:
                        term.w(b"\x08 \x08")
                continue
        if (kind == "fn" and code == chat.K_ENVOI) or (kind == "char" and code in (chat.CR, chat.LF)):
            if overflow:
                return None, "overflow"
            text = "".join(buf)
            if secret:
                if rose.normalized(text) in ("RETOUR", "SOMMAIRE"):
                    return None, rose.normalized(text).lower()
                return text, "submit"
            reason = rose.stop_reason(text, safeword)
            return text, reason or "submit"
        if kind == "char":
            if code in (chat.BS, 0x7F):
                if buf:
                    buf.pop()
                    if not secret:
                        term.w(b"\x08 \x08")
            elif 0x20 <= code <= 0x7E:
                if not overflow:
                    buf.append(chr(code))
                if len(buf) > (128 if secret else rose.MAX_INPUT):
                    # Drain through ENVOI: no secret suffix can spill into the
                    # ordinary conversation after rejecting an oversized field.
                    buf.clear()
                    overflow = True


def gate(term):
    if not rose.password_value():
        display(term, "Service verrouillé. Configuration locale requise.")
        return False, "retour"
    # Console has a direct echo switch; physical path uses protocol routing.
    # Actual Matra Minitel 1 echo support still requires physical validation.
    previous = getattr(term.s, "local_echo", True)
    setter = getattr(term.s, "set_local_echo", None)
    try:
        if callable(setter):
            setter(False)
        else:
            term.w(b"\x1b\x3b\x60\x58\x51")  # keyboard -> screen OFF
        display(term, "Mot de passe :")
        attempt, action = read_field(term, secret=True)
        accepted = action == "submit" and rose.authorized(attempt)
        attempt = None
    finally:
        term.clear()
        if callable(setter):
            setter(previous)
        else:
            term.w(b"\x1b\x3b\x61\x58\x51")
    if not accepted and action == "submit":
        display(term, "Accès refusé.")
    return accepted, action


def roster(term, test=False):
    """Provisional flat catalogue, two biographies/page; direct name at any point."""
    people = rose.catalogue(test=test)["personas"]
    pages = (len(people) + 1) // 2
    page = 0
    while True:
        text = f"3615 rose - Catalogue {page + 1}/{pages}\n\n"
        text += "\n\n".join(p["name"] + "\n" + p["bio"] for p in people[page*2:page*2+2])
        display(term, text)
        term.line("SUITE / nom puis ENVOI / RETOUR")
        name, action = read_field(term, paging=True)
        if action == "next":
            page = (page + 1) % pages
        elif action == "submit" and name.strip():
            alias = rose.entry(name)
            alias = name.strip() if alias is None else alias
            # Same alias grammar as ordinary entry; no prompt as a name.
            if rose.entry("3615 " + alias) is not None and alias:
                return alias, "submit"
        elif action != "help":
            return None, action


def wait(term, job, safeword, timeout):
    """Read exits during the request; discard all other typeahead on completion."""
    chat = api()
    typed = []
    while time.monotonic() < job.started + timeout:
        # Poll keys before accepting queued output so cancellation has priority.
        kind, code = term.read_key(0.01)
        if kind == "fn" and code in (chat.K_SOMMAIRE, chat.K_RETOUR, chat.K_ANNUL):
            job.cancel()
            return None, "sommaire" if code == chat.K_SOMMAIRE else "retour"
        if (kind == "fn" and code == chat.K_ENVOI) or (kind == "char" and code in (chat.CR, chat.LF)):
            reason = rose.stop_reason("".join(typed), safeword)
            typed.clear()
            if reason:
                job.cancel()
                return None, reason
        elif (kind == "fn" and code == chat.K_CORR) or (kind == "char" and code in (chat.BS, 0x7F)):
            if typed:
                typed.pop()
        elif kind == "char" and 0x20 <= code <= 0x7E and len(typed) < rose.MAX_INPUT:
            typed.append(chr(code))
        # Finish a begun command before displaying the reply; do not turn a
        # partially typed safeword into a new conversational submission.
        if typed:
            continue
        try:
            return job.output.get_nowait(), "done"
        except Empty:
            pass
    job.cancel()
    return None, "deadline"


def flush(term):
    for method in ("reset_output_buffer", "reset_input_buffer"):
        operation = getattr(term.s, method, None)
        if callable(operation):
            operation()


def show_bill(term, receipt, row_delay=0.32):
    chat = api()
    term.clear()
    term.w(chat.SCROLL_ON)
    term.w(chat.FG_WHITE)
    typed = []
    try:
        for row in receipt.rows:
            until = time.monotonic() + row_delay * (2 if row == "" else 1)
            while time.monotonic() < until:
                kind, code = term.read_key(min(0.05, max(0, until-time.monotonic())))
                if kind == "fn" and code == chat.K_SUITE:
                    flush(term)
                    display(term, "\n".join(receipt.final))
                    return "done"
                if kind == "fn" and code in (chat.K_SOMMAIRE, chat.K_RETOUR, chat.K_ANNUL):
                    flush(term)
                    term.clear()
                    return "sommaire" if code == chat.K_SOMMAIRE else "retour"
                if chat.retour_requested(kind, code, typed):
                    flush(term)
                    term.clear()
                    return "retour"
            term.line(chat.safe_display(row))
        # Keep the closing block at the top so the ordinary next prompt can
        # appear below it without clearing or wrapping over the totals.
        display(term, "\n".join(receipt.final))
        return "done"
    finally:
        term.w(chat.SCROLL_OFF)


def run(term, alias, model, timeout=30, row_delay=0.32, test=False):
    """Scope even the shared console keyboard hint to French, then restore it."""
    previous_hint = getattr(term.s, "hint", None)
    if isinstance(previous_hint, str):
        term.s.hint = "Entree ENVOI | ^N SUITE | ^P SOMMAIRE | ^G AIDE | ^R RETOUR | ^Q fermer"
    try:
        return _run(term, alias, model, timeout, row_delay, test=test)
    finally:
        if isinstance(previous_hint, str):
            term.s.hint = previous_hint


def _run(term, alias, model, timeout=30, row_delay=0.32, test=False):
    """Return ordinary-loop action and any outstanding isolated worker tuple."""
    accepted, action = gate(term)
    if not accepted:
        return action, None
    if not alias:
        alias, action = roster(term, test=test)
        if alias is None:
            return action, None
    if not model.ready():
        display(term, "Service indisponible. Clé Mistral non configurée.")
        return "retour", None
    session = rose.Session(alias, test=test)
    reason = "retour"
    try:
        display(term, "3615 rose\nService de conversation adulte fictive.\n\n"
                "RETOUR / SOMMAIRE : quitter.\nMot d'arrêt : " + session.safeword)
        text = None
        while session.active:
            if session.job and session.job.thread.is_alive():
                display(term, "Requête précédente en cours. Patientez ou quittez.")
            else:
                job = session.start(model, text)
                reply, action = wait(term, job, session.safeword, timeout)
                if action not in ("done", "deadline"):
                    reason = action
                    break
                if reply and reply.status == "moderation":
                    reason = "moderation"
                    break
                if reply and reply.status == "done":
                    display(term, session.receive(reply, text))
                else:
                    display(term, "Mot d'arrêt : " + session.safeword +
                            "\n\nService momentanément indisponible.\nRéessayez ou RETOUR.")
            while True:
                text, action = read_field(term, safeword=session.safeword)
                if action == "help" or (action == "submit" and rose.normalized(text) == "AIDE"):
                    display(term, "Mot d'arrêt : " + session.safeword +
                            "\n\nRETOUR / SOMMAIRE : quitter.\nLe mot d'arrêt quitte immédiatement.\nFrançais uniquement.")
                    continue
                if action == "submit" and not text.strip():
                    continue
                break
            if action != "submit":
                reason = action
                break
        receipt = session.close(reason)
        flush(term)
        action = show_bill(term, receipt, row_delay)
        return ("sommaire" if reason == "sommaire" or action == "sommaire" else "retour"), (
            session.job.pending() if session.job and session.job.thread.is_alive() else None)
    finally:
        # KeyboardInterrupt/disconnect also clears state; no crash-replayed bill.
        if session.active:
            session.close("disconnect")
