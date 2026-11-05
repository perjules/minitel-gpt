"""Small 40-column Videotex frame and loaded-result viewer.

Rows are one-based, excluding service row 00. No mixed/80-column mode or model
calls. The only mutable state is the loaded view, row cache and current input.
"""

from contextlib import nullcontext
from dataclasses import dataclass

from . import io_limits, rose_terminal


@dataclass(frozen=True)
class Geometry:
    columns: int = 40
    rows: int = 24
    content_first: int = 2
    content_rows: int = 18
    input_rows: int = 3

    def __post_init__(self):
        if not (8 <= self.columns <= 40 and self.rows == 24
                and self.content_first == 2 and self.content_rows >= 1
                and self.content_first + self.content_rows + self.input_rows + 1 == self.rows):
            raise ValueError("Invalid Videotex frame geometry")

    @property
    def width(self):
        # Never touch column 40: no automatic wrap or bottom-right rollover.
        return self.columns - 1

    @property
    def navigation_row(self):
        return self.content_first + self.content_rows

    @property
    def status_row(self):
        return self.navigation_row + 1

    @property
    def input_first(self):
        return self.status_row + 1

    def position(self, row, column=1):
        if not 1 <= row <= self.rows or not 1 <= column <= self.columns:
            raise ValueError("Cursor outside the usable screen")
        return bytes([0x1F, 0x40 + row, 0x40 + column])


STANDARD = Geometry()
NORMAL = b"\x0f\x1b\x4c\x1b\x49\x1b\x59\x1b\x5c\x1b\x47"
INVERSE = b"\x1b\x5d"
POSITIVE = b"\x1b\x5c"
INPUT_FOREGROUND = 2  # Green: nominal 70% CRT luminance; inversion makes it the field background.


def encode_runs(text):
    """Encode printable ASCII only; callers bound each span to its screen row.

    REP's parameter counts additional copies (maximum 63). Runs of at least
    four save bytes; shorter runs remain literal. Never apply this to controls.
    """
    data = text.encode("ascii")
    if any(code < 0x20 or code > 0x7E for code in data):
        raise ValueError("REP encoder accepts printable ASCII only")
    result = bytearray()
    start = 0
    while start < len(data):
        end = start + 1
        while end < len(data) and data[end] == data[start]:
            end += 1
        count = end - start
        while count:
            size = min(count, 64)
            result.append(data[start])
            if size >= 4:
                result.extend((0x12, 0x40 + size - 1))
            else:
                result.extend([data[start]] * (size - 1))
            count -= size
        start = end
    return bytes(result)


