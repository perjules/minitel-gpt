#!/usr/bin/env python3
"""Run the actual Minitel application in a Linux/macOS terminal or SSH session."""

import argparse
import curses
import logging
import os
import subprocess
import sys
from pathlib import Path


def launch_gmail_console():
    """Deliver existing encrypted credentials to an unprivileged console."""
    checkout = Path(__file__).resolve().parents[1]
    python = checkout / ".venv/bin/python"
    if not python.is_file():
        python = Path(sys.executable)
    command = [
        "sudo", "systemd-run", "--quiet", "--wait", "--pty", "--collect",
        "--uid=minitel",
        "--property=LoadCredentialEncrypted=gmail:/etc/credstore.encrypted/minitel-gmail",
        "--property=PrivateMounts=yes",
        f"--working-directory={checkout}",
        "--setenv=GMAIL_ENABLED=1",
        f"--setenv=TERM={os.getenv('TERM', 'xterm')}",
    ]
    if os.getenv("MINITEL_MEMORY_DIR"):
        command.append(f"--setenv=MINITEL_MEMORY_DIR={os.environ['MINITEL_MEMORY_DIR']}")
    # Child has systemd credentials and no --gmail flag: no recursive launch.
    command.extend([str(python), str(Path(__file__).resolve())])
    try:
        return subprocess.call(command)
    except OSError:
        print("Gmail console requires sudo and systemd-run on the enrolled Pi.",
              file=sys.stderr)
        return 2


def main():
    parser = argparse.ArgumentParser(
        description="Live 40x24 Minitel console; real APIs, memory and email actions.",
        epilog="Enter=ENVOI; Ctrl-N/F3=SUITE; Ctrl-P/F2=SOMMAIRE; "
               "Ctrl-G/F1=GUIDE; Ctrl-R/F4/Esc=RETOUR; Ctrl-Q/Ctrl-C=quit. "
               "Uses the checkout's .env and existing credentials; no UART access.",
    )
    parser.add_argument(
        "--gmail", action="store_true",
        help="use enrolled Gmail credentials via a temporary systemd service (may prompt for sudo)",
    )
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(2, "An interactive terminal is required (use ssh -t if needed).\n")

    if args.gmail:
        if not os.getenv("CREDENTIALS_DIRECTORY"):
            return launch_gmail_console()
        os.environ["GMAIL_ENABLED"] = "1"

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from services import minitel_chatgpt as chat
    from services.console_terminal import ConsoleSerial

    # Keep existing file logs, but avoid printing diagnostics over the screen.
    for handler in list(logging.getLogger().handlers):
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            logging.getLogger().removeHandler(handler)

    def session(window):
        # Disable tty flow control so Ctrl-Q reaches the application, and let
        # the adapter handle Ctrl-C with the same clean wrapper restoration.
        curses.raw()
        curses.curs_set(1)
        chat.run(chat.Term(ConsoleSerial(window)))

    try:
        curses.wrapper(session)
    except KeyboardInterrupt:
        return 0
    except curses.error:
        print("Console unavailable: use a terminal with TERM set correctly.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
