#!/usr/bin/env python3
"""Isolated SSH-driven 1200/7E1 experiment; --interaction tests CAN/REP/keys.

See docs/DISPLAY_PROBE.md (original WP1) and docs/INTERACTION_PROBE.md.
Interaction controls use /dev/tty and accept CR/LF, including with sudo/tee.

Never imports the application, loads .env, or changes the deployed configuration.
"""

import argparse
import fcntl
import json
import os
import re
import select
import signal
import subprocess
import sys
import termios
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import serial

VIDEO = b"\x1b\x3a\x32\x7e"
MIXED = b"\x1b\x3a\x32\x7d"
PAGE = b"\x1b\x3a\x6a\x43"
ROM = b"\x1b\x39\x7b"
CURSOR = b"\x1b\x61"
RECEIVERS = (0x58, 0x5A)  # screen, modem (local echo may loop through modem)
NORMAL = b"\x0f\x1b\x4c\x1b\x49\x1b\x59\x1b\x5c\x1b\x47"
GRAY = b"\x1b\x5d\x1b\x42"
MODE_STATUS = b"\x1b\x39\x72"
LOWER_ON = b"\x1b\x3a\x69\x45"
LOWER_OFF = b"\x1b\x3a\x6a\x45"


def repeat(character, count):
    """One encoded cell followed by REP for 0..63 additional cells."""
    if not 1 <= count <= 64:
        raise ValueError("Repeat count must be 1..64 cells")
    return character + bytes([0x12, 0x40 + count - 1])


def mode_status(reply):
    found = re.search(rb"\x1b\x3a\x73([\x40-\x7f])", reply)
    return found[1][0] if found else None


def report_summary(result):
    lines = ["\n=== MINITEL INTERACTION REPORT v1 ===",
             f"Completed: {result.get('completed', False)}; recovery: {result['recovery']}"]
    for label, check in result["checks"].items():
        lines.append(f"CHECK {label}: {check}")
    for label, observed in result["observations"].items():
        lines.append(f"OBS {label}: {observed['result']} {observed.get('note', '')}")
        if observed.get("keyboard_hex"):
            lines.append(f"KEYS {label}: {observed['keyboard_hex']}")
    lines.extend(f"RECOVERY ERROR: {error}" for error in result.get("recovery_errors", []))
    lines.append("Unknown/skipped is NOT a pass. Visual observations and raw keys decide the gates.")
    return "\n".join(lines)


