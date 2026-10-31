"""Manual ordinary-memory edits. No model, credentials or private journal access."""

try:
    from . import memory_database as sql
    from . import persistent_memory as memory
    from .user_registry import Registry
except ImportError:
    import memory_database as sql
    import persistent_memory as memory
    from user_registry import Registry


def change(store, user_id, profile_revision, memory_revision, action, identifier="",
           kind="fact", title="", text="", publish=False, share=None):
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
                       admin=True, publish=publish, expected_user=(user_id, profile_revision), share=share)


def share_facts(store, user_id, profile_revision, memory_revision, enabled, identifier=None):
    """Publish/retract reviewed current facts; never turn future learning public."""
    registry = Registry(store.root)
    if user_id == 1:
        raise memory.MemoryProblem("Utilisez la publication du foyer pour MinitelGPT.")
    with registry.connection() as db:
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if user is None or user["revision"] != profile_revision:
            raise memory.MemoryProblem("Profil modifié. Rechargez la page.")
        key = user["memory_key"]
    with store.locked(key), sql.connection(store) as db:
        current = sql.owner(db, key)
        state = db.execute("SELECT revision FROM memory_state WHERE user_id=?", (user_id,)).fetchone()
        if (current is None or current["id"] != user_id or current["revision"] != profile_revision
                or state is None or state[0] != memory_revision):
            raise memory.MemoryProblem("Profil ou mémoire modifié. Rechargez la page avant de partager.")
        rows = db.execute("SELECT * FROM memory_items WHERE user_id=?" + (" AND id=?" if identifier is not None else ""),
                          (user_id, identifier) if identifier is not None else (user_id,)).fetchall()
        if identifier is not None and (not rows or rows[0]["kind"] != "fact"):
            raise memory.MemoryProblem("Fait personnel introuvable.")
        for row in rows:
            item = dict(row)
            reviewed = enabled and item["kind"] == "fact" and item["status"] != "done"
            db.execute("UPDATE memory_items SET shared_hash=? WHERE user_id=? AND id=?",
                       (sql.fingerprint(item) if reviewed else None, user_id, item["id"]))
        # Visibility is not a content edit: keep eligible statements, but fence old jobs.
        db.execute("""UPDATE memory_state SET revision=revision+1,lease_until=0,lease_token='',
 retry_after=0,last_status='sharing' WHERE user_id=?""", (user_id,))
