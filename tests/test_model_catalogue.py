"""Provider lists are fetched only on demand with synthetic HTTP responses."""
import json
from unittest.mock import Mock

import pytest
import requests

from services import admin_ui as admin
from services import model_catalogue as catalogue
from test_admin_navigation import console  # noqa: F401 - shared isolated configuration fixture


def response(data):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.iter_content.return_value = [json.dumps(data).encode()]
    return result


def test_mistral_filters_non_chat_and_archived_models(monkeypatch):
    get = Mock(return_value=response({'data': [
        {'id': 'chat-new', 'capabilities': {'completion_chat': True}},
        {'id': 'embedding', 'capabilities': {'completion_chat': False}},
        {'id': 'old', 'archived': True, 'capabilities': {'completion_chat': True}}]}))
    monkeypatch.setattr(catalogue.requests, 'get', get)
    assert catalogue.models('mistral', 'synthetic-key') == [{'id': 'chat-new', 'label': 'chat-new'}]
    assert get.call_args.kwargs['timeout'] == (3, 10)


def test_claude_pagination_and_fixed_provider_urls(monkeypatch):
    get = Mock(side_effect=[response({'data': [{'id': 'claude-a'}], 'has_more': True, 'last_id': 'claude-a'}),
                            response({'data': [{'id': 'claude-b'}], 'has_more': False})])
    monkeypatch.setattr(catalogue.requests, 'get', get)
    assert len(catalogue.models('claude', 'synthetic-key')) == 2
    assert get.call_args.args == ('https://api.anthropic.com/v1/models',)
    assert get.call_args.kwargs['params']['after_id'] == 'claude-a'
    with pytest.raises(catalogue.CatalogueError):
        catalogue.models('https://untrusted.invalid', 'key')


def test_catalogue_failure_never_exposes_credentials(monkeypatch):
    monkeypatch.setattr(catalogue.requests, 'get', Mock(side_effect=requests.RequestException('secret-key')))
    with pytest.raises(catalogue.CatalogueError) as error:
        catalogue.models('mistral', 'secret-key')
    assert 'secret-key' not in str(error.value)


def test_model_route_is_protected_and_does_not_persist_draft_key(console, monkeypatch):
    client, headers, _ = console
    listing = Mock(return_value=[{'id': 'new-chat', 'label': 'New chat'}])
    monkeypatch.setattr(catalogue, 'models', listing)
    assert client.post('/models', json={'provider': 'mistral'}).status_code == 403
    result = client.post('/models', json={'provider': 'mistral', 'key': 'draft-key'}, headers=headers)
    assert result.json['models'][0]['id'] == 'new-chat'
    assert 'draft-key' not in result.text and not admin.ENV_FILE.exists()
    admin.restart_terminal.assert_not_called()
    assert client.post('/save-llm', data={'mistral_model': 'new-chat'}, headers=headers).status_code == 302
    assert admin.read_env()['MISTRAL_MODEL'] == 'new-chat'
