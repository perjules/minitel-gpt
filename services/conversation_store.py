"""Ordinary sessions in the existing SQLite database. No network or private modes."""

import json
import os
import re
import secrets
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

try:
    from . import memory_database as sql
    from . import persistent_memory as memory
except ImportError:
    import memory_database as sql
    import persistent_memory as memory

CHANNELS = {"chat", "memory", "onboarding", "theatre", "session"}
PAGE_SIZE = 40
IMPORT_LINES = 100
IMPORT_BYTES = 128 * 1024


def stamp(seconds=None):
    return datetime.fromtimestamp(time.time() if seconds is None else seconds, UTC).isoformat(timespec="microseconds")


def schema(db):
    for statement in (
        """CREATE TABLE IF NOT EXISTS conversation_sessions(
 id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 profile_revision INTEGER NOT NULL,surface TEXT NOT NULL,opened_at TEXT NOT NULL,
 last_activity TEXT NOT NULL,closed_at TEXT,status TEXT NOT NULL,close_reason TEXT NOT NULL DEFAULT '',
 writer TEXT NOT NULL DEFAULT '',legacy_source TEXT NOT NULL DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS conversation_messages(
 id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
 request_id TEXT NOT NULL,at TEXT NOT NULL,role TEXT NOT NULL,channel TEXT NOT NULL,text TEXT NOT NULL,
 barrier INTEGER NOT NULL DEFAULT -1,learning TEXT NOT NULL DEFAULT 'excluded',
 references_json TEXT NOT NULL DEFAULT '[]',UNIQUE(session_id,request_id))""",
        "CREATE INDEX IF NOT EXISTS conversation_owner ON conversation_sessions(user_id,opened_at,id)",
        "CREATE INDEX IF NOT EXISTS conversation_order ON conversation_messages(session_id,id)",
        "CREATE INDEX IF NOT EXISTS conversation_pending ON conversation_messages(learning,id)",
        """CREATE TABLE IF NOT EXISTS conversation_imports(
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,source TEXT NOT NULL,
 session_id TEXT NOT NULL,offset INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'pending',
 size INTEGER NOT NULL,mtime INTEGER NOT NULL,errors INTEGER NOT NULL DEFAULT 0,skip_private INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(user_id,source))""",
        "CREATE TABLE IF NOT EXISTS conversation_maintenance(id INTEGER PRIMARY KEY CHECK(id=1),last_run REAL NOT NULL)",
    ):
        db.execute(statement)


def writer_id():
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        start = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()[19]
        return f"{boot}:{os.getpid()}:{start}"
    except (OSError, IndexError):
        return f"process:{os.getpid()}:unknown"


def writer_alive(writer):
    try:
        boot, pid, start = writer.split(":")
        if boot != "process" and boot != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
            return False
        observed = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()[19]
        return start == "unknown" or observed == start
    except (OSError, ValueError, IndexError):
        try:
            os.kill(int(writer.split(":")[1]), 0)
            return True
        except PermissionError:
            return True
        except (OSError, ValueError, IndexError):
            return False


def checked_owner(db, user):
    current = db.execute("SELECT enabled,revision FROM users WHERE id=?", (user.id,)).fetchone()
    if current is None or not current["enabled"] or current["revision"] != user.revision:
        raise memory.MemoryProblem("Profil modifié ou supprimé; session abandonnée.")


