"""Fixed YOLO console and conservative chat-to-job proposal."""
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from queue import Empty, Queue

import requests

from . import mistral_answers, rose_terminal, terminal_display

APP_ID = re.compile(r"[a-z][a-z0-9-]{0,23}\Z")
TERMINAL = {"READY", "FAILED", "CANCELLED", "TIMED_OUT"}
STATES = {
    "QUEUED": "EN FILE D'ATTENTE",
    "PREPARING": "MISE SOUS TENSION",
    "CODING": "COMPILATION DU NEANT",
    "TESTING": "RECHERCHE DE SEMICOLONS",
    "REPAIRING": "NEGOCIATION AVEC LE SILICIUM",
    "PACKAGING": "MISE EN CARTOUCHE",
    "DEPLOYING": "DEPLOIEMENT DU CHEF-D'OEUVRE",
    "VERIFYING": "CONTROLE DU CHEF-D'OEUVRE",
    "READY": "APPLICATION PRETE",
    "FAILED": "ECHEC",
    "CANCELLED": "ANNULE",
    "TIMED_OUT": "DELAI DEPASSE",
}
TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_yolo",
        "description": "Préparer la création ou la modification explicite d'une application web locale. "
                       "ENVOI OUI au Minitel sera requis avant toute soumission.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "operation": {"type": "string", "enum": ["create", "edit"]},
                "app_id": {"type": "string", "pattern": APP_ID.pattern.removesuffix("\\Z")},
            },
            "required": ["app_id", "operation"],
        },
    },
}


class YoloError(Exception):
    pass


class Client:
    def __init__(self, token_file, port=8766):
        self.token_file = Path(token_file)
        self.origin = f"http://127.0.0.1:{int(port)}/api/v1"

    def request(self, path, *, payload=None):
        try:
            with self.token_file.open() as source:
                token = source.read(130).strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
                raise YoloError("Jeton YOLO invalide.")
            if not re.fullmatch(r"/[a-zA-Z0-9/-]+", path):
                raise YoloError("Commande YOLO invalide.")
            with requests.Session() as session:
                session.trust_env = False
                with session.request("GET" if payload is None else "POST", self.origin + path,
                                     headers={"Authorization": "Bearer " + token},
                                     json=payload, timeout=(.5, 1.5), allow_redirects=False,
                                     stream=True) as response:
                    raw = bytearray()
                    for block in response.iter_content(8192):
                        raw.extend(block)
                        if len(raw) > 256 * 1024:
                            raise YoloError("Réponse YOLO trop grande.")
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise YoloError("Réponse YOLO invalide.")
                    if response.status_code not in (200, 202):
                        raise YoloError(label(result.get("error", "Commande YOLO refusée."), 180))
                    return result
        except (OSError, ValueError, requests.RequestException) as error:
            raise YoloError("Superviseur YOLO indisponible.") from error

    def submit(self, app_id, operation, payload):
        return self.request("/jobs", payload={"app_id": app_id, "operation": operation, "payload": payload})


def configured():
    return bool(os.environ.get("YOLO_TOKEN_FILE"))


def client():
    if not configured():
        raise YoloError("YOLO désactivé sur ce Minitel.")
    try:
        return Client(os.environ["YOLO_TOKEN_FILE"], os.getenv("YOLO_API_PORT", "8766"))
    except ValueError as error:
        raise YoloError("Port YOLO invalide.") from error


def label(text, limit=40):
    return " ".join(str(text or "").split())[:limit]


def intent(question):
    text = unicodedata.normalize("NFKD", question.casefold()).encode("ascii", "ignore").decode().strip()
    match = re.match(
        r"^(?:(?:yolo[, :] *|(?:peux-tu|pourrais-tu|can you|could you|please) +))*"
        r"(?P<verb>cree(?:r|z)?|fais|construis|developpe|create|build|make|develop|"
        r"modifie(?:r|z)?|mets a jour|ameliore|modify|update|edit)\b", text,
    )
    if not match:
        return None
    # Mere discussion, code examples and ambiguous file edits stay ordinary chat.
    if not (re.search(r"\b(?:application|app) (?:web|flask|locale|yolo)\b|"
                      r"\b(?:web|flask|local|yolo) (?:app|application)\b", text)
            or re.search(r"\byolo\b", text)):
        return None
    return ("edit" if match["verb"].startswith(("modifi", "mets", "ameliore", "update", "edit"))
            else "create")


