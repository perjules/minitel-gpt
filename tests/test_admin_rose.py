"""Hidden-panel authorization and lossless local overrides, using synthetic text."""
import copy
import json

import pytest

from services import admin_ui as admin
from services import rose, rose_config


@pytest.fixture
def private(console, tmp_path, monkeypatch):
    config = tmp_path / 'private-config'
    config.mkdir()
    cast = {'safewords': ['stop'], 'personas': []}
    for e in range(1, 6):
        for s in range(1, 6):
            cast['personas'].append({'id': f'p{e}{s}', 'name': f'Name{e}{s}', 'aliases': [],
                'gender': 'femme', 'bio': 'Synthetic description',
                'voice': f'Registre E{e} : E{e}. Registre S{s} : S{s}. Style.',
                'fees': [], 'tempo': 0, 'kinks': [], 'asexual': False})
    for name in rose_config.CATALOGUES.values():
        (config / name).write_text(json.dumps(cast))
    (config / 'rose_prompt.txt').write_text('private-common-prompt')
    (config / 'rose_kinks.json').write_text('{}')
    monkeypatch.setattr(rose, 'CONFIG', config)
    monkeypatch.setattr(admin, 'PROJ_DIR', tmp_path)
    monkeypatch.setenv('ROSE_PASSWORD', 'synthetic-rose-password')
    return *console, config


def unlock(client, headers):
    return client.post('/admin/rose', data={'action': 'unlock', 'password': 'synthetic-rose-password'}, headers=headers)


def test_hidden_panel_requires_both_authentication_and_extra_password(private):
    client, headers, _, _ = private
    assert admin.app.test_client().get('/admin/rose').status_code == 302
    assert 'private-common-prompt' not in client.get('/admin/rose').text
    assert client.post('/admin/rose', data={'action': 'unlock', 'password': 'wrong'}, headers=headers).status_code == 403
    assert client.post('/admin/rose', data={'action': 'unlock', 'password': 'synthetic-rose-password'}).status_code == 403
    assert unlock(client, headers).status_code == 302
    assert 'private-common-prompt' in client.get('/admin/rose').text
    client.post('/admin/rose', data={'action': 'lock'}, headers=headers)
    assert 'private-common-prompt' not in client.get('/admin/rose?log=anything').text


def test_ordinary_pages_and_storage_payload_do_not_advertise_private_feature(private):
    client, _, _, _ = private
    for path in ('/?section=services', '/storage-status'):
        text = client.get(path).text.casefold()
        assert 'rose' not in text and 'private-common-prompt' not in text


def test_local_edits_preserve_tracked_defaults_and_wire_shared_levels(private):
    client, headers, _, config = private
    originals = {p.name: p.read_bytes() for p in config.iterdir() if p.suffix in {'.txt', '.json'}}
    bank = rose_config.levels(config)
    before = rose_config.catalogue(config)
    for profile in before['personas']:
        assert rose_config.voice(config, rose_config.editable(profile, bank)) == profile['voice']
    unlock(client, headers)
    replacement = dict(bank, E1='Changed E1.')
    assert client.post('/admin/rose', data={'action': 'levels', **replacement}, headers=headers).status_code == 302
    selected = rose_config.catalogue(config)['personas'][0]
    assert rose_config.voice(config, selected).startswith('Changed E1.')
    assert client.post('/admin/rose', data={'action': 'prompt', 'prompt': 'Changed common prompt'}, headers=headers).status_code == 302
    assert rose_config.prompt(config) == 'Changed common prompt'
    for name, content in originals.items():
        assert (config / name).read_bytes() == content
    assert (config / rose_config.LOCAL).stat().st_mode & 0o777 == 0o600


def test_profile_creation_and_invalid_edits(private):
    client, headers, _, config = private
    unlock(client, headers)
    form = {'action': 'person', 'catalog': 'main', 'id': 'new-person', 'name': 'Synthetic new',
            'gender': 'homme', 'voice': 'Calm style.', 'bio': 'Synthetic', 'aliases': 'Alias',
            'e_level': '2', 's_level': '3', 'metadata': '{"fees":[],"tempo":0,"kinks":[],"asexual":false}'}
    assert client.post('/admin/rose', data=form, headers=headers).status_code == 302
    assert any(p['id'] == 'new-person' for p in rose.catalogue()['personas'])
    before = (config / rose_config.LOCAL).read_bytes()
    for changed in ({'metadata': 'broken'}, {'e_level': '6'}, {'metadata': '[]'}):
        assert client.post('/admin/rose', data=form | changed, headers=headers).status_code == 400
        assert (config / rose_config.LOCAL).read_bytes() == before


def test_private_log_reader_is_bounded_and_rejects_links(private, tmp_path):
    client, headers, _, _ = private
    folder = tmp_path / 'logs/rose'
    folder.mkdir(parents=True)
    (folder / 'run.jsonl').write_text('A' * 70000 + 'END')
    (folder / 'link').symlink_to(tmp_path / 'outside')
    unlock(client, headers)
    assert 'END' in client.get('/admin/rose?log=run.jsonl').text
    assert client.get('/admin/rose?log=../outside').status_code == 400
    assert client.get('/admin/rose?log=link').status_code == 404


def test_legacy_level_variants_remain_exact(private):
    _, _, _, config = private
    profile = copy.deepcopy(rose.catalogue()['personas'][0])
    profile['voice'] = 'Registre E1 : E1. Registre S1 : Authored variant. Style.'
    normalized = rose_config.editable(profile, rose_config.levels(config))
    assert normalized['s_prompt_override'] == 'Registre S1 : Authored variant.'
    assert rose_config.voice(config, normalized) == profile['voice']
