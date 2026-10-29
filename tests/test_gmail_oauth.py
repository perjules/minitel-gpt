"""Browser enrollment with synthetic accounts; no Google/systemd calls."""
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from google_auth_oauthlib.flow import Flow

from services import admin_ui as admin
from services import gmail_auth as auth
from services import gmail_oauth as oauth

ORIGIN = "https://pi.synthetic.ts.net:9443"
CONFIG = {"client_id": "synthetic.apps.googleusercontent.com", "client_secret": "PRIVATE-CLIENT",
          "redirect_uri": ORIGIN + auth.OAUTH_CALLBACK}
INFO = {key: CONFIG[key] for key in ("client_id", "client_secret")} | {
    "refresh_token": "PRIVATE-REFRESH", "token_uri": auth.TOKEN_URI,
    "scopes": list(auth.SCOPES), "email_address": "synthetic@example.org"}


@pytest.fixture
def enrollment(monkeypatch):
    oauth.PENDING.clear()
    helper = Mock(side_effect=lambda request: ({'status':'saved','revision':1} if 'account_id' in request and request['operation']=='enroll' else {'status':'saved'}))
    monkeypatch.setattr(auth, "credential_helper", helper)
    monkeypatch.setattr(oauth, "configuration", lambda: CONFIG.copy())
    yield helper
    oauth.PENDING.clear()


def begin():
    identifier, url = oauth.begin(CONFIG.copy())
    return identifier, parse_qs(urlsplit(url).query)