def proposal(question, language, key, model, timeout):
    message = {"display": "Précisez une application et une seule action. :YOLO pour le panneau.", "turn": None}
    operation = intent(question)
    if not operation or len(question) > 1800 or not key or key == "...":
        return message
    payload = mistral_answers.request_payload([{"role": "user", "content": question}],
                                               language, model, False, "")
    payload["instructions"] += (
        "\nOnly prepare the explicitly requested local application action. "
        "For editing use the slug stated by the user. For creation choose a short ASCII slug. "
        "Never prepare a build for a request for advice, code samples or a question about existing software."
    )
    payload["tools"] = [TOOL]
    payload["completion_args"]["tool_choice"] = "auto"
    try:
        response = requests.post(mistral_answers.URL, headers={"Authorization": "Bearer " + key},
                                 json=payload, timeout=timeout)
        mistral_answers.raise_for_status(response, "yolo")
        calls = [item for item in response.json().get("outputs", []) if item.get("type") == "function.call"]
        if len(calls) != 1 or calls[0].get("name") != "prepare_yolo":
            return message
        args = calls[0].get("arguments")
        if isinstance(args, str):
            args = json.loads(args)
        if (not isinstance(args, dict) or args.keys() != {"operation", "app_id"}
                or args["operation"] != operation or not isinstance(args["app_id"], str)
                or not APP_ID.fullmatch(args["app_id"])):
            return message
        app_id = args["app_id"]
        if operation == "edit" and not re.search(r"(?<![a-z0-9-])" + re.escape(app_id) + r"(?![a-z0-9-])",
                                                 question.casefold()):
            return message
        return {"display": "", "turn": None, "yolo_proposal": {**args, "payload": question}}
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return {"display": "Interprète YOLO indisponible. Tapez :YOLO.", "turn": None}


def background(operation):
    results = Queue(maxsize=1)

    def worker():
        try:
            results.put((operation(), None))
        except YoloError as error:
            results.put((None, error))

    threading.Thread(target=worker, name="minitel-yolo-status", daemon=True).start()
    return results


def watch(chat, term, api, language, job_id):
    pending = None
    last = None
    due = 0.0
    view = terminal_display.View(term, f"YOLO / TACHE {job_id}\n\nANNULER : annuler la tâche\n"
                                 "MAJ : relire / RETOUR : panneau", language)
    view.install()

    def poll():
        nonlocal pending, due, last
        if pending is None and time.monotonic() >= due and (last or {}).get("status") not in TERMINAL:
            pending = background(lambda: api.request("/jobs/" + job_id))
        if pending is None:
            return
        try:
            result, error = pending.get_nowait()
        except Empty:
            return
        pending = None
        due = time.monotonic() + 2.5
        if error:
            view.status(label(error))
            return
        status = result.get("status")
        if last is None or last.get("status") != status:
            last = result
            if status == "READY" and result.get("url"):
                view.references = [{"url": result["url"], "title": label(result.get("name") or result["app_id"])}]
                view.open_link(1)
                view.status(label((result.get("name") or result["app_id"]) + " / PRETE"))
            else:
                view.status(label(result.get("error") or STATES.get(status, status)))

    while True:
        try:
            text, action = view.read(calculator_mode=True, idle=poll)
        except rose_terminal.EchoUnavailable:
            poll()
            text, action = chat.read_question(term, calculator_mode=True)
        if action != "envoi":
            return action
        command = text.strip().upper()
        if command == "RETOUR":
            return "panel"
        if command == "LOGOUT":
            return "logout"
        if command == "ANNULER":
            try:
                api.request("/jobs/" + job_id + "/cancel", payload={})
                due = 0
            except YoloError as error:
                view.status(label(error))
        elif command == "MAJ":
            if last and last.get("status") in TERMINAL:
                chat.show_response(term, label(last.get("error") or STATES.get(last["status"]), 320), language,
                                   references=view.references)
                _, action = chat.read_question(term, calculator_mode=True)
                if action != "envoi":
                    return action
                return "panel"
            due = 0


def confirm(chat, term, text, language):
    chat.show_response(term, text + "\n\nOUI + ENVOI pour confirmer\nRETOUR pour annuler", language)
    answer, action = chat.read_question(term, calculator_mode=True)
    return action, (answer or "").strip().upper() in {"OUI", "YES"}


