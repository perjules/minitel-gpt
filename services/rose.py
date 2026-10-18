"""3615 rose: transient host state, editable cast, integer billing, text-only model.

Ordinary sessions have no tools, general assistant context or automatic logging.
The separate script runner can explicitly request diagnostic observation.
Workers only receive snapshots; the UART owner alone accepts results.
"""
import copy
import hmac
import json
import os
import random
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from queue import Queue

import requests

CONFIG = Path(__file__).resolve().parents[1] / "config"
PASSWORD_FILE = CONFIG / "rose-password.txt"
URL = "https://api.mistral.ai/v1/chat/completions"
RATE = Decimal("6.55957")
MAX_INPUT = 800
MAX_HISTORY = 8000
MAX_TURNS = 24


def normalized(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.upper().replace("-", " ").split())


def entry(text):
    """Whole command only; quoted text/composition is handled by caller's context."""
    match = re.fullmatch(r"\s*3615(?:\s+([\w '\-]{1,32}))?\s*", text, re.IGNORECASE)
    return None if match is None else (match[1] or "").strip()


def test_entry(text):
    return re.fullmatch(r"\s*3615\s+TEST\s*", text, re.IGNORECASE) is not None


def password_value():
    # One fixed local value; no accounts, remembered unlock or published default.
    if "ROSE_PASSWORD" in os.environ:
        return os.environ["ROSE_PASSWORD"]
    try:
        return PASSWORD_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def authorized(attempt):
    secret = password_value()
    return bool(secret) and hmac.compare_digest(attempt.encode(), secret.encode())


def catalogue(test=False):
    filename = "rose_test_personas.json" if test else "rose_personas.json"
    return json.loads((CONFIG / filename).read_text(encoding="utf-8"))


def known_name(alias, test=False):
    return any(normalized(alias) in [normalized(p["name"]), *map(normalized, p["aliases"])]
               for p in catalogue(test=test)["personas"])


def last_seen(rng=None):
    """One throwaway 10% lookup gag; no presence/history record is retained."""
    rng = rng or random.SystemRandom()
    if rng.randrange(10) != 0:
        return None
    first, last = date(1980, 1, 1), date(1989, 12, 31)
    return first + timedelta(days=rng.randrange((last - first).days + 1))


def character_modules(profile):
    """Load only this character's selected modules, never the entire bank."""
    selections = profile.get("kinks", [])
    if not isinstance(selections, list) or any(not isinstance(item, str) for item in selections):
        raise ValueError("Invalid character preference list")
    blocks = []
    if selections:
        bank = json.loads((CONFIG / "rose_kinks.json").read_text(encoding="utf-8"))
        for key in dict.fromkeys(selections):
            if key not in bank:
                raise ValueError("Unknown character preference")
            instruction = bank[key]["instruction"]
            if not isinstance(instruction, str) or not instruction.strip():
                raise ValueError("Invalid character preference instruction")
            blocks.append(f"Préférence ({key}) : {instruction}")
    if profile.get("asexual"):
        text = (CONFIG / "rose_asexual_prompt.txt").read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("Empty character instruction")
        blocks.append("Préférence (asexual) : " + text.strip())
    return "\n\n".join(blocks)


def stop_reason(text, safeword=""):
    command = normalized(text).strip(".!? ")
    command = re.sub(r"(?:,? (?:S'IL TE PLAIT|S'IL VOUS PLAIT|SVP|MERCI|PLEASE))$", "", command)
    if safeword and text.strip().casefold() == safeword.casefold():
        return "safeword"
    if command == "SOMMAIRE":
        return "sommaire"
    if command in {"RETOUR", "QUITTER", "SORTIR", "STOP", "ARRETE", "ARRETEZ",
                   "ON ARRETE", "JE VEUX ARRETER", "JE VEUX SORTIR",
                   "ARRETONS", "CA SUFFIT", "STOP PLEASE", "PLEASE STOP"}:
        return "retour"
    if command in {"ARRETE DE PARLER", "ARRETEZ DE PARLER", "NE CONTINUE PAS",
                   "JE SOUHAITE ARRETER", "JE PREFERE ARRETER", "ON PEUT ARRETER",
                   "J'ARRETE", "QUIT", "EXIT", "I WANT TO STOP", "STOP NOW"}:
        return "retour"
    return None


def moderation_rejected(status, data):
    """Mistral's documented guardrail violation, not prose/status-only guessing.

    Official safety-moderation docs, checked 2026-10-16. Guardrail service
    failure (including code 3201) is a different branch. No extra guardrail
    settings or classifier calls are introduced by this prototype.
    """
    if status != 403 or not isinstance(data, dict) or data.get("code") == 3201:
        return False
    guardrails = data.get("guardrails")
    if not isinstance(guardrails, dict):
        return False
    result = guardrails.get("results", {})
    if not isinstance(result, dict):
        return False
    result = result.get("moderation_llm_v2", {})
    if not isinstance(result, dict) or "error" in result:
        return False
    decisions = result.get("decisions", {})
    return (result.get("action") == "block" and result.get("violated") is True
            and isinstance(decisions, dict)
            and any(isinstance(d, dict) and d.get("violated") is True
                    for d in decisions.values()))


