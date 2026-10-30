"""Browse loaded documents and accept commands on every page, including the last."""

from . import rose_terminal


def read(
    term, text, language="fr", *, reviewed=None, check=None, touch=None, echo=False
):
    """Return (command, action). Page navigation never invokes a service/model.

    A private caller supplies its lock/expiry checks and requests explicit echo
    while hardware echo is disabled. Draft approval occurs after all pages have
    been displayed, independently of when the reader submits a command.
    """
    chat = rose_terminal.api()
    lines = chat.wrap(chat.safe_display(text))
    pages = [
        lines[i : i + chat.CONTENT_ROWS]
        for i in range(0, len(lines), chat.CONTENT_ROWS)
    ] or [[""]]
    page, seen = 0, set()
    buffer = []
    overflow = False
    acknowledged = False

    def active():
        if check is not None:
            check()

    def draw(notice=""):
        nonlocal acknowledged
        active()
        term.clear()
        term.w(chat.FG_WHITE)
        for line in pages[page]:
            term.line(line)
        term.line(f"Page {page + 1}/{len(pages)} | SUITE / RETOUR")
        term.line(
            notice
            or (
                "Commande + ENVOI / SOMMAIRE"
                if language == "fr"
                else "Command + ENVOI / SOMMAIRE"
            )
        )
        term.w("> " + "".join(buffer))
        active()
        seen.add(page)
        if reviewed is not None and not acknowledged and len(seen) == len(pages):
            reviewed()
            acknowledged = True

    draw()
    while True:
        active()
        kind, code = term.read_key(0.2 if check is not None else chat.IDLE_TIMEOUT)
        active()
        if kind == "timeout":
            if check is not None:
                continue
            return None, "timeout"
        if touch is not None:
            touch()
        submitted = (kind == "fn" and code == chat.K_ENVOI) or (
            kind == "char" and code in (chat.CR, chat.LF)
        )
        if submitted:
            if overflow:
                buffer.clear()
                overflow = False
                draw("Saisie trop longue. Recommencez.")
            elif buffer:
                term.w(b"\r\n")
                return "".join(buffer), "envoi"
            continue
        if kind == "fn":
            if code == chat.K_SOMMAIRE:
                return None, "sommaire"
            if code == chat.K_GUIDE:
                return None, "guide"
            if code in (chat.K_SUITE, chat.K_RETOUR):
                page = max(
                    0, min(len(pages) - 1, page + (1 if code == chat.K_SUITE else -1))
                )
                draw()
                continue
            if code == chat.K_ANNUL:
                buffer.clear()
                overflow = False
                draw()
                continue
        if (kind == "fn" and code == chat.K_CORR) or (
            kind == "char" and code in (chat.BS, 0x7F)
        ):
            if buffer:
                buffer.pop()
                term.w(b"\x08 \x08")
        elif kind == "char" and 0x20 <= code <= 0x7E:
            if len(buffer) < 2000 and not overflow:
                buffer.append(chr(code))
                if echo:
                    term.w(bytes([code]))
            else:
                overflow = True