class SSHInput:
    """Read observation controls from the controlling SSH tty, not piped stdio.

    Accept CR and LF explicitly. Enable ISIG so Ctrl-C triggers recovery;
    restore the original tty settings on every context exit.
    """

    def __init__(self, path="/dev/tty"):
        self.path, self.fd, self.original = path, None, None
        self.buffer = bytearray()
        self.after_cr = False

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            self.original = termios.tcgetattr(self.fd)
            settings = termios.tcgetattr(self.fd)
            settings[0] &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR)
            settings[1] |= termios.OPOST | termios.ONLCR
            settings[3] = (settings[3] & ~(termios.ICANON | termios.ECHO)) | termios.ISIG
            settings[6][termios.VMIN] = 0
            settings[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def poll(self):
        while select.select([self.fd], [], [], 0)[0]:
            data = os.read(self.fd, 1)
            if not data:
                raise KeyboardInterrupt
            if self.after_cr and data == b"\n":
                self.after_cr = False
                continue
            self.after_cr = data == b"\r"
            if data in (b"\r", b"\n"):
                line = self.buffer.decode("utf-8", errors="replace")
                self.buffer.clear()
                os.write(self.fd, b"\n")
                return line
            if data == b"\x04" and not self.buffer:
                raise KeyboardInterrupt
            if data in (b"\x08", b"\x7f"):
                if self.buffer:
                    removed = self.buffer.pop()
                    while self.buffer and removed & 0xC0 == 0x80:
                        removed = self.buffer.pop()
                    os.write(self.fd, b"\x08 \x08")
            elif data[0] >= 0x20 and len(self.buffer) < 1024:
                self.buffer.extend(data)
                os.write(self.fd, data)
        return None

    def __exit__(self, *exc):
        try:
            if self.fd is not None and self.original is not None:
                termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None


def check_idle(port):
    """Fail closed before opening the UART; TIOCEXCL also guards later opens."""
    result = subprocess.run(
        ["systemctl", "show", "minitel-chatgpt.service", "--property=ActiveState", "--value"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if result.returncode or result.stdout.strip() not in {"inactive", "failed"}:
        raise RuntimeError("Stop minitel-chatgpt.service first; cannot confirm it is stopped.")
    # Unlike pyserial's cooperative flock, fuser detects an already-open port
    # even when that process does not use flock. Run with sudo for full visibility.
    result = subprocess.run(["fuser", port], capture_output=True, timeout=5, check=False)
    if result.returncode != 1:
        raise RuntimeError("UART is busy or fuser could not verify it is free.")


def position(row, column, mixed):
    if not 1 <= row <= 24 or not 1 <= column <= (80 if mixed else 40):
        raise ValueError("Outside the probe's 24-row display")
    if mixed:
        return f"\x1b[{row};{column}H".encode("ascii")
    return bytes([0x1F, 0x40 + row, 0x40 + column])


def test_page(mixed):
    """A labelled grid with explicit addressing; no CR/LF assumptions in mixte."""
    cols = 80 if mixed else 40
    data = bytearray(b"\x1b[0m\x0f\x1b[2J" if mixed else b"\x0c\x1e\x0f\x1b\x47\x1b\x59")

    def at(row, column, text):
        data.extend(position(row, column, mixed) + text)

    for row in range(1, 25):
        # Avoid the last cell on row 24, so autowrap cannot disturb the top row.
        width = cols - (row == 24)
        line = f"{row:02d} " + "." * (width - 4) + "|"
        at(row, 1, line.encode("ascii"))
    at(1, 4, f"{'MIXTE 80' if mixed else 'VIDEOTEX 40'} x 24 - WP1".encode())
    at(2, 1, ("1234567890" * (cols // 10)).encode())
    at(4, 4, b"Aa Bb 0123 O0 Il1 ,.;:!? +-=/")
    if mixed:
        at(6, 4, b"US: @ [ \\ ] { | } ~")
        at(7, 4, b"FR: \x0e@ [ \\ ] { | } ~\x0f")
        at(8, 4, b"FR attendu: a-grave degre c-cedille")
        at(9, 4, b"puis section e-aigu u-grave e-grave trema")
        for row, attribute, label in ((11, 0, b"normal"), (12, 1, b"surintensite"),
                                      (13, 4, b"souligne"), (14, 7, b"inverse"),
                                      (15, 5, b"clignotant")):
            # Matra accepts one SGR parameter per command, not combined SGR.
            at(row, 4, f"\x1b[{attribute}m".encode() + label + b"\x1b[0m")
    else:
        at(6, 4, b"Accents: \x19\x42e \x19\x41a \x19\x41e \x19\x41u \x19\x4bc")
        at(7, 4, b"Attendu: e-aigu a/e/u-grave c-cedille")
        for color in range(8):
            at(10 + color, 4, bytes([0x1B, 0x40 + color]) + f"COULEUR {color}".encode())
        data.extend(b"\x1b\x47")
    at(19, 4, b"Saisie ci-dessous; pilotage via SSH.")
    at(24, 4, b"BAS R24 (bord a C" + str(cols - 1).encode() + b")")
    at(20, 1, b"> ")
    return bytes(data)


class Probe:
    def __init__(self, uart):
        self.uart = uart
        self.saved_routes = {}

    def send(self, data):
        # Small paced writes avoid flooding old terminal buffers at 1200 baud.
        for start in range(0, len(data), 16):
            chunk = data[start:start + 16]
            if self.uart.write(chunk) != len(chunk):
                raise OSError("Short UART write")
            time.sleep(len(chunk) * 10 / 1200 + 0.005)

    def receive(self, seconds):
        end = time.monotonic() + seconds
        data = bytearray()
        while time.monotonic() < end:
            chunk = self.uart.read(1)
            if chunk:
                data.extend(chunk)
        return bytes(data)

    def exchange(self, label, command):
        stale = self.receive(0.15)
        if stale:
            print(f"RX before {label}: {stale.hex(' ')}", flush=True)
        self.send(command)
        reply = self.receive(1.2)
        print(f"{label}: TX {command.hex(' ')} / RX {reply.hex(' ') or '(none)'}", flush=True)
        return reply

    def pause(self, label, seconds=120):
        print(f"\n{label}\nType on the MINITEL; RX below is raw, never software-echoed.\n"
              f"Press Enter in SSH to continue, q then Enter to quit ({seconds}s timeout).", flush=True)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            data = self.uart.read(1)
            if data:
                print(f"KEY RX {data.hex(' ')}", flush=True)
            if select.select([sys.stdin], [], [], 0)[0]:
                answer = sys.stdin.readline()
                if not answer or answer.strip().lower() == "q":
                    raise KeyboardInterrupt
                return
        raise TimeoutError("Observation timeout; restoring Videotex")

    def routing(self, receiver, command=None):
        reply = self.exchange(f"route {receiver:02x}", command or bytes([0x1B, 0x3A, 0x62, receiver]))
        found = re.search(re.escape(bytes([0x1B, 0x3B, 0x63, receiver])) + b"([\x40-\x4f])", reply)
        if found is None:
            raise RuntimeError("No valid routing status; controlled echo test skipped")
        return bool(found[1][0] & 2)

    @contextmanager
    def without_echo(self):
        # Snapshot BOTH routes before changing either. Remember before writing:
        # an interrupted command may already have reached the terminal.
        saved = {receiver: self.routing(receiver) for receiver in RECEIVERS}
        try:
            for receiver, enabled in saved.items():
                self.saved_routes[receiver] = enabled
                if self.routing(receiver, bytes([0x1B, 0x3B, 0x60, receiver, 0x51])):
                    raise RuntimeError("Echo suppression not acknowledged")
            yield
        finally:
            self.restore_routes()

    def restore_routes(self):
        failed = {}
        for receiver, enabled in self.saved_routes.items():
            try:
                self.send(bytes([0x1B, 0x3B, 0x61 if enabled else 0x60, receiver, 0x51]))
            except OSError:
                failed[receiver] = enabled
        self.saved_routes = failed
        if failed:
            raise OSError("Could not restore keyboard routes; power-cycle the Minitel")

    def observe(self, mixed):
        self.send(PAGE)
        self.receive(0.3)  # consume page-mode acknowledgement before keyboard test
        self.send(test_page(mixed))
        for row, col in ((1, 1), (12, 40 if mixed else 20), (24, 80 if mixed else 40)):
            self.send(position(row, col, mixed))
            self.exchange(f"cursor requested R{row} C{col}", CURSOR)
        self.send(position(20, 3, mixed))
        self.pause(f"{'80-column' if mixed else '40-column'} grid: inspect edges, accents and attributes. "
                   "Test abc, CORRECTION, ANNULATION, ENVOI, SUITE, RETOUR, SOMMAIRE and arrows.")
        try:
            with self.without_echo():
                self.send(position(21, 1, mixed) + b"ECHO OFF: ")
                self.pause("Echo OFF: type dummy text abc123. It should be received but invisible.")
        except RuntimeError as error:
            print(f"Echo test inconclusive: {error}", flush=True)
        self.send(position(22, 1, mixed) + b"ECHO RESTORED: ")
        self.pause("Original echo restored: type abc123 again; compare with the first input test.")

    def restore(self):
        # Try mode restoration even if echo restoration fails, and vice versa.
        try:
            reply = self.exchange("restore Videotex (expected SEP 71 if transitioning)", VIDEO)
            ack_seen = b"\x13\x71" in reply
            print(f"Restoration ACK seen: {ack_seen}; visually confirm 40 columns.", flush=True)
            self.send(PAGE + b"\x0f\x0c\x1e\x1b\x47\x1b\x59\x11DISPLAY PROBE TERMINE - VIDEOTEX 40")
            self.receive(0.3)
        finally:
            self.restore_routes()

    def run(self, restore_only=False):
        try:
            if restore_only:
                return
            self.exchange("ROM identification", ROM)
            # Deliberate standard baseline, not a restoration of arbitrary modes.
            self.exchange("baseline Videotex", VIDEO)
            self.observe(False)
            reply = self.exchange("enter mixte (expected SEP 70)", MIXED)
            if b"\x13\x70" not in reply:
                print("No mixed-mode ACK: 80 columns UNSUPPORTED OR UNCONFIRMED. "
                      "Not sending an 80-column page.", flush=True)
                return
            self.observe(True)
        finally:
            self.restore()


class InteractionProbe(Probe):
    """40-column-only, manual acceptance tests; never changes the application."""

    def __init__(self, uart, report, ssh=None):
        super().__init__(uart)
        self.report = Path(report)
        self.ssh = ssh
        # Refuse collisions/unwritable destinations before any terminal mutation.
        self.report.open("x", encoding="utf-8").close()
        self.result = {"version": 1, "started_utc": datetime.now(UTC).isoformat(),
                       "serial": "1200 7E1", "stage": "setup", "events": [],
                       "observations": {}, "checks": {}, "recovery": "not_attempted"}
        self.saved_lower = None
        self.lower_changed = False
        self.save()

    def save(self):
        self.report.write_text(json.dumps(self.result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    def event(self, direction, data):
        if data:
            self.result["events"].append({"stage": self.result["stage"],
                                          "direction": direction, "hex": data.hex(" ")})

    def send(self, data):
        # This is attempted TX, not proof of delivery. Cursor replies/manual
        # observations are separate evidence. Keep raw input out of screen output.
        self.event("tx", data)
        super().send(data)

    def receive(self, seconds):
        data = super().receive(seconds)
        self.event("rx_protocol_or_typeahead", data)
        return data

    def page(self, label):
        self.result["stage"] = label
        self.save()
        self.send(b"\x14\x0c" + NORMAL)
        self.at(1, 1, label[:39])
        self.at(20, 1, "SSH: Enter=continue; q=quit")
        self.at(21, 1, "PHOTOS: include the stage heading")

    def at(self, row, column, text, inverse=False):
        data = text.encode("ascii") if isinstance(text, str) else text
        self.send(position(row, column, False) + NORMAL + (GRAY if inverse else b"") + data)

    def cursor_check(self, label, row, column):
        reply = self.exchange(label, CURSOR)
        positions = re.findall(rb"\x1f([\x40-\x7f])([\x40-\x7f])", reply)
        actual = [value[0] - 0x40 for value in positions[-1]] if positions else None
        self.result["checks"][label] = {"expected": [row, column], "actual": actual,
                                        "result": "unknown" if actual is None else
                                        "pass" if actual == [row, column] else "fail"}
        self.save()

    def observation(self, label, instructions, *, judge=True, seconds=180):
        print(f"\n[{label}] {instructions}", flush=True)
        print("MINITEL keys are recorded RAW, never sent back to its screen.\n"
              "SSH: p=pass, f=fail, Enter=unknown, or a short note; q=quit."
              if judge else "SSH: Enter=continue, or q=quit. Use dummy text only.", flush=True)
        data = bytearray()
        deadline = time.monotonic() + seconds
        try:
            while time.monotonic() < deadline:
                chunk = self.uart.read(1)
                if chunk:
                    data.extend(chunk)
                    print(f"KEY RX {chunk.hex(' ')}", flush=True)
                answer = self.ssh.poll() if self.ssh is not None else (
                    sys.stdin.readline() if select.select([sys.stdin], [], [], 0)[0] else None)
                if answer is not None:
                    if (self.ssh is None and not answer) or answer.strip().lower() == "q":
                        raise KeyboardInterrupt
                    # Drain bytes already in transit before interpreting a later
                    # protocol reply. Do not type during cursor/status queries.
                    data.extend(self.receive(0.3))
                    note = answer.strip()
                    self.result["observations"][label] = {
                        "result": {"p": "pass", "f": "fail"}.get(note.lower(), "unknown"),
                        "note": note, "keyboard_hex": data.hex(" ")}
                    return bytes(data)
            raise TimeoutError(f"Observation timeout at {label}")
        finally:
            self.result["observations"].setdefault(label, {
                "result": "unknown", "note": "Interrupted/timed out before SSH confirmation",
                "keyboard_hex": data.hex(" ")})
            self.event("rx_keyboard", bytes(data))
            self.save()

    def clear_tests(self):
        self.page("C1 CAN - BEFORE")
        self.at(3, 1, "Rows 8/10 clear; 12/14 keep LEFT.")
        self.at(4, 1, "Row 24 clears; fences must survive.")
        self.at(7, 1, "ABOVE MUST STAY")
        self.at(8, 1, "NORMAL: old text MUST DISAPPEAR")
        self.at(9, 1, "BETWEEN MUST STAY")
        self.at(10, 1, "GRAY: old text MUST DISAPPEAR".ljust(39), True)
        self.at(12, 1, "LEFT STAYS|RIGHT MUST DISAPPEAR")
        self.at(14, 1, "LEFT STAYS|RIGHT MUST DISAPPEAR".ljust(39), True)
        self.at(19, 1, "BELOW MUST STAY")
        self.at(24, 1, "BOTTOM old text MUST DISAPPEAR")
        self.observation("C1-before", "Inspect the populated rows, then continue.", judge=False)
        self.result["stage"] = "C2 CAN - AFTER"
        self.at(1, 1, "C2 CAN - AFTER".ljust(39))
        # Includes inverse spaces, partial-row erasure, and bottom-row safety.
        # US resets attributes: reapply them before CAN. Never print in C40.
        for row, col, gray in ((24, 1, False), (14, 11, True), (12, 11, False),
                               (10, 1, True), (8, 1, False), (16, 1, False)):
            self.at(row, col, b"\x18", gray)
            self.cursor_check(f"CAN-R{row}-C{col}", row, col)
        self.observation("C2-after", "PASS only if rows 8/24 are blank black, row 10 blank gray, "
                         "rows 12/14 retain only LEFT STAYS, and fences/header are intact. "
                         "Photo this screen. Cursor checks must also pass.")
        self.at(8, 1, "NEW SHORT ANSWER")
        self.at(10, 1, "> abc", True)
        self.observation("C3-redraw", "New short text must have no old tails; gray must have no holes.")

    def repeat_tests(self):
        self.page("R1 REP - LITERAL vs COMPRESSED")
        self.at(3, 1, "Each pair must look identical.")
        for row, char, count in ((5, b"X", 1), (8, b"X", 2), (11, b"-", 39)):
            self.at(row, 1, char * count)
            self.at(row + 1, 1, repeat(char, count))
            self.cursor_check(f"REP-{count}-cells", row + 1, count + 1)
        self.at(14, 1, b"\x19\x42e" * 8)
        self.at(15, 1, repeat(b"\x19\x42e", 8))
        self.cursor_check("REP-accent-8-cells", 15, 9)
        self.observation("R1-pairs", "Pairs 5/6: one X; 8/9: two X; 11/12: 39 dashes; "
                         "14/15: eight e-acute characters. No wrapping. Photo this screen.")
        self.page("R2 REP - GRAY SPACES")
        for row in (6, 7, 10, 11):
            self.at(row, 1, "OLD TEXT MUST DISAPPEAR".ljust(39))
        self.at(6, 1, b" " * 39, True)
        self.at(7, 1, repeat(b" ", 39), True)
        self.cursor_check("REP-gray-39-cells", 7, 40)
        self.at(10, 1, b" " * 39)
        self.at(11, 1, repeat(b" ", 39))
        self.observation("R2-spaces", "Rows 6/7 must be identical uninterrupted gray strips; "
                         "10/11 blank black. No OLD TEXT remains. Photo this screen.")

    def character_tests(self):
        self.page("T1 CHARACTERS - OUTPUT ONLY")
        samples = [(5, b"Aa Zz 0123456789 .,:;!? +-*/=@"),
                   (7, b"\x19\x42e \x19\x41a \x19\x41e \x19\x41u \x19\x4bc"),
                   (9, b"\x19\x43a \x19\x43e \x19\x43i \x19\x43o \x19\x43u"),
                   (11, b"\x19\x48e \x19\x48i \x19\x7a \x19\x6a \x19\x23")]
        for row, data in samples:
            self.at(row, 1, data)
        self.observation("T1-output", "Rows: Aa Zz/digits/punctuation; e-acute a/e/u-grave c-cedilla; "
                         "a/e/i/o/u-circumflex; e/i-diaeresis, oe/OE ligatures, pound sign. "
                         "Note unsupported glyphs; photograph. No application decoder is involved.")

    def keyboard_test(self, label):
        self.page(label)
        self.at(3, 1, "1: a z then SHIFT+a SHIFT+z")
        self.at(4, 1, "2: keypad 0123456789 * #")
        self.at(5, 1, "3: punctuation @ . , ? ! + - / =")
        self.at(6, 1, "4: accents: e' a` e` u` c-cedille")
        self.at(7, 1, "5: CORRECTION ANNULATION ENVOI")
        self.at(8, 1, "6: SUITE RETOUR GUIDE SOMMAIRE REPET")
        self.at(22, 1, "> ")
        self.send(b"\x11")
        self.observation(label, "Type the ordered list printed on the Minitel, then SSH Enter. "
                         "Use the terminal's normal accent combinations. Record any unavailable keys. "
                         "Do NOT press CONNEXION/FIN or LOUPE. Local echo may be visible in this stage.",
                         judge=False)

    def lowercase_tests(self):
        status = mode_status(self.exchange("snapshot operating mode", MODE_STATUS))
        self.result["checks"]["lowercase-snapshot"] = status
        if status is None:
            self.result["observations"]["K2-lowercase"] = {
                "result": "unknown", "note": "Skipped mutation: no operating-mode snapshot"}
            self.save()
            print("No mode snapshot: lowercase mutation skipped; raw baseline is still useful.", flush=True)
            return
        self.saved_lower = bool(status & 8)
        # Set the recovery flag BEFORE transmitting; a failed write may be partial.
        self.lower_changed = True
        for enabled, label in ((False, "K2 UPPER MODE"), (True, "K3 LOWER MODE")):
            reply = mode_status(self.exchange(label, LOWER_ON if enabled else LOWER_OFF))
            self.result["checks"][label] = {
                "result": "unknown" if reply is None else "pass" if bool(reply & 8) == enabled else "fail",
                "status": reply}
            self.keyboard_test(label)
        self.restore_case()
        self.keyboard_test("K4 ORIGINAL MODE RESTORED")

    def echo_and_typeahead(self):
        self.page("E1 CONTROLLED ECHO OFF")
        entered = False
        try:
            with self.without_echo():
                entered = True
                self.at(3, 1, "Type dummy abc123: must be invisible.")
                self.at(22, 1, "> ".ljust(39), True)
                self.observation("E1-hidden", "Type abc123 on Minitel. PASS if RAW bytes arrive "
                                 "in SSH but no typed characters appear on screen. Never type a password.")
                self.page("Q1 TYPE WHILE PAGE DRAWS")
                self.at(3, 1, "At SSH cue: type abc123 then ENVOI.")
                self.observation("Q1-ready", "Read the timing instructions. SSH Enter arms the redraw.", judge=False)
                self.result["stage"] = "Q1-drawing"
                print("NOW on MINITEL: type abc123 then ENVOI while rows draw.", flush=True)
                for row in range(5, 19):
                    self.at(row, 1, f"R{row:02d} " + "." * 35)
                self.observation("Q1-typeahead", "PASS if complete ordered bytes 61 62 63 31 32 33 13 41 "
                                 "arrive (or capitals if original mode is uppercase). No letters should "
                                 "appear in answer rows. This is buffered RAW capture, not the app editor.")
                self.at(22, 3, b"")
                self.send(b"\x11")
                self.cursor_check("input-caret-after-redraw", 22, 3)
                self.observation("Q2-caret", "Cursor must blink at R22 C3, below the output. "
                                 "Type abc: RAW bytes arrive but no echo appears. Photo if cursor is wrong.")
        except RuntimeError as error:
            if entered or self.saved_routes:
                raise  # Never advertise restored echo after an unconfirmed restore.
            self.result["observations"]["E1-echo-guard"] = {"result": "unknown", "note": str(error)}
            print(f"Echo-dependent tests skipped: {error}", flush=True)
        self.page("E2 ORIGINAL ECHO RESTORED")
        self.at(22, 1, "> ")
        self.send(b"\x11")
        self.observation("E2-restored", "Type abc123. Echo must match K1 baseline again. "
                         "Note duplicates, missing echo or changed letter case.")

    def restore_routes(self):
        # Unlike the historical WP1 helper, verify restoration acknowledgements.
        failed = {}
        for receiver, enabled in self.saved_routes.items():
            try:
                actual = self.routing(receiver, bytes([0x1B, 0x3B, 0x61 if enabled else 0x60, receiver, 0x51]))
                if actual != enabled:
                    failed[receiver] = enabled
            except (OSError, RuntimeError):
                failed[receiver] = enabled
        self.saved_routes = failed
        if failed:
            raise RuntimeError("Echo restoration unconfirmed; power-cycle MINITEL before service restart")

    def restore_case(self):
        if self.lower_changed:
            reply = mode_status(self.exchange("restore original case", LOWER_ON if self.saved_lower else LOWER_OFF))
            if reply is None or bool(reply & 8) != self.saved_lower:
                raise RuntimeError("Case restoration unconfirmed; power-cycle MINITEL before service restart")
            self.lower_changed = False

    def restore(self):
        self.result["stage"] = "RECOVERY"
        failures = []
        # Attempt every independent restoration even if another one fails.
        for action in (self.restore_case, self.restore_routes,
                       lambda: self.exchange("restore page mode", PAGE),
                       lambda: self.send(b"\x0c" + NORMAL + b"\x11INTERACTION PROBE FINISHED - 40 COL")):
            try:
                action()
            except (OSError, RuntimeError) as error:
                failures.append(str(error))
        self.result["recovery"] = "unconfirmed" if failures else "commands_completed_verify_visually"
        self.result["recovery_errors"] = failures
        self.save()
        print(f"Report: {self.report}\nRecovery: {self.result['recovery']}", flush=True)
        print(report_summary(self.result), flush=True)
        if failures:
            raise RuntimeError("; ".join(failures))

    def run(self, restore_only=False):
        try:
            if restore_only:
                return
            self.exchange("40-column page baseline", PAGE)
            self.keyboard_test("K1 ORIGINAL KEYBOARD BASELINE")
            self.clear_tests()
            self.repeat_tests()
            self.character_tests()
            self.lowercase_tests()
            self.echo_and_typeahead()
            self.result["completed"] = True
        finally:
            self.restore()


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="explicit UART, e.g. /dev/ttyUSB0")
    parser.add_argument("--restore-only", action="store_true", help="only attempt standard 40-column recovery")
    parser.add_argument("--interaction", action="store_true", help="40-column CAN/REP/keyboard test; no 80-column commands")
    parser.add_argument("--report", help="new JSON report path (only with --interaction)")
    args = parser.parse_args(argv)
    if args.report and not args.interaction:
        parser.error("--report requires --interaction")
    if not args.interaction and not sys.stdin.isatty():
        parser.error("Run from an interactive SSH terminal (ssh -t), not a pipe")
    if os.geteuid() != 0:
        parser.error("Run with sudo so fuser can detect all UART owners")
    handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        check_idle(args.port)
        with serial.Serial(args.port, 1200, bytesize=7, parity="E", stopbits=1,
                           timeout=0.05, write_timeout=2, exclusive=True,
                           xonxoff=False, rtscts=False, dsrdtr=False) as uart:
            # Linux kernel exclusivity; the application does not use flock.
            fcntl.ioctl(uart.fileno(), termios.TIOCEXCL)
            # Close the gap between the preflight check and acquiring TIOCEXCL.
            result = subprocess.run(["fuser", args.port], capture_output=True, text=True,
                                    timeout=5, check=False)
            if result.returncode != 0 or set(result.stdout.split()) != {str(os.getpid())}:
                raise RuntimeError("Another process acquired the UART; no probe bytes sent")
            if args.interaction:
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                report = args.report or f"/tmp/minitel-interaction-{stamp}-{os.getpid()}.json"
                # sudo/tee and some SSH clients do not expose Enter through
                # buffered stdin as expected. Use the controlling tty directly.
                with SSHInput() as ssh:
                    InteractionProbe(uart, report, ssh).run(args.restore_only)
            else:
                Probe(uart).run(args.restore_only)
    except KeyboardInterrupt:
        print("\nInterrupted. Recovery attempted; verify the screen before restarting service.")
        return 130
    except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"Probe failed: {error}\nIf recovery failed: power-cycle MINITEL before restarting service.", file=sys.stderr)
        return 1
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    sys.exit(main())
