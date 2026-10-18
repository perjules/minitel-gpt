#!/usr/bin/env python3
"""Personal rose console and opt-in scripted playtests."""
import argparse
import curses
import getpass
import io
import os
import secrets
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
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


def run_script(args):
    from dotenv import load_dotenv

    from services import rose, rose_script

    try:
        data = rose_script.load(args.script, test_only=args.test)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Script invalide : {error}", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = args.log or root / "logs" / "rose" / f"{Path(args.script).stem}-{stamp}-{secrets.token_hex(3)}.jsonl"
    transcript = None
    result = 2
    try:
        transcript = rose_script.Transcript(path, overwrite=args.log is not None)
        load_dotenv(root / ".env")
        model = rose.MockModel() if args.mock else rose.MistralModel(
            os.getenv("MISTRAL_KEY", ""), os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
            observer=transcript.provider_response)
        if not args.interactive:
            result = rose_script.play(data, model, transcript)
        else:
            # Loading the ordinary renderer must not add startup theatre to this mode.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                from services import minitel_chatgpt as chat
            from services.console_terminal import ConsoleSerial

            def session(window):
                curses.raw()
                curses.curs_set(1)
                term = chat.Term(ConsoleSerial(window))
                term.s.hint = "Entree ENVOI | ^G GUIDE | ^R RETOUR | ^P SOMMAIRE | ^Q fermer"
                term.w(chat.SCROLL_ON)
                try:
                    return rose_script.play(data, model, transcript, term=term)
                finally:
                    term.w(chat.SCROLL_OFF)

            result = curses.wrapper(session)
    except (OSError, ValueError, KeyError, TypeError, curses.error) as error:
        if transcript and not transcript.failed:
            try:
                transcript.write("run_error", error_type=type(error).__name__)
            except OSError:
                print("Journal indisponible.", file=sys.stderr)
        print(f"Exécution script impossible ({type(error).__name__}).", file=sys.stderr)
        result = 2
    finally:
        if transcript:
            try:
                transcript.close()
            except OSError:
                print("Fermeture du journal impossible.", file=sys.stderr)
                result = 2
    return result


def main():
    parser = argparse.ArgumentParser(description="Atelier privé 3615 rose ; journal complet uniquement avec --script.")
    parser.add_argument("--mock", action="store_true", help="dialogue écrit localement, aucun appel API")
    parser.add_argument("--test", action="store_true", help="utiliser le catalogue de personnalités de test")
    parser.add_argument("--name", default="", help="personnage ou alias ; catalogue si absent")
    parser.add_argument("--set-password", action="store_true", help="choisir la valeur locale privée sans écho")
    parser.add_argument("--script", "-s", type=Path, help="messages JSON ; journal complet, sans porte ni facture")
    parser.add_argument("--interactive", "-i", action="store_true", help="afficher le script puis poursuivre la même session")
    parser.add_argument("--log", type=Path, help="journal JSONL, écrasé si existant (défaut : logs/rose/)")
    args = parser.parse_args()
    if args.script and (args.name or args.set_password):
        parser.error("--script utilise sa persona JSON et ne peut pas configurer le mot de passe.")
    if not args.script and (args.interactive or args.log):
        parser.error("--interactive et --log nécessitent --script.")
    if (not args.script or args.interactive) and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        parser.exit(2, "Terminal interactif requis (ssh -t si nécessaire).\n")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.script:
        return run_script(args)
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
        rose_terminal.run(term, args.name, model, test=args.test)
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
