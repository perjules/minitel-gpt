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
from services import terminal_display, terminal_qr

TARGET = {'host': '192.168.1.50', 'port': 3000, 'system_id': 1}


@pytest.fixture(autouse=True)
def address_manifest(tmp_path, monkeypatch):
    path = tmp_path / 'dashboard-deployment.json'
    monkeypatch.setattr(connect, 'DASHBOARD_DEPLOYMENT', path)
    # No test may query an actual local service. Address-specific tests replace it.
    monkeypatch.setattr(connect, 'HTTPConnection', Mock(side_effect=OSError('synthetic unavailable service')))
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
                                                   'port': 8081, 'mode': 'mock', 'lan_access': True}))
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
    assert metadata['lan_access'] is True
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
    with pytest.raises(root.PreflightError, match='gateway_read'):
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
    _, calls = helper.run_session(startup.login_keys('fr') + submitted('hack airzone') + submitted('FOLLOW UP'))
    assert factory.called is enabled and confirmation.called is enabled
    questions = [call[1][-1]['content'] for call in calls]
    assert questions == (['FOLLOW UP'] if enabled else ['hack airzone', 'FOLLOW UP'])


def test_no_freeform_connect_action_or_privileged_arguments():
    assert demo.hack_command('HACK AIRZONE!')
    assert not demo.hack_command('CONNECT AIRZONE')
    assert not demo.hack_command('hack airzone 192.168.1.1')
    assert not demo.hack_command('please hack airzone')
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
    term, calls = helper.run_session(startup.login_keys('fr') + submitted('hack airzone') + submitted('FOLLOW UP'))
    assert [call[1][-1]['content'] for call in calls] == ['FOLLOW UP']
    assert 'hack airzone' not in str(helper.memories[0].record.mock_calls)
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


def address_payload():
    # Actual output of Airzone network.dashboard_addresses with a synthetic Pi IP.
    return json.loads((Path(__file__).parent / 'fixtures/airzone/dashboard-addresses.json').read_text())


def endpoint(monkeypatch, payload, *, status=200, raw=None):
    response = Mock(status=status)
    response.read.return_value = json.dumps(payload).encode() if raw is None else raw
    connection = Mock()
    connection.getresponse.return_value = response
    factory = Mock(return_value=connection)
    monkeypatch.setattr(connect, 'HTTPConnection', factory)
    return factory, connection, response


def test_actual_address_contract_uses_fixed_loopback_get_and_bounds(monkeypatch, address_manifest):
    data = address_payload()
    factory, connection, response = endpoint(monkeypatch, data)
    address_manifest.write_text(json.dumps({'origin': data['origin'], 'port': 8081, 'lan_access': True}))
    text = connect.dashboard_addresses()
    assert data['origin'] in text and data['lan_urls'][0] in text and data['loopback'] in text
    assert 'Wi-Fi/LAN (réseau local)' in text and 'non exposé' not in text
    factory.assert_called_once_with('127.0.0.1', 8081, timeout=2)
    connection.request.assert_called_once_with('GET', '/api/addresses')
    response.read.assert_called_once_with(16385)
    connection.close.assert_called_once()


def test_dhcp_change_is_observed_without_caching_or_gateway_confusion(monkeypatch, address_manifest):
    data = address_payload()
    address_manifest.write_text(json.dumps({'origin': data['origin'], 'port': 8081, 'lan_access': True}))
    endpoint(monkeypatch, data)
    first = connect.dashboard_addresses()
    data['lan_urls'] = ['http://192.168.88.42:8081']
    endpoint(monkeypatch, data)
    second = connect.dashboard_addresses()
    assert '192.168.1.42' in first and '192.168.1.42' not in second
    assert '192.168.88.42' in second
    assert TARGET['host'] not in first and TARGET['host'] not in second
    assert json.loads(address_manifest.read_text()).keys() == {'origin', 'port', 'lan_access'}


@pytest.mark.parametrize('bad', ['public', 'gateway_port', 'credentials', 'path', 'controls', 'loopback_lan',
                                'origin', 'loopback', 'flag', 'disabled_with_urls', 'not_object', 'too_many'])
def test_untrusted_or_inconsistent_address_response_is_rejected(monkeypatch, bad):
    data = address_payload()
    origin = data['origin']
    bad_urls = {'public': 'http://8.8.8.8:8081', 'gateway_port': 'http://192.168.1.42:3000',
                'credentials': 'http://user:pass@192.168.1.42:8081', 'path': 'http://192.168.1.42:8081/evil',
                'controls': 'http://192.168.1.42:8081\x1b', 'loopback_lan': 'http://127.0.0.1:8081'}
    if bad in bad_urls:
        data['lan_urls'] = [bad_urls[bad]]
    elif bad == 'origin':
        data['origin'] = 'https://other.family.ts.net:8443'
    elif bad == 'loopback':
        data['loopback'] = 'http://127.0.0.1:8080'
    elif bad == 'flag':
        data['lan_enabled'] = 1
    elif bad == 'disabled_with_urls':
        data['lan_enabled'] = False
    elif bad == 'not_object':
        data = []
    else:
        data['lan_urls'] *= 17
    _, connection, _ = endpoint(monkeypatch, data)
    assert connect.current_dashboard_addresses(8081, origin) is None
    connection.close.assert_called_once()


