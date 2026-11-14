"""Parse the terminal's one-shot command prefix without touching secret input."""

import re


class CommandError(ValueError):
    """Invalid command input must be reported locally, never sent to a model."""


def expand_shortcut(text, shortcuts):
    """Expand a leading single-letter command, preserving the argument verbatim."""
    match = re.fullmatch(r"([A-Za-z])(?:\s+(.*))?", text, re.DOTALL)
    if not match:
        return text
    key = match[1].upper()
    if key not in shortcuts:
        raise CommandError("Commande inconnue. Utilisez :AIDE.")
    command, accepts_argument = shortcuts[key]
    argument = match[2]
    if argument and not accepts_argument:
        raise CommandError(f":{key} doit être saisi seul. Aucune action effectuée.")
    return command + (" " + argument if argument else "")


class InputMode:
    """Keep pending command state local to the current terminal input context."""

    def __init__(self):
        self.pending = False

    def reset(self):
        self.pending = False

    def submit(self, text):
        """Return (kind, text): chat, command, or prompt for the next command."""
        if self.pending:
            self.pending = False
            command = text.removeprefix(":")
            return ("command", command.strip()) if command.strip() else ("empty", "")
        if not text.startswith(":"):
            return "chat", text
        command = text[1:].strip()
        if not command:
            self.pending = True
            return "prompt", ""
        return "command", command
