"""Exceptional offline merges: real temp SQLite/files; no Google/model/systemd calls."""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import merge_users as cli
from services import gmail_auth, journal_store, user_registry
from services import memory_automation as auto
from services import memory_database as sql
from services import memory_session as sessions
from services import persistent_memory as memory
from services import user_merge as merges


@pytest.fixture
def pair():
    source = sessions.MemorySession("Jullian", model=Mock())
    target = sessions.MemorySession("Julian", model=Mock())
    other = sessions.MemorySession("Other", model=Mock())
    for profile in (source, target, other):
        profile.store.apply(profile.key, {"operation": "save", "kind": "fact", "title": "job",
                            "text": profile.username + " has an independent fact"}, {})
        profile.record("user", "I enjoy my own scientific hobbies very much.")
    yield source, target, other
    for profile in (source, target, other):
        profile.close()


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_preview_and_list_are_readonly_and_unknown_id_does_not_create_profile(pair, capsys):
    source, target, _ = pair
    before = files(source.store.root)
    plan = merges.preview(source.store, source.user.id, target.user.id)
    assert plan["items_after"] == 2 and plan["pending_after"] == 2
    assert cli.main(["--source", str(source.user.id), "--target", str(target.user.id)]) == 0
    assert "APERÇU SEULEMENT" in capsys.readouterr().out
    assert cli.main(["--list"]) == 0
    assert "Jullian" in capsys.readouterr().out
    assert cli.main(["--source", "999999", "--target", str(target.user.id)]) == 1
    assert files(source.store.root) == before