def append(store, user, session_id, role, text, channel, *, surface="terminal", request_id=None,
           barrier=None, eligible=False, references=(), guard=None):
    if role not in {"user", "assistant", "event"} or channel not in CHANNELS or not isinstance(text, str):
        raise memory.MemoryProblem("Message de session invalide.")
    was_safe = memory.redact(text) == text
    text = memory.redact(text)
    safe_refs = []
    for ref in list(references or ())[:32]:
        if (isinstance(ref, dict) and isinstance(ref.get("url"), str) and isinstance(ref.get("title"), str)
                and ref["url"].startswith(("https://", "http://"))):
            safe_refs.append({"url": memory.redact(ref["url"][:2000]), "title": memory.redact(ref["title"][:500])})
    with store.locked(user.memory_key, guard=guard), sql.connection(store) as db:
        checked_owner(db, user)
        state = db.execute("SELECT barrier,auto_enabled FROM memory_state WHERE user_id=?", (user.id,)).fetchone()
        recorded_barrier = state["barrier"] if barrier is None and state else barrier
        learn = (eligible and was_safe and bool(text.strip()) and len(text) <= 2000
                 and role == "user" and channel == "chat" and user.id != 1 and state is not None
                 and state["auto_enabled"] and recorded_barrier == state["barrier"])
        now = stamp()
        db.execute("""INSERT OR IGNORE INTO conversation_sessions
 (id,user_id,profile_revision,surface,opened_at,last_activity,status,writer) VALUES(?,?,?,?,?,?,'open',?)""",
                   (session_id, user.id, user.revision, surface, now, now, writer_id()))
        session = db.execute("SELECT * FROM conversation_sessions WHERE id=? AND user_id=?", (session_id, user.id)).fetchone()
        if (session is None or session["status"] != "open" or session["profile_revision"] != user.revision
                or session["writer"] != writer_id()):
            raise memory.MemoryProblem("Session fermée ou modifiée; aucun message tardif.")
        token = request_id or secrets.token_hex(12)
        db.execute("""INSERT INTO conversation_messages(session_id,request_id,at,role,channel,text,barrier,learning,references_json)
 VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id,request_id) DO UPDATE SET channel=excluded.channel,
 learning=CASE WHEN conversation_messages.learning='excluded' AND excluded.learning='pending'
 THEN 'pending' ELSE conversation_messages.learning END""",
                   (session_id, token, now, role, channel, text, recorded_barrier if recorded_barrier is not None else -1,
                    "pending" if learn else "excluded", json.dumps(safe_refs, ensure_ascii=False)))
        db.execute("UPDATE conversation_sessions SET last_activity=? WHERE id=?", (now, session_id))
        row = db.execute("SELECT id FROM conversation_messages WHERE session_id=? AND request_id=?", (session_id, token)).fetchone()
        return row[0]


def close(store, user_id, session_id, reason="closed", interrupted=False):
    with sql.connection(store) as db:
        db.execute("""UPDATE conversation_sessions SET status=?,closed_at=?,close_reason=?
 WHERE id=? AND user_id=? AND status='open'""",
                   ("interrupted" if interrupted else "closed", stamp(), reason, session_id, user_id))
        db.execute("""UPDATE memory_state SET ready=1 WHERE user_id=? AND EXISTS(SELECT 1 FROM conversation_messages m
 JOIN conversation_sessions c ON c.id=m.session_id WHERE c.user_id=? AND m.learning='pending')""",
                   (user_id, user_id))


def list_sessions(store, user_id, offset=0, limit=PAGE_SIZE):
    with sql.connection(store) as db:
        return [dict(r) for r in db.execute("""SELECT c.*,count(m.id) AS messages,
 sum(CASE WHEN m.learning='pending' THEN 1 ELSE 0 END) AS pending
 FROM conversation_sessions c LEFT JOIN conversation_messages m ON m.session_id=c.id
 WHERE c.user_id=? GROUP BY c.id ORDER BY c.opened_at DESC,c.id LIMIT ? OFFSET ?""",
                                           (user_id, max(1, min(limit, PAGE_SIZE)), max(0, offset)))]


