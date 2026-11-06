"""Commissioning transaction tests: temp files, synthetic receipts, no root/Pi/HVAC."""
import json
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import test_terminal_startup as startup
from test_airzone_demo import envelope
from test_rose_echo import submitted
from test_terminal_display import VisibleUART, assert_gray_input
from test_terminal_startup import fn

from scripts import airzone_connect as root
from services import airzone_connect as connect
from services import airzone_demo as demo
from services import airzone_hack as hack
from services import minitel_chatgpt as chat
from services import terminal_display

TARGET = {'host': '192.168.1.50', 'port': 3000, 'system_id': 1}


@pytest.fixture(autouse=True)
def address_manifest(tmp_path, monkeypatch):
    path = tmp_path / 'dashboard-deployment.json'
    monkeypatch.setattr(connect, 'DASHBOARD_DEPLOYMENT', path)
    return path


@pytest.fixture
def installed(tmp_path, monkeypatch):
    base = tmp_path / 'opt/aizone'
    state = tmp_path / 'state'
    state.mkdir()
    config = tmp_path / 'etc/aizone/config.json'
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({'airzone_host': '192.168.1.99', 'public_origin': 'https://pi.example:8443',
                                 'allow_commands': True, 'zone_controls': {'1': {'on': True}},
                                 'master_zone_id': 1, 'supported_modes': [2], 'expected_zone_ids': [1],
                                 'operator_setting': 'keep'}))
    config.chmod(0o640)
    revision = 'a' * 40
    release = base / 'releases' / revision
    release.mkdir(parents=True)
    (release / 'aizone.service').write_text('[Service]\nUser=aizone\nExecStart=python app.py @@ARGS@@\nEnvironment=AIZONE_REVISION=@@REVISION@@\n')
    (base / 'current').symlink_to(release)
    (base / 'deployment.json').write_text(json.dumps({'revision': revision, 'origin': 'https://pi.example:8443',
                                                   'port': 8081, 'mode': 'mock'}))
    unit = tmp_path / 'aizone.service'
    unit.write_text('original mock service\n')
    for name, value in [('BASE', base), ('CONFIG', config), ('UNIT', unit), ('STATE', state)]:
        monkeypatch.setattr(root, name, value)
    # Production checks reject user-writable roots; tests deliberately use temp dirs.
    monkeypatch.setattr(root, 'trusted', Mock())
    monkeypatch.setattr(root, 'replace', lambda p, content, mode, gid=0: (p.write_bytes(content), p.chmod(mode)))
    monkeypatch.setattr(root, 'command', Mock())
    monkeypatch.setattr(root, 'receipt', Mock(return_value=envelope(real=True)))
    monkeypatch.setattr(root, 'dashboard', Mock(return_value={'zones': [{'id': 1, 'available': True}]}))
    monkeypatch.setattr(root.time, 'sleep', Mock())
    before = root.snapshot()
    return SimpleNamespace(base=base, config=config, unit=unit, state=state, before=before, release=release)


def test_success_switches_mode_preserves_url_and_turns_off_all_writes(installed):
    result = root.connect(TARGET)
    assert result == {'ok': True, 'origin': 'https://pi.example:8443', 'host': TARGET['host'], 'zones': 1}
    settings = json.loads(installed.config.read_text())
    assert settings['airzone_host'] == TARGET['host'] and settings['operator_setting'] == 'keep'
    assert settings['allow_commands'] is False and settings['zone_controls'] == {}
    assert settings['master_zone_id'] is None and settings['supported_modes'] == []
    assert settings['expected_zone_ids'] == []  # Observed IDs do not prove complete physical mapping.
    assert settings['public_origin'] == result['origin']
    assert '--config /etc/aizone/config.json --port 8081' in installed.unit.read_text()
    assert '--mock' not in installed.unit.read_text()
    metadata = json.loads((installed.base / 'deployment.json').read_text())
    assert metadata['mode'] == 'real' and metadata['port'] == 8081
    backup = installed.state / 'airzone-before-connect.json'
    assert json.loads(backup.read_text()) == installed.before
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    root.receipt.assert_called_once_with(installed.release, TARGET['host'], 3000, 1)
    assert root.dashboard.call_args.kwargs == {'state': True}
    assert all('restart' not in str(c) or 'aizone.service' in str(c) for c in root.command.call_args_list)


