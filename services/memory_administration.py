"""Manual ordinary-memory edits. No model, credentials or private journal access."""

try:
    from . import persistent_memory as memory
    from .user_registry import Registry
except ImportError:
    import persistent_memory as memory
    from user_registry import Registry


def change(store, user_id, profile_revision, memory_revision, action, identifier="",
           kind="fact", title="", text="", publish=False):
    registry = Registry(store.root)
    with registry.connection() as db:
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None or row["revision"] != profile_revision:
            raise memory.MemoryProblem("Profil modifié ou supprimé. Rechargez la page.")
        key = row["memory_key"]

    def guard():
        with registry.connection() as db:
            current = db.execute("SELECT revision,memory_key FROM users WHERE id=?", (user_id,)).fetchone()
            if current is None or current["revision"] != profile_revision or current["memory_key"] != key:
                raise memory.MemoryProblem("Profil modifié ou supprimé. Rechargez la page.")

    if action == "add":
        proposal = {"operation": "save", "kind": kind, "title": title, "text": text}
    elif action in {"edit", "delete"}:
        proposal = {"operation": "replace" if action == "edit" else "remove",
                    "ids": [identifier], "title": title, "text": text}
    else:
        raise memory.MemoryProblem("Action mémoire invalide.")
    return store.apply(key, proposal, {"revision": memory_revision}, guard=guard,
                       admin=True, publish=publish, expected_user=(user_id, profile_revision))
