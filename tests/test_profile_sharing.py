"""Explicit sharing, attribution and revocation. All profiles/API replies synthetic."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from test_admin_memory import unlock
from test_gmail_composition import compose_session, metadata
from test_memory_administration import change, form, path, selected
from test_onboarding import fact, response

from services import admin_ui as admin
from services import memory_administration as edit
from services import memory_automation as auto
from services import memory_database as sql
from services import memory_session as sessions
from services import memory_vectors as vectors
from services import persistent_memory as memory
from services import user_merge


@pytest.fixture
def people():
    subjects = [selected(name) for name in ('Alice', 'Bob', 'Charlie', '')]
    yield subjects
    for subject in subjects:
        subject.close()


def publish(subject, item=None, enabled=True):
    data = subject.store.read(subject.key)
    edit.share_facts(subject.store, subject.user.id, subject.user.revision,
                     data['revision'], enabled, item['id'] if item else None)


def downgrade(store):
    with sqlite3.connect(store.root / 'users.sqlite3') as db:
        db.execute('ALTER TABLE memory_items DROP COLUMN shared_hash')
        db.execute('UPDATE memory_schema SET version=1')


def test_migration_backups_preserve_records_vectors_identity_and_legacy_preview(people):
    alice, bob, _, default = people
    item, _ = change(alice, text='User is 10 years old.')
    change(default, text='The household owns a Thermomix', publish=True)
    with sql.connection(alice.store) as db:
        db.execute("UPDATE memory_items SET embedding=?,embedding_model='synthetic',embedding_hash='old' WHERE user_id=?",
                   (b'VECTOR', alice.user.id))
    downgrade(alice.store)
    db_path = alice.store.root / 'users.sqlite3'
    original = db_path.read_bytes()
    assert user_merge.preview(alice.store, alice.user.id, bob.user.id)['items_after'] == 1
    assert db_path.read_bytes() == original
    saved = alice.store.read(alice.key)
    assert saved['items'][0]['id'] == item['id'] and not saved['items'][0]['shared']
    backup = alice.store.root / 'users.before-profile-sharing.sqlite3'
    assert backup.is_file() and backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert db.execute('SELECT version FROM memory_schema').fetchone()[0] == 1
        assert db.execute('SELECT embedding FROM memory_items WHERE user_id=?', (alice.user.id,)).fetchone()[0] == b'VECTOR'
    with sql.connection(alice.store) as db:
        assert db.execute('SELECT version FROM memory_schema').fetchone()[0] == sql.SQL_MEMORY_SCHEMA
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert db.execute('SELECT embedding FROM memory_items WHERE user_id=?', (alice.user.id,)).fetchone()[0] == b'VECTOR'
    before = backup.read_bytes()
    assert alice.store.read(alice.key) == saved and backup.read_bytes() == before
    assert 'Thermomix' in bob.context('Thermomix')


def test_migration_failure_keeps_v1_and_all_data(people, monkeypatch):
    alice, *_ = people
    change(alice, text='Original fact')
    downgrade(alice.store)
    monkeypatch.setattr(sql, 'backup_before_sharing', Mock(side_effect=OSError('full disk')))
    with pytest.raises(memory.MemoryProblem):
        alice.store.read(alice.key)
    with sqlite3.connect(alice.store.root / 'users.sqlite3') as db:
        assert db.execute('SELECT version FROM memory_schema').fetchone()[0] == 1
        assert 'shared_hash' not in {r[1] for r in db.execute('PRAGMA table_info(memory_items)')}
        assert db.execute('SELECT text FROM memory_items').fetchone()[0] == 'Original fact'


def test_concurrent_first_access_migrates_once_without_sharing(people):
    alice, bob, *_ = people
    change(alice, text='Alice age fact')
    change(bob, text='Bob age fact')
    downgrade(alice.store)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda p: p.store.read(p.key), (alice, bob)))
    assert all(not r['items'][0]['shared'] for r in results)
    with sqlite3.connect(alice.store.root / 'users.before-profile-sharing.sqlite3') as db:
        assert db.execute('SELECT version FROM memory_schema').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM memory_items').fetchone()[0] == 2


def test_publication_controls_fact_only_bulk_is_current_items_and_quotes_stay_private(people, console):
    alice, bob, _, _ = people
    client, headers, _ = console
    age, _ = change(alice, text='User is 10 years old.')
    for kind in ('preference', 'note', 'list', 'task'):
        change(alice, kind=kind, title='Private '+kind, text='PRIVATE '+kind)
    with sql.connection(alice.store) as db:
        db.execute('UPDATE memory_items SET source=? WHERE user_id=? AND id=?',
                   (json.dumps([{'quote': 'PRIVATE SOURCE QUOTE', 'question': 'PRIVATE QUESTION'}]), alice.user.id, age['id']))
    assert '10 years' not in bob.context('How old is Alice?')
    payload = form(alice, 'sharing', share='yes')
    assert client.post(path(alice), headers=headers, data=payload).status_code == 403
    unlock(client, headers)
    assert client.post(path(alice), data=payload).status_code == 403
    assert client.post(path(alice), headers=headers, data=payload).status_code == 302
    records = alice.store.read(alice.key)['items']
    assert [r['kind'] for r in records if r['shared']] == ['fact']
    page = client.get(f'/maintenance/memory/{alice.user.id}').text
    assert 'Partager tous les faits actuels' in page and '1 / 1 faits partagés' in page
    context = bob.context('How old is Alice?')
    assert '10 years' in context and 'SHARED PROFILE FACTS' in context
    assert 'PRIVATE' not in context
    payload = form(alice, 'sharing', share='yes')
    change(alice, text='User lives in Paris.')
    assert client.post(path(alice), headers=headers, data=payload).status_code == 409
    assert sum(r['shared'] for r in alice.store.read(alice.key)['items']) == 1
    assert client.post(path(alice), headers=headers, data=form(alice, 'sharing')).status_code == 302
    assert not any(r['shared'] for r in alice.store.read(alice.key)['items'])
    assert '10 years' not in bob.context('How old is Alice?')
    alice.model.client.complete.assert_not_called()


@pytest.mark.parametrize('kind', ['preference', 'note', 'list', 'task'])
def test_nonfact_publication_is_rejected_without_writes(people, kind):
    alice, *_ = people
    item, _ = change(alice, kind=kind, title=kind, text='PRIVATE '+kind)
    before = alice.store.read(alice.key)
    with pytest.raises(memory.MemoryProblem):
        publish(alice, item)
    with pytest.raises(memory.MemoryProblem):
        change(alice, kind=kind, title=kind+'new', text='PRIVATE new', share=True)
    assert alice.store.read(alice.key) == before


def test_manual_content_changes_retract_until_explicit_new_review(people):
    alice, bob, *_ = people
    item, _ = change(alice, text='User is 10 years old.', share=True)
    assert '10 years' in bob.context('Alice age')
    alice.store.apply(alice.key, {'operation': 'replace', 'ids': [item['id']],
                      'text': 'User is 11 years old.'}, alice.store.read(alice.key))
    assert not alice.store.read(alice.key)['items'][0]['shared']
    assert '11 years' not in bob.context('Alice age')
    change(alice, 'edit', identifier=item['id'], text='User is 12 years old.', share=True)
    assert '12 years' in bob.context('Alice age')
    change(alice, 'edit', identifier=item['id'], text='User is 13 years old.')
    assert not alice.store.read(alice.key)['items'][0]['shared']


def test_auto_replacement_retracts_only_changed_fact(people):
    alice, bob, *_ = people
    item, _ = change(alice, text='User is 10 years old.', share=True)
    other, _ = change(alice, text='User enjoys robots.', share=True)
    statement = 'I am 11 years old now.'
    alice.record('user', statement)
    auto.ready(alice.store, alice.key)
    with sql.connection(alice.store) as db:
        event_id = db.execute('SELECT id FROM memory_events WHERE user_id=?', (alice.user.id,)).fetchone()[0]
    alice.model.client.complete.return_value = json.dumps({'memories': [{
        'operation': 'replace', 'kind': 'fact', 'id': item['id'], 'title': 'age',
        'text': 'User is 11 years old.', 'evidence': [{'event_id': event_id, 'quote': statement}]}]})
    assert auto.process(alice.store, alice.model, Mock())
    records = {r['id']: r for r in alice.store.read(alice.key)['items']}
    assert not records[item['id']]['shared'] and records[other['id']]['shared']
    assert '11 years' not in bob.context('Alice age')


def test_onboarding_replacement_retracts_and_does_not_read_other_shared_facts(people, monkeypatch):
    monkeypatch.setenv('MINITEL_ONBOARDING', '1')
    alice, bob, *_ = people
    item, _ = change(alice, text='User is 10 years old.', share=True)
    change(bob, text='User has a PUBLIC BOB FACT', share=True)
    proposal = fact('I am 11 years old.', text='User is 11 years old.')
    proposal.update(operation='replace', id=item['id'])
    alice.model.client.complete.return_value = response(memories=[proposal])
    alice.welcome.respond('I am 11 years old.', 'persona')
    assert not alice.store.read(alice.key)['items'][0]['shared']
    assert 'PUBLIC BOB' not in str(alice.model.client.complete.call_args)


def test_sharing_preserves_pending_content_and_fences_old_jobs(people):
    alice, *_ = people
    change(alice, text='User enjoys robots.')
    for _ in range(6):
        alice.record('user', 'I enjoy electronics and robots very much.')
    with sql.connection(alice.store) as db:
        before = [tuple(r) for r in db.execute('SELECT * FROM memory_events WHERE user_id=?', (alice.user.id,))]
        barrier = db.execute('SELECT barrier FROM memory_state WHERE user_id=?', (alice.user.id,)).fetchone()[0]
    alice.model.client.complete.side_effect = lambda *a, **k: (publish(alice) or '{"memories": []}')
    assert not auto.process(alice.store, alice.model, Mock())
    with sql.connection(alice.store) as db:
        assert before == [tuple(r) for r in db.execute('SELECT * FROM memory_events WHERE user_id=?', (alice.user.id,))]
        assert db.execute('SELECT barrier FROM memory_state WHERE user_id=?', (alice.user.id,)).fetchone()[0] == barrier
    assert alice.store.read(alice.key)['items'][0]['shared']


def shared_records(context):
    assert 'SHARED PROFILE FACTS' in context
    return json.loads(context.rsplit('\n', 1)[-1])


def test_owner_attribution_colliding_ids_and_beliefs_keep_distinct_subjects(people):
    alice, bob, charlie, default = people
    age, _ = change(alice, text='User is 10 years old.', share=True)
    other, _ = change(bob, text='User is 42 years old.', share=True)
    with sql.connection(alice.store) as db:
        db.execute('UPDATE memory_items SET id=? WHERE user_id=? AND id=?', (age['id'], bob.user.id, other['id']))
    context = charlie.context('User age')
    records = shared_records(context)
    assert {(r['owner_id'], r['text']) for r in records} == {
        (alice.user.id, 'User is 10 years old.'), (bob.user.id, 'User is 42 years old.')}
    assert 'Selected user: "Charlie"' in context
    assert 'THAT OWNER, not the current speaker' in context
    assert {r['owner_name'] for r in records} == {'Alice', 'Bob'}
    assert '10 years' in default.context('How old is Alice?')
    change(alice, text='User believes Bob dislikes school.', share=True)
    context = charlie.context('Alice school beliefs')
    assert any(r['owner_name'] == 'Alice' and 'User believes Bob' in r['text'] for r in shared_records(context))
    assert "not verified facts about another person" in context
    assert alice.store.read(alice.key)['items'][0]['text'] == 'User is 10 years old.'


def test_explicit_name_ranking_includes_requested_owner_among_many_public_facts(people):
    alice, _, charlie, _ = people
    others = [selected('Person '+str(i)) for i in range(8)]
    try:
        for p in [*others, alice]:
            change(p, text='User is 10 years old.', share=True)
        with sql.connection(alice.store) as db:
            db.execute('UPDATE user_aliases SET alias=? WHERE user_id=?', ('ali', alice.user.id))
        context = charlie.context('What do we know about Ali?')
        assert shared_records(context)[0]['owner_id'] == alice.user.id
    finally:
        for p in others:
            p.close()


def test_rename_disable_and_delete_are_rechecked(people):
    alice, _, charlie, _ = people
    change(alice, text='User is 10 years old.', share=True)
    alice.registry.configure(alice.user.id, 'Alicia', True, ['Alice', 'Al'], '', False, [])
    assert shared_records(charlie.context('How old is Al?'))[0]['owner_name'] == 'Alicia'
    user = alice.registry.resolve('Al')
    alice.registry.configure(user.id, 'Alicia', False, ['Alice', 'Al'], '', False, [])
    assert '10 years' not in charlie.context('How old is Al?')
    current = next(u for u in alice.registry.users() if u['id'] == alice.user.id)
    alice.registry.delete(current['id'], current['revision'], current['display_name'])
    assert '10 years' not in charlie.context('How old is Alice?')


@pytest.mark.parametrize('operation', ['unshare', 'disable', 'rename', 'delete'])
def test_unshare_during_embedding_drops_the_fact_before_model_context(people, operation):
    alice, _, charlie, _ = people
    item, _ = change(alice, text='User is 10 years old.', share=True)
    indexer = Mock()
    indexer.embed.return_value = [[1.] + [0.] * 1023]
    assert vectors.index_pending(alice.store, indexer) == 1
    charlie.embeddings = Mock()
    def revoke(_):
        if operation == 'unshare':
            publish(alice, item, enabled=False)
        elif operation == 'delete':
            alice.registry.delete(alice.user.id, alice.user.revision, alice.username)
        else:
            alice.registry.configure(alice.user.id, 'Alicia' if operation=='rename' else 'Alice',
                                     operation!='disable', ['Alice', 'Alicia'], '', False, [])
        return [[1.] + [0.] * 1023]
    charlie.embeddings.embed.side_effect = revoke
    assert '10 years' not in charlie.context('How old is Alice?')
    charlie.embeddings.embed.assert_called_once()


def test_one_embedding_shared_across_scopes_and_total_context_stays_bounded(people):
    alice, bob, _, default = people
    text = 'User is 10 years old. ' + 'A sufficiently long yet public profile fact. ' * 16
    change(alice, text=text, share=True)
    change(bob, kind='preference', text='User prefers detailed answers.')
    change(default, text='The household owns a Thermomix.', publish=True)
    indexer = Mock()
    indexer.embed.return_value = [[1.] + [0.] * 1023] * 3
    assert vectors.index_pending(alice.store, indexer) == 3
    bob.embeddings = Mock()
    bob.embeddings.embed.return_value = [[1.] + [0.] * 1023]
    context = bob.context('Tell me about Alice and our Thermomix')
    assert '10 years' in context and 'Thermomix' in context and 'detailed answers' in context
    assert len(context) <= memory.CONTEXT_LIMIT
    bob.embeddings.embed.assert_called_once()
    assert 'owner_name' in context and 'Alice' in context
    assert "Never apply another person's preferences" in context


def test_public_facts_never_become_foreign_edit_targets_or_extraction_candidates(people):
    alice, bob, *_ = people
    item, _ = change(alice, text='User is 10 years old.', share=True)
    bob.model.plan.return_value = {'operation': 'remove', 'ids': [item['id']]}
    assert 'invalide' in bob.handle("Oublie l’âge d’Alice")["display"]
    assert not bob.model.plan.call_args.args[1]
    assert 'owner_name' in bob.model.plan.call_args.kwargs['read_only_context']
    assert alice.store.read(alice.key)['items'][0]['shared']
    bob.record('assistant', 'Alice is 10 years old.')
    with sql.connection(bob.store) as db:
        assert db.execute('SELECT count(*) FROM memory_events WHERE user_id=?', (bob.user.id,)).fetchone()[0] == 0
    with pytest.raises(memory.MemoryProblem):
        publish(bob, item)


def test_shared_recall_questions_can_handoff_without_copying_to_current_user(people):
    alice, bob, *_ = people
    change(alice, text='User is 10 years old.', share=True)
    bob.model.plan.return_value = {'operation': 'public'}
    result = bob.handle('Do you remember Alice?')
    assert result['passthrough'] and not bob.store.read(bob.key)['items']
    context = bob.model.plan.call_args.kwargs['read_only_context']
    assert shared_records(context)[0]['owner_name'] == 'Alice'
    model = sessions.MemoryModel()
    model.client.complete = Mock(return_value='{"operation":"public"}')
    model.plan('Do you remember Alice?', [], False, [], 'en', 999999999, read_only_context=context)
    instructions, payload, _ = model.client.complete.call_args.args
    assert 'do not belong to the current user' in instructions
    assert 'Alice' in payload['read_only_context']


def test_mail_resolution_uses_attributed_public_relationship_and_writer_does_not(people):
    alice, _, charlie, _ = people
    change(alice, text="User's brother is Bob.", share=True)
    session, client, model = compose_session()
    session.memory = charlie
    client.correspondent_metadata.return_value['records'] = [metadata(
        From='Bob <bob@example.invalid>', To='Me <me@example.org>', Subject='Appointment')]
    def choose(description, candidates, deadline, context=None):
        assert description == "le frère d'Alice"
        assert 'Selected user: "Charlie"' in context
        record = shared_records(context)[0]
        assert record['owner_id'] == alice.user.id and record['text'] == "User's brother is Bob."
        return {'status': 'unique', 'ids': [candidates[0]['id']]}
    model.resolve.side_effect = choose
    assert 'Brouillon' in session.handle("ECRIRE le frère d'Alice : rendez-vous mardi")['display']
    assert session.draft['recipient'] == 'bob@example.invalid' and not session.draft['reviewed']
    assert "User's brother" not in str(model.composition.call_args)
    client.send_raw.assert_not_called()
    assert 'PROFILE' not in str(model.route.call_args)


def test_merge_keeps_target_public_facts_but_imported_owner_changes_are_private(people):
    alice, bob, *_ = people
    change(alice, text='User is 10 years old.', share=True)
    change(bob, text='User enjoys robots.', share=True)
    user_merge.merge(alice.store, alice.user.id, bob.user.id)
    records = bob.store.read(bob.key)['items']
    assert next(r for r in records if 'robots' in r['text'])['shared']
    assert not next(r for r in records if '10 years' in r['text'])['shared']


def test_exports_only_include_publication_state_not_hash_and_journal_stays_separate(people, console):
    alice, _, charlie, _ = people
    item, _ = change(alice, text='User is 10 years old.', share=True)
    folder = alice.store.root / alice.key
    (folder / 'journal.md.enc').write_bytes(b'PRIVATE JOURNAL')
    (folder / 'logs').mkdir(exist_ok=True)
    (folder / 'logs' / 'private.jsonl').write_text('PRIVATE ARCHIVE')
    client, headers, _ = console
    unlock(client, headers)
    result = client.get(f'/maintenance/memory/export/{alice.user.id}')
    assert result.json['profiles'][0]['data']['items'][0]['shared']
    assert 'shared_hash' not in result.text and 'PRIVATE' not in result.text
    assert 'PRIVATE' not in charlie.context('Alice age')
    with sql.connection(alice.store) as db:
        db.execute('UPDATE memory_items SET text=? WHERE user_id=? AND id=?',
                   ('Edited without approval', alice.user.id, item['id']))
    assert 'Edited without approval' not in charlie.context('Alice approval')


def test_browser_normal_chat_receives_attributed_facts(people, console, monkeypatch):
    alice, _, charlie, _ = people
    change(alice, text='User is 10 years old.', share=True)
    client, headers, _ = console
    def answer(system, question, knowledge='', selected=None):
        assert selected.user.id == charlie.user.id
        assert shared_records(knowledge)[0]['owner_name'] == 'Alice'
        return 'Synthetic attributed answer'
    monkeypatch.setattr(admin, 'llm_answer', answer)
    result = client.post('/test-preset', headers=headers, json={
        'preset_key': 'main', 'memory_user': 'Charlie', 'message': 'How old is Alice?'})
    assert result.json['ok'] and 'Synthetic attributed answer' in result.json['answer']


def test_foreign_vectors_from_another_model_are_ignored(people):
    alice, _, charlie, _ = people
    item, _ = change(alice, text='User is 10 years old.', share=True)
    with sql.connection(alice.store) as db:
        db.execute('UPDATE memory_items SET embedding=?,embedding_model=?,embedding_hash=? WHERE user_id=? AND id=?',
                   (vectors.pack([1.] + [0.] * 1023), 'obsolete-model', sql.fingerprint(item), alice.user.id, item['id']))
    charlie.embeddings = Mock()
    assert '10 years' in charlie.context('How old is Alice?')
    charlie.embeddings.embed.assert_not_called()
