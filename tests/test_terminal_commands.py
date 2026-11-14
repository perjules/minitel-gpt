from services.terminal_commands import InputMode


def test_explicit_and_one_shot_commands_preserve_chat():
    mode = InputMode()
    assert mode.submit("bonjour") == ("chat", "bonjour")
    assert mode.submit(" :bonjour") == ("chat", " :bonjour")
    assert mode.submit(":AIDE") == ("command", "AIDE")
    assert mode.submit(":") == ("prompt", "")
    assert mode.submit("  LIRE 2  ") == ("command", "LIRE 2")
    assert mode.submit("bonjour") == ("chat", "bonjour")


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
