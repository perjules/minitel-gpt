#!/usr/bin/env python3
"""Personal rose playtest, using the same gate, host loop and Term renderer."""
import argparse
import curses
import getpass
import os
import sys
from pathlib import Path


def set_password(path):
    first = getpass.getpass("Mot de passe local 3615 rose : ")
    second = getpass.getpass("Confirmer : ")
    if (first != second or not first or len(first) > 128
            or any(ord(c) < 32 or ord(c) > 126 for c in first)
            or first.strip().upper() in ("RETOUR", "SOMMAIRE")):
        print("Valeur invalide : 1–128 caractères ASCII, confirmation identique.", file=sys.stderr)
        return 2
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(first)  # exact value, no newline, stripping or case folding
    print("Mot de passe local enregistré.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Atelier privé 3615 rose, sans conservation des conversations.")
    parser.add_argument("--mock", action="store_true", help="dialogue écrit localement, aucun appel API")
    parser.add_argument("--name", default="", help="personnage ou alias ; catalogue si absent")
    parser.add_argument("--set-password", action="store_true", help="choisir la valeur locale privée sans écho")
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(2, "Terminal interactif requis (ssh -t si nécessaire).\n")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from services import rose
    if args.set_password:
        return set_password(rose.PASSWORD_FILE)
    if args.name and rose.entry("3615 " + args.name) is None:
        parser.error("Nom invalide (32 caractères maximum).")
    from services import minitel_chatgpt as chat
    from services import rose_terminal
    from services.console_terminal import ConsoleSerial
    model = rose.MockModel() if args.mock else rose.MistralModel(chat.MISTRAL_KEY, chat.MISTRAL_MODEL)
    print("Dialogue simulé ; aucun appel API." if args.mock else "Appels Mistral réels ; aucune conversation enregistrée localement.")

    def session(window):
        curses.raw()
        curses.curs_set(1)
        term = chat.Term(ConsoleSerial(window))
        rose_terminal.run(term, args.name, model)
        term.line()
        term.line("Conversation terminée. ENVOI : fermer.")
        # Final receipt remains visible until the owner closes this standalone
        # preview; ordinary application resumes immediately without this pause.
        while True:
            kind, code = term.read_key(300)
            if kind == "timeout" or (kind == "fn" and code in (chat.K_ENVOI, chat.K_RETOUR, chat.K_SOMMAIRE)):
                return

    try:
        curses.wrapper(session)
    except KeyboardInterrupt:
        return 0
    except curses.error:
        print("Console indisponible : vérifier TERM et la taille 40 × 24.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
