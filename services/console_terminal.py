"""Console transport for the real Term byte decoder and Minitel session loop.

Only the screen/keyboard connection changes. No serial port or baud pacing;
the application still owns every page, timeout, request and tool action.
"""

import curses
from collections import deque

from . import terminal_qr, videotex
from .terminal_display import STANDARD


class Screen:
    """Streaming 40-column screen: absolute cursor, gray levels and inverse text."""

    def __init__(self, geometry=STANDARD):
        self.geometry = geometry
        self.foreground, self.inverse = 7, False
        self.pending_background = 0
        self.zones = [{} for _ in range(geometry.rows)]
        self.address = None
        self.styles = [[(7, False, 0)] * geometry.columns for _ in range(geometry.rows)]
        self.rows = [list(" " * self.geometry.columns) for _ in range(self.geometry.rows)]
        self.row = self.column = 0
        self.scrolling = False
        self.cursor_visible = True
        self.escape = bytearray()
        self.repeat_pending = False
        self.last_character = " "
        self.ss2 = bytearray()
        self.mosaic = False

    def down(self):
        if self.row < self.geometry.rows - 1:
            self.row += 1
        elif self.scrolling:
            self.rows.pop(0)
            self.rows.append(list(" " * self.geometry.columns))
            self.zones.pop(0)
            self.zones.append({})
            self.styles.pop(0)
            self.styles.append([(7, False, 0)] * self.geometry.columns)
        else:
            self.row = 0

    def background(self, column):
        preceding = [key for key in self.zones[self.row] if key <= column]
        return self.zones[self.row][max(preceding)] if preceding else 0

    def put(self, code):
        char = chr(code) if isinstance(code, int) else code
        if (char == " " or self.mosaic) and self.pending_background is not None:
            self.zones[self.row][self.column] = self.pending_background
            self.pending_background = None
        else:
            self.zones[self.row].pop(self.column, None)
        # Zone changes affect existing cells until the next delimiter, too.
        for column in range(self.column, self.geometry.columns):
            foreground, inverse, _ = self.styles[self.row][column]
            self.styles[self.row][column] = (foreground, inverse, self.background(column))
        self.rows[self.row][self.column] = char
        self.styles[self.row][self.column] = (self.foreground, self.inverse, self.background(self.column))
        self.last_character = char
        self.column += 1
        if self.column == self.geometry.columns:
            self.column = 0
            self.down()

    def write(self, data):
        for code in data:
            if self.ss2:
                if code >= 0x20:
                    self.ss2.append(code)
                    if len(self.ss2) == 2 and code in videotex.ACCENTS:
                        continue
                    self.put(videotex.decode(self.ss2))
                    self.ss2.clear()
                    continue
                self.ss2.clear()
            if code == videotex.SS2:
                self.ss2.append(code)
                continue
            if self.repeat_pending:
                self.repeat_pending = False
                if 0x40 <= code <= 0x7F:
                    for _ in range(code - 0x40):
                        self.put(self.last_character)
            elif self.address is not None:
                self.address.append(code)
                if len(self.address) == 2:
                    row, column = (value - 0x40 for value in self.address)
                    if 1 <= row <= self.geometry.rows and 1 <= column <= self.geometry.columns:
                        self.row, self.column = row - 1, column - 1
                        self.foreground, self.inverse = 7, False
                        self.pending_background = 0
                    self.address = None
            elif self.escape:
                self.escape.append(code)
                command = self.escape[1]
                length = command - 0x38 + 2 if command in (0x39, 0x3A, 0x3B) else 2
                if len(self.escape) == length:
                    if bytes(self.escape) == b"\x1b\x3a\x69\x43":
                        self.scrolling = True
                    elif bytes(self.escape) == b"\x1b\x3a\x6a\x43":
                        self.scrolling = False
                    elif 0x40 <= command <= 0x47:
                        self.foreground = command - 0x40
                    elif 0x50 <= command <= 0x57:
                        self.pending_background = command - 0x50
                    elif command in (0x5C, 0x5D):
                        self.inverse = command == 0x5D
                    self.escape.clear()
            elif code in (0x0E, 0x0F):
                self.mosaic = code == 0x0E
                if self.mosaic:
                    self.inverse = False
            elif code == 0x12:
                self.repeat_pending = True
            elif code == 0x11:
                self.cursor_visible = True
            elif code == 0x14:
                self.cursor_visible = False
            elif code == 0x1F:
                self.address = bytearray()
            elif code == 0x1B:
                self.escape.append(code)
            elif code == 0x0C:
                self.rows = [list(" " * self.geometry.columns) for _ in range(self.geometry.rows)]
                self.styles = [[(7, False, 0)] * self.geometry.columns for _ in range(self.geometry.rows)]
                self.foreground, self.inverse = 7, False
                self.pending_background = 0
                self.zones = [{} for _ in range(self.geometry.rows)]
                self.row = self.column = 0
            elif code == 0x18:  # CAN: erase to end of line, without moving.
                for column in list(self.zones[self.row]):
                    if column >= self.column:
                        del self.zones[self.row][column]
                background = self.background(self.column)
                self.rows[self.row][self.column:] = [" "] * (self.geometry.columns - self.column)
                self.styles[self.row][self.column:] = [(self.foreground, self.inverse, background)] * (
                    self.geometry.columns - self.column)
            elif code == 0x1E:
                self.foreground, self.inverse = 7, False
                self.pending_background = 0
                self.row = self.column = 0
            elif code == 0x0D:
                self.column = 0
            elif code == 0x0A:
                self.down()
            elif code == 0x08:
                self.column = max(0, self.column - 1)
            elif 0x20 <= code <= (0x7F if self.mosaic else 0x7E):
                self.put(terminal_qr.mosaic_character(terminal_qr.mosaic_mask(code)) if self.mosaic else code)