@pytest.mark.parametrize('phase', ['restart', 'health', 'state', 'timeout'])
def test_activation_failure_restores_config_unit_and_mode(installed, monkeypatch, phase):
    if phase == 'restart':
        calls = 0
        def fail_once(*args):
            nonlocal calls
            if 'restart' in args:
                calls += 1
                if calls == 1:
                    raise subprocess.CalledProcessError(1, args)
        monkeypatch.setattr(root, 'command', Mock(side_effect=fail_once))
    else:
        def failed(*args, **kwargs):
            if phase == 'health' or kwargs.get('state'):
                raise TimeoutError() if phase == 'timeout' else ValueError('synthetic failure')
            return {}
        root.dashboard.side_effect = failed
    assert root.connect(TARGET) == {'ok': False, 'error': 'activation_failed', 'rollback': True}
    assert root.snapshot() == installed.before


def test_rollback_failure_is_not_reported_as_restored(installed):
    root.command.side_effect = subprocess.CalledProcessError(1, ['systemctl'])
    # First active check must pass; then both activation and restoration fail.
    root.command.side_effect = [None, None, subprocess.CalledProcessError(1, ['systemctl']),
                                subprocess.CalledProcessError(1, ['systemctl'])]
    assert root.connect(TARGET)['rollback'] is False


@pytest.mark.parametrize('target', [dict(TARGET, host='127.0.0.1'), dict(TARGET, host='8.8.8.8'),
                                  dict(TARGET, host='192.168.1.50;sh'), dict(TARGET, port=True),
                                  dict(TARGET, system_id=33), dict(TARGET, shell='bad'), {}])
def test_invalid_targets_never_change_configuration(installed, target):
    with pytest.raises((ValueError, TypeError)):
        root.connect(target)
    root.receipt.assert_not_called()
    assert root.snapshot() == installed.before


def test_failed_fresh_read_leaves_even_old_backup_alone(installed):
    path = installed.state / 'airzone-before-connect.json'
    path.write_text('prior rollback')
    root.receipt.side_effect = ValueError('real state failed')
    with pytest.raises(ValueError):
        root.connect(TARGET)
    assert path.read_text() == 'prior rollback'
    assert root.snapshot() == installed.before


def test_explicit_restore_and_refusal_after_another_release(installed):
    root.connect(TARGET)
    root.restore(json.loads((installed.state / 'airzone-before-connect.json').read_text()))
    assert root.snapshot() == installed.before
    (installed.base / 'current').unlink()
    (installed.base / 'current').symlink_to(installed.base / 'releases' / ('b' * 40))
    with pytest.raises((ValueError, FileNotFoundError)):
        root.restore(installed.before)


@pytest.mark.parametrize('kind', ['real', 'mock', 'schema', 'nonzero', 'invalid', 'timeout'])
def test_root_reuses_actual_cli_contract_and_fixed_read_only_invocation(tmp_path, monkeypatch, kind):
    value = envelope(real=kind != 'mock')
    if kind == 'schema':
        value['schema_version'] = 2
    capture = Mock(return_value=(0 if kind != 'nonzero' else 4,
                                json.dumps(value).encode() if kind != 'invalid' else b'no JSON', b''))
    if kind == 'timeout':
        capture.side_effect = demo.DiagnosticError('timeout')
    monkeypatch.setattr(root, 'validator', lambda: demo)
    monkeypatch.setattr(demo, 'capture', capture)
    if kind == 'real':
        assert root.receipt(tmp_path, TARGET['host'], 3000, 1)['ok']
    else:
        with pytest.raises((ValueError, TypeError)):
            root.receipt(tmp_path, TARGET['host'], 3000, 1)
    argv, budget = capture.call_args.args
    assert argv[:5] == ['/usr/sbin/runuser', '-u', 'aizone', '--', '/usr/bin/python3']
    assert argv[6] == 'state' and '--mock' not in argv and budget == 8


def test_helper_is_fixed_and_writes_env_only_on_confirmed_success(tmp_path, monkeypatch, address_manifest):
    origin = 'https://pi.family.ts.net:8443'
    address_manifest.write_text(json.dumps({'origin': origin, 'port': 8081}))
    result = {'ok': True, 'host': TARGET['host'], 'zones': 6, 'origin': origin}
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(result).encode()))
    monkeypatch.setattr(connect.subprocess, 'run', run)
    monkeypatch.setattr(hack, 'ROOT', tmp_path)
    text = connect.activate(TARGET)['display']
    assert '6 zones' in text and 'Commandes HVAC désactivées' in text
    assert 'Tailnet (URL configurée)' in text and origin in text and 'http://127.0.0.1:8081' in text
    assert 'http://192.168.1.50:8081' not in text
    assert 'AIRZONE_GATEWAY=192.168.1.50' in (tmp_path / '.env').read_text()
    assert run.call_args.args[0] == ['/usr/bin/sudo', '-n', '/usr/local/libexec/minitel-airzone-connect']
    assert run.call_args.kwargs['shell'] is False and run.call_args.kwargs['timeout'] == 210
    assert json.loads(run.call_args.kwargs['input']) == TARGET


