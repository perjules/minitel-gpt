"""Maintenance routes validate requests and only queue fixed operations."""
import re
from unittest.mock import Mock

import pytest

from services import admin_ui as admin


def test_explicit_password_required_and_no_default_can_authenticate(monkeypatch):
    monkeypatch.setattr(admin, 'ADMIN_PASSWORD', '')
    client = admin.app.test_client()
    page = client.get('/login')
    assert page.status_code == 503
    csrf = re.search(r'name=csrf value="([^"]+)"', page.text)[1]
    for password in ('', 'mistral'):
        assert client.post('/login', data={'csrf': csrf, 'password': password}).status_code == 503


def test_branch_preview_confirmation_and_csrf(console, monkeypatch):
    client, headers, _ = console
    sha = 'a' * 40
    git = Mock(side_effect=lambda *args: (0, sha if args[0] == 'rev-parse' else '', ''))
    queue = Mock(return_value=(True, 'queued'))
    monkeypatch.setattr(admin, 'git', git)
    monkeypatch.setattr(admin, 'queue_deployment', queue)
    assert client.post('/branch-preview', data={'branch': 'main'}).status_code == 403
    assert client.post('/switch-branch', headers=headers).status_code == 400
    assert client.post('/branch-preview', data={'branch': 'main'}, headers=headers).status_code == 302
    assert client.post('/switch-branch', headers=headers).status_code == 302
    queue.assert_called_once_with('switch-branch', {'branch': 'main', 'commit': sha})
    assert client.post('/switch-branch', headers=headers).status_code == 400
    for name in ('-bad', '../main', 'main;id'):
        # Git itself validates '..'; the API additionally rejects leading options/shell punctuation.
        if name != '../main':
            assert client.post('/branch-preview', data={'branch': name}, headers=headers).status_code == 400


@pytest.mark.parametrize('operation', ['reboot', 'poweroff'])
def test_power_controls_are_post_only_and_do_not_call_systemctl_directly(console, monkeypatch, operation):
    client, headers, _ = console
    queue = Mock(return_value=(True, 'queued'))
    monkeypatch.setattr(admin, 'queue_deployment', queue)
    assert client.get('/power/' + operation).status_code == 405
    assert client.post('/power/' + operation).status_code == 403
    assert client.post('/power/' + operation, headers=headers).status_code == 302
    queue.assert_called_once_with(operation)


def test_diagnostics_omit_private_configuration_and_conversation_contents(console):
    client, _, _ = console
    admin.ENV_FILE.write_text('MISTRAL_KEY=private-secret-marker\n')
    report = client.get('/diagnostics.txt')
    assert report.status_code == 200 and 'attachment' in report.headers['Content-Disposition']
    assert 'private-secret-marker' not in report.text and 'synthetic prompt' not in report.text
    assert admin.app.test_client().get('/diagnostics.txt').status_code == 302
