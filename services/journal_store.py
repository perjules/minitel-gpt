"""Password-encrypted Markdown. No plaintext persistence or password verifier."""

import hashlib
import os
import re
import stat
import struct
import tempfile
from datetime import date

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import persistent_memory, user_registry
from .gmail_auth import GmailError

MAGIC = b"3615JI01"
HEADER = struct.Struct(">8sQ16s12s")
MAX_BYTES = 2 * 1024 * 1024
TITLE = "# Journal intime\n"
FILENAME = "journal.md.enc"


class JournalError(ValueError):
    """Only fixed, public messages; never include user text or library exceptions."""


def derive(password, salt):
    # V1 fixes these parameters: 64 MiB, one lane. No attacker-controlled costs.
    if not isinstance(password, str) or not password or len(password) > 2000:
        raise JournalError("Saisissez un mot de passe (2000 caractères maximum).")
    return Scrypt(salt=salt, length=32, n=2**16, r=8, p=1).derive(
        password.encode("utf-8")
    )


def parse(markdown):
    """Date headings are structural; body text and paragraph spacing are retained."""
    if not isinstance(markdown, str) or len(markdown.encode("utf-8")) > MAX_BYTES:
        raise JournalError("Journal trop volumineux (2 Mio maximum).")
    if not markdown.startswith(TITLE) or any(
        ord(c) < 32 and c not in "\n\t" for c in markdown
    ):
        raise JournalError("Format Markdown du journal invalide.")
    rest = markdown[len(TITLE) :]
    sections = list(re.finditer(r"^## (\d{4}-\d{2}-\d{2})[ \t]*$", rest, re.MULTILINE))
    if rest[: sections[0].start() if sections else len(rest)].strip():
        raise JournalError("Texte sans date dans le journal.")
    days = {}
    try:
        for i, heading in enumerate(sections):
            day = date.fromisoformat(heading[1]).isoformat()
            body = rest[
                heading.end() : sections[i + 1].start()
                if i + 1 < len(sections)
                else len(rest)
            ].strip()
            if day in days or not body:
                raise ValueError()
            days[day] = body
    except ValueError:
        raise JournalError("Dates du journal invalides, vides ou répétées.") from None
    return days


def render(days):
    document = TITLE + "".join(
        f"\n## {day}\n\n{days[day].strip()}\n" for day in sorted(days)
    )
    if parse(document) != days:
        raise JournalError("Une entrée ne peut pas contenir un autre titre de date.")
    return document


def decrypt(blob, password, owner=None):
    if not HEADER.size + 16 <= len(blob) <= MAX_BYTES + HEADER.size + 16:
        raise JournalError("Fichier chiffré invalide; original conservé.")
    magic, stored_owner, salt, nonce = HEADER.unpack(blob[: HEADER.size])
    if magic != MAGIC or (owner is not None and owner != stored_owner):
        raise JournalError("Format ou propriétaire du journal invalide.")
    key = derive(password, salt)
    try:
        markdown = (
            AESGCM(key)
            .decrypt(nonce, blob[HEADER.size :], blob[: HEADER.size])
            .decode("utf-8")
        )
    except (InvalidTag, UnicodeError):
        raise JournalError("Mot de passe incorrect ou journal endommagé.") from None
    parse(markdown)
    return key, salt, markdown, stored_owner


def encrypt(markdown, key, salt, owner):
    parse(markdown)
    nonce = os.urandom(12)
    header = HEADER.pack(MAGIC, owner, salt, nonce)
    return header + AESGCM(key).encrypt(nonce, markdown.encode("utf-8"), header)


def read_file(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise JournalError("Fichier du journal non régulier.")
            blob = stream.read(MAX_BYTES + HEADER.size + 17)
        if len(blob) > MAX_BYTES + HEADER.size + 16:
            raise JournalError("Journal trop volumineux.")
        return blob
    except OSError:
        raise JournalError(
            "Lecture du journal impossible; original conservé."
        ) from None


def atomic_write(path, blob):
    """Temporary bytes are ciphertext too; preserve the old file on failed writes."""
    temporary = None
    try:
        if path.is_symlink():
            raise JournalError("Chemin du journal invalide.")
        fd, name = tempfile.mkstemp(prefix=".journal-encrypted-", dir=path.parent)
        temporary = type(path)(name)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    except OSError:
        raise JournalError(
            "Écriture du journal impossible. Vérifiez le fichier avant de réessayer."
        ) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def revision(blob):
    return hashlib.sha256(blob).digest()


class Vault:
    def __init__(self, user, registry):
        self.user, self.registry = user, registry
        self.store = persistent_memory.MemoryStore(registry.root)
        if user.id == 1 or user.memory_key == "shared":
            raise JournalError(
                "Choisissez un profil nommé pour ouvrir un journal intime."
            )

    def guard(self):
        try:
            current = self.registry.get(self.user.id)
        except GmailError:
            raise JournalError("Profil indisponible. Journal verrouillé.") from None
        if current.revision != self.user.revision:
            raise JournalError("Profil modifié. Rouvrez le journal.")

    def path(self, folder):
        path = folder / FILENAME
        if path.is_symlink():
            raise JournalError("Chemin du journal invalide.")
        return path

    def exists(self):
        with self.store.locked(self.user.memory_key, guard=self.guard) as folder:
            return self.path(folder).exists()

    def open(self, password):
        with self.store.locked(self.user.memory_key, guard=self.guard) as folder:
            blob = read_file(self.path(folder))
        key, salt, markdown, _ = decrypt(blob, password, self.user.id)
        self.guard()
        return key, salt, parse(markdown), revision(blob)

    def write(self, days, key, salt, expected):
        blob = encrypt(render(days), key, salt, self.user.id)
        with self.store.locked(self.user.memory_key, guard=self.guard) as folder:
            path = self.path(folder)
            actual = revision(read_file(path)) if path.exists() else None
            if actual != expected:
                raise JournalError(
                    "Journal modifié ailleurs. Verrouillez puis rouvrez-le avant de réessayer."
                )
            atomic_write(path, blob)
        return revision(blob)


def for_name(name, root=None):
    registry = user_registry.Registry(root)
    return Vault(registry.resolve(name), registry)