def run(term, language, *, initial=None):
    chat = rose_terminal.api()
    try:
        api = client()
    except YoloError as error:
        return chat.show_response(term, str(error), language)
    screen = ""
    if initial:
        action, approved = confirm(chat, term, "YOLO / " + initial["operation"].upper() + "\n"
                                   + initial["app_id"] + "\n\n" + label(initial["payload"], 280), language)
        if action != "envoi":
            return action
        if not approved:
            return chat.show_response(term, "YOLO / DEMANDE ANNULEE", language)
        try:
            result = api.submit(**initial)
        except YoloError as error:
            return chat.show_response(term, str(error), language)
        return chat.show_response(term, "YOLO / MISE EN FILE\nTACHE " + result["id"] +
                                  "\n:YOLO puis ETAT " + result["id"], language)
    while True:
        chat.show_response(term, screen or "YOLO / ATELIER LOGICIEL\n\n"
                           "NOUVEAU nom / MODIFIER nom\nLISTE / FICHE nom / QR nom\n"
                           "TACHES / ETAT id\nVERSIONS nom / RETABLIR nom numero\n"
                           "SUPPRIMER nom / PURGER nom\n\nRETOUR : conversation", language)
        text, action = chat.read_question(term, calculator_mode=True)
        if action != "envoi":
            return action
        parts = text.lower().split()
        screen = ""
        if not parts:
            continue
        command, *args = parts
        if command == "logout":
            return "logout"
        if command == "retour":
            return chat.show_response(term, "YOLO / A PLUS TARD", language)
        try:
            if command == "liste" and not args:
                apps = api.request("/apps")["applications"]
                screen = "YOLO / APPLICATIONS\n\n" + ("\n".join(
                    a["id"] + "  " + label(a["name"], 18) for a in apps) or "Aucune application.")
            elif command == "taches" and not args:
                jobs = api.request("/jobs")["jobs"]
                screen = "YOLO / TACHES\n\n" + ("\n".join(
                    f"{j['id']} {j['status']}\n  {j['app_id']}" for j in jobs[:15]) or "Aucune tâche.")
            elif command == "etat" and len(args) == 1 and re.fullmatch(r"[0-9a-f]{12}", args[0]):
                action = watch(chat, term, api, language, args[0])
                if action != "panel":
                    return action
            elif args and APP_ID.fullmatch(args[0]):
                app_id = args[0]
                if command in {"nouveau", "modifier"} and len(args) == 1:
                    chat.show_response(term, "YOLO / " + app_id + "\n\nDécrivez votre application ou la modification.",
                                       language)
                    requirement, action = chat.read_question(term, calculator_mode=True)
                    if action != "envoi":
                        return action
                    if requirement.strip().upper() != "RETOUR":
                        job = api.submit(app_id, "create" if command == "nouveau" else "edit", requirement)
                        action = watch(chat, term, api, language, job["id"])
                        if action != "panel":
                            return action
                elif command in {"fiche", "versions", "retablir", "qr"}:
                    item = api.request("/apps/" + app_id)
                    versions = item["versions"]
                    if command == "qr" and len(args) == 1:
                        chat.show_response(term, f"YOLO / {label(item['name'])}\n\nQR de l'application",
                                           language, references=[{"url": item["url"]}])
                        term.view.open_link(1)
                        _, action = chat.read_question(term, calculator_mode=True)
                        if action != "envoi":
                            return action
                    elif command == "fiche" and len(args) == 1:
                        screen = f"YOLO / {item['name']}\n\n{app_id}\n"
                        screen += ("EN SERVICE" if item["healthy"] else "INDISPONIBLE") + "\n" + item["version"][:12]
                    elif command == "versions" and len(args) == 1:
                        screen = "YOLO / VERSIONS\n\n" + "\n".join(
                            f"{i}  {v['version'][:12]}  {label(v['name'], 17)}"
                            for i, v in enumerate(versions, 1))
                    elif (command == "retablir" and len(args) == 2
                          and re.fullmatch(r"[0-9]{1,3}", args[1]) and 1 <= int(args[1]) <= len(versions)):
                        version = versions[int(args[1]) - 1]["version"]
                        action, approved = confirm(chat, term, f"RETABLIR {app_id}\n{version[:12]}", language)
                        if action != "envoi":
                            return action
                        if approved:
                            job = api.submit(app_id, "rollback", version)
                            action = watch(chat, term, api, language, job["id"])
                            if action != "panel":
                                return action
                    else:
                        screen = "YOLO / COMMANDE INVALIDE"
                elif command in {"supprimer", "purger"} and len(args) == 1:
                    purge = command == "purger"
                    action, approved = confirm(chat, term, f"{command.upper()} {app_id}\n"
                                               + ("Efface aussi les données SQLite." if purge
                                                  else "Les données SQLite sont conservées."), language)
                    if action != "envoi":
                        return action
                    if approved:
                        job = api.submit(app_id, "delete", purge)
                        screen = "YOLO / MISE EN FILE\nETAT " + job["id"]
                else:
                    screen = "YOLO / COMMANDE INVALIDE"
            else:
                screen = "YOLO / COMMANDE INVALIDE\nRETOUR pour sortir / ENVOI pour agir"
        except YoloError as error:
            screen = "YOLO\n\n" + str(error)