def key_bytes(key):
    """Translate console shortcuts into actual Minitel SEP/function bytes."""
    functions = {
        "\n": 0x41, "\r": 0x41, curses.KEY_ENTER: 0x41,
        "\x0e": 0x48, curses.KEY_NPAGE: 0x48, curses.KEY_F3: 0x48,
        "\x10": 0x46, curses.KEY_HOME: 0x46, curses.KEY_F2: 0x46,
        "\x07": 0x44, curses.KEY_F1: 0x44,
        "\x12": 0x42, "\x1b": 0x42, curses.KEY_F4: 0x42,
        "\x15": 0x45, curses.KEY_F5: 0x45,
        "\x02": 0x43, curses.KEY_F6: 0x43,
        "\x08": 0x47, "\x7f": 0x47, curses.KEY_BACKSPACE: 0x47,
    }
    if key in ("\x03", "\x11"):
        raise KeyboardInterrupt
    if key in functions:
        return bytes([0x13, functions[key]])
    if isinstance(key, str) and len(key) == 1 and videotex.input_character(ord(key)):
        for sequence, char in videotex.DECODE.items():
            if char == key:
                return sequence
        return key.encode("ascii")
    return b""


class ConsoleSerial:
    """Serial-shaped I/O, including the Minitel's local printable-key echo."""

    def __init__(self, window):
        self.window = window
        self.screen = Screen()
        self.pending = deque()
        self.local_echo = True
        self.gray_pairs = {}
        try:
            if curses.has_colors() and curses.COLORS >= 16:
                for pair, ink in ((1, curses.COLOR_WHITE), (2, curses.COLOR_BLACK)):
                    curses.init_pair(pair, ink, 8)  # Terminal's dark-gray palette entry.
                    self.gray_pairs[7 if pair == 1 else 0] = curses.color_pair(pair)
        except curses.error:
            pass  # A monochrome console approximates the physical gray field.
        self.hint = "Enter ENVOI | ^N SUITE | ^P SOMMAIRE | ^G GUIDE | ^R RETOUR | ^U ANNUL | ^B REPET | ^Q quit"
        window.keypad(True)
        window.timeout(100)

    def redraw(self):
        height, width = self.window.getmaxyx()
        self.window.erase()
        if height < 24 or width < 40:
            message = "Resize to at least 40 columns x 24 rows. Ctrl-Q exits."
            try:
                self.window.addnstr(0, 0, message, max(0, width - 1))
            except curses.error:
                pass
        else:
            for row, cells in enumerate(self.screen.rows):
                try:
                    start = 0
                    while start < len(cells):
                        style = self.screen.styles[row][start]
                        end = start + 1
                        while end < len(cells) and self.screen.styles[row][end] == style:
                            end += 1
                        foreground, inverse, background = style
                        attribute = curses.A_DIM if foreground in (1, 4, 5) else curses.A_NORMAL
                        if foreground in (3, 7):
                            attribute |= curses.A_BOLD
                        if inverse:
                            attribute |= curses.A_REVERSE
                        text = "".join(cells[start:end])
                        ink, paper = (background, foreground) if inverse else (foreground, background)
                        if paper == 2 and ink in self.gray_pairs:
                            attribute = self.gray_pairs[ink] | (curses.A_BOLD if ink == 7 else 0)
                        if ink == paper == 0:
                            text = " " * len(text)
                        self.window.addstr(row, start, text, attribute)
                        start = end
                except curses.error:
                    # ncurses reports ERR after writing the bottom-right cell.
                    pass
            if height > 24:
                self.window.addnstr(24, 0, self.hint, width - 1)
            self.window.move(self.screen.row, self.screen.column)
            try:
                curses.curs_set(1 if self.screen.cursor_visible else 0)
            except curses.error:
                pass
        self.window.refresh()

    def write(self, data):
        self.screen.write(data)
        self.redraw()
        return len(data)

    def read(self, count):
        if not self.pending:
            try:
                key = self.window.get_wch()
            except curses.error:
                return b""
            if key == curses.KEY_RESIZE:
                self.redraw()
                return b""
            data = key_bytes(key)
            if self.window.getmaxyx()[0] < 24 or self.window.getmaxyx()[1] < 40:
                return b""
            if data and self.local_echo and (data[0] >= 0x20 or data[0] == videotex.SS2):
                self.screen.write(data)
                self.redraw()
            self.pending.extend(data)
        return bytes(self.pending.popleft() for _ in range(min(count, len(self.pending))))

    def reset_output_buffer(self):
        # Output is synchronous; nothing remains queued after an interruption.
        pass

    @property
    def in_waiting(self):
        if not self.pending:
            self.window.timeout(0)
            try:
                self.read(0)  # Queue one available key without consuming it.
            finally:
                self.window.timeout(100)
        return len(self.pending)

    def set_local_echo(self, enabled):
        self.local_echo = bool(enabled)

    def reset_input_buffer(self):
        self.pending.clear()
        curses.flushinp()
