"""Memory tables in users.sqlite3. Short transactions; no network inside locks."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager

try:
    from . import persistent_memory as memory
    from .gmail_auth import GmailError
except ImportError:
    import persistent_memory as memory
    from gmail_auth import GmailError

SQL_MEMORY_SCHEMA = 1


@contextmanager
def connection(store):
    # Lazy import: the identity registry uses MemoryStore for its root validation.
    try:
        from .user_registry import Registry
    except ImportError:
        from user_registry import Registry
    try:
        with Registry(store.root).connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS memory_schema(version INTEGER NOT NULL)")
            version = db.execute("SELECT version FROM memory_schema").fetchone()
            if version is None:
                statements = (
                    """CREATE TABLE memory_state(user_id INTEGER PRIMARY KEY REFERENCES users(id),
 revision INTEGER NOT NULL, language TEXT, barrier INTEGER NOT NULL DEFAULT 0,
 auto_enabled INTEGER NOT NULL, ready INTEGER NOT NULL DEFAULT 0,
 lease_until REAL NOT NULL DEFAULT 0, lease_token TEXT NOT NULL DEFAULT '',
 retry_after REAL NOT NULL DEFAULT 0, last_status TEXT NOT NULL DEFAULT 'idle')""",
                    """CREATE TABLE memory_items(user_id INTEGER NOT NULL REFERENCES users(id),
 id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
 status TEXT NOT NULL, updated TEXT NOT NULL, origin TEXT NOT NULL,
 source TEXT NOT NULL DEFAULT '[]', embedding BLOB, embedding_model TEXT,
 embedding_hash TEXT, PRIMARY KEY(user_id,id))""",
                    """CREATE TABLE memory_events(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL
 REFERENCES users(id), session_id TEXT NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL,
 barrier INTEGER NOT NULL)""",
                    "CREATE INDEX memory_events_owner ON memory_events(user_id,id)",
                    "INSERT INTO memory_schema VALUES(1)",
                )
                for statement in statements:
                    db.execute(statement)
            elif version[0] != SQL_MEMORY_SCHEMA:
                raise memory.MemoryProblem("Version de mémoire SQL non prise en charge.")
            yield db
    except (GmailError, sqlite3.Error, OSError):
        raise memory.MemoryProblem("Stockage mémoire SQL indisponible; aucune donnée effacée.") from None


def owner(db, key):
    return db.execute("SELECT * FROM users WHERE memory_key=?", (key,)).fetchone()


def embedding_text(item):
    return item["kind"] + "\n" + item["title"] + "\n" + item["text"]


def fingerprint(item):
    return hashlib.sha256(embedding_text(item).encode()).hexdigest()


def put_items(db, user_id, items):
    ids = {item["id"] for item in items}
    for row in db.execute("SELECT id FROM memory_items WHERE user_id=?", (user_id,)).fetchall():
        if row[0] not in ids:
            db.execute("DELETE FROM memory_items WHERE user_id=? AND id=?", (user_id, row[0]))
    for item in items:
        previous = db.execute("SELECT kind,title,text FROM memory_items WHERE user_id=? AND id=?",
                              (user_id, item["id"])).fetchone()
        changed = previous is not None and fingerprint(dict(previous)) != fingerprint(item)
        db.execute("""INSERT INTO memory_items(user_id,id,kind,title,text,status,updated,origin,source)
 VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,id) DO UPDATE SET kind=excluded.kind,
 title=excluded.title,text=excluded.text,status=excluded.status,updated=excluded.updated,
 origin=excluded.origin,source=excluded.source""",
                   (user_id, item["id"], item["kind"], item["title"], item["text"],
                    item["status"], item.get("updated", memory.now()),
                    item.get("origin", "explicit"), json.dumps(item.get("source", []), ensure_ascii=False)))
        if changed:
            db.execute("UPDATE memory_items SET embedding=NULL,embedding_model=NULL,embedding_hash=NULL WHERE user_id=? AND id=?",
                       (user_id, item["id"]))


def read_data(db, user_id):
    state = db.execute("SELECT * FROM memory_state WHERE user_id=?", (user_id,)).fetchone()
    data = {"version": 1, "revision": state["revision"], "items": []}
    if state["language"]:
        data["language"] = state["language"]
    for row in db.execute("SELECT id,kind,title,text,status,updated,origin,source FROM memory_items WHERE user_id=? ORDER BY rowid", (user_id,)):
        item = dict(row)
        item["source"] = json.loads(item["source"])
        data["items"].append(item)
    return data


def seal_legacy(store, folder, user_id):
    """Keep original bytes; make old JSON-only code fail visibly instead of forking data."""
    path = folder / "memory.json"
    if not path.exists():
        store._write_json(folder, {"version": 2, "storage": "users.sqlite3", "user_id": user_id})
        return
    if path.stat().st_size > memory.MAX_FILE:
        raise memory.MemoryProblem("Ancien fichier mémoire trop volumineux; fichier conservé.")
    raw = path.read_bytes()
    try:
        if json.loads(raw).get("version") == 2:
            return
    except (ValueError, AttributeError):
        raise memory.MemoryProblem("Ancien fichier mémoire illisible. Les données existantes sont conservées.") from None
    backup = folder / "memory.before-sqlite.json"
    if backup.exists():
        if backup.is_symlink() or backup.read_bytes() != raw:
            raise memory.MemoryProblem("Sauvegarde mémoire existante différente; fichiers conservés.")
    else:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    store._write_json(folder, {"version": 2, "storage": "users.sqlite3", "user_id": user_id,
                               "legacy_backup": "memory.before-sqlite.json"})


def read(store, folder):
    key = str(folder.relative_to(store.root))
    with connection(store) as db:
        user = owner(db, key)
        if user is None:
            if (folder / "memory.json").exists():
                raise memory.MemoryProblem("Sélectionnez d'abord le profil pour importer sa mémoire.")
            return {"version": 1, "revision": 0, "items": []}
        state = db.execute("SELECT 1 FROM memory_state WHERE user_id=?", (user["id"],)).fetchone()
        if state is None:
            legacy = store._read_legacy(folder)
            db.execute("INSERT INTO memory_state(user_id,revision,language,auto_enabled) VALUES(?,?,?,?)",
                       (user["id"], legacy["revision"], legacy.get("language"), user["id"] != 1))
            put_items(db, user["id"], [{**item, "origin": "legacy"} for item in legacy["items"]])
        data = read_data(db, user["id"])
    # Only seal after the import transaction has committed. A retry is idempotent.
    seal_legacy(store, folder, user["id"])
    return data


def write(store, folder, data):
    key = str(folder.relative_to(store.root))
    if len(json.dumps(data, ensure_ascii=False).encode()) > memory.MAX_FILE:
        raise memory.MemoryProblem("Mémoire pleine (1 Mio). Aucun élément tronqué ni fichier modifié.")
    with connection(store) as db:
        user = owner(db, key)
        if user is None:
            raise memory.MemoryProblem("Profil mémoire inconnu.")
        state = db.execute("SELECT revision FROM memory_state WHERE user_id=?", (user["id"],)).fetchone()
        if state is None or state[0] != data["revision"] - 1:
            raise memory.MemoryProblem("La mémoire a changé depuis la sélection. Reformulez la demande.")
        put_items(db, user["id"], data["items"])
        db.execute("UPDATE memory_state SET revision=?,language=?,barrier=barrier+1,ready=0 WHERE user_id=?",
                   (data["revision"], data.get("language"), user["id"]))
        # Explicit saves/corrections/forgetting override every older queued extraction.
        db.execute("DELETE FROM memory_events WHERE user_id=?", (user["id"],))
