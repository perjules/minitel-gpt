"""Manual edits and household publication with real temporary SQL, no model calls."""

import time
from unittest.mock import Mock

import pytest
from test_admin_memory import unlock

from services import memory_administration as edits
from services import memory_automation as auto
from services import memory_database as sql
from services import memory_session as sessions
from services import persistent_memory as memory


def selected(name="Alice"):
    return sessions.MemorySession(name, model=Mock())


def change(subject, action="add", **values):
    data = subject.store.read(subject.key)
    return edits.change(subject.store, subject.user.id, subject.user.revision,
                        data["revision"], action, **values)


def form(subject, action="add", **fields):
    return {"action": action, "profile_revision": subject.user.revision,
            "memory_revision": subject.store.read(subject.key)["revision"],
            "kind": "fact", "text": "Synthetic fact", **fields}


def path(subject):
    return f"/maintenance/memory/{subject.user.id}/change"


def test_links_lead_to_unlock_and_then_selected_profile(console):
    client, headers, _ = console
    subject = selected()
    try:
        page = client.get("/maintenance/memory")
        assert f'?profile={subject.user.id}#memory-unlock' in page.text
        result = client.post("/maintenance/memory", headers=headers, data={
            "action": "unlock", "password": "synthetic-admin-password", "profile": subject.user.id})
        assert result.status_code == 302
        assert result.location.endswith(f"/maintenance/memory/{subject.user.id}")
        page = client.get(result.location).text
        assert "Ajouter un souvenir" in page
        assert '<form method=post action=/maintenance/memory>' in page
        assert client.post("/maintenance/memory", headers=headers, data={"action": "lock"}).status_code == 302
        assert client.get(result.location).status_code == 403
    finally:
        subject.close()


def test_writes_need_login_gate_csrf_and_unexpired_unlock(console):
    client, headers, _ = console
    subject = selected()
    try:
        payload = form(subject)
        assert client.post(path(subject), data=payload, headers=headers).status_code == 403
        unlock(client, headers)
        assert client.post(path(subject), data=payload).status_code == 403
        assert client.get(path(subject)).status_code == 405
        with client.session_transaction() as cookie:
            cookie["memory_unlock_until"] = time.time() - 1
        assert client.post(path(subject), data=payload, headers=headers).status_code == 403
        assert subject.store.read(subject.key)["items"] == []
        subject.model.client.complete.assert_not_called()
    finally:
        subject.close()


def test_add_edit_full_text_clear_title_delete_and_preserve_other_profile(console):
    client, headers, _ = console
    subject, other = selected(), selected("Bob")
    try:
        change(other, text="Bob private hobbies")
        unlock(client, headers)
        assert client.post(path(subject), headers=headers, data=form(subject, title="Old title")).status_code == 302
        data = subject.store.read(subject.key)
        item = data["items"][0]
        assert item["origin"] == "admin"
        with sql.connection(subject.store) as db:
            db.execute("UPDATE memory_items SET embedding=?,embedding_model='synthetic',embedding_hash='old' WHERE user_id=?", (b"vector", subject.user.id))
        subject.record("user", "I enjoy scientific computing very much.")
        full_text = ("<script>alert(1)</script>\n" + "long note body " * 1000).strip()
        payload = form(subject, "edit", identifier=item["id"], title="", text=full_text)
        assert client.post(path(subject), headers=headers, data=payload).status_code == 302
        changed = subject.store.read(subject.key)["items"][0]
        assert changed["text"] == full_text and changed["title"] == ""
        with sql.connection(subject.store) as db:
            assert db.execute("SELECT embedding FROM memory_items WHERE user_id=?", (subject.user.id,)).fetchone()[0] is None
            assert db.execute("SELECT count(*) FROM memory_events WHERE user_id=?", (subject.user.id,)).fetchone()[0] == 0
        page = client.get(f"/maintenance/memory/{subject.user.id}").text
        assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
        # The same old form cannot overwrite an edit that arrived in the meantime.
        assert client.post(path(subject), headers=headers, data=payload).status_code == 409
        payload = form(subject, "delete", identifier=item["id"])
        assert client.post(path(subject), headers=headers, data=payload).status_code == 400
        payload["confirm"] = "yes"
        assert client.post(path(subject), headers=headers, data=payload).status_code == 302
        assert subject.store.read(subject.key)["items"] == []
        assert other.store.read(other.key)["items"][0]["text"] == "Bob private hobbies"
        subject.model.client.complete.assert_not_called()
    finally:
        subject.close()
        other.close()


