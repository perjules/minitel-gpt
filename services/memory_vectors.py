"""Small exact vector scan. SQLite owns the text; embeddings are rebuildable."""

import logging
import math
import struct
import time

import requests

try:
    from . import memory_database as sql
    from . import persistent_memory as memory
    from .gmail_auth import GmailError
except ImportError:
    import memory_database as sql
    import persistent_memory as memory
    from gmail_auth import GmailError

MODEL = "mistral-embed"
DIMENSIONS = 1024
MIN_SIMILARITY = 0.55
log = logging.getLogger(__name__)


class Embeddings:
    def __init__(self, key):
        self.key = key

    def embed(self, texts, deadline=None):
        try:
            key = self.key()
        except (GmailError, KeyError, TypeError):
            raise memory.MemoryProblem(
                "Configuration Mistral indisponible pour la mémoire."
            ) from None
        if not isinstance(key, str) or not key or key == "...":
            raise memory.MemoryProblem("Clé Mistral indisponible pour la mémoire.")
        if not 0 < len(texts) <= 16 or any(
            not isinstance(t, str) or not t.strip() for t in texts
        ):
            raise memory.MemoryProblem("Lot de vectorisation invalide.")
        remaining = (deadline or time.monotonic() + 4) - time.monotonic()
        if remaining <= 0:
            raise memory.MemoryProblem("Délai de vectorisation dépassé.")
        try:
            response = requests.post(
                "https://api.mistral.ai/v1/embeddings",
                headers={"Authorization": "Bearer " + key},
                json={"model": MODEL, "input": [t[:6000] for t in texts]},
                timeout=min(4, remaining),
            )
            response.raise_for_status()
            rows = response.json()["data"]
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise ValueError()
            indexed = {}
            for row in rows:
                index = row["index"]
                if (
                    type(index) is not int
                    or not 0 <= index < len(texts)
                    or index in indexed
                ):
                    raise ValueError()
                indexed[index] = unit(row["embedding"])
            return [indexed[i] for i in range(len(texts))]
        except (
            requests.RequestException,
            ValueError,
            KeyError,
            TypeError,
            OverflowError,
        ):
            # Fixed public error: response text can echo private input or credentials.
            raise memory.MemoryProblem(
                "Vectorisation indisponible; recherche locale conservée."
            ) from None


def unit(values):
    if not isinstance(values, (list, tuple)) or len(values) != DIMENSIONS:
        raise ValueError("Invalid embedding dimensions")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError("Invalid embedding values")
    norm = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("Invalid embedding norm")
    return [v / norm for v in values]


def pack(values):
    return struct.pack("<" + "f" * DIMENSIONS, *unit(values))


def unpack(blob):
    if not isinstance(blob, bytes) or len(blob) != 4 * DIMENSIONS:
        raise ValueError("Invalid stored embedding")
    return unit(struct.unpack("<" + "f" * DIMENSIONS, blob))


def current_vector(item):
    if item["embedding_model"] != MODEL or item["embedding_hash"] != sql.fingerprint(
        item
    ):
        return False
    try:
        unpack(item["embedding"])
        return True
    except ValueError:
        return False


def index_pending(store, embeddings, limit=8):
    """One bounded batch. Recheck the source hash after the network call."""
    pending = []
    with sql.connection(store) as db:
        for row in db.execute(
            "SELECT m.* FROM memory_items m JOIN users u ON u.id=m.user_id WHERE u.enabled=1 AND m.status!='done' ORDER BY m.user_id,m.rowid"
        ):
            item = dict(row)
            if not current_vector(item):
                pending.append(item)
                if len(pending) >= limit:
                    break
    if not pending:
        return 0
    vectors = embeddings.embed([sql.embedding_text(item) for item in pending])
    count = 0
    with sql.connection(store) as db:
        for item, vector in zip(pending, vectors, strict=True):
            current = db.execute(
                "SELECT * FROM memory_items WHERE user_id=? AND id=?",
                (item["user_id"], item["id"]),
            ).fetchone()
            if (
                current
                and current["status"] != "done"
                and sql.fingerprint(dict(current)) == sql.fingerprint(item)
            ):
                db.execute(
                    "UPDATE memory_items SET embedding=?,embedding_model=?,embedding_hash=? WHERE user_id=? AND id=?",
                    (
                        pack(vector),
                        MODEL,
                        sql.fingerprint(item),
                        item["user_id"],
                        item["id"],
                    ),
                )
                count += 1
    return count


def ordered(store, key, data, question, recent, embeddings):
    """Return relevance candidates, then the remaining records for explicit planners."""
    lexical = memory.ranked(data["items"], question)
    words = memory.tokens(question)
    selected = [
        item
        for item in lexical
        if item["kind"] == "preference" and item["status"] != "done"
    ][:3]
    scores = []
    try:
        with sql.connection(store) as db:
            user = sql.owner(db, key)
            rows = (
                []
                if user is None or not user["enabled"]
                else db.execute(
                    "SELECT * FROM memory_items WHERE user_id=? AND status!='done' AND embedding_model=? AND embedding IS NOT NULL",
                    (user["id"], MODEL),
                ).fetchall()
            )
        usable = []
        current = {item["id"]: item for item in data["items"]}
        for row in rows:
            item = current.get(row["id"])
            if item is None or row["embedding_hash"] != sql.fingerprint(item):
                continue
            try:
                usable.append((item, unpack(row["embedding"])))
            except ValueError:
                continue
        if usable and memory.redact(question) == question:
            context = "\n".join(
                r["text"][:500]
                for r in recent[-2:]
                if r.get("channel", "chat") == "chat"
            )
            query = question[:2000] + (
                "\nRecent context: " + context if context else ""
            )
            vector = embeddings.embed([query])[0]
            for item, saved in usable:
                score = sum(a * b for a, b in zip(vector, saved, strict=True))
                if score >= MIN_SIMILARITY:
                    scores.append((score, item))
    except memory.MemoryProblem:
        log.info("memory_recall status=local_fallback")
    selected += [
        item for _, item in sorted(scores, key=lambda r: (-r[0], r[1]["id"]))[:6]
    ]
    selected += [
        item
        for item in lexical
        if item["status"] != "done"
        and memory.tokens(item["title"] + " " + item["text"]) & words
    ][:3]
    unique = {item["id"]: item for item in selected}
    selected = list(unique.values())
    return selected, selected + [item for item in lexical if item["id"] not in unique]
