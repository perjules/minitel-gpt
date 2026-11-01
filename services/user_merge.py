"""Exceptional offline profile merge. No model/API calls or privilege transfer."""

import copy
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
from contextlib import ExitStack, closing, contextmanager

from . import memory_database as sql
from . import persistent_memory as memory
from .memory_automation import MAX_PENDING
from .user_registry import Registry


class MergeProblem(memory.MemoryProblem):
    pass


@contextmanager
def readonly(store):
    path = store.root / "users.sqlite3"
    if path.is_symlink() or not path.is_file():
        raise MergeProblem("Base utilisateurs absente ou invalide; aucun profil créé.")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA user_version").fetchone()[0] != 2:
            raise MergeProblem("Version du registre inconnue. Mettre l’application à jour avant la fusion.")
        db.execute("BEGIN")
        yield db


def profiles(store):
    with readonly(store) as db:
        return [dict(r) for r in db.execute("SELECT * FROM users ORDER BY id")]


def checked_folder(store, key):
    if not re.fullmatch(r"users/[a-z0-9-]{1,50}", key):
        raise MergeProblem("Seuls deux profils nommés peuvent être fusionnés.")
    folder = store.root / key
    if folder.resolve() != folder:
        raise MergeProblem("Chemin symbolique interdit; aucun fichier déplacé.")
    if folder.exists() and not folder.is_dir():
        raise MergeProblem("Dossier de profil invalide; fusion refusée.")
    if folder.exists():
        for path in (folder, *folder.rglob("*")):
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise MergeProblem("Lien ou fichier non régulier dans un profil; fusion refusée.")
    return folder


def snapshot(store, db, identifier):
    user = db.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone()
    if user is None:
        raise MergeProblem("Identifiant utilisateur inconnu; aucun profil créé.")
    if identifier == 1:
        raise MergeProblem("Le profil MinitelGPT et la mémoire du foyer sont exclus des fusions.")
    folder = checked_folder(store, user["memory_key"])
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "memory_schema" in tables:
        version = db.execute("SELECT version FROM memory_schema").fetchone()
        if version is None or version[0] not in {1, 2, sql.SQL_MEMORY_SCHEMA}:
            raise MergeProblem("Version de mémoire inconnue; fusion refusée.")
    state = (db.execute("SELECT * FROM memory_state WHERE user_id=?", (identifier,)).fetchone()
             if "memory_state" in tables else None)
    sharing = "memory_schema" in tables and version[0] >= 2
    data = sql.read_data(db, identifier, sharing=sharing) if state else store._read_legacy(folder)
    events = ([dict(r) for r in db.execute("SELECT * FROM memory_events WHERE user_id=? ORDER BY id", (identifier,))]
              if "memory_events" in tables else [])
    welcome = db.execute("SELECT * FROM user_onboarding WHERE user_id=?", (identifier,)).fetchone()
    return {"user": dict(user), "memory": data, "pending": events,
            "sessions": (db.execute("SELECT count(*) FROM conversation_sessions WHERE user_id=?", (identifier,)).fetchone()[0]
                         if "conversation_sessions" in tables else 0),
            "welcome": dict(welcome) if welcome else None,
            "aliases": [r[0] for r in db.execute("SELECT alias FROM user_aliases WHERE user_id=? ORDER BY alias", (identifier,))],
            "journal_archived": (folder / "journal.md.enc").exists(),
            "drafts_archived": len(list(folder.glob("mail-draft-*.json")))}


def combine(source, target):
    data = copy.deepcopy(target["memory"])
    data["revision"] += 1
    if not data.get("language") and source["memory"].get("language"):
        data["language"] = source["memory"]["language"]
    used = {r["id"] for r in data["items"]}
    mapping = {}
    for record in source["memory"]["items"]:
        item = copy.deepcopy(record)
        original = item["id"]
        while item["id"] in used:
            item["id"] = "m_" + secrets.token_hex(8)
        mapping[original] = item["id"]
        used.add(item["id"])
        data["items"].append(item)
    if len(data["items"]) > memory.MAX_ITEMS or len(json.dumps(data, ensure_ascii=False).encode()) > memory.MAX_FILE:
        raise MergeProblem("Mémoire fusionnée trop volumineuse (256 éléments / 1 Mio). Aucun élément supprimé; nettoyer d’abord.")
    if len(source["pending"]) + len(target["pending"]) > MAX_PENDING:
        raise MergeProblem("Plus de 48 énoncés en attente. Laisser terminer l’apprentissage avant de fusionner.")
    return data, mapping


def preview(store, source_id, target_id):
    if source_id == target_id:
        raise MergeProblem("Source et destination doivent être différentes.")
    with readonly(store) as db:
        source, target = snapshot(store, db, source_id), snapshot(store, db, target_id)
    data, _ = combine(source, target)
    return {"source": source, "target": target, "items_after": len(data["items"]),
            "pending_after": len(source["pending"]) + len(target["pending"])}


def write_json(path, value):
    # Fixed private metadata, never passwords or decrypted journal content.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        sync_directory(path.parent)
    finally:
        if temporary:
            os.unlink(temporary)