def test_complete_merge_preserves_records_vectors_queues_archives_aliases_and_rights(pair, monkeypatch):
    source, target, other = pair
    registry, store = source.registry, source.store
    registry.record_account("a" * 32, "source@example.invalid", 1)
    registry.record_account("b" * 32, "target@example.invalid", 1)
    registry.configure(source.user.id, "Jullian", True, ["Jullian", "Jullien"], "a" * 32, True, ["SourceChild"])
    registry.configure(target.user.id, "Julian", True, ["Julian"], "b" * 32, False, ["TargetChild"])
    with registry.connection() as db:
        source_user = dict(db.execute("SELECT * FROM users WHERE id=?", (source.user.id,)).fetchone())
        target_user = dict(db.execute("SELECT * FROM users WHERE id=?", (target.user.id,)).fetchone())
    # Existing sessions now intentionally stale; raw fixtures represent offline files/state.
    source_folder, target_folder = store.root / source.key, store.root / target.key
    source_item = store.read(source.key)["items"][0]
    target_item = store.read(target.key)["items"][0]
    with sql.connection(store) as db:
        db.execute("UPDATE memory_items SET id=?,embedding=?,embedding_model='synthetic',embedding_hash='source-hash',origin='automatic',source=? WHERE user_id=?",
                   (target_item["id"], b"source-vector", json.dumps([{"quote": "original assertion", "session_id": "source-session"}]), source.user.id))
        db.execute("UPDATE memory_state SET language='en',lease_token='old-source',lease_until=9999999999 WHERE user_id=?", (source.user.id,))
        db.execute("UPDATE memory_state SET language='fr',lease_token='old-target',lease_until=9999999999 WHERE user_id=?", (target.user.id,))
        db.execute("UPDATE user_onboarding SET status='complete',turns=9 WHERE user_id=?", (target.user.id,))
        db.execute("UPDATE user_onboarding SET status='active',history=? WHERE user_id=?", (json.dumps([{"role": "user", "text": "source welcome assertion"}]), source.user.id))
    for p in (source_folder, target_folder):
        (p / "logs").mkdir(exist_ok=True)
    (source_folder / "logs" / "same.jsonl").write_text("SOURCE TRANSCRIPT")
    (target_folder / "logs" / "same.jsonl").write_text("TARGET TRANSCRIPT")
    salt = b"s" * 16
    encrypted = journal_store.encrypt(journal_store.TITLE, journal_store.derive("test-password", salt), salt, source.user.id)
    (source_folder / "journal.md.enc").write_bytes(encrypted)
    (target_folder / "journal.md.enc").write_bytes(b"TARGET ENCRYPTED JOURNAL")
    (source_folder / "mail-draft-fake.json").write_text("SOURCE DRAFT")
    (source_folder / "memory.before-sqlite.json").write_text("ORIGINAL LEGACY")
    before_other = files(store.root / other.key)
    before_source = files(source_folder)
    before_target = files(target_folder)
    reporter = Mock()
    monkeypatch.setattr(user_registry.Registry, "report_event", reporter)
    result = merges.merge(store, source.user.id, target.user.id)
    assert not source_folder.exists()
    data = store.read(target.key)
    assert len(data["items"]) == 2 and data["language"] == "fr"
    assert len({r["id"] for r in data["items"]}) == 2
    imported = next(r for r in data["items"] if r["text"] == source_item["text"])
    assert imported["origin"] == "automatic" and imported["source"][0]["quote"] == "original assertion"
    assert imported["id"] != target_item["id"]
    archive, backup = Path(result["archive"]), Path(result["backup"])
    assert (archive / "journal.md.enc").read_bytes() == encrypted
    assert journal_store.decrypt(encrypted, "test-password")[3] == source.user.id
    assert (target_folder / "journal.md.enc").read_bytes() == b"TARGET ENCRYPTED JOURNAL"
    assert (archive / "mail-draft-fake.json").read_text() == "SOURCE DRAFT"
    assert (archive / "memory.before-sqlite.json").read_text() == "ORIGINAL LEGACY"
    assert (target_folder / "logs" / "same.jsonl").read_text() == "TARGET TRANSCRIPT"
    assert (target_folder / "logs" / f"merged-{source.user.id}-same.jsonl").read_text() == "SOURCE TRANSCRIPT"
    assert "source welcome assertion" in (archive / "merge-source.json").read_text()
    assert files(backup / "source") == before_source and files(backup / "target") == before_target
    assert json.loads((backup / "status.json").read_text())["status"] == "complete"
    assert (backup / "users.sqlite3").stat().st_mode & 0o777 == 0o600
    assert registry.resolve("Jullian").id == target.user.id == registry.resolve("Jullien").id
    assert registry.gmail(target.user.id)[1] == "b" * 32
    with pytest.raises(gmail_auth.GmailError):
        registry.gmail(target.user.id, "send")
    assert len(registry.accounts()) == 2
    with registry.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT child FROM user_pronote_children WHERE user_id=?", (target.user.id,)).fetchone()[0] == "TargetChild"
        assert db.execute("SELECT count(*) FROM users WHERE id=?", (source.user.id,)).fetchone()[0] == 0
        assert db.execute("SELECT revision FROM users WHERE id=?", (target.user.id,)).fetchone()[0] == target_user["revision"] + 1
        row = db.execute("SELECT * FROM memory_state WHERE user_id=?", (target.user.id,)).fetchone()
        assert row["lease_token"] == "" and row["ready"]
        pending = db.execute("SELECT * FROM memory_events WHERE user_id=?", (target.user.id,)).fetchall()
        assert len(pending) == 2 and all(r["barrier"] == row["barrier"] for r in pending)
        assert db.execute("SELECT embedding FROM memory_items WHERE user_id=? AND id=?", (target.user.id, imported["id"])).fetchone()[0] == b"source-vector"
        assert db.execute("SELECT status FROM user_onboarding WHERE user_id=?", (target.user.id,)).fetchone()[0] == "complete"
    with sqlite3.connect(backup / "users.sqlite3") as db:
        assert db.execute("SELECT revision FROM users WHERE id=?", (source.user.id,)).fetchone()[0] == source_user["revision"]
    assert files(store.root / other.key) == before_other
    reporter.assert_not_called()
    assert registry.resolve("Brand new").id > other.user.id