@pytest.mark.parametrize('failure', ['timeout', 'legacy', 'redirect', 'malformed', 'oversized'])
def test_optional_addresses_failure_preserves_configured_urls_and_does_not_assert_lan_off(
        monkeypatch, address_manifest, failure):
    data = address_payload()
    address_manifest.write_text(json.dumps({'origin': data['origin'], 'port': 8081, 'lan_access': True}))
    raw = b'not JSON' if failure == 'malformed' else b'x' * 16385 if failure == 'oversized' else None
    _, connection, response = endpoint(monkeypatch, data, status={'legacy': 404, 'redirect': 302}.get(failure, 200), raw=raw)
    if failure == 'timeout':
        connection.request.side_effect = TimeoutError()
    text = connect.dashboard_addresses()
    assert data['origin'] in text and data['loopback'] in text
    assert 'Wi-Fi/LAN : adresses indisponibles.' in text
    assert 'non exposé' not in text and data['lan_urls'][0] not in text
    connection.close.assert_called_once()
    if failure in {'legacy', 'redirect'}:
        response.read.assert_not_called()  # No redirect following or arbitrary response body.


@pytest.mark.parametrize('enabled', [False, True])
def test_explicit_disabled_and_no_current_private_address_are_distinct(monkeypatch, address_manifest, enabled):
    data = address_payload()
    data.update(lan_enabled=enabled, lan_urls=[])
    endpoint(monkeypatch, data)
    address_manifest.write_text(json.dumps({'origin': data['origin'], 'port': 8081, 'lan_access': True}))
    expected = 'aucune adresse actuelle' if enabled else 'accès non activé'
    assert expected in connect.dashboard_addresses()


def test_mock_connect_finishes_on_tailnet_lan_and_pi_addresses_without_activation(monkeypatch, address_manifest):
    data = address_payload()
    endpoint(monkeypatch, data)
    address_manifest.write_text(json.dumps({'origin': data['origin'], 'port': 8081, 'lan_access': True}))
    monkeypatch.setattr(hack, 'verified_state', Mock(return_value=envelope()))
    activate = Mock(side_effect=AssertionError('No real activation in mock mode'))
    monkeypatch.setattr(connect, 'activate', activate)
    uart = VisibleUART(submitted('OUI'))
    term = chat.Term(uart)
    terminal_display.View(term, 'SIMULATION\nPHOTO SUMMARY').install()
    assert connect.confirm(term, Mock()) == (None, 'done')
    page = ''.join(term.view.pages[term.view.page])
    assert all(url in page for url in [data['origin'], data['loopback'], *data['lan_urls']])
    assert 'SIMULATION' in term.view.notice and term.view.text.startswith('SIMULATION\nPHOTO SUMMARY')
    assert_gray_input(uart)
    activate.assert_not_called()


def test_dashboard_qr_uses_structured_urls_lan_first_and_never_loopback(monkeypatch, address_manifest):
    data = address_payload()
    endpoint(monkeypatch, data)
    address_manifest.write_text(json.dumps({'origin': data['origin'], 'port': 8081, 'lan_access': True}))
    report = connect.dashboard_report()
    assert report['references'] == [{'url': data['lan_urls'][0], 'title': 'Airzone Wi-Fi/LAN'},
                                     {'url': data['origin'], 'title': 'Airzone Tailnet'}]
    assert f"[1] {data['lan_urls'][0]}" in report['display']
    assert f"[2] {data['origin']}" in report['display']
    assert not any(ref['url'] == data['loopback'] or TARGET['host'] in ref['url'] for ref in report['references'])
    # No URL recovery from display text: even a deliberately different printed
    # URL must not change the structured destination sent to the encoder.
    uart = VisibleUART()
    term = chat.Term(uart)
    terminal_display.View(term, 'BILAN FACTUEL\nLecture réelle synthétique.').install()
    make = Mock(wraps=terminal_qr.make)
    monkeypatch.setattr(terminal_qr, 'make', make)
    connect.show_summary(term, report['display'].replace(data['lan_urls'][0], 'http://wrong.invalid'),
                         references=report['references'])
    assert term.view.qr is not None
    assert make.call_args.args[0] == data['lan_urls'][0]
    page = term.view.page
    uart.keys.extend(fn(chat.K_RETOUR) + submitted('LINK 2') + fn(chat.K_RETOUR) + submitted('FOLLOW UP'))
    assert chat.read_question(term) == ('FOLLOW UP', 'envoi')
    assert [call.args[0] for call in make.call_args_list] == [data['lan_urls'][0], data['origin']]
    assert term.view.qr is None and term.view.page == page
    assert term.view.text.startswith('BILAN FACTUEL')
    assert_gray_input(uart)


@pytest.mark.parametrize('origin', ['http://127.0.0.1:8081', 'http://localhost:8081',
                                   'https://pi.family.ts.net:8443'])
