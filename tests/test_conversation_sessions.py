"""Durable ordinary history: actual temporary SQL, mocked APIs, no Pi/network."""

import json
import os
import sqlite3
import time
from unittest.mock import Mock

import pytest
from test_admin_memory import unlock

from services import admin_ui as admin
from services import conversation_store as history
from services import memory_automation as auto
from services import memory_database as sql
from services import memory_session as sessions
from services import minitel_chatgpt as chat
from services import persistent_memory as memory
from services import user_merge


@pytest.fixture
def people():
    selected = [sessions.MemorySession(name, model=Mock()) for name in ("Alice", "Bob", "")]
    for subject in selected:
        subject.embeddings = Mock()
        subject.embeddings.embed.return_value = [[1.] + [0.] * 1023] * 8
    yield selected
    for subject in selected:
        subject.close()


def messages(subject):
    return history.read_session(subject.store, subject.user.id, subject.session_id)[1]


def legacy(subject, rows, name="old.jsonl"):
    logs = subject.store.folder(subject.key) / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / name
    path.write_text("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in rows) + "\n")
    os.utime(path, (time.time() - 120, time.time() - 120))
    return path


def row(text, role="user", channel="chat", **extra):
    return {"at": history.stamp(), "role": role, "channel": channel, "text": text, **extra}


def empty_review(subject):
    subject.model.client.complete.return_value = '{"memories":[]}'
    return auto.process(subject.store, subject.model, subject.embeddings)


def test_lazy_creation_and_input_before_api_with_one_final_record(people, monkeypatch):
    alice, bob, _ = people
    assert history.list_sessions(alice.store, alice.user.id) == []
    monkeypatch.setattr(chat, "PROVIDER", "claude")
    def answer(*args):
        assert messages(alice)[0]["text"] == "I build mechanical clocks."
        raise RuntimeError("synthetic interrupted provider")
    monkeypatch.setattr(chat, "call_llm", answer)
    with pytest.raises(RuntimeError, match="interrupted"):
        chat.call_answer("persona", [{"role": "user", "content": "I build mechanical clocks."}],
                         "en", ("chat_completions", False, 30), "", memory=alice)
    assert len(messages(alice)) == 1
    alice.record("user", "I build mechanical clocks.")
    alice.record("assistant", "What do you like about that?", references=[
        {"url": "https://example.invalid/source", "title": "Source", "raw": "NO RAW DATA"},
        {"url": "javascript:alert(1)", "title": "BAD"}])
    assert len(messages(alice)) == 2
    assert messages(alice)[1]["references"] == [{"url": "https://example.invalid/source", "title": "Source"}]
    assert not history.list_sessions(bob.store, bob.user.id)
    with pytest.raises(KeyError):
        history.read_session(bob.store, bob.user.id, alice.session_id)
    alice.close(reason="sommaire")
    assert history.list_sessions(alice.store, alice.user.id)[0]["close_reason"] == "sommaire"
    with pytest.raises(memory.MemoryProblem):
        alice.journal.append("assistant", "Late result")


def test_default_learning_off_and_gmail_stays_outside_sql(people):
    _, _, default = people
    default.record("user", "We own an old mainframe.")
    default.record("assistant", "GMAIL PRIVATE BODY", "gmail")
    assert len(messages(default)) == 1 and messages(default)[0]["learning"] == "excluded"
    assert list(default.store.folder(default.key).glob("logs/*.jsonl"))
    assert auto.claim(default.store) is None


def test_periodic_and_close_reviews_are_one_pipeline_with_provenance(people):
    alice, *_ = people
    for i in range(7):
        alice.record("user", f"I build mechanical clock number {i}.")
    alice.model.client.complete.return_value = '{"memories":[]}'
    assert empty_review(alice)
    assert len([m for m in messages(alice) if m["learning"] == "done"]) == 6
    alice.close()
    assert empty_review(alice)
    assert not empty_review(alice)
    assert all(m["learning"] == "done" for m in messages(alice))
    assert alice.model.client.complete.call_count == 2


def test_pending_history_exceeds_dispatch_bound_without_losing_statements(people):
    alice, *_ = people
    for i in range(55):
        alice.record("user", f"I build mechanical clock number {i}.")
    with sql.connection(alice.store) as db:
        assert db.execute("SELECT count(*) FROM memory_events").fetchone()[0] == auto.MAX_PENDING
        assert db.execute("SELECT count(*) FROM conversation_messages WHERE learning='pending'").fetchone()[0] == 55
    alice.close()
    processed = 0
    while empty_review(alice):
        processed += 1
        assert processed < 20
    assert processed == 10
    with sql.connection(alice.store) as db:
        assert db.execute("SELECT count(*) FROM conversation_messages WHERE learning='done'").fetchone()[0] == 55


def test_explicit_barrier_never_replays_old_history(people):
    alice, *_ = people
    alice.begin_turn()
    alice.capture("I build mechanical clocks.")
    alice.store.apply(alice.key, {"operation": "save", "kind": "fact", "text": "I now build boats."}, {})
    alice.record("user", "I build mechanical clocks.")
    alice.close()
    assert auto.claim(alice.store) is None
    assert messages(alice)[0]["learning"] == "superseded"
    assert len(alice.store.read(alice.key)["items"]) == 1


def test_deleted_session_fences_inflight_extraction(people):
    alice, *_ = people
    alice.record("user", "I build mechanical clocks.")
    alice.close()
    def delayed(*args, **kwargs):
        history.delete_session(alice.store, alice.user.id, alice.session_id)
        return '{"memories":[]}'
    alice.model.client.complete.side_effect = delayed
    assert not auto.process(alice.store, alice.model, alice.embeddings)
    assert not history.list_sessions(alice.store, alice.user.id)
    assert auto.claim(alice.store) is None


def test_legacy_import_resumes_is_idempotent_and_never_learns(people):
    alice, *_ = people
    path = legacy(alice, [row(f"ordinary statement {i}") for i in range(205)] + ["broken JSON"])
    original = path.read_bytes()
    assert history.import_history(alice.store, alice.user)
    with sql.connection(alice.store) as db:
        entry = dict(db.execute("SELECT * FROM conversation_imports").fetchone())
        assert entry["status"] == "pending" and entry["offset"] > 0
        assert db.execute("SELECT count(*) FROM conversation_messages").fetchone()[0] == 100
    for _ in range(3):
        history.import_history(alice.store, alice.user)
    assert not history.import_history(alice.store, alice.user)
    assert path.read_bytes() == original
    with sql.connection(alice.store) as db:
        assert db.execute("SELECT count(*) FROM conversation_messages").fetchone()[0] == 205
        assert db.execute("SELECT count(*) FROM conversation_messages WHERE learning!='excluded'").fetchone()[0] == 0
        entry = db.execute("SELECT * FROM conversation_imports").fetchone()
        assert entry["errors"] == 1 and entry["status"] == "done"
    assert auto.claim(alice.store) is None


def test_import_excludes_private_channels_and_old_unmarked_school_pairs(people):
    alice, *_ = people
    legacy(alice, [row("ordinary"), row("GMAIL SECRET", channel="gmail"),
                   row("GMAIL BODY", "assistant", "gmail"), row("PRONOTE grades?"),
                   row("SCHOOL SECRET", "assistant"), row("journal intime"),
                   row("JOURNAL SECRET", "assistant"), row("ordinary again"),
                   row("PRIVATE FLAG", "assistant", private=True), row("ROSE SECRET", channel="rose"),
                   row("safe answer", "assistant", "chat")])
    history.import_history(alice.store, alice.user)
    with sql.connection(alice.store) as db:
        texts = [r[0] for r in db.execute("SELECT text FROM conversation_messages")]
    assert texts == ["ordinary", "ordinary again"]


def test_partial_import_changed_file_is_flagged_without_replay(people):
    alice, *_ = people
    path = legacy(alice, [row(str(i)) for i in range(150)])
    history.import_history(alice.store, alice.user)
    path.write_text(path.read_text() + json.dumps(row("new content")))
    history.import_history(alice.store, alice.user)
    with sql.connection(alice.store) as db:
        assert db.execute("SELECT status FROM conversation_imports").fetchone()[0] == "changed"
        assert db.execute("SELECT count(*) FROM conversation_messages").fetchone()[0] == 100


def test_retention_preserves_saved_fact_quote_and_import_ledger(people):
    alice, *_ = people
    legacy(alice, [row("old ordinary statement")])
    history.import_history(alice.store, alice.user)
    item = {"operation": "save", "kind": "fact", "text": "Saved fact survives."}
    alice.store.apply(alice.key, item, {})
    alice.record("user", "A recent ordinary statement.")
    alice.close()
    old = history.stamp(time.time() - 400 * 86400)
    with sql.connection(alice.store) as db:
        db.execute("UPDATE conversation_sessions SET closed_at=? WHERE surface='legacy'", (old,))
        db.execute("DELETE FROM conversation_maintenance")
    assert history.maintenance(alice.store) == 1
    assert not history.import_history(alice.store, alice.user)
    assert len(alice.store.read(alice.key)["items"]) == 1
    assert len(history.list_sessions(alice.store, alice.user.id)) == 1
    with sql.connection(alice.store) as db:
        assert db.execute("SELECT status FROM conversation_imports").fetchone()[0] == "expired"


def test_recovery_only_retires_dead_writers_not_another_live_frontend(people):
    alice, bob, _ = people
    alice.record("user", "Unanswered question")
    bob.record("user", "Still using another frontend")
    with sql.connection(alice.store) as db:
        db.execute("UPDATE conversation_sessions SET writer='dead:99999999:0' WHERE id=?", (alice.session_id,))
    history.maintenance(alice.store)
    assert history.list_sessions(alice.store, alice.user.id)[0]["status"] == "interrupted"
    assert history.list_sessions(bob.store, bob.user.id)[0]["status"] == "open"
    assert auto.claim(alice.store)["user_id"] == alice.user.id


def test_delete_profile_cascades_and_cannot_resurrect_on_late_record(people):
    alice, bob, _ = people
    alice.record("user", "A statement pending review.")
    bob.record("assistant", "Other profile history")
    alice.registry.delete(alice.user.id, alice.user.revision, alice.username)
    alice.capture("Late input")
    alice.record("assistant", "Late answer")
    assert not (alice.store.root / alice.key).exists()
    recreated = sessions.MemorySession("Alice", model=Mock())
    try:
        assert recreated.user.id != alice.user.id
        assert history.list_sessions(alice.store, recreated.user.id) == []
        assert messages(bob)[0]["text"] == "Other profile history"
        with sql.connection(alice.store) as db:
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert db.execute("SELECT count(*) FROM memory_events WHERE user_id=?", (alice.user.id,)).fetchone()[0] == 0
    finally:
        recreated.close()


def test_merge_transfers_histories_pending_jobs_and_import_checkpoints(people):
    alice, bob, _ = people
    alice.record("user", "I build mechanical clocks.")
    bob.record("user", "I collect old terminals.")
    legacy(alice, [row("older history")])
    history.import_history(alice.store, alice.user)
    result = user_merge.merge(alice.store, alice.user.id, bob.user.id)
    assert result["pending_after"] == 2
    transferred = history.list_sessions(bob.store, bob.user.id)
    assert len(transferred) == 3 and all(s["status"] != "open" for s in transferred)
    assert not history.list_sessions(alice.store, alice.user.id)
    fresh = sessions.MemorySession("Bob", model=Mock())
    try:
        assert not history.import_history(fresh.store, fresh.user)
        with sql.connection(fresh.store) as db:
            assert db.execute("SELECT count(*) FROM conversation_messages").fetchone()[0] == 3
            assert db.execute("SELECT count(*) FROM memory_events WHERE user_id=?", (bob.user.id,)).fetchone()[0] == 2
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        alice.record("assistant", "Late source answer")
        bob.record("assistant", "Late old destination answer")
        assert len(history.list_sessions(fresh.store, fresh.user.id)) == 3
    finally:
        fresh.close()


def test_v2_migration_backup_and_unsupported_schema_fail_closed(people):
    alice, *_ = people
    alice.store.apply(alice.key, {"operation": "save", "kind": "fact", "text": "Preserved fact"}, {})
    path = alice.store.root / "users.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("DROP INDEX memory_event_message")
        db.execute("ALTER TABLE memory_events DROP COLUMN message_id")
        for table in ("conversation_messages", "conversation_sessions", "conversation_imports", "conversation_maintenance"):
            db.execute(f"DROP TABLE {table}")
        db.execute("UPDATE memory_schema SET version=2")
    assert alice.store.read(alice.key)["items"][0]["text"] == "Preserved fact"
    backup = alice.store.root / "users.before-sessions.sqlite3"
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT version FROM memory_schema").fetchone()[0] == 2
    with sqlite3.connect(path) as db:
        db.execute("UPDATE memory_schema SET version=999")
    with pytest.raises(memory.MemoryProblem, match="Version"):
        alice.store.read(alice.key)


def test_gated_admin_pagination_export_confirmed_delete_and_cross_owner(console, people):
    client, headers, _ = console
    alice, bob, _ = people
    for i in range(45):
        alice.record("assistant", f"Private transcript <script>{i}</script>")
    base = f"/maintenance/sessions/{alice.user.id}/{alice.session_id}"
    assert client.get("/maintenance/sessions").status_code == 403
    assert client.get(base).status_code == 403
    assert unlock(client, headers).status_code == 302
    assert client.get("/maintenance/sessions").status_code == 200
    assert client.get(f"/maintenance/sessions/{alice.user.id}").status_code == 200
    page = client.get(base)
    assert page.status_code == 200 and "&lt;script&gt;" in page.text and "<script>0</script>" not in page.text
    assert "<script>44</script>" not in page.text and "Suite" in page.text
    exported = client.get(base + "/export")
    assert len(exported.json["messages"]) == 45 and exported.json["complete"]
    assert "writer" not in exported.json["session"]
    assert client.get(f"/maintenance/sessions/{bob.user.id}/{alice.session_id}").status_code == 404
    assert client.get("/maintenance/memory/export").status_code == 200
    assert "Private transcript" not in client.get("/maintenance/memory/export").text
    assert client.post(base + "/delete", data={"confirm": alice.session_id}, headers=headers).status_code == 409
    alice.close()
    assert client.post(base + "/delete", data={"confirm": "wrong"}, headers=headers).status_code == 400
    assert client.post(base + "/delete", data={"confirm": alice.session_id}).status_code == 403
    assert client.post(base + "/delete", data={"confirm": alice.session_id}, headers=headers).status_code == 302
    assert client.get(base).status_code == 404
    with client.session_transaction() as cookie:
        cookie["memory_unlock_until"] = time.time() - 1
    assert client.get("/maintenance/sessions").status_code == 403


def test_saved_memory_links_to_message_and_survives_transcript_expiry(people):
    alice, *_ = people
    text = "I build mechanical clocks."
    alice.record("user", text)
    alice.close()
    with sql.connection(alice.store) as db:
        event = dict(db.execute("SELECT * FROM memory_events WHERE user_id=?", (alice.user.id,)).fetchone())
    alice.model.client.complete.return_value = json.dumps({"memories": [{
        "operation": "save", "kind": "fact", "title": "hobby", "text": text, "id": None,
        "evidence": [{"event_id": event["id"], "quote": text}]}]})
    assert auto.process(alice.store, alice.model, alice.embeddings)
    saved = alice.store.read(alice.key)["items"][0]
    assert saved["source"][0]["message_id"] == messages(alice)[0]["id"]
    with sql.connection(alice.store) as db:
        db.execute("UPDATE conversation_sessions SET closed_at=?", (history.stamp(time.time() - 400 * 86400),))
        db.execute("DELETE FROM conversation_maintenance")
    assert history.maintenance(alice.store) == 1
    assert alice.store.read(alice.key)["items"][0] == saved


def test_private_provider_result_removes_provisional_input_and_cancels_job(people, monkeypatch):
    alice, *_ = people
    monkeypatch.setattr(chat, "PROVIDER", "mistral")
    monkeypatch.setattr(chat.pronote, "backend", lambda: None)
    monkeypatch.setattr(chat.mistral_answers, "answer", lambda *args, **kwargs: {
        "status": "ok", "text": "PRIVATE SCHOOL BODY", "private": True, "chunks": [], "references": []})
    result = chat.call_answer("persona", [{"role": "user", "content": "Tell me about my day"}],
                             "en", ("conversations", False, 30), "", memory=alice)
    assert result["turn"]["private"]
    assert messages(alice) == []
    alice.close()
    assert auto.claim(alice.store) is None


def test_assistant_context_is_bounded_and_is_not_user_evidence(people):
    alice, *_ = people
    alice.record("assistant", "What is your hobby? " + "x" * 2000)
    alice.record("user", "I build mechanical clocks.")
    alice.close()
    assert empty_review(alice)
    payload = alice.model.client.complete.call_args.args[1]
    assert len(payload["user_statements"][0]["assistant_context"]) == 1400
    assert payload["user_statements"][0]["text"] == "I build mechanical clocks."


@pytest.mark.parametrize("value", ["0", "-3", "bad"])
def test_invalid_retention_does_not_prune_anything(people, monkeypatch, value):
    alice, *_ = people
    alice.record("user", "Some ordinary text")
    alice.close()
    monkeypatch.setenv("MINITEL_SESSION_RETENTION_DAYS", value)
    with pytest.raises(memory.MemoryProblem):
        history.maintenance(alice.store)
    assert len(messages(alice)) == 1


def test_import_privacy_state_survives_chunk_boundary_and_does_not_touch_symlink(people):
    alice, *_ = people
    legacy(alice, [row("ordinary") for _ in range(99)] + [row("PRONOTE grades?")] +
                  [row("SCHOOL SECRET", "assistant"), row("ordinary again")])
    history.import_history(alice.store, alice.user)
    history.import_history(alice.store, alice.user)
    with sql.connection(alice.store) as db:
        assert db.execute("SELECT count(*) FROM conversation_messages").fetchone()[0] == 100
        assert not db.execute("SELECT 1 FROM conversation_messages WHERE text LIKE '%SECRET%'").fetchone()
    outside = alice.store.root / "private-outside.jsonl"
    original = json.dumps(row("OUTSIDE PRIVATE"))
    outside.write_text(original)
    (alice.store.root / alice.key / "logs" / "symlink.jsonl").symlink_to(outside)
    with pytest.raises(memory.MemoryProblem):
        history.import_history(alice.store, alice.user)
    assert outside.read_text() == original


def test_retention_is_bounded_and_continues_on_later_activity(people):
    alice, *_ = people
    old = history.stamp(time.time() - 400 * 86400)
    with sql.connection(alice.store) as db:
        for i in range(23):
            db.execute("""INSERT INTO conversation_sessions
 (id,user_id,profile_revision,surface,opened_at,last_activity,closed_at,status)
 VALUES(?,?,?,'legacy',?,?,?,'closed')""",
                       (f"expired-{i}", alice.user.id, alice.user.revision, old, old, old))
        db.execute("DELETE FROM conversation_maintenance")
    assert history.maintenance(alice.store) == 20
    assert history.maintenance(alice.store) == 3
    assert history.maintenance(alice.store) == 0
    assert not history.list_sessions(alice.store, alice.user.id)


def test_archive_import_failure_does_not_disable_normal_learning(people, monkeypatch):
    alice, *_ = people
    def failed(*args):
        raise memory.MemoryProblem("Synthetic unreadable old archive")
    monkeypatch.setattr(history, "import_history", failed)
    alice.begin_turn()
    alice.record("user", "I build mechanical clocks.")
    assert "unreadable" in alice.take_warning()
    assert messages(alice)[0]["learning"] == "pending"
    alice.close()
    assert empty_review(alice)


def test_browser_sommaire_and_expiry_close_durable_sessions(console, monkeypatch):
    client, headers, _ = console
    monkeypatch.setattr(admin, "llm_answer", lambda *args, **kwargs: "Synthetic ordinary answer")
    base = {"preset_key": "main", "memory_user": "Alice"}
    result = client.post("/test-preset", headers=headers, json={**base, "message": "Hello mainframe"})
    assert result.json["ok"]
    with client.session_transaction() as cookie:
        identifier = cookie["memory_session"]
    first = admin.MEMORY_SESSIONS[identifier]["memory"]
    assert len(messages(first)) == 2
    client.post("/test-preset", headers=headers, json={**base, "message": "SOMMAIRE"})
    assert history.list_sessions(first.store, first.user.id)[0]["close_reason"] == "sommaire"
    client.post("/test-preset", headers=headers, json={**base, "message": "Another ordinary question"})
    with client.session_transaction() as cookie:
        identifier = cookie["memory_session"]
    item = admin.MEMORY_SESSIONS[identifier]
    second = item["memory"]
    item["used"] = time.monotonic() - 1300
    client.post("/test-preset", headers=headers, json={**base, "message": "A new session"})
    assert history.read_session(second.store, second.user.id, second.session_id)[0]["close_reason"] == "timeout"
    for identifier in list(admin.MEMORY_SESSIONS):
        admin.discard_memory(identifier)


def test_calculator_input_survives_interrupted_scene_generation(people, monkeypatch):
    alice, *_ = people
    from services import calculator
    def generate(*args, **kwargs):
        assert messages(alice)[0]["text"] == "CALC 2/0"
        assert messages(alice)[0]["learning"] == "excluded"
        raise RuntimeError("synthetic interrupted calculator")
    monkeypatch.setattr(calculator, "generate_scene", generate)
    with pytest.raises(RuntimeError, match="calculator"):
        chat.call_answer("persona", [{"role": "user", "content": "CALC 2/0"}],
                         "en", ("chat_completions", False, 30), "", memory=alice, calc=calculator.Session())
    assert len(messages(alice)) == 1