@pytest.mark.parametrize("source_id,target_id", [(1, 2), (2, 1), (2, 2), (99999, 2)])
def test_reserved_same_and_missing_profiles_refused(pair, source_id, target_id):
    source, _, _ = pair
    before = files(source.store.root)
    with pytest.raises(merges.MergeProblem):
        merges.merge(source.store, source_id, target_id)
    assert files(source.store.root) == before


@pytest.mark.parametrize("worker_index", [0, 1])
def test_stale_sessions_and_inflight_extraction_cannot_restore_source(pair, worker_index):
    source, target, _ = pair
    worker = (source, target)[worker_index]
    for _ in range(5):
        worker.record("user", "I enjoy source-specific hobbies very much.")
    worker.model.client.complete.side_effect = lambda *a, **k: (
        merges.merge(source.store, source.user.id, target.user.id) and '{"memories": []}')
    assert not auto.process(worker.store, worker.model, Mock())
    for subject in (source, target):
        with pytest.raises(memory.MemoryProblem):
            subject.active()
    source.record("user", "Late source assertion must not be queued")
    restored = sessions.MemorySession("Jullian", model=Mock())
    try:
        assert restored.user.id == target.user.id
        assert len(restored.store.read(restored.key)["items"]) == 2
        with sql.connection(restored.store) as db:
            assert db.execute("SELECT count(*) FROM memory_events WHERE text LIKE 'Late%'").fetchone()[0] == 0
    finally:
        restored.close()