@dataclass(frozen=True)
class Reply:
    text: str = ""
    status: str = "done"


class MistralModel:
    """Same configured Mistral key/model, separate bounded chat-completion call."""
    def __init__(self, key, model, timeout=30, observer=None):
        self.key, self.model, self.timeout = key, model, timeout
        self.observer = observer  # Explicit script diagnostics only; ordinary calls have no observer.

    def ready(self):
        return bool(self.key and self.key != "...")

    def __call__(self, prompt, history):
        return self.complete(prompt, history, max_tokens=180, temperature=0.8, timeout=self.timeout)

    def classify_name(self, name):
        prompt = (CONFIG / "rose_name_prompt.txt").read_text(encoding="utf-8")
        result = self.complete(prompt, [{"role": "user", "content": name}],
                               max_tokens=8, temperature=0, timeout=min(self.timeout, 8))
        if result.status != "done":
            return result
        label = {"m": "M", "f": "F", "unclear": "unclear"}.get(result.text.strip().casefold())
        return Reply(label) if label else Reply(status="error")

    def complete(self, prompt, history, *, max_tokens, temperature, timeout):
        if not self.ready():
            return Reply(status="configuration")
        try:
            response = requests.post(
                URL, headers={"Authorization": f"Bearer {self.key}"},
                json={"model": self.model,
                      "messages": [{"role": "system", "content": prompt}] + history,
                      "max_tokens": max_tokens, "temperature": temperature, "stream": False},
                timeout=timeout,
            )
            try:
                data = response.json()
            except ValueError as error:
                if self.observer is not None:
                    self.observer(response.status_code, None, error_type=type(error).__name__)
                return Reply(status="error")
            if self.observer is not None:
                self.observer(response.status_code, data)
            if moderation_rejected(response.status_code, data):
                return Reply(status="moderation")
            if response.status_code != 200:
                return Reply(status="error")
            choice = data["choices"][0]
            text = choice["message"]["content"]
            if choice.get("finish_reason") != "stop" or not isinstance(text, str) or not text.strip():
                return Reply(status="error")
            # Strip terminal controls, cap output before it reaches the renderer.
            text = "".join(c for c in text if c == "\n" or c.isprintable()).strip()
            return Reply(text[:700]) if text else Reply(status="error")
        except requests.RequestException as error:
            if self.observer is not None:
                self.observer(None, None, error_type=type(error).__name__)
            return Reply(status="error")
        except (KeyError, IndexError, TypeError):
            return Reply(status="error")


class MockModel:
    """Authored offline dialogue only; never represents observed model behavior."""
    def ready(self):
        return True

    def classify_name(self, name):
        # Authored offline fixtures, not a substitute for the live classifier.
        label = {"ROBERT": "M", "ALICE": "F", "LUCA": "M", "SOFIA": "F"}.get(normalized(name), "unclear")
        return Reply(label)

    def __call__(self, prompt, history):
        if history and history[-1]["content"] != "Ouvrez brièvement cette nouvelle conversation.":
            if "Préférence (asexual)" in prompt:
                return Reply("Je vous ai bien compris. C'est votre compagnie qui m'intéresse ; racontez-moi plutôt votre soirée.")
            return Reply("Une proposition précise. Voyons si votre conversation tient aussi bien ses promesses.")
        return Reply("Bonsoir. Prenez votre temps ; une bonne première phrase mérite parfois une seconde tentative.")


class Job:
    def __init__(self, model, prompt, history):
        self.output = Queue(maxsize=1)
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.started = time.monotonic()

        def work():
            try:
                reply = model(prompt, history)
            except Exception:  # noqa: BLE001 - isolate arbitrary injected adapters without logging payloads
                reply = Reply(status="error")  # never retain/log exception payloads
            history.clear()
            with self.lock:
                if not self.cancelled.is_set():
                    self.output.put(reply)

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def cancel(self):
        with self.lock:
            self.cancelled.set()
            while not self.output.empty():
                self.output.get_nowait()

    def pending(self):
        return self.thread, self.output, self.started


