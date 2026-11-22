"""Parse the terminal's one-shot command prefix without touching secret input."""

import re


class CommandError(ValueError):
    """Invalid command input must be reported locally, never sent to a model."""


def expand_shortcut(text, shortcuts):
    """Expand a leading single-letter command, preserving the argument verbatim."""
    match = re.fullmatch(r"([A-Za-z])(?:\s+(.*))?", text, re.DOTALL)
    if not match:
        return text
    key = match[1].upper()
    if key not in shortcuts:
        raise CommandError("Commande inconnue. Utilisez :AIDE.")
    command, accepts_argument = shortcuts[key]
    argument = match[2]
    if argument and not accepts_argument:
        raise CommandError(f":{key} doit être saisi seul. Aucune action effectuée.")
    return command + (" " + argument if argument else "")


class Command(str):
    """A physical function key is already an explicit local command."""
    fixed = True


class InputMode:
    """Keep pending command state local to the current terminal input context."""

    def __init__(self):
        self.pending = False

    def reset(self):
        self.pending = False

    def submit(self, text):
        """Return (kind, text): chat, command, or prompt for the next command."""
        if getattr(text, "fixed", False):
            self.reset()
            return "command", str(text)
        if self.pending:
            self.pending = False
            command = text.removeprefix(":")
            return ("command", command.strip()) if command.strip() else ("empty", "")
        if not text.startswith(":"):
            return "chat", text
        command = text[1:].strip()
        if not command:
            self.pending = True
            return "prompt", ""
        return "command", command


UNKNOWN = "Commande inconnue. Utilisez :AIDE."
HELP = (
    ": + ENVOI : prochaine saisie = commande\n"
    ":COMMANDE : execution directe\n"
    ":MAIL / :PRONOTE / :JOURNAL INTIME\n"
    ":AIRZONE / :YOLO / :3615 / :CALC / :NOEL\n"
    ":IMPRIMER / :MAJ / :I (INTELLIGENCE)\n"
    ":LOGOUT / :REBOOT / :SHUTDOWN\n"
    "Sans : = conversation. GUIDE = aide."
)


def prompt(term, pending):
    view = getattr(term, "view", None)
    if view is not None:
        view.buffer.clear()
        view.pending_input.clear()
        view.input()
        view.status("COMMANDE : prochaine saisie" if pending else "Commande vide. :AIDE")
    else:
        term.line("COMMANDE : prochaine saisie" if pending else "Commande vide. :AIDE")


def execute(text, language, timeout, mail, ticket, memory, calc, *, generate=None):
    """Dispatch only named commands; never fall through to general conversation."""
    from . import christmas_list, gmail_mail

    def result(display, **extra):
        return {"display": display, "turn": None, **extra}

    word = text.strip().upper()
    if not word:
        return result(UNKNOWN)
    current = getattr(memory, "christmas", None)
    if word in {"AIDE", "GUIDE", "HELP"}:
        if isinstance(current, christmas_list.Session) and current.mode:
            return current.result(current.display())
        if mail is not None and (mail.items or mail.selected or mail.draft or mail.composition):
            return result(":MAIL / :L n / :REPONDRE texte\n:ECRIRE nom : instructions\n:T texte exact / :M corrections\n:E envoyer apres relecture / :A annuler\n:B brouillon / :Q quitter", gmail=True)
        return result(HELP)
    if christmas_list.entry(text) or (isinstance(current, christmas_list.Session) and current.mode):
        if isinstance(current, christmas_list.Session) and current.mode:
            try:
                text = expand_shortcut(text, christmas_list.SHORTCUTS)
            except CommandError as error:
                return result(str(error))
        return christmas_list.route(memory, text, timeout=timeout) or result(UNKNOWN)
    if calc is not None and (word == "CALC" or word.startswith("CALC ") or calc.mode):
        return calc.handle(text, language, generate=generate) or result(UNKNOWN)
    if memory is not None and re.match(
            r"^(?:RETIENS|SOUVIENS-TOI|OUBLIE|MEMOIRE|NOTE|NOTES|LISTE|TACHE|TACHES)(?:\s|$)", word):
        return memory.handle(text, language, timeout)
    if mail is not None and gmail_mail.enabled():
        try:
            command = expand_shortcut(text, gmail_mail.SHORTCUTS)
        except CommandError as error:
            return result(str(error))
        verb = command.split()[0].upper()
        if verb in {"AJOUTER", "REMPLACER", "TEXTE"} and command.upper() != "TEXTE" and not gmail_mail.literal_command(command):
            return result(UNKNOWN)
        if verb == "DESTINATAIRE" and not re.fullmatch(r"DESTINATAIRE [0-9]+", command.upper()):
            return result(UNKNOWN)
        if verb in {"RECHERCHE", "SEARCH"} and len(command.split(maxsplit=1)) == 1:
            return result(UNKNOWN)
        if verb == "MES" and command.upper() != "MES MAILS":
            return result(UNKNOWN)
        if command.upper() == "READ":
            command = "LIRE"
        if verb in {"ENVOYER", "ANNULER", "QUITTER", "BROUILLON", "MAIL", "MAILS", "FULL", "RESUMER", "RÉSUMER"} and command.upper() != verb:
            return result(UNKNOWN)
        if verb in {"LIRE", "READ"} and not re.fullmatch(r"(?:LIRE|READ)(?: [0-9]+)?", command.upper()):
            return result(UNKNOWN)
        if re.match(r"^(?:MAILS?|MES MAILS|BROUILLON|LIRE|READ|DESTINATAIRE|ECRIRE|ÉCRIRE|REPONDRE|RÉPONDRE|MODIFIER|ENVOYER|ANNULER|QUITTER|TEXTE|AJOUTER|REMPLACER|FULL|RESUMER|RÉSUMER|RECHERCHE|SEARCH)(?:\s|:|$)", command.upper()):
            return {**mail.handle(command, ticket, timeout), "gmail": True}
    return result(UNKNOWN)


def power_operation(text):
    """Exact explicit commands only; caller retains physical confirmation."""
    command = " ".join(text.strip().upper().split())
    return {
        "SHUTDOWN": "poweroff", "POWEROFF": "poweroff",
        "ARRET": "poweroff", "ARRÊT": "poweroff",
        "REBOOT": "reboot", "REDEMARRER": "reboot", "REDÉMARRER": "reboot",
        "MAINFRAME --SHUTDOWN": "poweroff", "MAINFRAME --REBOOT": "reboot",
    }.get(command)
