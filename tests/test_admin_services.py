"""Service configuration uses local synthetic settings, never real accounts."""
from unittest.mock import Mock

from werkzeug.datastructures import MultiDict

from services import admin_ui as admin
from services import pronote


def test_uniform_cards_and_inert_placeholders(console):
    client, headers, _ = console
    page = client.get('/?section=services').text
    for key in ('gmail', 'pronote', 'alexa', 'printer', 'smart-home'):
        assert f'/services/{key}' in page or key == 'pronote'
    for key in ('alexa', 'printer', 'smart-home'):
        assert 'En construction' in client.get('/services/' + key).text
        assert client.post('/services/' + key + '/toggle', headers=headers).status_code == 404
    admin.restart_terminal.assert_not_called()


def test_gmail_toggle_persists_without_destroying_credentials(console, monkeypatch):
    client, headers, _ = console
    monkeypatch.setenv('GMAIL_ENABLED', '0')
    assert client.post('/services/gmail/toggle', data={'enabled': '1'}).status_code == 403
    response = client.post('/services/gmail/toggle', data={'enabled': '1'}, headers=headers)
    assert response.status_code == 302
    assert admin.read_env()['GMAIL_ENABLED'] == '1' and admin.gmail_auth.enabled()
    assert 'Associations utilisateurs' in client.get('/services/gmail').text
    admin.restart_terminal.assert_called_once()


def test_gmail_connection_check_is_explicit_and_read_only(console, monkeypatch):
    client, headers, _ = console
    monkeypatch.setenv('GMAIL_ENABLED', '1')
    connection = Mock()
    monkeypatch.setattr(admin.gmail_client, 'GmailClient', Mock(return_value=connection))
    client.get('/services/gmail')
    connection.list.assert_not_called()
    result = client.post('/services/gmail', data={'action': 'check'}, headers=headers)
    assert 'Connexion Gmail vérifiée' in result.text
    connection.list.assert_called_once()
    connection.send.assert_not_called()


def test_pronote_table_preserves_schema_and_rejects_duplicate_aliases(console, tmp_path, monkeypatch):
    client, headers, _ = console
    local = pronote.Pronote(root=tmp_path)
    monkeypatch.setattr(pronote, 'backend', lambda: local)
    form = MultiDict([('csrf', 'test-token'), ('action', 'configure'), ('enabled', 'on'), ('alias', 'Alex'),
                      ('child_account', 'family'), ('child_name', 'Synthetic child')])
    assert client.post('/pronote', data=form, headers=headers).status_code == 200
    assert local.config()['children'] == {'Alex': {'account': 'family', 'name': 'Synthetic child'}}
    before = local.config_path.read_bytes()
    for key, value in [('alias', 'Alex'), ('child_account', 'family'), ('child_name', 'Other child')]:
        form.add(key, value)
    client.post('/pronote', data=form, headers=headers)
    assert local.config_path.read_bytes() == before
    client.post('/services/pronote/toggle', headers=headers)
    assert not local.config()['enabled'] and local.config()['children']['Alex']['account'] == 'family'


def test_shared_test_entry_and_direct_gmail_user_selection(console, tmp_path):
    client, headers, _ = console
    admin.config_store.update_env(admin.ENV_FILE, {'MINITEL_MEMORY_DIR': str(tmp_path / 'memory')})
    assert 'Conversation' in client.get('/test').text
    assert 'Gmail' in client.get('/test?mode=gmail').text
    result = client.post('/test?mode=gmail', data={'csrf': 'test-token', 'action': 'user',
                                                'username': 'Synthetic user'}, headers=headers)
    assert result.status_code == 200 and 'Synthetic user' in result.text
    assert 'value="Synthetic user"' in client.get('/test').text
    with client.session_transaction() as state:
        assert admin.MAIL_SESSIONS[state['gmail_session']]['mail'].username == 'Synthetic user'
        admin.discard_mail(state['gmail_session'])
        admin.discard_memory(state['memory_session'])


def test_advanced_draft_testing_does_not_save_or_activate(console):
    client, headers, prompts = console
    original = prompts.read_bytes()
    result = client.post('/test?mode=conversation', data={'preset_key': 'other',
        'system_prompt': '</script><script>private draft</script>'}, headers=headers)
    assert result.status_code == 200 and 'Brouillon non enregistré' in result.text
    assert '<script>private draft</script>' not in result.text
    assert prompts.read_bytes() == original
    admin.restart_terminal.assert_not_called()
