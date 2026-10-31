"""Admin welcome reset, retained personal data and authoritative newer assertions."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_onboarding import fact, response, run
from test_user_deletion import profiles

from services import admin_ui as admin
from services import memory_automation, memory_database, memory_session, user_registry


def test_admin_reset_preserves_memories_archives_grants_and_other_profiles(console, monkeypatch):
    monkeypatch.setenv("MINITEL_ONBOARDING", "1")
    client, headers, _ = console
    registry, (alice, bob) = profiles()
    tables = ("users", "user_aliases", "user_gmail_access", "user_pronote_children",
              "gmail_accounts", "memory_state", "memory_items", "memory_events")

    def contents():
        with registry.connection() as db:
            return {table: [tuple(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in tables}

    with registry.connection() as db:
        db.execute("UPDATE user_onboarding SET status='complete',progress=?,asked='age',turns=8 WHERE user_id=?",
                   (json.dumps({"age": "covered", "friends": "skipped"}), alice.user.id))
        before_revision = db.execute("SELECT revision FROM user_onboarding WHERE user_id=?", (alice.user.id,)).fetchone()[0]
        other_welcome = tuple(db.execute("SELECT * FROM user_onboarding WHERE user_id=?", (bob.user.id,)).fetchone())
    before = contents()
    archives = {str(p): p.read_bytes() for profile in (alice, bob)
                for p in (registry.root / profile.key).rglob("*") if p.is_file()}
    form = {"revision": alice.user.revision}
    path = f"/users/{alice.user.id}/reset-onboarding"
    page = client.get('/users').text
    assert f'action="{path}"' in page and 'Réinitialiser l’accueil' in page
    assert 'action="/users/1/reset-onboarding"' not in page
    assert client.get(path).status_code == 405
    assert client.post(path, data=form).status_code == 403
    assert admin.app.test_client().post(path, data=form).status_code in (302, 403)
    assert contents() == before
    assert client.post(path, data=form, headers=headers).status_code == 302
    assert contents() == before
    assert all(Path(path).read_bytes() == content for path, content in archives.items())
    state = alice.welcome.state()
    assert state['status'] == 'new' and state['progress'] == {} and state['history'] == []
    assert state['turns'] == 0 and state['asked'] == '' and state['revision'] > before_revision
    assert alice.welcome.should_greet()
    with registry.connection() as db:
        assert tuple(db.execute("SELECT * FROM user_onboarding WHERE user_id=?", (bob.user.id,)).fetchone()) == other_welcome
    run(alice)
    assert alice.model.client.complete.call_args.args[1]['phase'] == 'first'
    assert 'Accueil réinitialisé' in client.get('/users').text
    alice.close()
    bob.close()


@pytest.mark.parametrize("user_id,revision,status", [(1, 0, 400), (999999, 0, 400), (2, -1, 409)])
def test_reset_refuses_default_unknown_and_stale_profile(console, user_id, revision, status):
    client, headers, _ = console
    registry = user_registry.Registry()
    user = registry.resolve('Only')
    assert user.id == 2
    assert client.post(f'/users/{user_id}/reset-onboarding', data={'revision': revision}, headers=headers).status_code == status
    with registry.connection() as db:
        assert db.execute('SELECT revision FROM user_onboarding WHERE user_id=?', (user.id,)).fetchone()[0] == 0


def test_reset_supersedes_in_flight_answer_without_memory_write(monkeypatch):
    monkeypatch.setenv('MINITEL_ONBOARDING', '1')
    selected = memory_session.MemorySession('Late reset', model=Mock())
    run(selected)

    def delayed(*args, **kwargs):
        selected.registry.reset_onboarding(selected.user.id, selected.user.revision)
        return response(memories=[fact('10')])

    selected.model.client.complete.side_effect = delayed
    selected.welcome.respond('10', 'persona')
    state = selected.welcome.state()
    assert state['status'] == 'new' and not state['history'] and state['turns'] == 0
    assert not selected.store.read(selected.key)['items']
    selected.welcome.pause()
    assert selected.welcome.state()['status'] == 'new'
    selected.close()


def test_new_welcome_correction_replaces_fact_and_fences_older_extraction(monkeypatch):
    monkeypatch.setenv('MINITEL_ONBOARDING', '1')
    selected = memory_session.MemorySession('Growing up', model=Mock())
    old, _ = selected.store.apply(selected.key, {'operation': 'save', 'kind': 'fact',
        'title': 'age', 'text': 'User is 10 years old.'}, {})
    note, _ = selected.store.apply(selected.key, {'operation': 'save', 'kind': 'note',
        'title': 'Recipe', 'text': 'KEEP THIS NOTE'}, {})
    note = next(item for item in selected.store.read(selected.key)['items'] if item['id'] == note['id'])
    selected.registry.reset_onboarding(selected.user.id, selected.user.revision)
    run(selected)
    with memory_database.connection(selected.store) as db:
        db.execute("UPDATE memory_items SET embedding=?,embedding_model='mistral-embed' WHERE user_id=? AND id=?",
                   (b'OLD VECTOR', selected.user.id, old['id']))
    memory_automation.enqueue(selected.store, selected.key, 'old-chat', "J'ai 10 ans.")
    with memory_database.connection(selected.store) as db:
        db.execute('UPDATE memory_state SET ready=1 WHERE user_id=?', (selected.user.id,))

    def current_reply(instructions, data, *args, **kwargs):
        supplied = next(item for item in data['saved_facts'] if item['id'] == old['id'])
        assert supplied['text'] == 'User is 10 years old.'
        assert 'newest clear personal assertion' in instructions
        proposal = fact("J'ai 11 ans.", text='User is 11 years old.', event_id=data['current_event_id'])
        proposal.update(operation='replace', id=old['id'])
        return response(reply='Quelle matière aimes-tu ?', topic='daily_life', memories=[proposal])

    selected.model.client.complete.side_effect = current_reply
    previous = Mock()

    def older_extraction(instructions, data, *args, **kwargs):
        selected.welcome.respond("J'ai 11 ans.", 'persona')
        proposal = fact("J'ai 10 ans.", event_id=data['user_statements'][0]['event_id'])
        proposal.pop('topic')
        proposal.update(operation='replace', id=old['id'])
        return json.dumps({'memories': [proposal]})

    previous.client.complete.side_effect = older_extraction
    assert not memory_automation.process(selected.store, previous, Mock())
    saved = {item['id']: item for item in selected.store.read(selected.key)['items']}
    assert saved[old['id']]['text'] == 'User is 11 years old.'
    assert saved[old['id']]['source'][0]['quote'] == "J'ai 11 ans."
    assert saved[note['id']] == note
    with memory_database.connection(selected.store) as db:
        assert db.execute('SELECT embedding FROM memory_items WHERE user_id=? AND id=?', (selected.user.id, old['id'])).fetchone()[0] is None
        assert db.execute('SELECT count(*) FROM memory_events WHERE user_id=?', (selected.user.id,)).fetchone()[0] == 0
        state = db.execute('SELECT barrier FROM memory_state WHERE user_id=?', (selected.user.id,)).fetchone()[0]
    assert selected.welcome.state()['barrier'] == state
    assert selected.welcome.state()['history'][-1]['text'] == 'Quelle matière aimes-tu ?'
    selected.close()


@pytest.mark.parametrize('old_status', ['active', 'complete'])
def test_old_session_cannot_pause_reset_or_restarted_welcome(monkeypatch, old_status):
    monkeypatch.setenv('MINITEL_ONBOARDING', '1')
    old = memory_session.MemorySession('Reset cleanup', model=Mock())
    fresh = None
    try:
        run(old, action='finish' if old_status == 'complete' else 'continue')
        old.registry.reset_onboarding(old.user.id, old.user.revision)
        # Inspection may synchronize the memory barrier, but must not make an old
        # UI the owner of a later reset's welcome.
        assert old.welcome.state()['status'] == 'new'
        old.welcome.pause()
        old.welcome.pause()  # Both end-of-loop and next-session cleanup may run.
        assert old.welcome.state()['status'] == 'new'
        fresh = memory_session.MemorySession('Reset cleanup', model=Mock())
        assert fresh.welcome.should_greet()
        run(fresh)
        before = fresh.welcome.state()
        assert fresh.model.client.complete.call_args.args[1]['phase'] == 'first'
        old.welcome.pause()
        assert fresh.welcome.state() == before
        # The new UI can still pause its own successfully committed welcome.
        fresh.welcome.pause()
        assert fresh.welcome.state()['status'] == 'paused'
    finally:
        old.close()
        if fresh:
            fresh.close()


def test_unobserved_session_cleanup_preserves_admin_reset(monkeypatch):
    monkeypatch.setenv('MINITEL_ONBOARDING', '1')
    old = memory_session.MemorySession('Not greeted yet', model=Mock())
    try:
        old.registry.reset_onboarding(old.user.id, old.user.revision)
        old.welcome.pause()
        assert old.welcome.state()['status'] == 'new'
    finally:
        old.close()


@pytest.mark.parametrize('reset_button', [False, True])
def test_browser_session_exit_does_not_cancel_admin_welcome_reset(console, monkeypatch, reset_button):
    monkeypatch.setenv('MINITEL_ONBOARDING', '1')
    client, headers, _ = console
    model = Mock()
    model.client.complete.return_value = response()
    monkeypatch.setattr(memory_session, 'MemoryModel', lambda **kwargs: model)
    ordinary = Mock(return_value='Conversation libre.')
    monkeypatch.setattr(admin, 'llm_answer', ordinary)
    base = {'preset_key': 'main', 'memory_user': 'Browser reset'}
    assert client.post('/test-preset', headers=headers, json={**base, 'welcome_start': True}).json['ok']
    with client.session_transaction() as cookie:
        old = admin.MEMORY_SESSIONS[cookie['memory_session']]['memory']
    old.store.apply(old.key, {'operation': 'save', 'kind': 'fact', 'text': 'Existing personal fact'}, {})
    saved = old.store.read(old.key)
    assert client.post(f'/users/{old.user.id}/reset-onboarding', headers=headers,
                       data={'revision': old.user.revision}).status_code == 302
    assert client.post('/test-preset', headers=headers, json={**base,
        'reset': reset_button, 'message': '' if reset_button else 'SOMMAIRE'}).json['ok']
    with old.registry.connection() as db:
        assert db.execute('SELECT status FROM user_onboarding WHERE user_id=?', (old.user.id,)).fetchone()[0] == 'new'
    assert client.post('/test-preset', headers=headers, json={**base, 'message': 'Bonjour'}).json['ok']
    assert model.client.complete.call_count == 2
    assert model.client.complete.call_args.args[1]['phase'] == 'first'
    ordinary.assert_not_called()
    assert old.store.read(old.key) == saved
    client.post('/logout', headers=headers)
