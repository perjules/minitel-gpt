"""Offline dial/controller, real terminal/editor, routing and authenticated forms."""
import copy
import threading
from dataclasses import asdict
from unittest.mock import Mock

import pytest
import requests
import test_terminal_startup as startup
from test_rose_echo import submitted
from test_terminal_display import VisibleUART

from services import admin_ui as admin
from services import (
    config_store,
    intelligence,
    mistral_answers,
    mistral_settings,
    terminal_display,
)
from services import minitel_chatgpt as chat


@pytest.fixture(autouse=True)
def dial(tmp_path, monkeypatch):
    controller = intelligence.Controller(tmp_path)
    monkeypatch.setattr(intelligence, "controller", controller)
    return controller


def profiles():
    return {str(n): asdict(p) for n, p in intelligence.defaults().items()}


def test_default_migration_and_restart(dial):
    assert dial.level == 5
    assert dial.profiles() == intelligence.defaults()
    assert not dial.profiles_path.exists() and not dial.state_path.exists()
    data = profiles()
    data['11']['temperature'] = 1.4
    dial.save_profiles(data)
    dial.set_level(11)
    other = intelligence.Controller(dial.state_path.parent)
    assert other.level == 11
    assert other.resolve().temperature == 1.4
    dial.save_profiles(profiles())
    assert dial.level == 11  # Restoring profiles cannot move the physical dial.
    assert dial.state_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('field,value', [
    ('model', ''), ('model', 'bad\nMODEL=x'), ('model', '../ bad'), ('model', 3),
    ('reasoning_effort', 'medium'), ('reasoning_effort', None),
    ('temperature', '1'), ('temperature', True), ('temperature', -0.1),
    ('temperature', 1.51), ('temperature', 10 ** 500), ('temperature', float('nan')), ('temperature', float('inf')),
])
def test_invalid_profiles_leave_previous_save_intact(dial, field, value):
    dial.save_profiles(profiles())
    before = dial.profiles_path.read_bytes()
    data = profiles()
    data['1'][field] = value
    with pytest.raises(config_store.InvalidConfig):
        dial.save_profiles(data)
    assert dial.profiles_path.read_bytes() == before


@pytest.mark.parametrize('model,effort', [('custom/model:v1', ''), ('mistral-small-2603', 'high'),
                                        ('mistral-medium-3-5', 'none'), ('mistral-large-4-0', 'low')])
def test_model_specific_validation(dial, model, effort):
    data = profiles()
    data['1'].update(model=model, reasoning_effort=effort, temperature=1.5)
    dial.save_profiles(data)
    assert dial.profiles()[1].model == model


@pytest.mark.parametrize('model,effort', [('custom', 'high'), ('mistral-large-4-0', 'none'),
                                        ('mistral-large-2512', 'high')])
def test_unknown_reasoning_rejected(dial, model, effort):
    data = profiles()
    data['1'].update(model=model, reasoning_effort=effort)
    with pytest.raises(config_store.InvalidConfig):
        dial.save_profiles(data)


@pytest.mark.parametrize('bad', [None, {}, {'5': {}}, [], {'version': 1}])
def test_six_fixed_levels_required(dial, bad):
    with pytest.raises(config_store.InvalidConfig):
        dial.save_profiles(bad)


@pytest.mark.parametrize('level', intelligence.LEVELS)
def test_commands_and_immutable_snapshots(dial, level):
    old = dial.resolve()
    assert f'{level:02d}' in intelligence.command(f'  iNtElLiGeNcE \t {level}  ')
    assert dial.level == level
    assert dial.resolve() == intelligence.defaults()[level]
    assert old == intelligence.defaults()[5]
    assert f'I:{level:02d}' == intelligence.indicator()
    assert f'{level:02d}' in intelligence.command('INTELLIGENCE')


@pytest.mark.parametrize('bad', ['0', '2', '10', '12', '05', 'x', '3 extra', '-1'])
def test_invalid_commands(dial, bad):
    assert intelligence.command('INTELLIGENCE ' + bad) == 'INTELLIGENCE : 1, 3, 5, 7, 9, 11'
    assert dial.level == 5