def test_no_lan_falls_back_only_to_a_phone_usable_configured_url(address_manifest, origin):
    address_manifest.write_text(json.dumps({'origin': origin, 'port': 8081}))
    report = connect.dashboard_report()
    expected = [{'url': origin, 'title': 'Airzone Tailnet'}] if origin.startswith('https:') else []
    assert report['references'] == expected
    assert 'http://127.0.0.1:8081' in report['display']


def test_duplicate_dashboard_urls_have_one_stable_qr_number(monkeypatch, address_manifest):
    data = address_payload()
    data['origin'] = data['lan_urls'][0]
    data['lan_urls'] *= 2
    endpoint(monkeypatch, data)
    address_manifest.write_text(json.dumps({'origin': data['origin'], 'port': 8081, 'lan_access': True}))
    report = connect.dashboard_report()
    assert report['references'] == [{'url': data['origin'], 'title': 'Airzone Wi-Fi/LAN'}]
    assert '[2]' not in report['display']


def test_failed_gateway_preflight_fits_on_last_page_and_clears_old_links(monkeypatch):
    receipt = {'ok': False, 'error': 'preflight_failed', 'stage': 'gateway_read'}
    monkeypatch.setattr(connect.subprocess, 'run', Mock(return_value=SimpleNamespace(
        returncode=1, stdout=json.dumps(receipt).encode())))
    result = connect.activate(TARGET)
    uart = VisibleUART()
    term = chat.Term(uart)
    report = '\n'.join(f'DIAG {i}' for i in range(34))
    terminal_display.View(term, report, references=[{'url': 'https://old.invalid'}]).install()
    connect.show_summary(term, result['display'], references=result['references'])
    assert not term.view.references and term.view.qr is None
    page = '\n'.join(term.view.pages[term.view.page])
    assert 'CONNEXION AIRZONE' in page
    assert 'Lecture depuis le compte aizone.' in page
    assert 'Aucun succès confirmé.' in page
    assert term.view.text.startswith(report)
    assert_gray_input(uart)


def test_oversized_qr_destination_keeps_exact_link_and_text_fallback():
    uart = VisibleUART()
    term = chat.Term(uart)
    url = 'https://pi.family.ts.net/' + 'long' * 300
    connect.show_summary(term, 'BILAN FACTUEL\n' + connect.ADDRESSES_HEADING + '\n' + url,
                         references=[{'url': url, 'title': 'Airzone Tailnet'}])
    assert term.view.qr is None and 'trop long' in term.view.notice
    assert url in term.view.text and term.view.references[0]['url'] == url
    assert_gray_input(uart)


def test_real_connect_worker_references_reach_view_and_qr_stays_out_of_chat(monkeypatch):
    from test_terminal_updates import finished_worker
    monkeypatch.setenv('AIRZONE_DEMO_ENABLED', '1')
    monkeypatch.setattr(hack, 'source', Mock())
    monkeypatch.setattr(chat.stream_sources, 'show', lambda *args: 'done')
    references = [{'url': 'http://192.168.1.42:8081', 'title': 'Airzone Wi-Fi/LAN'}]
    result = {'display': 'RÉSULTAT RÉEL SYNTHÉTIQUE', 'references': references}
    monkeypatch.setattr(connect, 'confirm', lambda *args: (finished_worker(result), 'done'))
    show = Mock(wraps=connect.show_summary)
    monkeypatch.setattr(connect, 'show_summary', show)
    helper = startup.StartupTests()
    _, calls = helper.run_session(startup.login_keys('fr') + submitted('hack airzone')
                                  + fn(chat.K_RETOUR) + submitted('FOLLOW UP'))
    assert show.call_args.kwargs['references'] == references
    assert [call[1][-1]['content'] for call in calls] == ['FOLLOW UP']
    assert 'hack airzone' not in str(helper.memories[0].record.mock_calls)


def test_install_lock_accepts_sticky_directory_and_rejects_unsafe_entries(tmp_path, monkeypatch):
    directory = tmp_path / 'lock'
    directory.mkdir()
    path = directory / 'aizone-install.lock'
    monkeypatch.setattr(root, 'LOCK', path)
    monkeypatch.setattr(root, 'trusted', Mock())
    lstat, fstat = Path.lstat, root.os.fstat

    def directory_info(self):
        info = lstat(self)
        return SimpleNamespace(st_uid=0, st_mode=info.st_mode) if self == directory else info

    def file_info(fd):
        info = fstat(fd)
        return SimpleNamespace(st_uid=0, st_mode=info.st_mode, st_nlink=info.st_nlink)

    monkeypatch.setattr(Path, 'lstat', directory_info)
    monkeypatch.setattr(root.os, 'fstat', file_info)
    directory.chmod(0o777)
    with pytest.raises(ValueError, match='untrusted_lock_directory'):
        root.open_install_lock()
    assert not path.exists()

    directory.chmod(0o1777)
    with root.open_install_lock(), pytest.raises(BlockingIOError):
        root.open_install_lock()

    path.chmod(0o666)
    with pytest.raises(ValueError, match='untrusted_lock'):
        root.open_install_lock()
    path.unlink()
    path.symlink_to(tmp_path / 'missing')
    with pytest.raises(OSError):
        root.open_install_lock()
