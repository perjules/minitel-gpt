"""Local identities and explicit grants. Names select profiles, not authentication."""

import json
import logging
import os
import sqlite3
import tempfile
import unicodedata
from contextlib import closing, contextmanager
from dataclasses import dataclass

try:
    from . import persistent_memory as memory
    from .gmail_auth import GmailError
except ImportError:
    import persistent_memory as memory
    from gmail_auth import GmailError


@dataclass(frozen=True)
class User:
    id: int
    display_name: str
    memory_key: str
    revision: int


class Registry:
    def __init__(self, root=None):
        self.root = memory.MemoryStore(root).root
        self.path = self.root / "users.sqlite3"

    @contextmanager
    def connection(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise GmailError("user_registry_invalid")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o777 != 0o600:
            os.chmod(self.path, 0o600)
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1, 2):
                    raise GmailError("user_registry_version")
                if version == 0:
                    db.executescript("""BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, display_name TEXT NOT NULL,
 memory_key TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)), revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS user_aliases(alias TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id));
CREATE TABLE IF NOT EXISTS gmail_accounts(id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)), revision INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS user_gmail_access(user_id INTEGER PRIMARY KEY REFERENCES users(id),
 account_id TEXT NOT NULL REFERENCES gmail_accounts(id), can_read INTEGER NOT NULL CHECK(can_read IN (0,1)),
 can_send INTEGER NOT NULL CHECK(can_send IN (0,1) AND can_send<=can_read));
CREATE TABLE IF NOT EXISTS user_pronote_children(user_id INTEGER NOT NULL REFERENCES users(id), child TEXT NOT NULL,
 PRIMARY KEY(user_id,child));
INSERT OR IGNORE INTO users(id,display_name,memory_key) VALUES(1,'MinitelGPT','shared');
PRAGMA user_version=2;
COMMIT;""")
                elif version == 1:
                    self.migrate_shared_mailboxes(db)
                    db.commit()
                yield db
        except (sqlite3.Error, OSError):
            raise GmailError("user_registry_unavailable") from None
        finally:
            db.close()

    def migrate_shared_mailboxes(self, db):
        """Remove only the reverse uniqueness rule, preserving all existing grants."""
        db.execute("BEGIN IMMEDIATE")
        # Another process may have upgraded while this connection waited for the lock.
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            return
        backup = self.path.with_name("users.before-shared-gmail.sqlite3")
        if not backup.exists():
            fd, temporary = tempfile.mkstemp(
                prefix=".users-before-shared-", dir=self.root
            )
            os.close(fd)
            # A separate reader can back up the committed version while this connection
            # holds the writer reservation; backing up the writer itself would wait.
            try:
                with (
                    closing(sqlite3.connect(self.path)) as source,
                    closing(sqlite3.connect(temporary)) as target,
                ):
                    source.backup(target)
                os.replace(temporary, backup)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        db.execute("""CREATE TABLE user_gmail_access_v2(
 user_id INTEGER PRIMARY KEY REFERENCES users(id),
 account_id TEXT NOT NULL REFERENCES gmail_accounts(id),
 can_read INTEGER NOT NULL CHECK(can_read IN (0,1)),
 can_send INTEGER NOT NULL CHECK(can_send IN (0,1) AND can_send<=can_read))""")
        db.execute("INSERT INTO user_gmail_access_v2 SELECT * FROM user_gmail_access")
        db.execute("DROP TABLE user_gmail_access")
        db.execute("ALTER TABLE user_gmail_access_v2 RENAME TO user_gmail_access")
        db.execute("PRAGMA user_version=2")

    def resolve(self, name=""):
        key, label = memory.user_key(name)
        canonical = " ".join(unicodedata.normalize("NFKC", name).split()).casefold()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not canonical:
                row = db.execute("SELECT * FROM users WHERE id=1").fetchone()
            else:
                row = db.execute(
                    "SELECT u.* FROM users u JOIN user_aliases a ON u.id=a.user_id WHERE a.alias=?",
                    (canonical,),
                ).fetchone()
                if row is None:
                    existing = db.execute(
                        "SELECT * FROM users WHERE memory_key=?", (key,)
                    ).fetchone()
                    if existing is not None:
                        raise GmailError("user_alias_conflict")
                    user_id = db.execute(
                        "INSERT INTO users(display_name,memory_key) VALUES(?,?)",
                        (label, key),
                    ).lastrowid
                    db.execute(
                        "INSERT INTO user_aliases VALUES(?,?)", (canonical, user_id)
                    )
                    row = db.execute(
                        "SELECT * FROM users WHERE id=?", (user_id,)
                    ).fetchone()
            return self.user(row)

    @staticmethod
    def user(row):
        if row is None or not row["enabled"]:
            raise GmailError("user_disabled")
        return User(row["id"], row["display_name"], row["memory_key"], row["revision"])

    def get(self, user_id):
        with self.connection() as db:
            return self.user(
                db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            )

    def users(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM users ORDER BY id")]

    def accounts(self):
        with self.connection() as db:
            accounts = [
                dict(r)
                for r in db.execute("SELECT * FROM gmail_accounts ORDER BY email")
            ]
            assignments = {}
            for row in db.execute("""SELECT g.*,u.display_name FROM user_gmail_access g
 JOIN users u ON u.id=g.user_id ORDER BY u.id"""):
                assignments.setdefault(row["account_id"], []).append(dict(row))
            for account in accounts:
                account["users"] = assignments.get(account["id"], [])
            return accounts

    def record_account(
        self, account_id, email, revision, target_user=None, expected_revision=None
    ):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if target_user is not None:
                current = self.user(
                    db.execute(
                        "SELECT * FROM users WHERE id=?", (target_user,)
                    ).fetchone()
                )
                if current.revision != expected_revision:
                    raise GmailError("account_assignment_changed")
            previous = db.execute(
                "SELECT revision FROM gmail_accounts WHERE id=?", (account_id,)
            ).fetchone()
            db.execute(
                "INSERT INTO gmail_accounts(id,email,revision) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET email=excluded.email,revision=excluded.revision",
                (account_id, email, revision),
            )
            if previous is None or previous[0] != revision:
                db.execute(
                    "UPDATE users SET revision=revision+1 WHERE id IN (SELECT user_id FROM user_gmail_access WHERE account_id=?)",
                    (account_id,),
                )
            if target_user is not None:
                return self.user(
                    db.execute(
                        "SELECT * FROM users WHERE id=?", (target_user,)
                    ).fetchone()
                ).revision

    def configure(self, user_id, name, enabled, aliases, account_id, send, children):
        name = memory.clean_text(name, 60)
        normalized = []
        for alias in [*aliases, name] if user_id != 1 else aliases:
            memory.user_key(alias)
            value = " ".join(unicodedata.normalize("NFKC", alias).split()).casefold()
            if value:
                normalized.append(value)
        if user_id != 1 and not normalized:
            raise GmailError("user_alias_required")
        if (
            len(normalized) > 20
            or len(children) > 20
            or any(not isinstance(c, str) or not 0 < len(c) <= 80 for c in children)
        ):
            raise GmailError("invalid_access")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
                is None
            ):
                raise GmailError("unknown_user")
            db.execute(
                "UPDATE users SET display_name=?,enabled=?,revision=revision+1 WHERE id=?",
                (name, 1 if user_id == 1 else bool(enabled), user_id),
            )
            db.execute("DELETE FROM user_aliases WHERE user_id=?", (user_id,))
            for alias in set(normalized):
                db.execute("INSERT INTO user_aliases VALUES(?,?)", (alias, user_id))
            db.execute("DELETE FROM user_gmail_access WHERE user_id=?", (user_id,))
            if account_id:
                db.execute(
                    "INSERT INTO user_gmail_access VALUES(?,?,1,?)",
                    (user_id, account_id, bool(send)),
                )
            db.execute("DELETE FROM user_pronote_children WHERE user_id=?", (user_id,))
            db.executemany(
                "INSERT INTO user_pronote_children VALUES(?,?)",
                [(user_id, c) for c in set(children)],
            )

    def details(self, user_id):
        with self.connection() as db:
            gmail = db.execute(
                "SELECT account_id,can_read,can_send FROM user_gmail_access WHERE user_id=?",
                (user_id,),
            ).fetchone()
            return {
                **(
                    dict(gmail)
                    if gmail
                    else {"account_id": None, "can_read": False, "can_send": False}
                ),
                "aliases": [
                    r[0]
                    for r in db.execute(
                        "SELECT alias FROM user_aliases WHERE user_id=?", (user_id,)
                    )
                ],
                "children": [
                    r[0]
                    for r in db.execute(
                        "SELECT child FROM user_pronote_children WHERE user_id=?",
                        (user_id,),
                    )
                ],
            }

    def gmail(self, user_id, operation="read", expected=None):
        with self.connection() as db:
            user = self.user(
                db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            )
            row = db.execute(
                "SELECT a.*,g.can_read,g.can_send FROM user_gmail_access g JOIN gmail_accounts a ON a.id=g.account_id WHERE g.user_id=?",
                (user_id,),
            ).fetchone()
            if (
                row is None
                or not row["enabled"]
                or not row["can_read"]
                or (operation == "send" and not row["can_send"])
            ):
                raise GmailError("gmail_not_assigned_or_denied")
            binding = (user.revision, row["id"], row["revision"])
            if expected is not None and binding != expected:
                raise GmailError("account_assignment_changed")
            return binding

    def children(self, user_id):
        self.get(user_id)
        return set(self.details(user_id)["children"])

    def audit(
        self, user_id, account_id, operation, outcome, session_id=None, request_id=None
    ):
        logging.getLogger(__name__).info(
            "user_operation %s",
            json.dumps(
                {
                    "user_id": user_id,
                    "account_id": account_id,
                    "operation": operation,
                    "outcome": outcome,
                    "session_id": session_id,
                    "request_id": request_id,
                }
            ),
        )
