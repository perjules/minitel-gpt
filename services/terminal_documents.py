"""Browse loaded feature results using the shared fixed-frame editor/view."""

from . import rose_terminal, terminal_display


def read(term, text, language="fr", *, reviewed=None, check=None, touch=None,
         echo=False, title="3615 IA / DOCUMENT"):
    view = terminal_display.View(term, text, language, title=title, reviewed=reviewed, check=check)
    view.install()
    try:
        return view.read(echo=echo, touch=touch)
    except rose_terminal.EchoUnavailable:
        # Leave the feature cleanly rather than crashing or guessing whether
        # hardware echo is still active. Secret journal input has its own guard.
        term.clear()
        term.line("Saisie indisponible. Reconnectez-vous.")
        return None, "sommaire"
