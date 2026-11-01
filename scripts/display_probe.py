#!/usr/bin/env python3
"""Isolated, SSH-driven 1200/7E1 display experiment. See docs/DISPLAY_PROBE.md.

Never imports the application, loads .env, or changes the deployed configuration.
"""

import argparse
import fcntl
import os
import re
import select
import signal
import subprocess
import sys
import termios
import time
from contextlib import contextmanager

import serial

VIDEO = b"\x1b\x3a\x32\x7e"
MIXED = b"\x1b\x3a\x32\x7d"
PAGE = b"\x1b\x3a\x6a\x43"
ROM = b"\x1b\x39\x7b"
CURSOR = b"\x1b\x61"
RECEIVERS = (0x58, 0x5A)  # screen, modem (local echo may loop through modem)


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


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="explicit UART, e.g. /dev/ttyUSB0")
    parser.add_argument("--restore-only", action="store_true", help="only attempt standard 40-column recovery")
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
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
