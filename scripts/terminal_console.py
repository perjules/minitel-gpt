#!/usr/bin/env python3
"""Run the actual Minitel application in a Linux/macOS terminal or SSH session."""

import argparse
import curses
import logging
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Live 40x24 Minitel console; real APIs, memory and email actions.",
        epilog="Enter=ENVOI; Ctrl-N/F3=SUITE; Ctrl-P/F2=SOMMAIRE; "
               "Ctrl-G/F1=GUIDE; Ctrl-R/F4/Esc=RETOUR; Ctrl-Q/Ctrl-C=quit. "
               "Uses the checkout's .env and existing credentials; no UART access.",
    )
    parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(2, "An interactive terminal is required (use ssh -t if needed).\n")

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
