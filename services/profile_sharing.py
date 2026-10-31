"""Read-only recall of explicitly published facts, always attributed to their owner."""

import re

try:
    from . import memory_database as sql
    from . import memory_vectors as vectors
    from . import persistent_memory as memory
except ImportError:
    import memory_database as sql
    import memory_vectors as vectors
    import persistent_memory as memory


FIELDS = """m.id,m.kind,m.title,m.text,m.status,m.updated,m.shared_hash,
 u.id AS owner_id,u.display_name AS owner_name,u.revision AS owner_revision"""


def read(db, viewer_id, *, indexed=False):
    # Filter visibility before ranking. Never read source quotes, logs or credentials.
    rows = db.execute("SELECT " + FIELDS +
                      (",m.embedding,m.embedding_model,m.embedding_hash" if indexed else "") + """
 FROM memory_items m JOIN users u ON u.id=m.user_id WHERE u.enabled=1
 AND u.id NOT IN (1,?) AND m.kind='fact' AND m.status!='done' AND m.shared_hash IS NOT NULL""",
                      (viewer_id,)).fetchall()
    items, indexed_rows = [], []
    for row in rows:
        item = dict(row)
        try:
            memory.clean_text(item["title"], 160, allow_empty=True)
            memory.clean_text(item["text"])
            memory.clean_text(item["owner_name"], 60)
            valid = sql.is_shared(item, item["owner_id"])
        except (memory.MemoryProblem, TypeError, ValueError):
            continue
        if not valid:
            continue
        indexed_rows.append(item)
        items.append({k: v for k, v in item.items()
                      if k not in {"shared_hash", "embedding", "embedding_model", "embedding_hash"}})
    return items, indexed_rows


def recall(store, viewer_id, question, recent, embeddings, cache):
    with sql.connection(store) as db:
        items, rows = read(db, viewer_id, indexed=True)
        owners = {item["owner_id"] for item in items}
        names = [(item["owner_id"], item["owner_name"]) for item in items]
        names += [(r["user_id"], r["alias"]) for r in db.execute("SELECT user_id,alias FROM user_aliases")
                  if r["user_id"] in owners]
    folded = memory.normalized(question)
    mentioned = {owner for owner, name in names if re.search(
        r"(?<!\w)" + re.escape(memory.normalized(name)) + r"(?!\w)", folded)}
    selected, _ = vectors.ordered(store, None, {"items": items}, question, recent, embeddings,
                                  cache=cache, rows=rows, mentioned_owners=mentioned)
    return selected


def revalidate(store, viewer_id, selected):
    with sql.connection(store) as db:
        current, _ = read(db, viewer_id)
    current = {vectors.record_key(item): item for item in current}
    return [item for item in selected if current.get(vectors.record_key(item)) == item]