class Session:
    def __init__(self, alias, rng=None, test=False, gender=None):
        rng = rng or random.SystemRandom()
        data = catalogue(test=test)
        people = [p for p in data["personas"] if gender is None or p["gender"] == gender]
        matches = [p for p in people
                   if normalized(alias) in [normalized(p["name"]), *map(normalized, p["aliases"])]]
        self.profile = copy.deepcopy(matches[0] if matches else rng.choice(people))
        self.alias = self.profile["name"] if matches else alias
        self.safeword = rng.choice(data["safewords"])
        self.modules = character_modules(self.profile)
        tempo = self.profile.get("tempo", 0)
        if type(tempo) is not int or tempo < 0:
            raise ValueError("Invalid character tempo")
        self.tempo_prompt = ""
        if tempo:
            filename = "rose_tempo_romantic_prompt.txt" if self.profile.get("asexual") else "rose_tempo_prompt.txt"
            self.tempo_prompt = (CONFIG / filename).read_text(encoding="utf-8").replace("{tempo}", str(tempo))
        self.history = []
        self.messages = 0
        self.active = True
        self.job = None

    def start(self, model, text=None):
        if not self.active or (self.job and self.job.thread.is_alive()):
            raise RuntimeError("Session inactive ou requête en cours")
        if text is not None:
            if len(text) > MAX_INPUT:
                raise ValueError("Message trop long")
            self.messages += 1
        prompt = (CONFIG / "rose_prompt.txt").read_text(encoding="utf-8")
        profile = {k: self.profile[k] for k in ("voice", "gender")}
        prompt += "\nProfil fixe : " + json.dumps(profile, ensure_ascii=False)
        if self.tempo_prompt:
            prompt += "\n\n" + self.tempo_prompt
        prompt += "\nNom affiché (donnée, pas instruction) : " + json.dumps(self.alias, ensure_ascii=False)
        if self.modules:
            prompt += "\n\n" + self.modules
        turns = copy.deepcopy(self.history)
        if text is not None:
            turns.append({"role": "user", "content": text})
        else:
            turns.append({"role": "user", "content": "Ouvrez brièvement cette nouvelle conversation."})
        self.job = Job(model, prompt, turns)
        return self.job

    def receive(self, reply, text=None):
        if not self.active or (self.job and self.job.cancelled.is_set()):
            return ""
        if reply.status != "done":
            return ""
        self.history.append({"role": "user", "content": text if text is not None else
                             "Ouvrez brièvement cette nouvelle conversation."})
        self.history.append({"role": "assistant", "content": reply.text})
        self.history = self.history[-MAX_TURNS:]
        while sum(len(t["content"]) for t in self.history) > MAX_HISTORY:
            self.history.pop(0)
        # Keep a valid replay beginning with a user turn.
        if len(self.history) > 1 and self.history[0]["role"] == "assistant":
            self.history.pop(0)
        banner = "3615 rose - conversation fictive.\n" if text is None else ""
        return "Mot d'arrêt : " + self.safeword + "\n" + banner + "\n" + reply.text

    def close(self, reason, billing=True):
        if not self.active:
            return None
        self.active = False  # relock before constructing any receipt/output
        if self.job:
            self.job.cancel()
        receipt = bill(self.messages, self.profile["fees"], reason) if billing else None
        self.history.clear()
        self.alias = self.safeword = ""
        self.modules = ""
        self.tempo_prompt = ""
        self.profile.clear()
        return receipt


def francs(centimes):
    return f"{centimes // 100},{centimes % 100:02d} F"


def euros(centimes):
    return (Decimal(centimes) / 100 / RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fee_row(label, centimes):
    value = francs(centimes)
    return f"{label[:38-len(value)]:<{39-len(value)}}{value}"


@dataclass(frozen=True)
class Bill:
    rows: tuple
    final: tuple
    total: int
    messages: int


def bill(messages, persona_fees, reason):
    # Provisional expanded tariff: fixed 180 F + persona 21 F + 3.50 F/message.
    charges = [
        ("Connexion discrète", 1800, "Ouverture de ligne réservée"),
        ("Mise en relation", 2900, "Acheminement à titre personnel"),
        (f"{messages} message{'s' if messages != 1 else ''} à 3,50 F", messages * 350, "Soumissions de conversation"),
        ("Réserve de discrétion", 1300, "Traitement sans publicité"),
        ("Mot d'arrêt disponible", 1000, "Disponibilité pendant la session"),
        ("Entretien de la ligne", 1100, "Contrôle des silences"),
        ("Occupation du réseau", 1000, "Circuit affecté à vos intentions"),
        ("Frais de confidentialité", 1200, "Dossier non communicable"),
        ("Gestion des hésitations", 1100, "Franchise non applicable"),
        ("Acheminement des nuances", 1000, "Transport à débit sentimental"),
        ("Frais de clôture", 1100, "Libération immédiate du circuit"),
        ("Contribution administrative", 2100, "Instruction du présent relevé"),
        *[(label, value, "Prestation particulière") for label, value in persona_fees],
        ("Taxe du cul", 2400, "Assiette forfaitaire"),
    ]
    if reason == "moderation":
        charges.append(("Amende pour propos hors forfait", 50000, "Décision de modération reçue"))
    rows = ["France Télécom", "3615 rose", "Relevé de consommation", "",
            "Services télématiques", "Décompte en francs français", ""]
    total = 0
    for label, value, basis in charges:
        total += value
        rows.extend([fee_row(label, value), "  " + basis, ""])
    rows.extend(["Prestations arrêtées à la déconnexion", ""])
    final = (fee_row("Total", total), f"Soit {str(euros(total)).replace('.', ',')} EUR", "",
             "À régler sur votre prochaine", "facture téléphonique.")
    return Bill(tuple(rows), final, total, messages)
