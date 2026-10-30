"""Real forms and persistence, with synthetic keys and no provider calls."""

from services import admin_ui as admin
from services import config_store, gmail_mail


def form(**updates):
    return {"llm_provider": "mistral", "mistral_model": "mistral-small-latest",
            "gmail_overrides": "1", "gmail_model": "", "gmail_draft_model": "mistral-large-4-0",
            "gmail_draft_reasoning_effort": "medium", **updates}


def test_profile_form_persists_applies_and_can_clear_overrides(console):
    client, headers, _ = console
    config_store.update_env(admin.ENV_FILE, {"MISTRAL_KEY": "PRIVATE-KEY"})
    assert client.post('/save-llm', data=form()).status_code == 403
    assert client.post('/save-llm', data=form(), headers=headers).status_code == 302
    saved = admin.read_env()
    assert saved['MISTRAL_GMAIL_DRAFT_MODEL'] == 'mistral-large-4-0'
    assert saved['MISTRAL_GMAIL_DRAFT_REASONING_EFFORT'] == 'medium'
    assert saved['MISTRAL_KEY'] == 'PRIVATE-KEY'
    admin.restart_terminal.assert_called_once()
    page = client.get('/?section=ai').text
    assert 'Gmail — modèles par tâche' in page and 'PRIVATE-KEY' not in page
    assert 'value="medium" selected' in page and 'value="mistral-large-4-0" selected' in page
    # The administration adapter observes the same saved overrides as the terminal.
    model = gmail_mail.MailModel(env=lambda: {**admin.os.environ, **admin.read_env()})
    assert model.profile(True)['model'] == 'mistral-large-4-0'
    response = client.post('/save-llm', headers=headers,
                           data=form(gmail_draft_model='', gmail_draft_reasoning_effort=''))
    assert response.status_code == 302
    assert model.profile(True)['model'] == model.profile()['model'] == 'mistral-small-latest'


def test_invalid_combination_rejects_entire_save_and_inherited_model_change(console):
    client, headers, _ = console
    config_store.update_env(admin.ENV_FILE, {'MISTRAL_KEY': 'old',
        'MISTRAL_GMAIL_DRAFT_REASONING_EFFORT': 'high'})
    before = admin.ENV_FILE.read_bytes()
    for values in [form(mistral_key='new', gmail_draft_reasoning_effort='none'),
                   form(gmail_draft_model='bad\nMODEL=other'),
                   {'mistral_model': 'unknown-model'}]:
        assert client.post('/save-llm', data=values, headers=headers).status_code == 400
        assert admin.ENV_FILE.read_bytes() == before
    admin.restart_terminal.assert_not_called()


def test_invalid_manual_configuration_keeps_console_usable(console):
    client, _, _ = console
    config_store.update_env(admin.ENV_FILE, {'MISTRAL_GMAIL_DRAFT_MODEL': 'mistral-large-4-0',
        'MISTRAL_GMAIL_DRAFT_REASONING_EFFORT': 'none'})
    page = client.get('/?section=ai')
    assert page.status_code == 200
    assert 'Réglages Gmail incompatibles' in page.text and 'none (incompatible)' in page.text


def test_manual_model_and_effort_choices_are_shown_with_inheritance(console):
    client, headers, _ = console
    response = client.post('/save-llm', headers=headers, data=form(
        gmail_model_custom='mail-model', gmail_draft_model_custom='mistral-medium-3-5',
        gmail_draft_reasoning_effort='high'))
    assert response.status_code == 302
    context = admin.gmail_model_settings()
    assert context['general']['model'] == 'mail-model'
    assert context['draft']['model'] == 'mistral-medium-3-5'
    assert context['efforts'] == ('none','high')
    assert 'key' not in context['draft'] and 'key' not in context['general']
