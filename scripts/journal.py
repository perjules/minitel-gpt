#!/usr/bin/env python3
"""Offline export/import/password change for a journal.md.enc (passwords via getpass)."""

import argparse
import fcntl
import getpass
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import journal_store as storage


def ask(prompt):
    # getpass normally falls back to potentially echoed stdin without a TTY.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except (getpass.GetPassWarning, EOFError):
            raise storage.JournalError(
                "Saisie masquée indisponible. Utilisez un terminal interactif."
            ) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("export", "import", "password"))
    parser.add_argument(
        "file", type=Path, help="encrypted journal, including a standalone backup copy"
    )
    parser.add_argument(
        "--output", type=Path, help="explicit plaintext export; must not already exist"
    )
    parser.add_argument(
        "--input", type=Path, help="edited UTF-8 Markdown to re-encrypt"
    )
    args = parser.parse_args(argv)
    if (args.operation == "export") != bool(args.output) or (
        args.operation == "import"
    ) != bool(args.input):
        parser.error(
            "export requires --output; import requires --input; password accepts neither"
        )
    try:
        password = ask("Mot de passe actuel : ")
        # Same directory lock as the live application's profile lock. A copied
        # encrypted file can also be edited independently of the Pi/registry.
        path = args.file.absolute()
        if path.resolve() != path:
            raise storage.JournalError("Les chemins symboliques ne sont pas autorisés.")
        descriptor = os.open(
            path.parent / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            key, salt, markdown, owner = storage.decrypt(
                storage.read_file(path), password
            )
            password = None
            if args.operation == "export":
                # Never overwrite an existing file or editor backup accidentally.
                fd = os.open(
                    args.output,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(markdown)
                    stream.flush()
                    os.fsync(stream.fileno())
                print("Export Markdown créé. Ce fichier est en clair.")
                return 0
            if args.operation == "import":
                with args.input.open("rb") as stream:
                    raw = stream.read(storage.MAX_BYTES + 1)
                if len(raw) > storage.MAX_BYTES:
                    raise storage.JournalError("Journal trop volumineux.")
                markdown = raw.decode("utf-8")
                storage.parse(markdown)
            else:
                first = ask("Nouveau mot de passe : ")
                second = ask("Confirmer le nouveau mot de passe : ")
                if first != second:
                    raise storage.JournalError(
                        "Les deux saisies diffèrent. Journal inchangé."
                    )
                salt = os.urandom(16)
                key = storage.derive(first, salt)
                first = second = None
            storage.atomic_write(path, storage.encrypt(markdown, key, salt, owner))
        print("Journal chiffré mis à jour. Rouvrez les sessions du Minitel.")
        return 0
    except storage.JournalError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        print(
            "Opération impossible. Aucun mot de passe ni contenu affiché.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