@pytest.mark.parametrize('result', [{'ok': False, 'rollback': True}, {'ok': False, 'rollback': False},
                                    {'ok': False}, [], {'ok': True, 'host': '8.8.8.8'}])
def test_failure_or_bad_receipt_never_saves_gateway(tmp_path, monkeypatch, result):
    monkeypatch.setattr(hack, 'ROOT', tmp_path)
    monkeypatch.setattr(connect.subprocess, 'run', Mock(return_value=SimpleNamespace(
        returncode=1 if isinstance(result, dict) and not result.get('ok') else 0,
        stdout=json.dumps(result).encode())))
    text = connect.activate(TARGET)['display']
    assert 'CONNECTÉ' not in text and not (tmp_path / '.env').exists()
    if isinstance(result, dict) and result.get('rollback') is False:
        assert 'incertain' in text


@pytest.mark.parametrize('mock,reply', [(True, 'OUI'), (False, 'NON'), (False, 'OUI')])
def test_french_confirmation_and_mock_do_not_activate_service(monkeypatch, mock, reply, address_manifest):
    monkeypatch.setenv('AIRZONE_DEMO_ENABLED', '1')
    monkeypatch.setattr(hack, 'verified_state', Mock(return_value=envelope(real=not mock)))
    activate = Mock(return_value={'display': 'ACTUAL RESULT'})
    monkeypatch.setattr(connect, 'activate', activate)
    address_manifest.write_text(json.dumps({'origin': 'https://pi.family.ts.net:8443', 'port': 8081}))
    uart = VisibleUART()
    term = chat.Term(uart)
    terminal_display.View(term, 'PHOTO SUMMARY').install()
    uart.keys.extend(submitted(reply))
    pending, action = connect.confirm(term, Mock())
    assert action == 'done' and term.view.text.startswith('PHOTO SUMMARY')
    if mock and reply == 'OUI':
        assert 'https://pi.family.ts.net:8443' in term.view.text
        assert 'http://127.0.0.1:8081' in term.view.text
        assert term.view.page == len(term.view.pages) - 1
        assert 'SIMULATION' in term.view.notice
        assert not (address_manifest.parent / '.env').exists()
    else:
        assert term.view.text == 'PHOTO SUMMARY'
    if pending is not None:
        pending[0].join(1)
        assert pending[1].get_nowait() == ({'display': 'ACTUAL RESULT'}, None)
    assert activate.called is (not mock and reply == 'OUI')


def test_expired_receipt_after_confirmation_cannot_start(monkeypatch):
    monkeypatch.setattr(hack, 'verified_state', Mock(side_effect=[envelope(real=True), None]))
    monkeypatch.setattr(chat, 'read_question', Mock(return_value=('OUI', 'envoi')))
    assert connect.confirm(Mock(), Mock()) == (None, 'done')


@pytest.mark.parametrize('enabled', [True, False])
def test_main_loop_routes_exact_command_and_recovers_to_ordinary_chat(monkeypatch, enabled):
    monkeypatch.setenv('AIRZONE_DEMO_ENABLED', '1' if enabled else '0')
    factory = Mock()
    monkeypatch.setattr(hack, 'source', factory)
    def display(term, current, language):
        chat.show_response(term, 'PHOTO SUMMARY', language)
        return 'done'
    monkeypatch.setattr(chat.stream_sources, 'show', display)
    confirmation = Mock(return_value=(None, 'done'))
    monkeypatch.setattr(connect, 'confirm', confirmation)
    helper = startup.StartupTests()
    _, calls = helper.run_session(startup.login_keys('fr') + submitted('connect airzone') + submitted('FOLLOW UP'))
    assert factory.called is enabled and confirmation.called is enabled
    questions = [call[1][-1]['content'] for call in calls]
    assert questions == (['FOLLOW UP'] if enabled else ['connect airzone', 'FOLLOW UP'])


def test_no_freeform_connect_action_or_privileged_arguments():
    assert connect.command('CONNECT AIRZONE!')
    assert not connect.command('connect airzone 192.168.1.1')
    assert not connect.command('please connect airzone')
    assert set(demo.TOOL['function']['parameters']['properties']['action']['enum']) == {'discover', 'identify', 'probe', 'state'}
    sudoers = (Path(__file__).parents[1] / 'config/minitel-airzone-sudoers').read_text()
    assert 'ALL=(ALL)' not in sudoers and '*' not in sudoers