def sync_directory(folder):
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def backup_profiles(store, source_folder, target_folder, manifest):
    backups = store.root / "merge-backups"
    if backups.resolve() != backups:
        raise MergeProblem("Dossier de sauvegarde symbolique interdit.")
    backups.mkdir(mode=0o700, exist_ok=True)
    for previous in backups.glob("*/status.json"):
        if previous.is_symlink():
            raise MergeProblem("Sauvegarde invalide; vérifier le dossier merge-backups.")
        state = json.loads(previous.read_text())
        if state.get("status") not in {"complete", "rolled_back"}:
            raise MergeProblem("Une fusion précédente est inachevée. Voir merge-backups avant de recommencer.")
    folder = backups / (memory.now().replace(":", "-") + "-" + secrets.token_hex(4))
    folder.mkdir(mode=0o700)
    write_json(folder / "status.json", {**manifest, "status": "started"})
    # Separate reader: backing up the connection holding BEGIN IMMEDIATE would wait.
    path = folder / "users.sqlite3"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    with readonly(store) as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)
    for label, profile in (("source", source_folder), ("target", target_folder)):
        shutil.copytree(profile, folder / label)
    for copied in folder.rglob("*"):
        if copied.is_file():
            with copied.open("rb") as stream:
                os.fsync(stream.fileno())
    for directory in sorted((p for p in folder.rglob("*") if p.is_dir()), reverse=True):
        sync_directory(directory)
    sync_directory(folder)
    return folder


def merge_rows(db, source, target, data, mapping):
    source_id, target_id = source["user"]["id"], target["user"]["id"]
    sql.put_items(db, target_id, data["items"])
    for row in db.execute("SELECT * FROM memory_items WHERE user_id=?", (source_id,)).fetchall():
        db.execute("""UPDATE memory_items SET embedding=?,embedding_model=?,embedding_hash=?
 WHERE user_id=? AND id=?""", (row["embedding"], row["embedding_model"], row["embedding_hash"], target_id, mapping[row["id"]]))
    barrier = db.execute("SELECT barrier FROM memory_state WHERE user_id=?", (target_id,)).fetchone()[0] + 1
    db.execute("""UPDATE conversation_messages SET learning='superseded' WHERE learning='pending'
 AND session_id IN (SELECT c.id FROM conversation_sessions c JOIN memory_state s ON s.user_id=c.user_id
 WHERE c.user_id IN (?,?) AND conversation_messages.barrier!=s.barrier)""", (source_id, target_id))
    db.execute("""UPDATE conversation_messages SET barrier=? WHERE learning='pending'
 AND session_id IN (SELECT id FROM conversation_sessions WHERE user_id IN (?,?))""", (barrier, source_id, target_id))
    db.execute("""DELETE FROM memory_events WHERE user_id IN (?,?)
 AND message_id IN (SELECT id FROM conversation_messages WHERE learning!='pending')""", (source_id, target_id))
    db.execute("UPDATE memory_events SET user_id=?,barrier=? WHERE user_id IN (?,?)", (target_id, barrier, source_id, target_id))
    db.execute("""UPDATE memory_state SET revision=?,language=?,barrier=?,ready=?,
 lease_until=0,lease_token='',retry_after=0,last_status='merged' WHERE user_id=?""",
               (data["revision"], data.get("language"), barrier,
                bool(source["pending"] or target["pending"]), target_id))
    # The target's welcome checklist survives; old ephemeral dialogue is archived/cleared.
    db.execute("""UPDATE user_onboarding SET revision=revision+1,barrier=?,history='[]',asked=''
 WHERE user_id=?""", (barrier, target_id))
    db.execute("UPDATE users SET revision=revision+1 WHERE id=?", (target_id,))
    # Transfer ownership before deleting the source (FK cascades), retire old
    # live sessions and keep original IDs so memory citations remain meaningful.
    db.execute("""UPDATE conversation_sessions SET user_id=?,profile_revision=(SELECT revision FROM users WHERE id=?),
 closed_at=CASE WHEN status='open' THEN last_activity ELSE closed_at END,
 close_reason=CASE WHEN status='open' THEN 'profile_merge' ELSE close_reason END,
 status=CASE WHEN status='open' THEN 'interrupted' ELSE status END WHERE user_id IN (?,?)""",
               (target_id, target_id, source_id, target_id))
    for entry in db.execute("SELECT * FROM conversation_imports WHERE user_id=?", (source_id,)).fetchall():
        db.execute("""INSERT INTO conversation_imports(user_id,source,session_id,offset,status,size,mtime,errors,skip_private)
 VALUES(?,?,?,?,?,?,?,?,?)""", (target_id, f"merged-{source_id}-" + entry["source"], entry["session_id"],
                             entry["offset"], entry["status"], entry["size"], entry["mtime"], entry["errors"], entry["skip_private"]))
    # Old duplicate profiles could own this alias; never move the reserved
    # default selector onto another named profile during a repair.
    db.execute("DELETE FROM user_aliases WHERE user_id=? AND alias='minitelgpt'", (source_id,))
    db.execute("UPDATE user_aliases SET user_id=? WHERE user_id=?", (target_id, source_id))
    Registry.retain_user_ids(db)
    for table in ("user_onboarding", "user_gmail_access", "user_pronote_children",
                  "memory_events", "memory_items", "memory_state"):
        db.execute(f"DELETE FROM {table} WHERE user_id=?", (source_id,))
    db.execute("DELETE FROM users WHERE id=?", (source_id,))