def mock_exchange(monkeypatch, granted=auth.SCOPES, refresh="PRIVATE-REFRESH"):
    credentials = SimpleNamespace(token="PRIVATE-ACCESS", refresh_token=refresh,
                                  client_id=CONFIG["client_id"], client_secret=CONFIG["client_secret"],
                                  scopes=auth.SCOPES, granted_scopes=granted)
    monkeypatch.setattr(Flow, "credentials", property(lambda _: credentials))
    exchange = Mock()
    monkeypatch.setattr(Flow, "fetch_token", exchange)
    response = Mock(status_code=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [b'{"emailAddress":"synthetic@example.org"}']
    get = Mock(return_value=response)
    monkeypatch.setattr(oauth.requests, "get", get)
    return exchange, get


def test_exact_scopes_pkce_encrypted_save_and_one_use(enrollment, monkeypatch):
    identifier, query = begin()
    assert query['redirect_uri'] == [CONFIG['redirect_uri']]
    assert set(query['scope'][0].split()) == set(auth.SCOPES)
    assert query['code_challenge_method'] == ['S256'] and query['access_type'] == ['offline']
    assert query['include_granted_scopes'] == ['false']
    assert 'PRIVATE-CLIENT' not in str(query)
    exchange, get = mock_exchange(monkeypatch)
    with pytest.raises(auth.GmailError, match='oauth_state_error'):
        oauth.complete(identifier, 'wrong-state', 'PRIVATE-CODE')
    exchange.assert_not_called()
    assert oauth.complete(identifier, query['state'][0], 'PRIVATE-CODE') == 'synthetic@example.org'
    exchange.assert_called_once_with(code='PRIVATE-CODE', timeout=(3, 15))
    assert get.call_args.args == ('https://gmail.googleapis.com/gmail/v1/users/me/profile',)
    assert enrollment.call_args.args[0] == {'operation': 'enroll', 'info': INFO}
    assert 'PRIVATE-ACCESS' not in str(enrollment.call_args)
    with pytest.raises(auth.GmailError, match='expired'):
        oauth.complete(identifier, query['state'][0], 'PRIVATE-CODE')


@pytest.mark.parametrize('failure', ['expired', 'denied', 'scopes', 'refresh', 'network', 'profile'])
def test_failures_preserve_existing_enrollment(enrollment, monkeypatch, failure):
    identifier, query = begin()
    exchange, get = mock_exchange(monkeypatch, granted=['bad'] if failure == 'scopes' else auth.SCOPES,
                                   refresh=None if failure == 'refresh' else 'PRIVATE-REFRESH')
    if failure == 'expired':
        future = oauth.PENDING[identifier][0] + oauth.LIFETIME + 1
        monkeypatch.setattr(oauth.time, 'monotonic', lambda: future)
    elif failure == 'network':
        exchange.side_effect = RuntimeError('PRIVATE-TOKEN-RESPONSE')
    elif failure == 'profile':
        get.return_value.iter_content.return_value = [b'x' * 32769]
    with pytest.raises(auth.GmailError) as error:
        oauth.complete(identifier, query['state'][0], 'PRIVATE-CODE', denied=failure == 'denied')
    assert 'PRIVATE' not in str(error.value)
    assert not oauth.PENDING
    enrollment.assert_not_called()


def test_attempts_are_bounded_and_cancelled(enrollment, monkeypatch):
    for _ in range(oauth.MAX_PENDING):
        begin()
    with pytest.raises(auth.GmailError, match='busy'):
        begin()
    identifier = next(iter(oauth.PENDING))
    oauth.cancel(identifier)
    begin()
    future = max(entry[0] for entry in oauth.PENDING.values()) + oauth.LIFETIME + 1
    monkeypatch.setattr(oauth.time, 'monotonic', lambda: future)
    begin()
    assert len(oauth.PENDING) == 1


def test_client_upload_validates_web_client_and_registered_uri(enrollment):
    web = {key: CONFIG[key] for key in ('client_id', 'client_secret')} | {
        'auth_uri': oauth.AUTH_URI, 'token_uri': auth.TOKEN_URI, 'redirect_uris': [CONFIG['redirect_uri']]}
    oauth.configure(json.dumps({'web': web}).encode(), CONFIG['redirect_uri'])
    assert enrollment.call_args.args[0] == {'operation': 'oauth_configure', 'config': CONFIG}
    enrollment.reset_mock()
    for raw in (b'{}', json.dumps({'installed': web}).encode(), b'x' * 32769,
                json.dumps({'web': web | {'token_uri': 'https://evil.invalid/token'}}).encode(),
                json.dumps({'web': web | {'redirect_uris': []}}).encode()):
        with pytest.raises(auth.GmailError):
            oauth.configure(raw, CONFIG['redirect_uri'])
    enrollment.assert_not_called()


@pytest.mark.parametrize('uri', ['http://pi.synthetic.ts.net' + auth.OAUTH_CALLBACK,
    'https://100.1.2.3' + auth.OAUTH_CALLBACK, ORIGIN + '/wrong', CONFIG['redirect_uri'] + '?extra=1',
    'https://user@pi.synthetic.ts.net' + auth.OAUTH_CALLBACK, CONFIG['redirect_uri'] + '#secret'])
def test_callback_configuration_rejects_unsafe_urls(uri):
    with pytest.raises(auth.GmailError):
        auth.validate_oauth_config(CONFIG | {'redirect_uri': uri})


def test_ui_callback_bound_to_login_browser_origin_and_csrf(console, enrollment, monkeypatch):
    client, headers, _ = console
    with client.session_transaction(base_url=ORIGIN) as state:
        state.update(logged_in=True, csrf_token='test-token')
    # The console fixture deliberately defaults to no configured OAuth client.
    monkeypatch.setattr(oauth, 'configuration', lambda: CONFIG.copy())
    route = '/services/gmail/oauth/start'
    assert client.post(route, base_url=ORIGIN).status_code == 403
    assert client.post(route, headers=headers).status_code == 303  # Plain HTTP refused.
    assert not oauth.PENDING
    response = client.post(route, headers=headers, base_url=ORIGIN)
    query = parse_qs(urlsplit(response.location).query)
    with client.session_transaction(base_url=ORIGIN) as state:
        identifier = state['gmail_oauth']
        assert set(state).isdisjoint({'refresh_token', 'client_secret', 'code_verifier'})
    mock_exchange(monkeypatch)
    other = admin.app.test_client()
    with other.session_transaction(base_url=ORIGIN) as state:
        state['logged_in'] = True
    callback = auth.OAUTH_CALLBACK + '?state=' + query['state'][0] + '&code=PRIVATE-CODE'
    other.get(callback, base_url=ORIGIN)
    enrollment.assert_not_called()
    assert identifier in oauth.PENDING
    result = client.get(callback, base_url=ORIGIN)
    assert result.status_code == 303 and result.location == '/services/gmail'
    assert result.headers['Referrer-Policy'] == 'no-referrer'
    assert 'PRIVATE' not in str(result.headers) and not oauth.PENDING
    page = client.get(result.location, base_url=ORIGIN)
    assert 'Appliquer l’autorisation enregistrée' in page.text
    assert not any(secret in page.text for secret in ('PRIVATE-CLIENT', 'PRIVATE-REFRESH', 'PRIVATE-CODE'))
    enrollment.side_effect = None
    enrollment.return_value = {'status': 'queued'}
    result = client.post('/services/gmail/oauth/apply', base_url=ORIGIN, headers=headers)
    assert result.status_code == 303
    assert enrollment.call_args.args[0] == {'operation': 'activate'}
    assert client.get(callback, base_url=ORIGIN).status_code == 303  # Replay never saves.
    assert sum(c.args[0]['operation'] == 'enroll' for c in enrollment.call_args_list) == 1


def test_https_proxy_cannot_be_spoofed_from_lan(console, enrollment):
    client, _headers, _ = console
    with (admin.app.test_request_context('/services/gmail/oauth/start', base_url=ORIGIN.replace('https:', 'http:'),
            headers={'X-Forwarded-Proto': 'https'}, environ_base={'REMOTE_ADDR': '192.0.2.4'}),
          pytest.raises(auth.GmailError)):
        admin.gmail_https_origin(CONFIG['redirect_uri'])
    with admin.app.test_request_context('/', base_url=ORIGIN.replace('https:', 'http:'),
            headers={'X-Forwarded-Proto': 'https'}, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        admin.gmail_https_origin(CONFIG['redirect_uri'])
    for action in ('start', 'configure', 'apply'):
        assert client.post('/services/gmail/oauth/' + action).status_code == 403
    anonymous = admin.app.test_client()
    assert anonymous.get(auth.OAUTH_CALLBACK, base_url=ORIGIN).location == '/login'


@pytest.mark.parametrize('path', [auth.OAUTH_CALLBACK, auth.OAUTH_CALLBACK.replace('callback', '%63allback')])
def test_callback_logger_strips_private_query(path):
    handler = object.__new__(admin.AdminRequestHandler)
    handler.path = path + '?state=PRIVATE-STATE&code=PRIVATE-CODE'
    handler.command, handler.request_version, handler.log = 'GET', 'HTTP/1.1', Mock()
    handler.log_request(303, 0)
    assert 'PRIVATE' not in str(handler.log.call_args)
    assert auth.OAUTH_CALLBACK in str(handler.log.call_args)


def test_configuration_upload_is_authenticated_https_and_bounded(console, enrollment, monkeypatch):
    client, headers, _ = console
    with client.session_transaction(base_url=ORIGIN) as state:
        state.update(logged_in=True, csrf_token='test-token')
    configure = Mock()
    monkeypatch.setattr(oauth, 'configure', configure)
    result = client.post('/services/gmail/oauth/configure', headers=headers, base_url=ORIGIN,
        data={'redirect_uri': CONFIG['redirect_uri'], 'client_json': (io.BytesIO(b'{}'), 'client.json')})
    assert result.status_code == 303
    configure.assert_called_once_with(b'{}', CONFIG['redirect_uri'])
    configure.reset_mock()
    result = client.post('/services/gmail/oauth/configure', headers=headers, base_url=ORIGIN,
        data={'redirect_uri': CONFIG['redirect_uri'], 'client_json': (io.BytesIO(b'x' * 40001), 'client.json')})
    assert result.status_code == 413
    configure.assert_not_called()


def root_helper(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('gmail_helper_oauth_test',
                                                 Path(__file__).parents[1] / 'scripts/gmail_credential_helper.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, 'gmail_auth', auth)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ACCOUNTS_STORE', tmp_path / 'accounts')
    monkeypatch.setattr(module, 'Path', lambda p: tmp_path / str(p).lstrip('/'))
    (tmp_path / 'run').mkdir()
    (tmp_path / 'var/lib/minitel-gpt').mkdir(parents=True)
    monkeypatch.setattr(module, 'decrypt_info', lambda: INFO.copy())
    monkeypatch.setattr(module, 'decrypt_oauth_config', lambda: CONFIG.copy())
    monkeypatch.setattr(module, 'encrypt_info', Mock())
    monkeypatch.setattr(module, 'encrypt_oauth_config', Mock())
    monkeypatch.setattr(module.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=0)))
    return module


def test_root_helper_only_saves_validated_credentials_and_fixed_activation(tmp_path, monkeypatch):
    module = root_helper(tmp_path, monkeypatch)
    assert module.process({'operation': 'oauth_configure', 'config': CONFIG}) == {'status': 'saved'}
    assert module.process({'operation': 'enroll', 'info': INFO}) == {'status': 'saved'}
    module.encrypt_info.assert_called_once_with(INFO)
    for request in ({'operation': 'enroll', 'info': INFO | {'client_id': 'other.apps.googleusercontent.com'}},
                    {'operation': 'activate', 'command': '/bin/evil'},
                    {'operation': 'enroll', 'info': INFO, 'path': '/tmp/plaintext'}):
        with pytest.raises(auth.GmailError):
            module.process(request)
    assert module.process({'operation': 'activate'}) == {'status': 'queued'}
    args = [call.args[0] for call in module.subprocess.run.call_args_list]
    assert args == [['systemctl', 'daemon-reload'], ['systemd-run', '--quiet', '--collect',
        '--unit=minitel-gmail-activate', '--on-active=3s', '/usr/bin/systemctl', 'restart', 'minitel-chatgpt', 'admin-ui']]
    for service in ('minitel-chatgpt', 'admin-ui'):
        assert 'LoadCredentialEncrypted=gmail:' in (tmp_path / f'etc/systemd/system/{service}.service.d/gmail.conf').read_text()
    (tmp_path / 'var/lib/minitel-gpt/deployment.json').write_text('{"phase":"activating"}')
    module.subprocess.run.reset_mock()
    with pytest.raises(auth.GmailError, match='deployment_busy'):
        module.process({'operation': 'activate'})
    module.subprocess.run.assert_not_called()


def test_configuration_encryption_uses_separate_ciphertext_and_no_plaintext(tmp_path, monkeypatch):
    payload = json.dumps(CONFIG).encode()
    run = Mock(side_effect=[SimpleNamespace(returncode=0, stdout=b'ENCRYPTED'),
                           SimpleNamespace(returncode=0, stdout=payload)])
    monkeypatch.setattr(auth.subprocess, 'run', run)
    destination = tmp_path / 'gmail-oauth'
    auth.encrypt_oauth_config(CONFIG, destination)
    assert destination.read_bytes() == b'ENCRYPTED' and destination.stat().st_mode & 0o777 == 0o600
    assert '--name=gmail-oauth' in run.call_args_list[0].args[0]
    assert 'PRIVATE-CLIENT' not in str([c.args for c in run.call_args_list])