class View:
    """Complete loaded text and display-only paging, independent of feature state."""

    def __init__(self, term, text, language="fr", *, title="3615 IA", reviewed=None, check=None):
        chat = rose_terminal.api()
        self.term, self.geometry, self.language = term, getattr(term, "geometry", STANDARD), language
        term.geometry = self.geometry
        self.title = chat.safe_display(title)
        heading = self.title.replace("\n", " ").replace("\t", " ")[:max(0, self.geometry.width - 8)]
        self.heading = f"*** {heading} ***".center(self.geometry.width)
        self.text = text
        lines = chat.wrap(chat.safe_display(text), self.geometry.columns)
        height = self.geometry.content_rows
        self.pages = [lines[i:i + height] for i in range(0, len(lines), height)] or [[""]]
        self.page = 0
        self.reviewed, self.check = reviewed, check
        self.seen = set()
        self.acknowledged = False
        self.cache = {}
        previous = getattr(term, "view", None)
        self.pending_input = list(previous.buffer) if previous is not None and previous.carry_input else []
        self.buffer = list(self.pending_input)
        self.carry_input = False
        self.notice = ""
        self.feature_navigation = False
        self.processing = False

    def active(self):
        if self.check is not None:
            self.check()

    def row(self, row, text, inverse=False, foreground=7):
        """Transmit only the changed span, including spaces that erase old text."""
        chat = rose_terminal.api()
        text = chat.safe_display(text).replace("\n", " ").replace("\t", " ")
        text = text[:self.geometry.width].ljust(self.geometry.width)
        previous = self.cache.get(row)
        if previous == (text, inverse, foreground):
            return
        left, right = 0, len(text)
        if previous is not None and previous[1:] == (inverse, foreground):
            old = previous[0]
            while left < right and old[left] == text[left]:
                left += 1
            while right > left and old[right - 1] == text[right - 1]:
                right -= 1
        self.term.w(self.geometry.position(row, left + 1))
        self.term.w((INVERSE if inverse else POSITIVE) + bytes([0x1B, 0x40 + foreground]))
        if previous is None or previous[1:] != (inverse, foreground):
            # CAN also establishes the untouched C40 background without printing
            # there or advancing the cursor. Keep its shade stable on later clears.
            self.term.w(b"\x18")
        self.term.w(encode_runs(text[left:right]))
        self.term.w(POSITIVE)
        self.cache[row] = (text, inverse, foreground)

    def install(self):
        self.active()
        previous = getattr(self.term, "view", None)
        if previous is None:
            self.term.clear()
            self._blank_cache()
        else:
            self.cache = dict(previous.cache)
        self.term.w(NORMAL)
        self.term.view = self
        if previous is not None:
            if not previous.carry_input:
                self._erase_input()
            self._clear_content()
        self.draw()
        return self

    def _blank_cache(self):
        self.cache = {row: (" " * self.geometry.width, False, 7)
                      for row in range(1, self.geometry.rows + 1)}

    def draw(self):
        self.active()
        self.row(1, self.heading)
        if self.processing:
            self._frame()
            self.active()
            return
        for offset in range(self.geometry.content_rows):
            page = self.pages[self.page]
            self.row(self.geometry.content_first + offset, page[offset] if offset < len(page) else "")
        self._frame()
        # Private lock/expiry validation must bracket every displayed page and
        # its review acknowledgement, including the last page.
        self.active()
        self.seen.add(self.page)
        if self.reviewed is not None and not self.acknowledged and len(self.seen) == len(self.pages):
            self.reviewed()
            self.acknowledged = True

    def _frame(self):
        """Restore chrome/input after FF without displaying or acknowledging a page."""
        self.row(1, self.heading)
        navigation = {"grades": "SUITE:note+ RETOUR:note-", "exit": "SUITE:page RETOUR:quitter"}.get(
            self.feature_navigation, "SUITE:suiv RETOUR:prec")
        self.row(self.geometry.navigation_row, "Requete en cours..." if self.processing else
                 f"Page {self.page + 1}/{len(self.pages)} | {navigation}")
        self.status(self.notice)
        self.input()

    def status(self, text=""):
        self.notice = text
        fallback = "Commande + ENVOI / SOMMAIRE" if self.language == "fr" else "Command + ENVOI / SOMMAIRE"
        self.row(self.geometry.status_row, text or fallback)

    def busy(self, message):
        """Accept input, show waiting status, then clear the previous answer."""
        self.active()
        chat = rose_terminal.api()
        label = chat.safe_display(message).replace("\n", " ").replace("\t", " ")
        if len(label) > self.geometry.width:
            label = label[:self.geometry.width - 3] + "..."
        self.buffer.clear()
        self.pending_input.clear()
        self.carry_input = False
        self.processing = True
        self._erase_input()
        self.status(label.center(self.geometry.width))
        self.row(self.geometry.navigation_row, "Requete en cours...")
        self._clear_content()
        self.term.w(self.geometry.position(self.geometry.input_first, 3))
        self.active()

    def _erase_input(self):
        self.term.w(NORMAL + b"\x14")
        for row in reversed(range(self.geometry.input_first, self.geometry.rows + 1)):
            self._erase_row(row, 3 if row == self.geometry.input_first else 1)

    def _clear_content(self):
        """One transition for submission, replacement documents and loaded pages."""
        self.active()
        self.term.w(NORMAL + b"\x14")
        rows = range(self.geometry.content_first, self.geometry.navigation_row)
        if len(self.cache) != self.geometry.rows:
            # An uncertain cache requires recovery; ordinary page moves retain
            # the frame and input, regardless of the old answer's density.
            self.term.w(b"\x0c\x1e" + NORMAL + b"\x14")
            self._blank_cache()
            self._frame()
        else:
            for row in reversed(rows):
                self._erase_row(row)
        self.active()

    def _erase_row(self, row, column=1):
        """Clear a row tail with validated CAN; cursor stays at its start.

        Rows are visited bottom-to-top by callers. CAN clears through physical
        C40 with the selected attributes and never causes a wrap.
        """
        old, inverse, foreground = self.cache.get(row, (" " * self.geometry.width, False, 7))
        if not old[column - 1:].strip():
            return
        self.term.w(self.geometry.position(row, column))
        self.term.w((INVERSE if inverse else POSITIVE) + bytes([0x1B, 0x40 + foreground]))
        self.term.w(b"\x18")
        self.term.w(POSITIVE)
        self.cache[row] = (old[:column - 1] + " " * (self.geometry.width - column + 1),
                           inverse, foreground)

    def input(self):
        text = "".join(self.buffer)
        # A three-row viewport, with one cell reserved for the cursor. Move in
        # chunks rather than repainting three rows for every long-input key.
        capacity = self.geometry.width * self.geometry.input_rows - 3
        start = len(text) // capacity * capacity
        visible = "> " + text[start:]
        for offset in range(self.geometry.input_rows):
            self.row(self.geometry.input_first + offset,
                     visible[offset * self.geometry.width:(offset + 1) * self.geometry.width],
                     inverse=True, foreground=INPUT_FOREGROUND)
        if start or len(text) >= capacity:
            self.status(f"Saisie {len(text)}/{io_limits.MAX_QUESTION_CHARS} | CORRECTION / ANNUL")
        elif self.notice.startswith("Saisie "):
            self.status()
        self.term.w(self.geometry.position(self.geometry.input_first + len(visible) // self.geometry.width,
                                          len(visible) % self.geometry.width + 1))

    def move(self, step):
        page = max(0, min(len(self.pages) - 1, self.page + step))
        if page == self.page:
            self.input()
            return
        self.page = page
        self._clear_content()
        self.draw()
        self.term.w(b"\x11")

    def read(self, *, allow_empty=False, calculator_mode=False, navigation_mode=False,
             touch=None, echo=False):
        """Own the editor only while receiving input; always restore echo on exit.

        echo=True is for the journal, whose outer password/session guard already
        keeps hardware echo disabled. It must not be re-enabled by an inner reader.
        """
        context = nullcontext() if echo else rose_terminal.hidden_echo(self.term)
        with context:
            return self._read(allow_empty, calculator_mode, navigation_mode, touch)

    def _read(self, allow_empty, calculator_mode, navigation_mode, touch):
        chat = rose_terminal.api()
        self.feature_navigation = "grades" if navigation_mode else "exit" if calculator_mode else False
        self.buffer, self.pending_input = self.pending_input, []
        self.carry_input = False
        overflow = False
        self.draw()
        self.term.w(b"\x11")  # Input is ready; keep the caret inside its region.
        while True:
            self.active()
            kind, code = self.term.read_key(0.2 if self.check is not None else chat.IDLE_TIMEOUT)
            self.active()
            if kind == "timeout":
                if self.check is not None:
                    continue
                return None, "timeout"
            if touch is not None:
                touch()
            submitted = (kind == "fn" and code == chat.K_ENVOI) or (
                kind == "char" and code in (chat.CR, chat.LF))
            if submitted:
                if overflow:
                    self.buffer.clear()
                    overflow = False
                    self.input()
                    self.status("Saisie trop longue (2000 max). Recommencez.")
                elif self.buffer or allow_empty:
                    text = "".join(self.buffer)
                    word = text.strip().upper()
                    if not navigation_mode and word in ("SUIVANT", "SUITE", "PRECEDENT"):
                        self.buffer.clear()
                        self.move(-1 if word == "PRECEDENT" else 1)
                    else:
                        return text, "envoi"
                continue
            if kind == "fn":
                if code == chat.K_SOMMAIRE:
                    return None, "sommaire"
                if code == chat.K_GUIDE:
                    return None, "guide"
                if code in (chat.K_SUITE, chat.K_RETOUR):
                    if navigation_mode:
                        self.carry_input = True
                        return "SUIVANT" if code == chat.K_SUITE else "PRECEDENT", "envoi"
                    if code == chat.K_RETOUR and calculator_mode:
                        return "RETOUR", "envoi"
                    self.move(1 if code == chat.K_SUITE else -1)
                    continue
                if code == chat.K_ANNUL:
                    self.buffer.clear()
                    overflow = False
                    self.input()
                    continue
            if (kind == "fn" and code == chat.K_CORR) or (
                kind == "char" and code in (chat.BS, 0x7F)):
                if self.buffer:
                    self.buffer.pop()
                    self.input()
            elif kind == "char" and 0x20 <= code <= 0x7E:
                if len(self.buffer) < io_limits.MAX_QUESTION_CHARS and not overflow:
                    self.buffer.append(chr(code))
                    self.input()
                else:
                    overflow = True
                    self.status("Saisie trop longue. ENVOI / ANNULATION")
