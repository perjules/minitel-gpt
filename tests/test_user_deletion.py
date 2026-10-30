"""Confirmed per-profile purge with real SQL, archives and stale sessions."""
import json
from unittest.mock import Mock

import pytest

from services import admin_ui as admin
from services import (
    gmail_auth,
    memory_automation,
    memory_database,
    memory_session,
    user_registry,
    user_services,
)


def profiles():
    registry = user_registry.Registry()
    users = [registry.resolve(name) for name in ("Alice", "Bob")]
    registry.record_account("a" * 32, "shared@example.invalid", 1)
    for user in users:
        registry.configure(user.id, user.display_name, True, [user.display_name], "a" * 32, True, ["Child"])
    selected = [memory_session.MemorySession(user.display_name, model=Mock()) for user in users]
    for profile in selected:
        profile.store.apply(profile.key, {"operation": "save", "kind": "fact", "text": "PRIVATE " + profile.username}, {})
        profile.record("assistant", "ARCHIVE " + profile.username, "onboarding")
        folder = profile.store.root / profile.key
        (folder / "memory.before-sqlite.json").write_text("LEGACY " + profile.username)
        with memory_database.connection(profile.store) as db:
            db.execute("UPDATE memory_items SET embedding=?,embedding_model='synthetic' WHERE user_id=?", (b"vector", profile.user.id))
            db.execute("INSERT INTO memory_events(user_id,session_id,text,created,barrier) VALUES(?,?,?,'now',0)",
                       (profile.user.id, "test", "PENDING " + profile.username))
            db.execute("UPDATE user_onboarding SET history=? WHERE user_id=?",
                       (json.dumps([{"role": "user", "text": "HELLO " + profile.username}]), profile.user.id))
    return registry, selected