def merge(store, source_id, target_id):
    """Call only with app services stopped. Failures roll back SQL and created paths."""
    before = preview(store, source_id, target_id)
    expected = {p["user"]["id"]: p["user"] for p in (before["source"], before["target"])}
    registry = Registry(store.root)
    backup, manifest, moved = None, {}, False
    copied = []
    source_folder = target_folder = archive = metadata = None
    committed = metadata_created = False
    with ExitStack() as locks:
        try:
            folders = {identifier: locks.enter_context(store.locked(expected[identifier]["memory_key"]))
                       for identifier in sorted(expected)}
            source_folder, target_folder = folders[source_id], folders[target_id]
            # Backup precedes even the existing lazy JSON-to-SQLite compatibility path.
            with registry.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                for identifier, user in expected.items():
                    current = db.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone()
                    if current is None or dict(current) != user:
                        raise MergeProblem("Profil modifié depuis l’aperçu. Recommencer la vérification.")
                checked_folder(store, expected[source_id]["memory_key"])
                checked_folder(store, expected[target_id]["memory_key"])
                manifest = {"source_id": source_id, "target_id": target_id,
                            "source_key": expected[source_id]["memory_key"], "target_key": expected[target_id]["memory_key"]}
                backup = backup_profiles(store, source_folder, target_folder, manifest)
            # These initialize/import memory only, never identities, permissions or models.
            sql.read(store, source_folder)
            sql.read(store, target_folder)
            with registry.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                source, target = snapshot(store, db, source_id), snapshot(store, db, target_id)
                if source["user"] != expected[source_id] or target["user"] != expected[target_id]:
                    raise MergeProblem("Profil modifié pendant la préparation; fusion refusée.")
                data, mapping = combine(source, target)
                parent = target_folder / "merged-profiles"
                parent.mkdir(exist_ok=True)
                archive = parent / str(source_id)
                if archive.exists():
                    raise MergeProblem("Une archive existe déjà pour cette source. Vérifier la fusion précédente.")
                metadata = source_folder / "merge-source.json"
                if metadata.exists():
                    raise MergeProblem("Métadonnées de fusion déjà présentes; vérifier les archives.")
                metadata_created = True
                write_json(metadata, {**source, "target_id": target_id, "id_mapping": mapping})
                logs = target_folder / "logs"
                logs.mkdir(exist_ok=True)
                for path in sorted((source_folder / "logs").glob("*.jsonl")):
                    destination = logs / f"merged-{source_id}-{path.name}"
                    if destination.exists():
                        raise MergeProblem("Archive de conversation déjà présente; aucun écrasement.")
                    copied.append(destination)
                    shutil.copy2(path, destination)
                    with destination.open("rb") as stream:
                        os.fsync(stream.fileno())
                sync_directory(logs)
                os.rename(source_folder, archive)
                moved = True
                sync_directory(source_folder.parent)
                sync_directory(parent)
                sync_directory(target_folder)
                merge_rows(db, source, target, data, mapping)
            committed = True
            write_json(backup / "status.json", {**manifest, "status": "complete"})
            return {"backup": str(backup), "archive": str(archive),
                    "items_after": len(data["items"]), "pending_after": len(source["pending"]) + len(target["pending"])}
        except (Exception, KeyboardInterrupt) as error:
            # A signal can arrive just after SQLite committed but before the flag assignment.
            if backup and not committed:
                with readonly(store) as db:
                    committed = db.execute("SELECT 1 FROM users WHERE id=?", (source_id,)).fetchone() is None
            # No full-database restore here: preserve other profiles and compatibility imports.
            if not committed:
                try:
                    if moved:
                        os.rename(archive, source_folder)
                    if metadata_created:
                        metadata.unlink(missing_ok=True)
                    for path in copied:
                        path.unlink(missing_ok=True)
                    if moved:
                        sync_directory(source_folder.parent)
                        sync_directory(archive.parent)
                    if copied:
                        sync_directory(target_folder / "logs")
                    if backup:
                        write_json(backup / "status.json", {**manifest, "status": "rolled_back"})
                except OSError:
                    raise MergeProblem("Fusion inachevée. Garder les services arrêtés et restaurer la sauvegarde merge-backups.") from None
            if committed:
                raise MergeProblem("Fusion enregistrée, statut de sauvegarde non confirmé. Vérifier merge-backups avant toute autre fusion.") from None
            if isinstance(error, memory.MemoryProblem):
                raise
            raise MergeProblem("Fusion impossible; consulter la sauvegarde merge-backups avant de redémarrer.") from None