def test_limits_and_unknown_schema_refuse_without_changes(pair):
    source, target, _ = pair
    with sql.connection(source.store) as db:
        item = db.execute("SELECT * FROM memory_items WHERE user_id=?", (source.user.id,)).fetchone()
        for owner in (source, target):
            for i in range(memory.MAX_ITEMS // 2):
                db.execute("INSERT INTO memory_items(user_id,id,kind,title,text,status,updated,origin,source) VALUES(?,?,?,?,?,?,?,?,?)",
                           (owner.user.id, f"m_{i:016x}", item["kind"], item["title"], item["text"], item["status"], item["updated"], item["origin"], item["source"]))
    before = files(source.store.root)
    with pytest.raises(merges.MergeProblem, match="volumineuse"):
        merges.merge(source.store, source.user.id, target.user.id)
    assert files(source.store.root) == before
    with sql.connection(source.store) as db:
        db.execute("UPDATE memory_schema SET version=99")
    before = files(source.store.root)
    with pytest.raises(merges.MergeProblem, match="Version"):
        merges.merge(source.store, source.user.id, target.user.id)
    assert files(source.store.root) == before


def test_symlinks_and_existing_metadata_are_never_removed_or_followed(pair, tmp_path):
    source, target, _ = pair
    original = tmp_path / "outside.txt"
    original.write_text("KEEP OUTSIDE")
    folder = source.store.root / source.key
    link = folder / "link"
    link.symlink_to(original)
    with pytest.raises(merges.MergeProblem, match="régulier"):
        merges.merge(source.store, source.user.id, target.user.id)
    assert original.read_text() == "KEEP OUTSIDE"
    link.unlink()
    metadata = folder / "merge-source.json"
    metadata.write_text("KEEP EXISTING")
    with pytest.raises(merges.MergeProblem, match="déjà présentes"):
        merges.merge(source.store, source.user.id, target.user.id)
    assert metadata.read_text() == "KEEP EXISTING"
    assert source.registry.get(source.user.id).id == source.user.id


@pytest.mark.parametrize("stage", ["copy", "sql"])
def test_failures_restore_live_folders_and_keep_sql_records(pair, monkeypatch, stage):
    source, target, _ = pair
    source_folder, target_folder = source.store.root / source.key, source.store.root / target.key
    before_source, before_target = files(source_folder), files(target_folder)
    if stage == "copy":
        original = merges.shutil.copy2
        def disk_failure(src, dst, **kwargs):
            if Path(dst).parent == target_folder / "logs":
                Path(dst).write_text("PARTIAL")
                raise OSError("synthetic full disk")
            return original(src, dst, **kwargs)
        monkeypatch.setattr(merges.shutil, "copy2", disk_failure)
    else:
        original = merges.merge_rows
        def sql_failure(*args):
            original(*args)
            raise sqlite3.OperationalError("synthetic commit failure")
        monkeypatch.setattr(merges, "merge_rows", sql_failure)
    with pytest.raises(merges.MergeProblem):
        merges.merge(source.store, source.user.id, target.user.id)
    assert files(source_folder) == before_source
    assert files(target_folder) == before_target
    assert source.registry.get(source.user.id).id == source.user.id
    assert len(source.store.read(target.key)["items"]) == 1
    status = next((source.store.root / "merge-backups").glob("*/status.json"))
    assert json.loads(status.read_text())["status"] == "rolled_back"


def test_cli_apply_requires_stopped_services_and_unknown_db_never_created(pair, monkeypatch, capsys, tmp_path):
    source, target, _ = pair
    argv = ["--source", str(source.user.id), "--target", str(target.user.id), "--apply"]
    monkeypatch.setattr(cli.subprocess, "run", Mock(return_value=SimpleNamespace(stdout="active\n")))
    assert cli.main(argv) == 1
    assert "Arrêter" in capsys.readouterr().err
    assert source.registry.get(source.user.id)
    monkeypatch.setattr(cli.subprocess, "run", Mock(return_value=SimpleNamespace(stdout="inactive\n")))
    assert cli.main(argv) == 0
    assert "Fusion terminée" in capsys.readouterr().out
    root = tmp_path / "does-not-exist"
    assert cli.main(["--list", "--data-dir", str(root)]) == 1
    assert not root.exists()


def test_legacy_json_preview_is_readonly_and_apply_backs_up_before_import(pair):
    source, target, _ = pair
    store = source.store
    folder = store.root / source.key
    with sql.connection(store) as db:
        db.execute("DELETE FROM memory_events WHERE user_id=?", (source.user.id,))
        db.execute("DELETE FROM memory_items WHERE user_id=?", (source.user.id,))
        db.execute("DELETE FROM memory_state WHERE user_id=?", (source.user.id,))
    legacy = {"version": 1, "revision": 4, "language": "en", "items": [{
        "id": "m_0123456789abcdef", "kind": "note", "title": "Legacy note", "text": "Full legacy content",
        "status": "", "updated": "2026-10-31"}]}
    original = json.dumps(legacy).encode()
    (folder / "memory.json").write_bytes(original)
    before = files(store.root)
    assert merges.preview(store, source.user.id, target.user.id)["items_after"] == 2
    assert files(store.root) == before
    result = merges.merge(store, source.user.id, target.user.id)
    backup, archive = Path(result["backup"]), Path(result["archive"])
    assert (backup / "source" / "memory.json").read_bytes() == original
    assert (archive / "memory.before-sqlite.json").read_bytes() == original
    assert any(r["text"] == "Full legacy content" for r in store.read(target.key)["items"])


def test_pending_queue_limit_and_unfinished_merge_are_refused(pair):
    source, target, _ = pair
    for _ in range(auto.MAX_PENDING):
        source.record("user", "An eligible source statement about my hobbies.")
    before = files(source.store.root)
    with pytest.raises(merges.MergeProblem, match="48"):
        merges.merge(source.store, source.user.id, target.user.id)
    assert files(source.store.root) == before
    with sql.connection(source.store) as db:
        db.execute("DELETE FROM memory_events WHERE user_id=?", (source.user.id,))
    unfinished = source.store.root / "merge-backups" / "interrupted"
    unfinished.mkdir(parents=True)
    (unfinished / "status.json").write_text('{"status":"started"}')
    with pytest.raises(merges.MergeProblem, match="inachevée"):
        merges.merge(source.store, source.user.id, target.user.id)
    assert source.registry.get(source.user.id).id == source.user.id


def test_completed_status_failure_does_not_undo_committed_merge(pair, monkeypatch):
    source, target, _ = pair
    original = merges.write_json
    def fail_status(path, value):
        if value.get("status") == "complete":
            raise OSError("synthetic fsync failure")
        original(path, value)
    monkeypatch.setattr(merges, "write_json", fail_status)
    with pytest.raises(merges.MergeProblem, match="Fusion enregistrée"):
        merges.merge(source.store, source.user.id, target.user.id)
    assert source.registry.resolve("Jullian").id == target.user.id
    assert len(source.store.read(target.key)["items"]) == 2
    assert not (source.store.root / source.key).exists()


def test_missing_target_language_is_filled_without_losing_sources_or_other_types(pair):
    source, target, _ = pair
    with sql.connection(source.store) as db:
        db.execute("UPDATE memory_state SET language='en' WHERE user_id=?", (source.user.id,))
    for kind in ("preference", "note", "list", "task"):
        source.store.apply(source.key, {"operation": "save", "kind": kind,
                           "title": kind, "text": "Original " + kind}, {})
    result = merges.merge(source.store, source.user.id, target.user.id)
    assert result["items_after"] == 6
    records = source.store.read(target.key)
    assert records["language"] == "en"
    assert {r["kind"] for r in records["items"]} == memory.KINDS
    assert any(r["kind"] == "list" and r["status"] == "open" for r in records["items"])


def test_failed_backup_never_moves_or_deletes_live_data(pair, monkeypatch):
    source, target, _ = pair
    before = files(source.store.root)
    monkeypatch.setattr(merges, "backup_profiles", Mock(side_effect=OSError("synthetic full disk")))
    with pytest.raises(merges.MergeProblem):
        merges.merge(source.store, source.user.id, target.user.id)
    assert files(source.store.root) == before
    assert source.registry.get(source.user.id).id == source.user.id


def test_profile_changed_after_preview_is_rejected_before_backup(pair, monkeypatch):
    source, target, _ = pair
    preview = merges.preview
    def changed(*args):
        plan = preview(*args)
        with source.registry.connection() as db:
            db.execute("UPDATE users SET revision=revision+1 WHERE id=?", (target.user.id,))
        return plan
    monkeypatch.setattr(merges, "preview", changed)
    with pytest.raises(merges.MergeProblem, match="Profil modifié"):
        merges.merge(source.store, source.user.id, target.user.id)
    assert source.registry.get(source.user.id).id == source.user.id
    assert not (source.store.root / "merge-backups").exists()
    assert len(source.store.read(target.key)["items"]) == 1


def test_highest_retired_id_is_not_reused_even_without_legacy_allocator(pair):
    _, target, _ = pair
    newest = sessions.MemorySession("Newest typo", model=Mock())
    try:
        with newest.registry.connection() as db:
            db.execute("DROP TABLE user_identity_sequence")
        merges.merge(newest.store, newest.user.id, target.user.id)
        assert newest.registry.resolve("Fresh user").id > newest.user.id
        assert newest.registry.resolve("Newest typo").id == target.user.id
    finally:
        newest.close()


def test_legacy_reserved_default_alias_is_retired_not_transferred(pair):
    source, target, _ = pair
    with source.registry.connection() as db:
        db.execute("INSERT INTO user_aliases VALUES('minitelgpt',?)", (source.user.id,))
    merges.merge(source.store, source.user.id, target.user.id)
    assert source.registry.resolve("MinitelGPT").id == 1
    assert source.registry.resolve("Jullian").id == target.user.id
    assert source.store.read("shared")["items"] == []