def test_admin_purge_removes_only_target_data_and_preserves_shared_accounts(console, monkeypatch):
    client, headers, _ = console
    registry, (alice, bob) = profiles()
    credential_helper = Mock()
    monkeypatch.setattr(gmail_auth, "credential_helper", credential_helper)
    alice_folder, bob_folder = registry.root / alice.key, registry.root / bob.key
    bob_files = {str(p.relative_to(bob_folder)): p.read_bytes() for p in bob_folder.rglob("*") if p.is_file()}
    default = registry.resolve("")
    page = client.get("/users")
    assert "Supprimer le profil et toutes ses mémoires" in page.text
    assert f'action="/users/{alice.user.id}/delete"' in page.text
    assert 'action="/users/1/delete"' not in page.text
    preview = client.post('/test-preset', headers=headers, json={
        'preset_key': 'main', 'memory_user': 'Alice', 'message': 'MEMOIRE'})
    assert preview.json['ok']
    with client.session_transaction() as cookie:
        cached = cookie['memory_session']
    assert cached in admin.MEMORY_SESSIONS
    payload = {"revision": alice.user.revision, "confirmation": "Alice"}
    assert client.get(f"/users/{alice.user.id}/delete").status_code == 405
    assert client.post(f"/users/{alice.user.id}/delete", data=payload).status_code == 403
    assert admin.app.test_client().post(f"/users/{alice.user.id}/delete", data=payload).status_code in (302, 403)
    assert registry.get(alice.user.id) == alice.user
    assert client.post(f"/users/{alice.user.id}/delete", data=payload, headers=headers).status_code == 302
    assert cached not in admin.MEMORY_SESSIONS
    assert 'Profil, accès et données personnelles supprimés.' in client.get('/users').text
    assert not alice_folder.exists()
    assert {str(p.relative_to(bob_folder)): p.read_bytes() for p in bob_folder.rglob("*") if p.is_file()} == bob_files
    assert registry.get(default.id) == default
    assert registry.gmail(bob.user.id)[1] == "a" * 32
    assert len(registry.accounts()) == 1
    credential_helper.assert_not_called()
    with registry.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in ("user_aliases", "user_gmail_access", "user_pronote_children", "user_onboarding",
                      "memory_items", "memory_events", "memory_state"):
            assert db.execute(f"SELECT count(*) FROM {table} WHERE user_id=?", (alice.user.id,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM users WHERE id=?", (alice.user.id,)).fetchone()[0] == 0
    alice.record("assistant", "LATE RESPONSE", "onboarding")
    alice.close()
    assert not alice_folder.exists()
    bob.close()


def test_confirmation_stale_forms_and_default_profile_are_refused(console):
    client, headers, _ = console
    registry, (alice, bob) = profiles()
    for identifier, revision, name, expected in ((1, 0, "MinitelGPT", 400),
        (alice.user.id, alice.user.revision, "Bob", 400),
        (alice.user.id, alice.user.revision - 1, "Alice", 409)):
        assert client.post(f"/users/{identifier}/delete", headers=headers,
                           data={"revision": revision, "confirmation": name}).status_code == expected
    assert registry.get(alice.user.id).display_name == "Alice"
    assert alice.store.read(alice.key)["items"]
    alice.close()
    bob.close()


def test_highest_id_never_reused_and_same_name_starts_with_empty_memory():
    old = memory_session.MemorySession("Only test", model=Mock())
    old.store.apply(old.key, {"operation": "save", "kind": "note", "title": "Old", "text": "OLD NOTE"}, {})
    tokens = user_services.ProfileTokens(old.user, old.registry)
    old.registry.delete(old.user.id, old.user.revision, old.username)
    again = memory_session.MemorySession("Only test", model=Mock())
    try:
        assert again.user.id > old.user.id and again.store.read(again.key)["items"] == []
        with pytest.raises(memory_session.memory.MemoryProblem):
            old.active()
        with pytest.raises(gmail_auth.GmailError):
            tokens.guard()
        old.record("user", "Old profile likes rockets", "chat")
        memory_automation.enqueue(old.store, old.key, "old", "Old profile likes rockets", expected_user=old.user)
        with memory_database.connection(again.store) as db:
            assert db.execute("SELECT count(*) FROM memory_events WHERE user_id=?", (again.user.id,)).fetchone()[0] == 0
        with pytest.raises(memory_session.memory.MemoryProblem):
            old.journal.append("assistant", "LATE PRIVATE DATA")
        assert not any("LATE PRIVATE DATA" in p.read_text() for p in (again.store.root / again.key).rglob("*.jsonl"))
    finally:
        old.close()
        again.close()


def test_late_onboarding_reply_cannot_restore_deleted_memories_or_archives(monkeypatch):
    from test_onboarding import fact, response
    monkeypatch.setenv("MINITEL_ONBOARDING", "1")
    selected = memory_session.MemorySession("Late", model=Mock())

    def reply(*args, **kwargs):
        selected.registry.delete(selected.user.id, selected.user.revision, selected.username)
        return response(memories=[fact("10")])

    selected.model.client.complete.side_effect = reply
    selected.welcome.respond("10", "persona")
    selected.record("assistant", "OLD PRIVATE REPLY", "onboarding")
    selected.close()
    assert not (selected.store.root / selected.key).exists()
    with selected.registry.connection() as db:
        assert not db.execute("SELECT * FROM memory_items WHERE user_id=?", (selected.user.id,)).fetchall()


def test_failed_archive_cleanup_leaves_disabled_profile_and_can_be_retried(monkeypatch):
    registry, (alice, bob) = profiles()
    original = user_registry.shutil.rmtree
    monkeypatch.setattr(user_registry.shutil, "rmtree", Mock(side_effect=PermissionError()))
    with pytest.raises(memory_session.memory.MemoryProblem, match="incomplète"):
        registry.delete(alice.user.id, alice.user.revision, "Alice")
    current = next(u for u in registry.users() if u["id"] == alice.user.id)
    assert not current["enabled"]
    with pytest.raises(gmail_auth.GmailError):
        registry.resolve("Alice")
    monkeypatch.setattr(user_registry.shutil, "rmtree", original)
    registry.delete(alice.user.id, current["revision"], "Alice")
    assert not (registry.root / alice.key).exists()
    assert registry.get(bob.user.id).display_name == "Bob"
    alice.close()
    bob.close()


def test_purge_does_not_follow_links_outside_profile(tmp_path):
    selected = memory_session.MemorySession("Links", model=Mock())
    outside = tmp_path / "outside.txt"
    outside.write_text("KEEP")
    (selected.store.root / selected.key / "extra-link").symlink_to(outside)
    selected.registry.delete(selected.user.id, selected.user.revision, selected.username)
    assert outside.read_text() == "KEEP"
    selected.close()