@pytest.mark.parametrize("values", [
    {"kind": "invalid"}, {"text": ""}, {"text": "x" * 32001},
    {"kind": "note", "title": ""}, {"text": "password: do-not-store"},
    {"publish": True},
])
def test_invalid_or_personal_publication_requests_do_not_write(values):
    subject = selected()
    try:
        with pytest.raises(memory.MemoryProblem):
            change(subject, **({"text": "Synthetic fact"} | values))
        assert subject.store.read(subject.key)["items"] == []
    finally:
        subject.close()


def test_profile_change_between_form_and_write_is_fenced(monkeypatch):
    subject = selected()
    try:
        write = subject.store._write

        def changed_profile(folder, data):
            with subject.registry.connection() as db:
                db.execute("UPDATE users SET revision=revision+1 WHERE id=?", (subject.user.id,))
            write(folder, data)

        monkeypatch.setattr(subject.store, "_write", changed_profile)
        with pytest.raises(memory.MemoryProblem, match="Profil modifié"):
            change(subject, text="Must not be saved")
        assert subject.store.read(subject.key)["items"] == []
    finally:
        subject.close()


def test_deleted_recreated_profile_and_foreign_item_cannot_be_edited():
    subject, other = selected(), selected("Bob")
    try:
        item, _ = change(other, text="Bob fact")
        with pytest.raises(memory.MemoryProblem, match="introuvable"):
            change(subject, "edit", identifier=item["id"], text="Wrong owner")
        old_revision = subject.store.read(subject.key)["revision"]
        subject.registry.delete(subject.user.id, subject.user.revision, subject.username)
        replacement = selected()
        try:
            with pytest.raises(memory.MemoryProblem):
                edits.change(subject.store, subject.user.id, subject.user.revision,
                             old_revision, "add", text="From old form")
            assert replacement.store.read(replacement.key)["items"] == []
        finally:
            replacement.close()
    finally:
        subject.close()
        other.close()


def test_admin_edit_fences_inflight_extraction_and_late_ordinary_turn():
    subject = selected()
    try:
        item, _ = change(subject, text="Old job is teacher")
        subject.begin_turn()
        for i in range(6):
            subject.record("user", f"My old job is teacher number {i}.")
        subject.begin_turn()

        def extraction(*args, **kwargs):
            change(subject, "delete", identifier=item["id"])
            return '{"memories": []}'

        subject.model.client.complete.side_effect = extraction
        assert not auto.process(subject.store, subject.model, Mock())
        subject.record("user", "My old job is teacher after delayed reply.")
        assert subject.store.read(subject.key)["items"] == []
        with sql.connection(subject.store) as db:
            assert db.execute("SELECT count(*) FROM memory_events WHERE user_id=?", (subject.user.id,)).fetchone()[0] == 0
    finally:
        subject.close()


def test_admin_can_publish_and_unpublish_default_fact_through_forms(console):
    client, headers, _ = console
    default, alice = selected(""), selected()
    try:
        unlock(client, headers)
        payload = form(default, text="Alice's brother is Bob", publish="yes")
        assert client.post(path(default), headers=headers, data=payload).status_code == 302
        item = default.store.read(default.key)["items"][0]
        assert item["origin"] == "household"
        assert "brother is Bob" in alice.context("my brother")
        payload = form(default, "edit", identifier=item["id"], text=item["text"])
        assert client.post(path(default), headers=headers, data=payload).status_code == 302
        assert "brother is Bob" not in alice.context("my brother")
        assert "Non partagé" in client.get(f"/maintenance/memory/{default.user.id}").text
    finally:
        default.close()
        alice.close()
