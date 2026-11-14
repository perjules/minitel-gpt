"""Parse the terminal's one-shot command prefix without touching secret input."""


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
