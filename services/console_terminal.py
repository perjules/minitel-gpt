"""Console transport for the real Term byte decoder and Minitel session loop.

Only the screen/keyboard connection changes. No serial port or baud pacing;
the application still owns every page, timeout, request and tool action.
"""

import curses
from collections import deque


class Screen:
    """Small streaming Videotex text screen, without colour or double-size art."""

    def __init__(self):
        self.rows = [list(" " * 40) for _ in range(24)]
        self.row = self.column = 0
        self.scrolling = False
        self.escape = bytearray()

    def down(self):
        if self.row < 23:
            self.row += 1
        elif self.scrolling:
            self.rows.pop(0)
            self.rows.append(list(" " * 40))
        else:
            self.row = 0

    def write(self, data):
        for code in data:
            if self.escape:
                self.escape.append(code)
                command = self.escape[1]
                length = command - 0x38 + 2 if command in (0x39, 0x3A, 0x3B) else 2
                if len(self.escape) == length:
                    if bytes(self.escape) == b"\x1b\x3a\x69\x43":
                        self.scrolling = True
                    elif bytes(self.escape) == b"\x1b\x3a\x6a\x43":
                        self.scrolling = False
                    self.escape.clear()
            elif code == 0x1B:
                self.escape.append(code)
            elif code == 0x0C:
                self.rows = [list(" " * 40) for _ in range(24)]
                self.row = self.column = 0
            elif code == 0x1E:
                self.row = self.column = 0
            elif code == 0x0D:
                self.column = 0
            elif code == 0x0A:
                self.down()
            elif code == 0x08:
                self.column = max(0, self.column - 1)
            elif 0x20 <= code <= 0x7E:
                self.rows[self.row][self.column] = chr(code)
                self.column += 1
                if self.column == 40:
                    self.column = 0
                    self.down()


def key_bytes(key):
    """Translate console shortcuts into actual Minitel SEP/function bytes."""
    functions = {
        "\n": 0x41, "\r": 0x41, curses.KEY_ENTER: 0x41,
        "\x0e": 0x48, curses.KEY_NPAGE: 0x48, curses.KEY_F3: 0x48,
        "\x10": 0x46, curses.KEY_HOME: 0x46, curses.KEY_F2: 0x46,
        "\x07": 0x44, curses.KEY_F1: 0x44,
        "\x12": 0x42, "\x1b": 0x42, curses.KEY_F4: 0x42,
        "\x15": 0x45, curses.KEY_F5: 0x45,
        "\x08": 0x47, "\x7f": 0x47, curses.KEY_BACKSPACE: 0x47,
    }
    if key in ("\x03", "\x11"):
        raise KeyboardInterrupt
    if key in functions:
        return bytes([0x13, functions[key]])
    if isinstance(key, str) and len(key) == 1 and 0x20 <= ord(key) <= 0x7E:
        return key.encode("ascii")
    return b""


class ConsoleSerial:
    """Serial-shaped I/O, including the Minitel's local printable-key echo."""

    def __init__(self, window):
        self.window = window
        self.screen = Screen()
        self.pending = deque()
        self.local_echo = True
        self.hint = "Enter ENVOI | ^N SUITE | ^P SOMMAIRE | ^G GUIDE | ^R RETOUR | ^U ANNUL | ^Q quit"
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
                    self.window.addstr(row, 0, "".join(cells))
                except curses.error:
                    # ncurses reports ERR after writing the bottom-right cell.
                    pass
            if height > 24:
                self.window.addnstr(24, 0, self.hint, width - 1)
            self.window.move(self.screen.row, self.screen.column)
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
            if len(data) == 1 and self.local_echo:
                self.screen.write(data)
                self.redraw()
            self.pending.extend(data)
        return bytes(self.pending.popleft() for _ in range(min(count, len(self.pending))))

    def reset_output_buffer(self):
        # Output is synchronous; nothing remains queued after an interruption.
        pass

    def set_local_echo(self, enabled):
        self.local_echo = bool(enabled)

    def reset_input_buffer(self):
        self.pending.clear()
        curses.flushinp()
