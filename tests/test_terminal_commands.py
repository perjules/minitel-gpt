import pytest

from services.terminal_commands import CommandError, InputMode, expand_shortcut


def test_explicit_and_one_shot_commands_preserve_chat():
    mode = InputMode()
    assert mode.submit("bonjour") == ("chat", "bonjour")
    assert mode.submit(" :bonjour") == ("chat", " :bonjour")
    assert mode.submit(":AIDE") == ("command", "AIDE")
    assert mode.submit(":") == ("prompt", "")
    assert mode.submit("  LIRE 2  ") == ("command", "LIRE 2")
    assert mode.submit("bonjour") == ("chat", "bonjour")


def test_shortcuts_expand_without_interpreting_payload():
    shortcuts = {"T": ("TEXTE :", True), "E": ("ENREGISTRER", False)}
    assert expand_shortcut("t :bonjour", shortcuts) == "TEXTE : :bonjour"
    assert expand_shortcut("E", shortcuts) == "ENREGISTRER"
    assert expand_shortcut("LIRE 2", shortcuts) == "LIRE 2"
    with pytest.raises(CommandError):
        expand_shortcut("E autre", shortcuts)
    with pytest.raises(CommandError):
        expand_shortcut("Z", shortcuts)


def test_command_prompt_can_be_cancelled_or_submitted_with_colon():
    mode = InputMode()
    assert mode.submit(": ") == ("prompt", "")
    assert mode.submit(":AIDE") == ("command", "AIDE")
    assert mode.submit(":") == ("prompt", "")
    mode.reset()
    assert mode.submit("bonjour") == ("chat", "bonjour")
    assert mode.submit(":") == ("prompt", "")
    assert mode.submit("") == ("empty", "")
    assert mode.submit("bonjour") == ("chat", "bonjour")