@pytest.mark.parametrize('bad', [True, 5.0, '5', 10, None])
def test_hardware_contract_accepts_only_valid_integers(dial, bad):
    with pytest.raises(config_store.InvalidConfig):
        dial.set_level(bad)


def test_non_commands_and_corrupt_configuration(dial):
    for text in ['intelligent', 'what intelligence?', 'INTELLIGENCE11']:
        assert intelligence.command(text) is None
    dial.profiles_path.write_text('{')
    with pytest.raises(config_store.InvalidConfig):
        dial.profiles()
    assert intelligence.snapshot() == intelligence.defaults()[5]
    dial.state_path.write_text('[]')
    assert intelligence.indicator() == 'I:??'
    assert 'invalide' in intelligence.command('INTELLIGENCE')
    assert '09' in intelligence.command('INTELLIGENCE 9')  # A new position repairs state.


def response(status=200, body=None):
    result = Mock(status_code=status)
    result.json.return_value = body or {'outputs': [{'type': 'message.output', 'role': 'assistant',
                                                    'content': [{'type': 'text', 'text': 'Bonjour.'}]}]}
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(response=result)
    return result


@pytest.mark.parametrize('level', intelligence.LEVELS)
def test_actual_api_payload_per_level(dial, monkeypatch, level):
    dial.set_level(level)
    post = Mock(return_value=response())
    monkeypatch.setattr(mistral_answers.requests, 'post', post)
    history = [{'role': 'user', 'content': 'Bonjour'}]
    original = copy.deepcopy(history)
    mistral_answers.answer(history, 'fr', 'synthetic', 'base', False, profile=dial.resolve())
    payload = post.call_args.kwargs['json']
    selected = intelligence.defaults()[level]
    assert payload['model'] == selected.model
    assert payload['completion_args']['temperature'] == selected.temperature
    assert payload['completion_args']['reasoning_effort'] == selected.reasoning_effort
    assert 'top_p' not in payload['completion_args']
    assert payload['tools'] == [] and history == original


@pytest.mark.parametrize('status,field,retry', [(422, 'temperature', True), (400, 'reasoning_effort', True),
                                             (404, 'model', True), (400, 'inputs', False),
                                             (429, 'model', False), (503, 'temperature', False)])
def test_fallback_is_bounded_and_only_for_configuration_rejection(monkeypatch, status, field, retry):
    sent = []
    responses = iter([response(status, {'param': 'completion_args.' + field}), response()])
    def post(*args, **kwargs):
        sent.append(copy.deepcopy(kwargs['json']))
        return next(responses)
    monkeypatch.setattr(mistral_answers.requests, 'post', post)
    def invoke():
        return mistral_answers.answer([{'role': 'user', 'content': 'Bonjour'}], 'fr', 'synthetic',
                                     'configured-base', False, profile=intelligence.defaults()[11])
    if retry:
        assert invoke()['intelligence_fallback']
        assert len(sent) == 2
        assert sent[1]['model'] == 'configured-base'
        assert sent[1]['completion_args'] == {'max_tokens': 700, 'temperature': .2}
    else:
        with pytest.raises(requests.HTTPError):
            invoke()
        assert len(sent) == 1


def test_network_failures_never_replay(monkeypatch):
    post = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(mistral_answers.requests, 'post', post)
    with pytest.raises(requests.Timeout):
        mistral_answers.answer([{'role': 'user', 'content': 'hello'}], 'fr', 'k', 'base',
                               profile=intelligence.defaults()[11])
    post.assert_called_once()