def read_session(store, user_id, session_id, after=0, limit=PAGE_SIZE):
    with sql.connection(store) as db:
        row = db.execute("SELECT * FROM conversation_sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if row is None:
            raise KeyError(session_id)
        messages = [dict(r) for r in db.execute("SELECT * FROM conversation_messages WHERE session_id=? AND id>? ORDER BY id LIMIT ?",
                                               (session_id, max(0, after), max(1, min(limit, PAGE_SIZE))))]
    for message in messages:
        message["references"] = json.loads(message.pop("references_json"))
    return dict(row), messages


def delete_session(store, user_id, session_id):
    with sql.connection(store) as db:
        row = db.execute("SELECT status FROM conversation_sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if row is None:
            raise KeyError(session_id)
        if row[0] == "open":
            raise memory.MemoryProblem("Fermez la session avant de la supprimer.")
        # Cancel extraction even if its events were already copied into a worker.
        db.execute("UPDATE memory_state SET revision=revision+1,lease_token='',lease_until=0 WHERE user_id=?", (user_id,))
        db.execute("UPDATE conversation_imports SET status='expired' WHERE session_id=? AND user_id=?", (session_id, user_id))
        db.execute("DELETE FROM conversation_sessions WHERE id=? AND user_id=?", (session_id, user_id))


def prepare_learning(db, max_pending=48):
    db.execute("""UPDATE conversation_messages SET learning='superseded' WHERE learning='pending'
 AND EXISTS(SELECT 1 FROM conversation_sessions c JOIN memory_state s ON s.user_id=c.user_id
 WHERE c.id=conversation_messages.session_id AND s.barrier!=conversation_messages.barrier)""")
    rows = db.execute("""SELECT m.*,c.user_id FROM conversation_messages m
 JOIN conversation_sessions c ON c.id=m.session_id JOIN users u ON u.id=c.user_id
 JOIN memory_state s ON s.user_id=u.id WHERE m.learning='pending' AND u.enabled=1 AND s.auto_enabled=1 AND u.id!=1
 AND m.barrier=s.barrier AND NOT EXISTS(SELECT 1 FROM memory_events e WHERE e.message_id=m.id)
 ORDER BY m.id LIMIT 48""").fetchall()
    counts = {}
    for row in rows:
        uid = row["user_id"]
        if uid not in counts:
            counts[uid] = db.execute("SELECT count(*) FROM memory_events WHERE user_id=?", (uid,)).fetchone()[0]
        if counts[uid] >= max_pending:
            continue
        db.execute("INSERT INTO memory_events(user_id,session_id,text,created,barrier,message_id) VALUES(?,?,?,?,?,?)",
                   (uid, row["session_id"], row["text"], row["at"], row["barrier"], row["id"]))
        counts[uid] += 1
    db.execute("""UPDATE memory_state SET ready=1 WHERE
 (SELECT count(*) FROM memory_events e WHERE e.user_id=memory_state.user_id)>=6
 OR EXISTS(SELECT 1 FROM conversation_sessions c JOIN conversation_messages m ON m.session_id=c.id
 WHERE c.user_id=memory_state.user_id AND c.status!='open' AND m.learning='pending')""")


def legacy_time(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp without timezone")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def import_history(store, user, extra_secrets=()):
    try:
        return _import_history(store, user, extra_secrets)
    except OSError:
        raise memory.MemoryProblem("Import des anciens historiques indisponible; originaux conservés.") from None


def _import_history(store, user, extra_secrets):
    """At most one 100-line/128-KiB chunk. Originals are never written or removed.

    The ledger intentionally has no session FK: deleting a transcript must not
    cause its JSONL original to be imported again. Changed files require manual
    inspection instead of guessing where a replacement file continues.
    """
    logs = store.root / user.memory_key / "logs"
    if not logs.is_dir() or logs.is_symlink():
        return False
    def guard():
        try:
            from .user_registry import Registry
        except ImportError:
            from user_registry import Registry
        try:
            current = Registry(store.root).get(user.id)
        except memory.GmailError:
            raise memory.MemoryProblem("Profil supprimé; import abandonné.") from None
        if current.revision != user.revision:
            raise memory.MemoryProblem("Profil modifié; import abandonné.")

    with store.locked(user.memory_key, guard=guard), sql.connection(store) as db:
        checked_owner(db, user)
        known = {r["source"]: dict(r) for r in db.execute(
            "SELECT * FROM conversation_imports WHERE user_id=?", (user.id,))}
        for path in sorted(logs.glob("*.jsonl")):
            if path.name in known and known[path.name]["status"] != "pending":
                continue
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    continue
                entry = known.get(path.name)
                if entry is None:
                    entry = {"session_id": secrets.token_hex(12), "offset": 0, "skip_private": 0,
                             "size": info.st_size, "mtime": info.st_mtime_ns, "errors": 0}
                    db.execute("""INSERT INTO conversation_imports(user_id,source,session_id,size,mtime)
 VALUES(?,?,?,?,?)""", (user.id, path.name, entry["session_id"], info.st_size, info.st_mtime_ns))
                sid = entry["session_id"]
                # Do not import an active JSONL writer or a replaced/truncated file.
                if info.st_size != entry["size"] or info.st_mtime_ns != entry["mtime"]:
                    db.execute("UPDATE conversation_imports SET status='changed' WHERE user_id=? AND source=?",
                               (user.id, path.name))
                    db.execute("""UPDATE conversation_sessions SET status='interrupted',closed_at=last_activity,
 close_reason='legacy_changed' WHERE id=? AND status='importing'""", (sid,))
                    return True
                if time.time() - info.st_mtime < 60:
                    return False
                stream.seek(entry["offset"])
                consumed, errors, finished = 0, entry["errors"], False
                skip_private = bool(entry["skip_private"])
                for _ in range(IMPORT_LINES):
                    offset = stream.tell()
                    line = stream.readline(IMPORT_BYTES + 1)
                    if not line:
                        finished = True
                        break
                    consumed += len(line)
                    if consumed > IMPORT_BYTES:
                        stream.seek(offset)
                        # An oversized single record is flagged for manual review.
                        if offset == entry["offset"]:
                            db.execute("UPDATE conversation_imports SET status='oversized',errors=errors+1 WHERE user_id=? AND source=?",
                                       (user.id, path.name))
                            db.execute("""UPDATE conversation_sessions SET status='interrupted',closed_at=last_activity,
 close_reason='legacy_oversized' WHERE id=? AND status='importing'""", (sid,))
                            return True
                        break
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise TypeError("not an object")
                        role, channel, text = row.get("role"), row.get("channel"), row.get("text")
                        if role == "user" and isinstance(text, str):
                            try:
                                from .pronote_answers import school_record_request
                            except ImportError:
                                from pronote_answers import school_record_request
                            normalized = memory.normalized(text).strip()
                            skip_private = (channel not in CHANNELS or bool(row.get("private"))
                                            or school_record_request(text, {})
                                            or normalized == "journal intime"
                                            or bool(re.match(r"^(?:3615\b|activer memoire\b)", normalized)))
                        if row.get("private") or (skip_private and role in {"user", "assistant"}):
                            continue
                        if channel not in CHANNELS:
                            continue
                        if role not in {"user", "assistant", "event"} or not isinstance(text, str):
                            raise ValueError("invalid message")
                        at = legacy_time(row["at"])
                    except (ValueError, TypeError, KeyError, UnicodeError):
                        errors += 1
                        continue
                    db.execute("""INSERT OR IGNORE INTO conversation_sessions
 (id,user_id,profile_revision,surface,opened_at,last_activity,status,legacy_source)
 VALUES(?,?,?,'legacy',?,?,'importing',?)""", (sid, user.id, user.revision, at, at, path.name))
                    db.execute("""INSERT OR IGNORE INTO conversation_messages
 (session_id,request_id,at,role,channel,text) VALUES(?,?,?,?,?,?)""",
                               (sid, "legacy:" + str(offset), at, role, channel, memory.redact(text, extra_secrets)))
                    db.execute("UPDATE conversation_sessions SET opened_at=min(opened_at,?),last_activity=max(last_activity,?) WHERE id=?",
                               (at, at, sid))
                db.execute("UPDATE conversation_imports SET offset=?,errors=?,status=?,skip_private=? WHERE user_id=? AND source=?",
                           (stream.tell(), errors, "done" if finished else "pending", skip_private, user.id, path.name))
                if finished:
                    db.execute("""UPDATE conversation_sessions SET status='closed',closed_at=last_activity,
 close_reason='legacy_import' WHERE id=? AND user_id=?""", (sid, user.id))
                return True
    return False


def maintenance(store, now=None):
    """Bounded local housekeeping, at most daily after a completed prune.

    A live process's session is never recovered by another frontend. Idle browser
    entries are retired by the browser pool; a disconnected terminal closes in
    its own cleanup. Dead writers are recovered here on next activity.
    """
    now = time.time() if now is None else now
    try:
        days = int(os.getenv("MINITEL_SESSION_RETENTION_DAYS", "365"))
        if days < 1:
            raise ValueError()
    except ValueError:
        raise memory.MemoryProblem("MINITEL_SESSION_RETENTION_DAYS doit être un entier positif.") from None
    with sql.connection(store) as db:
        # Recovery is cheap and runs on activity, independently of daily pruning.
        for row in db.execute("""SELECT c.id,c.writer,c.profile_revision,u.revision,u.enabled
 FROM conversation_sessions c JOIN users u ON u.id=c.user_id WHERE c.status='open' LIMIT 32""").fetchall():
            stale = not row["enabled"] or row["revision"] != row["profile_revision"]
            if stale or not writer_alive(row["writer"]):
                db.execute("""UPDATE conversation_sessions SET status='interrupted',closed_at=last_activity,
 close_reason=? WHERE id=?""", ("profile_changed" if stale else "writer_stopped", row["id"]))
        prepare_learning(db)
        previous = db.execute("SELECT last_run FROM conversation_maintenance WHERE id=1").fetchone()
        if previous and now - previous[0] < 86400:
            return 0
        cutoff = stamp(now - days * 86400)
        expired = db.execute("""SELECT id,user_id FROM conversation_sessions
 WHERE status IN ('closed','interrupted') AND closed_at<? ORDER BY closed_at LIMIT 20""", (cutoff,)).fetchall()
        for row in expired:
            db.execute("UPDATE memory_state SET revision=revision+1,lease_token='',lease_until=0 WHERE user_id=?", (row["user_id"],))
            db.execute("UPDATE conversation_imports SET status='expired' WHERE session_id=?", (row["id"],))
            db.execute("DELETE FROM conversation_sessions WHERE id=?", (row["id"],))
        # Leave an unfinished backlog eligible for the next bounded invocation.
        if len(expired) < 20:
            db.execute("INSERT INTO conversation_maintenance VALUES(1,?) ON CONFLICT(id) DO UPDATE SET last_run=excluded.last_run", (now,))
        return len(expired)


class SessionLog:
    """Adapter at the existing recorder boundary; Gmail alone keeps legacy JSONL."""
    def __init__(self, store, user, session_id, extra_secrets=(), guard=None, surface="terminal"):
        self.store, self.user, self.session_id = store, user, session_id
        self.extra_secrets, self.guard, self.surface = extra_secrets, guard, surface
        self.gmail = memory.ConversationLog(store, user.memory_key, user.display_name, extra_secrets, guard)

    def append(self, role, text, channel="chat", **kwargs):
        if channel == "gmail":
            return self.gmail.append(role, text, channel)
        if role == "event" and channel == "session":
            if text == "Session terminée":
                close(self.store, self.user.id, self.session_id)
            return None
        if kwargs.get("references"):
            kwargs["references"] = [{k: memory.redact(ref[k], self.extra_secrets) for k in ("url", "title")}
                                    for ref in list(kwargs["references"])[:32] if isinstance(ref, dict)
                                    and all(isinstance(ref.get(k), str) for k in ("url", "title"))]
        return append(self.store, self.user, self.session_id, role, memory.redact(text, self.extra_secrets), channel,
                      guard=self.guard, surface=self.surface, **kwargs)
