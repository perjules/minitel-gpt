"""Read-only inspection/export helpers. No model calls or transcript ingestion."""

import json
import re
from pathlib import Path

try:
    from . import memory_database as sql
    from . import memory_vectors
    from . import persistent_memory as memory
    from .user_registry import Registry
except ImportError:
    import memory_database as sql
    import memory_vectors
    import persistent_memory as memory
    from user_registry import Registry


def profile(store, user):
    data = store.read(user["memory_key"])
    with sql.connection(store) as db:
        state = dict(db.execute("SELECT * FROM memory_state WHERE user_id=?", (user["id"],)).fetchone())
        rows = db.execute("SELECT * FROM memory_items WHERE user_id=?", (user["id"],)).fetchall()
        indexed = sum(row["embedding"] is not None and row["embedding_model"] == memory_vectors.MODEL
                      and row["embedding_hash"] == sql.fingerprint(dict(row)) for row in rows if row["status"] != "done")
        pending = db.execute("SELECT count(*) FROM memory_events WHERE user_id=?", (user["id"],)).fetchone()[0]
    return {"user_id": user["id"], "name": user["display_name"], "enabled": bool(user["enabled"]),
            "memory_key": user["memory_key"], "count": len(data["items"]),
            "bytes": sum(len(r["text"].encode()) for r in data["items"]),
            "updated": max((r["updated"] for r in data["items"]), default="—"),
            "indexed": indexed, "indexable": sum(r["status"] != "done" for r in data["items"]),
            "pending": pending, "auto_enabled": bool(state["auto_enabled"]),
            "processing": state["last_status"], "retry_after": state["retry_after"], "data": data}


def inventory(store):
    results = []
    for user in Registry(store.root).users():
        try:
            results.append(profile(store, user))
        except memory.MemoryProblem as error:
            results.append({"user_id": user["id"], "name": user["display_name"],
                            "enabled": bool(user["enabled"]), "memory_key": user["memory_key"], "error": str(error)})
    return results


def unassigned(store, profiles, contents=False):
    known = {p["memory_key"] for p in profiles}
    result = []
    for path in sorted((store.root / "users").glob("*/memory.json")):
        key = str(path.parent.relative_to(store.root))
        if key in known or not re.fullmatch(r"users/[a-z0-9-]{1,50}", key):
            continue
        item = {"memory_key": key}
        if contents:
            try:
                # folder() checks links/path traversal; _read_legacy validates the entire file.
                item["data"] = store._read_legacy(store.folder(key))
            except memory.MemoryProblem as error:
                item["error"] = str(error)
        result.append(item)
    return result


def export(store, user_id=None):
    profiles = inventory(store)
    if user_id is not None:
        profiles = [p for p in profiles if p["user_id"] == user_id]
        if not profiles:
            raise KeyError(user_id)
    legacy = unassigned(store, profiles, contents=True) if user_id is None else []
    return {"format": "3615-memory-export", "version": 1, "exported_at": memory.now(),
            "complete": not any(p.get("error") for p in [*profiles, *legacy]),
            "profiles": profiles, "unassigned_legacy": legacy}


def encode_export(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def storage_paths(store):
    return {"database": str(store.root / "users.sqlite3"),
            "archives": str(store.root / "users" / "<profil>" / "logs"),
            "default_archives": str(store.root / "shared" / "logs"),
            "legacy_backup": str(Path("<profil>") / "memory.before-sqlite.json")}
