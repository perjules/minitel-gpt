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


def test_unknown_keyboard_command_never_reaches_services_or_model(monkeypatch):
    from unittest.mock import Mock

    from services import minitel_chatgpt as chat
    model = Mock(side_effect=AssertionError("general model must not be called"))
    monkeypatch.setattr(chat.mistral_answers, 'answer', model)
    result = chat.call_answer('', [{'role': 'user', 'content': 'NOT_A_COMMAND'}],
                              'fr', ('conversations', True, 10), '',
                              keyboard_mode=True, fixed_command=True)
    assert result['turn'] is None
    assert 'inconnue' in result['display']
    model.assert_not_called()


def test_physical_navigation_is_explicit_and_consumes_pending_mode():
    from services.terminal_commands import Command
    mode = InputMode()
    mode.submit(':')
    assert mode.submit(Command('PRECEDENT')) == ('command', 'PRECEDENT')
    assert mode.submit('bonjour') == ('chat', 'bonjour')


def test_mail_command_dispatch_bypasses_intent_router(monkeypatch):
    from unittest.mock import Mock

    from services import gmail_mail, terminal_commands
    monkeypatch.setattr(gmail_mail, 'enabled', lambda: True)
    mail = Mock()
    mail.handle.return_value = {'display': 'review required', 'turn': None}
    result = terminal_commands.execute('E', 'fr', 10, mail, 'review-ticket', None, None)
    mail.handle.assert_called_once_with('ENVOYER', 'review-ticket', 10)
    assert result['gmail']
    mail.reset_mock()
    assert 'inconnue' in terminal_commands.execute('LIRE invented', 'fr', 10, mail, None, None, None)['display']
    mail.handle.assert_not_called()


def test_real_uart_one_shot_unknown_and_chat_boundary():
    from test_rose_echo import submitted
    from test_terminal_startup import StartupTests, login_keys

    helper = StartupTests()
    _, calls = helper.run_session(login_keys('fr') + submitted(':') + submitted('AIDE')
        + submitted(':NOT_A_COMMAND') + submitted('ordinary greeting'))
    assert [history[-1]['content'] for _, history, _ in calls] == ['ordinary greeting']
    assert any('inconnue' in page for page in helper.result_screens)
    assert any(':COMMANDE' in page for page in helper.result_screens)


def test_real_uart_pending_command_is_reset_by_sommaire():
    from test_rose_echo import submitted
    from test_terminal_startup import StartupTests, fn, login_keys

    from services import minitel_chatgpt as chat
    helper = StartupTests()
    _, calls = helper.run_session(login_keys('fr') + submitted(':')
        + fn(chat.K_SOMMAIRE) + submitted('ordinary greeting'))
    assert [history[-1]['content'] for _, history, _ in calls] == ['ordinary greeting']


def test_plain_send_word_uses_natural_mail_router():
    from test_gmail import make_session
    mail, client, model = make_session()
    model.route.return_value = {'action': 'public'}
    result = mail.handle('ENVOYER', natural=True)
    assert result['passthrough']
    model.route.assert_called_once()
    client.send.assert_not_called()


@pytest.mark.parametrize('text,operation', [
    ('REBOOT', 'reboot'), (' reBoOt ', 'reboot'), ('REDEMARRER', 'reboot'),
    ('REDÉMARRER', 'reboot'), ('MAINFRAME   --reboot', 'reboot'),
    ('SHUTDOWN', 'poweroff'), ('POWEROFF', 'poweroff'), ('ARRET', 'poweroff'),
    ('ARRÊT', 'poweroff'), ('mainframe --shutdown', 'poweroff'),
])
def test_explicit_power_command_names(text, operation):
    from services.terminal_commands import power_operation
    assert power_operation(text) == operation


@pytest.mark.parametrize('text', ['', 'please reboot', 'reboot now', 'shutdown; reboot',
                                'mainframe --reboot extra', 'poweroff --force'])
def test_power_commands_require_exact_name(text):
    from services.terminal_commands import power_operation
    assert power_operation(text) is None


@pytest.mark.parametrize('command,operation', [('REBOOT', 'reboot'), ('SHUTDOWN', 'poweroff'),
                                              ('POWEROFF', 'poweroff'), ('MAINFRAME --reboot', 'reboot'),
                                              ('MAINFRAME --shutdown', 'poweroff')])
@pytest.mark.parametrize('one_shot', [False, True])
@pytest.mark.parametrize('confirmed', [False, True])
def test_real_power_dispatch_keeps_confirmation_and_bypasses_chat(monkeypatch, command, operation, one_shot, confirmed):
    from unittest.mock import Mock

    from test_rose_echo import submitted
    from test_terminal_startup import StartupTests, login_keys

    from services import minitel_chatgpt as chat
    launch = Mock()
    monkeypatch.setattr(chat.subprocess, 'Popen', launch)
    helper = StartupTests()
    keys = submitted(':') + submitted(command) if one_shot else submitted(':' + command)
    _, calls = helper.run_session(login_keys('fr') + keys + submitted('OUI' if confirmed else 'NON'),
                                  normal_exit=confirmed)
    assert not calls
    if confirmed:
        launch.assert_called_once()
        assert launch.call_args.args[0] == ['sudo', '-n', '/usr/bin/systemctl', operation]
    else:
        launch.assert_not_called()
