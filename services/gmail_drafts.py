"""One local plaintext recovery slot per profile/mailbox; never stores approval."""

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager

try:
    from . import persistent_memory as memory
    from .gmail_auth import GmailError
except ImportError:
    import persistent_memory as memory
    from gmail_auth import GmailError

MAX_FILE = 2 * 1024 * 1024
FIELDS = (
    "kind", "parent", "thread", "candidate", "instruction", "recipient", "subject",
    "body", "reply_source", "personal_signoff", "raw", "message_id", "revision",
)


class DraftStore:
    def __init__(self, tokens):
        self.tokens = tokens
        self.memory = memory.MemoryStore(tokens.registry.root)

    @contextmanager
    def locked(self):
        try:
            account = self.tokens.guard("read")[1]
            name = "mail-draft-" + hashlib.sha256(account.encode()).hexdigest()[:32] + ".json"
            with self.memory.locked(self.tokens.user.memory_key,
                                    guard=lambda: self.tokens.guard("read")) as folder:
                yield folder / name, account
        except memory.MemoryProblem:
            raise GmailError("draft_storage_unavailable") from None

    def read(self, path, account):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                details = os.fstat(stream.fileno())
                if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_FILE:
                    raise ValueError()
                data = json.load(stream)
            if (not isinstance(data, dict) or data.get("version") != 1
                    or data.get("user") != self.tokens.user.id or data.get("account") != account
                    or not isinstance(data.get("id"), str)
                    or not re.fullmatch(r"[a-f0-9]{32}", data["id"])
                    or data.get("state") not in {"draft", "sending"}
                    or not isinstance(data.get("draft"), dict)
                    or set(data["draft"]) != set(FIELDS)):
                raise ValueError()
            return data
        except (ValueError, UnicodeError, TypeError, RecursionError):
            raise GmailError("saved_draft_invalid") from None

    def load(self):
        with self.locked() as (path, account):
            return self.read(path, account)

    @staticmethod
    def sync_folder(folder):
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def write(self, draft, selected, *, expected=None, sending=False):
        with self.locked() as (path, account):
            current = self.read(path, account)
            if expected is not None and (current is None or current["id"] != expected):
                raise GmailError("saved_draft_changed")
            if current and current["state"] == "sending":
                raise GmailError("saved_send_uncertain")
            saved = {key: draft[key] for key in FIELDS}
            saved["raw"] = saved["raw"].decode("utf-8")
            if selected is not None:
                # Keep editable reply context, not the API payload/attachment data.
                selected = {key: selected[key] for key in
                            ("id", "thread_id", "sender", "subject", "date", "body", "attachments")} | {
                    "raw": {"payload": {"headers": [
                        header for header in selected["raw"].get("payload", {}).get("headers", [])
                        if header.get("name", "").casefold() in {"from", "reply-to", "message-id", "references"}
                    ]}}}
            data = {"version": 1, "user": self.tokens.user.id, "account": account,
                    "id": secrets.token_hex(16), "state": "sending" if sending else "draft",
                    "draft": saved, "selected": selected}
            encoded = json.dumps(data, ensure_ascii=False, indent=2)
            if len(encoded.encode()) > MAX_FILE:
                raise GmailError("draft_size_limit")
            temporary = None
            try:
                # NamedTemporaryFile creates a private 0600 file on this filesystem.
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                                 prefix=".mail-draft-", delete=False) as stream:
                    temporary = stream.name
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                temporary = None
                self.sync_folder(path.parent)
            finally:
                if temporary:
                    os.unlink(temporary)
            return data["id"]

    def delete(self, expected=None):
        with self.locked() as (path, account):
            # Explicit ANNULER can also remove a damaged slot; an old session may
            # only delete the exact revision it last saved or restored.
            if expected is not None:
                current = self.read(path, account)
                if current is None:
                    return
                if current["id"] != expected:
                    raise GmailError("saved_draft_changed")
            path.unlink(missing_ok=True)
            self.sync_folder(path.parent)