def test_worker_freezes_config_before_start_and_changes_apply_next_time(dial, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    seen = []
    def answer(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        seen.append(kwargs['intelligence_profile'])
        return {'display': 'ok', 'turn': None}
    monkeypatch.setattr(chat, 'call_answer', answer)
    dial.set_level(9)
    pending = chat.start_answer('', [{'role': 'user', 'content': 'hi'}], 'fr', ('conversations', False, 60), '')
    assert entered.wait(1)
    dial.set_level(11)
    release.set()
    pending[0].join(2)
    assert not pending[0].is_alive()
    assert seen == [intelligence.defaults()[9]]
    pending = chat.start_answer('', [], 'fr', ('conversations', False, 60), '')
    pending[0].join(2)
    assert seen[-1] == intelligence.defaults()[11]


@pytest.mark.parametrize('level', intelligence.LEVELS)
def test_real_frame_status_preserves_input_pages_and_columns(dial, monkeypatch, level):
    monkeypatch.setattr(chat.time, 'sleep', lambda _: None)
    uart = VisibleUART()
    term = chat.Term(uart)
    view = terminal_display.View(term, '\n'.join(f'Line {n}' for n in range(54))).install()
    view.buffer = list('partially typed input')
    view.input()
    before = uart.lines()
    wire = len(uart.output)
    notice = intelligence.command(f'INTELLIGENCE {level}')
    view.status(notice)
    view.input()
    after = uart.lines()
    assert before[:20] == after[:20] and before[21:] == after[21:]
    assert after[20][35:39] == f'I:{level:02d}'
    assert after[20][39] == ' '
    assert 'INTELLIGENCE' in after[20]
    assert b'\x0c' not in uart.output[wire:]
    view.move(1)
    assert view.page == 1 and view.buffer == list('partially typed input')
    assert uart.lines()[20][35:39] == f'I:{level:02d}'
    terminal_display.View(term, 'Returned from menu').install()
    assert uart.lines()[20][35:39] == f'I:{level:02d}'


def test_real_dispatcher_never_calls_model_or_records_dial(dial):
    seen = []
    def answer(history, *args, **kwargs):
        seen.append((copy.deepcopy(history), kwargs['profile']))
        return {'status': 'ordinary', 'text': 'Bonjour', 'chunks': [], 'references': []}
    helper = startup.StartupTests()
    helper.run_session(startup.login_keys('fr') + submitted('hello') + submitted('INTELLIGENCE 11')
                       + submitted('INTELLIGENCE') + submitted('INTELLIGENCE 10') + submitted('follow up'),
                       factual_reply=answer)
    assert len(seen) == 2
    assert seen[0][1].temperature == .2 and seen[1][1].temperature == 1.1
    assert [turn['content'] for turn in seen[1][0]] == ['hello', 'Bonjour', 'follow up']
    assert 'INTELLIGENCE' not in str(helper.memories[0].record.mock_calls)
    assert dial.level == 11


def test_admin_saves_validates_csrf_and_restores_without_restart(console, dial):
    client, headers, _ = console
    form = {}
    for n, p in intelligence.defaults().items():
        form.update({f'model_{n}': p.model, f'effort_{n}': p.reasoning_effort,
                     f'temperature_{n}': str(p.temperature)})
    assert client.post('/save-intelligence', data=form).status_code == 403
    assert client.post('/save-intelligence', data=form, headers=headers).status_code == 302
    old = dial.profiles_path.read_bytes()
    form['effort_9'] = 'none'
    client.post('/save-intelligence', data=form, headers=headers)
    assert dial.profiles_path.read_bytes() == old
    page = client.get('/?section=ai').text
    assert 'raisonnement non pris en charge' in page
    assert 'dial-model-11' in page and 'dial-temperature-1' in page
    dial.set_level(11)
    client.post('/save-intelligence', data={'restore': '1'}, headers=headers)
    assert dial.level == 11 and dial.profiles() == intelligence.defaults()
    assert not admin.ENV_FILE.exists()
    admin.restart_terminal.assert_not_called()


def test_service_config_ignores_dial(dial):
    before = mistral_settings.resolve({'MISTRAL_GMAIL_MODEL': 'mail'}, service='gmail')
    dial.set_level(11)
    assert mistral_settings.resolve({'MISTRAL_GMAIL_MODEL': 'mail'}, service='gmail') == before


def test_level_11_can_be_configured_independently(dial):
    data = profiles()
    data['11'] = {'model': 'custom-chat', 'reasoning_effort': '', 'temperature': 1.2}
    dial.save_profiles(data)
    assert dial.profiles()[9] == intelligence.defaults()[9]
    assert dial.profiles()[11] == intelligence.Profile('custom-chat', '', 1.2)


def test_pronote_routing_never_receives_dial_profile(dial, monkeypatch):
    dial.set_level(11)
    school = Mock()
    school.config.return_value = {'children': {'Alice': {}}}
    school.tools.return_value = [{'type': 'function'}]
    monkeypatch.setattr(chat, 'PROVIDER', 'mistral')
    monkeypatch.setattr(chat.pronote, 'backend', lambda: school)
    monkeypatch.setattr(chat.pronote_answers, 'school_tool_request', lambda *args: True)
    answer = Mock(return_value={'status': 'ordinary', 'text': 'Notes vérifiées', 'chunks': [], 'references': []})
    monkeypatch.setattr(mistral_answers, 'answer', answer)
    chat.call_answer('', [{'role': 'user', 'content': 'Notes Alice ?'}], 'fr', ('conversations', False, 60), '')
    assert answer.call_args.kwargs['pronote'] is school
    assert 'profile' not in answer.call_args.kwargs
    assert answer.call_args.args[3] == chat.MISTRAL_MODEL


def test_airzone_routing_never_receives_dial_profile(dial, monkeypatch):
    dial.set_level(11)
    monkeypatch.setattr(chat, 'PROVIDER', 'mistral')
    monkeypatch.setattr(chat.pronote, 'backend', lambda: None)
    monkeypatch.setattr(chat.home_config, 'enabled', lambda: True)
    monkeypatch.setattr(chat.airzone_answers, 'interested', lambda _: True)
    answer = Mock(return_value={'display': 'Zone confirmée', 'turn': None, 'airzone': True})
    monkeypatch.setattr(chat.airzone_answers, 'answer', answer)
    ordinary = Mock()
    monkeypatch.setattr(mistral_answers, 'answer', ordinary)
    chat.call_answer('', [{'role': 'user', 'content': 'Mets le salon à 20 degrés'}], 'fr',
                     ('conversations', False, 60), '')
    assert answer.call_args.args[3] == chat.MISTRAL_MODEL
    assert not answer.call_args.kwargs
    ordinary.assert_not_called()


def test_gmail_routing_never_receives_dial_profile(dial, monkeypatch):
    dial.set_level(11)
    monkeypatch.setattr(chat.gmail_mail, 'enabled', lambda: True)
    mail = Mock(draft=None, composition=None, recipient_choices=None)
    mail.handle.return_value = {'display': 'Courrier vérifié', 'turn': None}
    ordinary = Mock()
    monkeypatch.setattr(mistral_answers, 'answer', ordinary)
    chat.call_answer('', [{'role': 'user', 'content': 'MAIL'}], 'fr', ('conversations', False, 60), '', mail=mail)
    assert 'profile' not in mail.handle.call_args.kwargs
    ordinary.assert_not_called()


def test_school_backend_defensively_discards_profile_even_if_supplied(monkeypatch):
    from services import pronote_answers
    school = Mock()
    school.config.return_value = {'children': {'Alice': {}}}
    monkeypatch.setattr(pronote_answers, 'school_tool_request', lambda *args: True)
    answer = Mock(return_value={'private': True})
    monkeypatch.setattr(pronote_answers, 'answer', answer)
    result = mistral_answers.answer([{'role': 'user', 'content': 'Notes Alice'}], 'fr', 'key', 'base',
                                    pronote=school, profile=intelligence.defaults()[11])
    assert result == {'private': True}
    assert 'profile' not in answer.call_args.kwargs
    assert answer.call_args.args[3] == 'base'