@pytest.mark.parametrize('bad', [None, 'mock', 'enabled', 'offline', 'empty'])
def test_dashboard_check_uses_get_loopback_and_rejects_unusable_or_simulated_state(monkeypatch, bad):
    payload = {'demo': False, 'connection': 'online', 'commands_enabled': False,
               'zones': [{'id': 1, 'available': True}]}
    if bad == 'mock':
        payload['demo'] = True
    elif bad == 'enabled':
        payload['commands_enabled'] = True
    elif bad == 'offline':
        payload['connection'] = 'offline'
    elif bad == 'empty':
        payload['zones'] = []
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(payload).encode()
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(root, 'build_opener', Mock(return_value=opener))
    if bad:
        with pytest.raises(ValueError):
            root.dashboard(8081, 'https://pi.example:8443', 'a' * 40, state=True)
    else:
        assert root.dashboard(8081, 'https://pi.example:8443', 'a' * 40, state=True) == payload
    request = opener.open.call_args.args[0]
    assert request.full_url == 'http://127.0.0.1:8081/api/state'
    assert request.get_method() == 'GET' and request.get_header('Host') == 'pi.example:8443'


def test_confirmed_main_loop_activation_uses_result_and_normal_chat_recovers(monkeypatch):
    from test_terminal_updates import finished_worker
    monkeypatch.setenv('AIRZONE_DEMO_ENABLED', '1')
    monkeypatch.setattr(hack, 'source', Mock())
    monkeypatch.setattr(chat.stream_sources, 'show', lambda *args: 'done')
    monkeypatch.setattr(connect, 'confirm', lambda *args: (finished_worker({'display': 'ACTUAL CONNECT RESULT'}), 'done'))
    helper = startup.StartupTests()
    term, calls = helper.run_session(startup.login_keys('fr') + submitted('connect airzone') + submitted('FOLLOW UP'))
    assert [call[1][-1]['content'] for call in calls] == ['FOLLOW UP']
    assert 'connect airzone' not in str(helper.memories[0].record.mock_calls)
    assert term is not None


@pytest.mark.parametrize('metadata', [None, [], {'origin': 'https://pi.ts.net/evil\x1b', 'port': True},
                                    {'origin': 'https://pi.ts.net:99999', 'port': 70000},
                                    {'origin': 'https://user:pass@pi.ts.net', 'port': '8081'}])
def test_missing_or_invalid_address_configuration_never_fabricates_urls(address_manifest, metadata):
    if metadata is not None:
        address_manifest.write_text(json.dumps(metadata))
    result = connect.dashboard_addresses()
    assert 'Tailnet : adresse non déterminée.' in result and 'Adresse indisponible.' in result
    assert '8081' not in result and '\x1b' not in result and 'pass@' not in result


@pytest.mark.parametrize('bad', ['oversized', 'invalid_json', 'symlink', 'fifo'])
def test_unavailable_or_unsafe_manifest_does_not_block_summary(address_manifest, bad):
    import os
    if bad == 'oversized':
        address_manifest.write_text(json.dumps({'origin': 'https://pi.ts.net', 'port': 8081, 'extra': 'x' * 4096}))
    elif bad == 'invalid_json':
        address_manifest.write_text('not JSON')
    elif bad == 'symlink':
        address_manifest.symlink_to(address_manifest.parent / 'missing')
    else:
        os.mkfifo(address_manifest)
    assert 'Adresse indisponible.' in connect.dashboard_addresses()


def test_verified_origin_survives_missing_manifest_and_custom_origin_is_not_claimed_tailnet(address_manifest):
    result = connect.dashboard_addresses('https://pi.family.ts.net:8443')
    assert 'https://pi.family.ts.net:8443' in result and 'Adresse indisponible.' in result
    address_manifest.write_text(json.dumps({'origin': 'http://127.0.0.1:8081', 'port': 8081}))
    result = connect.dashboard_addresses()
    assert 'Tailnet : adresse non déterminée.' in result and 'URL configurée :' in result


def test_final_page_shows_both_complete_urls_and_diagnostic_pages_remain_navigable(address_manifest):
    origin = 'https://minitel-mainframe.family-network.ts.net:8443'
    uart = VisibleUART()
    term = chat.Term(uart)
    original = '\n'.join(f'DIAGNOSTIC {i:02d}' for i in range(55))
    terminal_display.View(term, original).install()
    address_manifest.write_text(json.dumps({'origin': origin, 'port': 8081}))
    text = connect.dashboard_addresses()
    assert connect.show_summary(term, 'SIMULATION\n\n' + text) == 'done'
    assert term.view.text.startswith(original)
    assert term.view.page == len(term.view.pages) - 1
    page = ''.join(term.view.pages[term.view.page])
    assert origin in page and 'http://127.0.0.1:8081' in page
    assert 'ADRESSES DU DASHBOARD' in page
    assert_gray_input(uart)
    last = term.view.page
    uart.keys.extend(fn(chat.K_RETOUR) + submitted('FOLLOW UP'))
    assert chat.read_question(term) == ('FOLLOW UP', 'envoi')
    assert term.view.page == last - 1
    assert_gray_input(uart)
