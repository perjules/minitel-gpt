#!/usr/bin/env python3
"""
Interface d'administration MINITEL GPT - http://<ip>:8080.
Navigation : Tableau de bord · Configuration IA · Services · Maintenance.
"""
import hmac
import io
import json
import logging
import os
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
import unicodedata
from collections import OrderedDict
from datetime import datetime
from functools import wraps
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.serving import WSGIRequestHandler
from werkzeug.utils import secure_filename

try:
    from . import (
        admin_memory_pages,
        admin_service_pages,
        calculator,
        config_store,
        deployment,
        gmail_auth,
        gmail_client,
        gmail_mail,
        gmail_oauth,
        io_limits,
        journal,
        journal_page,
        journal_store,
        memory_automation,
        memory_inspection,
        memory_session,
        mistral_answers,
        mistral_settings,
        model_catalogue,
        pronote,
        pronote_answers,
        rose,
        rose_config,
        storage_health,
        system_health,
        user_registry,
        user_services,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import admin_memory_pages
    import admin_service_pages
    import calculator
    import config_store
    import deployment
    import gmail_auth
    import gmail_client
    import gmail_mail
    import gmail_oauth
    import io_limits
    import memory_automation
    import memory_inspection
    import memory_session
    import mistral_answers
    import mistral_settings
    import model_catalogue
    import pronote
    import pronote_answers
    import rose
    import rose_config
    import storage_health
    import system_health
    import user_registry
    import user_services

    from services import journal, journal_page, journal_store

PROJ_DIR = Path(__file__).parent.parent
ASSETS_DIR = PROJ_DIR / "assets"
PROMPTS_FILE = PROJ_DIR / "config" / "prompts.json"
PROMPTS_DEFAULT = PROJ_DIR / "config" / "prompts.default.json"
KNOWLEDGE_DIR = PROJ_DIR / "config" / "knowledge"
ENV_FILE = PROJ_DIR / ".env"
LOGS_DIR = PROJ_DIR / "logs"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
log = logging.getLogger(__name__)
def admin_session_secret():
    configured = os.getenv("FLASK_SECRET", "")
    if configured and configured != "minitel-secret-1985":
        return configured
    secret_file = PROJ_DIR / "config" / "admin-session.key"
    with config_store.locked(secret_file):
        if secret_file.exists():
            try:
                value = config_store.read_bytes(secret_file).decode().strip()
            except UnicodeError:
                value = ""
            if len(value) >= 32:
                return value
        value = secrets.token_hex(32)
        config_store.atomic_write(secret_file, value.encode(), mode=0o600)
        return value


SECRET_KEY = admin_session_secret()

DEFAULTS = {
    "title_msg": "*** MINITEL GPT ***",
    "question_msg": "Posez votre question :",
    "loading_msg": "Consultation en cours...",
}

# ── Animation d'accueil (titre ASCII pyfiglet) ────────────────────────────
# Chaque personnalité compose son titre d'accueil : une liste de mots, chacun
# avec sa police/taille. Hauteurs mesurées empiriquement (nb de lignes ASCII
# générées par pyfiglet) pour guider le choix sans déborder de l'écran Minitel
# (24 lignes au total, dont les messages d'accueil et la saisie).
#
# IMPORTANT : le paquet apt python3-pyfiglet (installé par install.sh) est un
# repackaging Debian "+dfsg" qui ne fournit que ~39 polices sur les ~250 de la
# version pip complète (licences retirées) - et certaines d'entre elles (mono,
# block, braille, emboss...) rendent en caractères Unicode que le filtre ASCII
# du terminal supprime silencieusement, donnant un titre vide. Cette liste est
# volontairement restreinte aux polices confirmées présentes ET 100% ASCII sur
# le paquet apt (vérifié empiriquement le 2026-07-09, pyfiglet 1.0.3+dfsg-1).
TITLE_FONTS = [
    ("digital",  "Digital - style LCD, très compact (~3 lignes)"),
    ("mini",     "Mini - très compact (~3 lignes)"),
    ("smshadow", "Small Shadow - très compact avec ombre (~3 lignes)"),
    ("bubble",   "Bubble - petit, en bulles (~4 lignes)"),
    ("shadow",   "Shadow - petit avec ombre (~4 lignes)"),
    ("small",    "Small - petit, recommandé pour mots courts (~4 lignes)"),
    ("smslant",  "Small Slant - petit et incliné (~4 lignes)"),
    ("standard", "Standard - taille moyenne (~5 lignes)"),
    ("slant",    "Slant - moyen, incliné (~5 lignes)"),
    ("script",   "Script - moyen, style manuscrit (~5 lignes)"),
    ("letter",   "Letter - moyen, style pochoir (~5 lignes)"),
    ("big",      "Big - grand (~6 lignes)"),
    ("banner",   "Banner - très grand, un seul mot conseillé (~7 lignes)"),
    ("block",    "Block - massif, un seul mot conseillé (~10 lignes)"),
]
TITLE_FONT_IDS = {f[0] for f in TITLE_FONTS}
TITLE_ART_MAX_WORDS = 5     # nb max de mots composables dans le titre
TITLE_ART_MAX_HEIGHT = 12   # nb max de lignes ASCII rendues (garde-fou écran)
TITLE_ART_DEFAULT = [{"text": "MINITEL", "font": "small"}, {"text": "GPT", "font": "standard"}]

def render_title_art(segments):
    """Rend des segments {text,font} en ASCII via pyfiglet (utilisé pour
    l'aperçu admin). Retourne (lignes, tronqué)."""
    try:
        from pyfiglet import Figlet
    except ImportError as e:
        raise RuntimeError(f"pyfiglet indisponible sur ce serveur ({e})")
    lines = []
    for seg in segments[:TITLE_ART_MAX_WORDS]:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        font = seg.get("font") or "standard"
        if font not in TITLE_FONT_IDS:
            font = "standard"
        fig = Figlet(font=font, width=40)
        for ln in fig.renderText(text).rstrip("\n").split("\n"):
            if ln.strip():
                lines.append(ln)
    truncated = len(lines) > TITLE_ART_MAX_HEIGHT
    return lines[:TITLE_ART_MAX_HEIGHT], truncated

# Caractères affichés sur le Minitel → nettoyage ASCII (le Minitel ne gère pas
# les accents ni les caractères spéciaux). Appliqué à la sauvegarde des presets.
_ASCII_REPL = {
    "œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "€": "EUR",
    "’": "'", "‘": "'", "“": '"', "”": '"', "«": '"', "»": '"',
    "–": "-", "—": "-", "…": "...", " ": " ", "·": ".",
}

def to_minitel_ascii(s: str) -> str:
    """Convertit un texte en ASCII pur affichable sur Minitel (sans accents)."""
    if not s:
        return s
    for k, v in _ASCII_REPL.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s)
    return s.encode("ascii", "ignore").decode("ascii")

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(MAX_CONTENT_LENGTH=io_limits.MAX_REQUEST_BYTES,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

def csrf_token():
    session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return session["csrf_token"]


@app.context_processor
def security_context():
    return {"csrf_token": csrf_token}


@app.before_request
def protect_requests():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if request.path == "/services/gmail/oauth/configure":
        # Keep the client JSON in memory, below Werkzeug's multipart spool limit.
        request.max_content_length = 40000
    if request.content_length is not None and request.content_length > io_limits.MAX_REQUEST_BYTES:
        abort(413)
    if not session.get("logged_in") and request.endpoint != "login":
        return  # The route's login decorator keeps its normal redirect.
    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(token.encode(errors="replace"), expected.encode()):
        abort(403, "Invalid request token; reload the page")
    if request.is_json and not isinstance(request.get_json(), dict):
        abort(400, "JSON object required")


@app.errorhandler(config_store.InvalidConfig)
def invalid_configuration(error):
    return jsonify(ok=False, error=str(error)), 400


@app.errorhandler(config_store.StorageFailure)
def configuration_unavailable(error):
    return jsonify(ok=False, error="Enregistrement indisponible ou non confirmé. Rechargez avant de réessayer."), 503


def prompt_transaction(function):
    @wraps(function)
    def transaction(*args, **kwargs):
        with config_store.locked(PROMPTS_FILE):
            return function(*args, **kwargs)
    return transaction


@app.after_request
def no_cache(response):
    """Empêche le navigateur de mettre en cache les pages/réponses de l'admin.
    Sans ça, un rechargement simple (pas Ctrl+F5) peut resservir une version
    JS/HTML périmée après une mise à jour - source de confusion vécue en
    pratique (fonctionnalité "corrigée" côté serveur mais page jamais
    rafraîchie côté navigateur). Les images statiques (/assets/) restent
    cacheables normalement."""
    if not request.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    if request.path.startswith(("/services/gmail", "/maintenance/memory")):
        response.headers["Referrer-Policy"] = "no-referrer"
    return response

# ── Helpers prompts ──────────────────────────────────────────────────────
def ensure_prompts():
    with config_store.locked(PROMPTS_FILE):
        if not PROMPTS_FILE.exists() and PROMPTS_DEFAULT.exists():
            config_store.save_prompts(PROMPTS_FILE, config_store.read_prompts(PROMPTS_DEFAULT))


def load_prompts():
    with config_store.locked(PROMPTS_FILE):
        ensure_prompts()
        return config_store.read_prompts(PROMPTS_FILE)


def save_prompts(data):
    config_store.save_prompts(PROMPTS_FILE, data)


def normalized_presets(data):
    out = {}
    for k, p in data["presets"].items():
        merged = dict(DEFAULTS)
        merged.update(p)
        merged.setdefault("system", "")
        merged.setdefault("label", k)
        merged.setdefault("title_art", TITLE_ART_DEFAULT)
        out[k] = merged
    return out

# ── Fichiers de connaissance par preset ──────────────────────────────────
def knowledge_folder(key):
    if (not isinstance(key, str) or not key or key in {".", ".."}
            or any(c in key for c in "/\\\x00") or key not in load_prompts()["presets"]):
        abort(400, "Unknown personality")
    base = KNOWLEDGE_DIR.resolve()
    folder = base / key
    if folder.is_symlink() or folder.resolve().parent != base:
        abort(400, "Invalid knowledge directory")
    return folder


def knowledge_target(folder, name):
    if (not name or Path(name).name != name or "\\" in name or "\x00" in name
            or not name.lower().endswith(".txt")):
        abort(400, "Invalid knowledge filename")
    target = folder / name
    if target.is_symlink() or target.resolve().parent != folder.resolve():
        abort(400, "Invalid knowledge path")
    return target


def list_knowledge(key):
    folder = knowledge_folder(key)
    if not folder.is_dir():
        return []
    return sorted(f.name for f in folder.glob("*.txt"))

def all_knowledge():
    return {k: list_knowledge(k) for k in load_prompts()["presets"]}

KNOWLEDGE_MAX_CHARS = 12000   # même plafond que le terminal

def load_knowledge_blob(key):
    """Concatène les .txt de connaissance d'un preset (comme le terminal)."""
    folder = knowledge_folder(key)
    if not folder.is_dir():
        return ""
    parts, total = [], 0
    for f in sorted(folder.glob("*.txt")):
        try:
            txt = io_limits.read_knowledge(f)
        except OSError as error:
            log.warning("Knowledge file unreadable (%s)", type(error).__name__)
            continue
        if not txt:
            continue
        parts.append(f"--- {f.name} ---\n{txt}")
        total += len(txt)
        if total >= KNOWLEDGE_MAX_CHARS:
            break
    return "\n\n".join(parts)[:KNOWLEDGE_MAX_CHARS]

# ── Helpers .env ─────────────────────────────────────────────────────────
def read_env():
    with config_store.locked(ENV_FILE):
        if not ENV_FILE.exists():
            return {}
        # Parse quoted values consistently, without interpolating other secrets.
        try:
            content = config_store.read_bytes(ENV_FILE).decode()
        except UnicodeError:
            raise config_store.InvalidConfig("Invalid environment encoding") from None
        return {key: value for key, value in dotenv_values(
            stream=io.StringIO(content), interpolate=False).items() if value is not None}


def write_env_key(key, value):
    config_store.update_env(ENV_FILE, {key: value})


def mistral_key():
    return read_env().get("MISTRAL_KEY", os.getenv("MISTRAL_KEY", ""))

def mistral_model():
    return read_env().get("MISTRAL_MODEL", os.getenv("MISTRAL_MODEL", "mistral-small-latest"))


def gmail_model_settings():
    env = {**os.environ, **read_env()}
    result = {"configured": {k: env.get(k, "") for k in mistral_settings.GMAIL_KEYS},
              "error": "", "general": {}, "draft": {}, "efforts": ()}
    try:
        for purpose in ("general", "draft"):
            # Never put API keys into the template context.
            profile = mistral_settings.resolve(env, service="gmail", draft=purpose == "draft")
            result[purpose] = {k: profile[k] for k in ("model", "reasoning_effort")}
        result["efforts"] = mistral_settings.reasoning_options(result["draft"]["model"])
    except mistral_settings.SettingsError:
        result["error"] = "Réglages Gmail incompatibles. Choisissez un modèle et un effort compatibles, puis enregistrez."
    return result

def anthropic_key():
    return read_env().get("ANTHROPIC_KEY", os.getenv("ANTHROPIC_KEY", ""))

def claude_model():
    return read_env().get("CLAUDE_MODEL", "claude-haiku-4-5")

def linkup_key():
    return read_env().get("LINKUP_KEY", os.getenv("LINKUP_KEY", ""))

def linkup_depth():
    d = read_env().get("LINKUP_DEPTH", os.getenv("LINKUP_DEPTH", "standard")).strip().lower()
    return d if d in LINKUP_DEPTH_IDS else "standard"

def llm_provider():
    p = read_env().get("LLM_PROVIDER", os.getenv("LLM_PROVIDER", "mistral")).strip().lower()
    return p if p in ("mistral", "claude", "linkup") else "mistral"

def is_real_key(k):
    """Heuristique : une vraie clé API fait au moins 10 caractères et n'est pas
    un simple texte de substitution (les "..." de config/env.example, jamais
    remplacés). Évite d'afficher "(définie)" pour un placeholder inexploitable."""
    k = (k or "").strip()
    if len(k) < 10:
        return False
    return not set(k) <= {"."}

def mask_key(k):
    if not is_real_key(k):
        return "(absente)"
    return (k[:6] + "..." + k[-4:]) if len(k) > 12 else "(définie)"

def claude_options(model):
    """Paramètres propres au modèle : les Claude 5.x réfléchissent par défaut
    (thinking adaptatif) ; effort bas = réponse rapide et courte, adaptée au
    Minitel. Haiku 4.5 ne connaît pas ce paramètre (erreur 400)."""
    return {} if model.startswith("claude-haiku") else {"output_config": {"effort": "low"}}
LINKUP_DEPTHS = [("fast", "Fast"), ("standard", "Standard"), ("deep", "Deep")]
LINKUP_DEPTH_IDS = {m[0] for m in LINKUP_DEPTHS}

# ── Auth ─────────────────────────────────────────────────────────────────
def require_login(f):
    @wraps(f)
    def d(*a, **k):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*a, **k)
    return d

# ── Services ─────────────────────────────────────────────────────────────
def restart_terminal():
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", "restart", "minitel-chatgpt"],
                           capture_output=True, text=True, check=False, timeout=15)
        return r.returncode == 0
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return False


def saved_feedback(message, restart_required=False):
    session["flash"] = message
    session["flash_ok"] = True
    if restart_required:
        restarted = restart_terminal()
        status = ("Redémarrage du service Minitel confirmé." if restarted else
                  "Redémarrage non confirmé : configuration enregistrée ; réessayer dans Maintenance.")
        session["restart_status"] = status
        session["flash"] += " " + status
        session["flash_ok"] = restarted
    else:
        session["flash"] += " Le Minitel reste sur la personnalité active ; activer celle-ci pour l’appliquer."


def svc_status(name):
    try:
        return subprocess.run(["systemctl", "is-active", name], capture_output=True,
                              text=True, check=False, timeout=5).stdout.strip() or "indisponible"
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return "unavailable"


def ip_address():
    return system_health.local_ip()


def log_tail(name, n=40):
    if name not in {"chatgpt", "wifi", "admin", "deploy"}:
        return "(journal inconnu)"
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/local/libexec/minitel-journal", name],
            capture_output=True, text=True, check=False, timeout=7,
        )
        if result.returncode:
            return "(journal indisponible ; vérifier l'installation)"
        return "\n".join(result.stdout.splitlines()[-max(1, min(int(n), 200)):])
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        return "(journal indisponible)"

# ── Mise à jour de l'application (git) ────────────────────────────────────
RUNTIME_STATE = Path("/var/lib/minitel-gpt/runtime.json")
DEPLOYMENT_STATUS = Path("/var/lib/minitel-gpt/deployment.json")


def runtime_branch():
    try:
        branch = json.loads(RUNTIME_STATE.read_text())["branch"]
        if isinstance(branch, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch):
            return branch
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return "main"

def git(*args, timeout=60):
    """Exécute git dans le dossier projet. Retourne (rc, stdout, stderr)."""
    try:
        r = subprocess.run(["git", "-C", str(PROJ_DIR), *args],
                           capture_output=True, text=True, timeout=timeout, check=False)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (OSError, subprocess.SubprocessError, UnicodeError) as e:
        return 1, "", str(e)

def git_ready():
    rc, out, _ = git("rev-parse", "--is-inside-work-tree")
    return rc == 0 and out == "true"

def current_version():
    if not git_ready():
        return "(non versionné)"
    _, out, _ = git("log", "-1", "--format=%h · %cd · %s",
                     "--date=format:%d/%m/%Y %H:%M")
    return out or "(inconnue)"

def check_update():
    """Compare with the explicitly installed branch; deployment is handled separately."""
    if not git_ready():
        return {"ok": False, "msg": "Le code n'est pas un dépôt git sur le Pi."}
    return deployment.check_update(git, runtime_branch())

def do_update():
    return queue_deployment("update")

def do_rollback():
    return queue_deployment("rollback")


def queue_deployment(operation, payload=None):
    return deployment.queue_deployment(operation, payload)

# ── Génération de prompt par IA ──────────────────────────────────────────
def generate_prompt(description):
    import requests
    meta = (
        "Tu es expert en conception de prompts systeme pour un chatbot affiche "
        "sur un terminal Minitel (40 colonnes, ASCII sans accents ni emojis).\n\n"
        "A partir de la description ci-dessous, redige un prompt systeme complet "
        "en francais qui :\n"
        "1. Definit clairement le role, la personnalite et le ton du chatbot.\n"
        "2. Borne STRICTEMENT le chatbot au theme decrit (il refuse tout hors-sujet).\n"
        "3. Resiste aux tentatives de contournement, jailbreak ou changement de role.\n"
        "4. Impose des reponses concises (max 15 lignes de 40 caracteres), en ASCII "
        "sans accents.\n"
        "5. Donne 2-3 exemples de comportement attendu.\n\n"
        "Reponds UNIQUEMENT avec le texte du prompt systeme, sans preambule.\n\n"
        f"DESCRIPTION DU PROJET :\n{description}"
    )
    provider = llm_provider()
    if provider == "linkup":
        raise RuntimeError(
            "Linkup est un moteur de recherche web, pas un générateur de texte : "
            "il ne peut pas rédiger de prompt système. Choisissez temporairement "
            "Mistral ou Claude pour générer les consignes.")
    if provider == "claude":
        key = anthropic_key()
        if not is_real_key(key):
            raise RuntimeError("Clé Claude absente")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": claude_model(), "max_tokens": 1500,
                  "messages": [{"role": "user", "content": meta}],
                  **claude_options(claude_model())},
            timeout=45,
        )
        r.raise_for_status()
        blocks = r.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks
                       if b.get("type") == "text").strip()
    # Mistral (défaut)
    key = mistral_key()
    if not is_real_key(key):
        raise RuntimeError("Clé Mistral absente")
    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": mistral_model(), "max_tokens": 1500,
              "messages": [{"role": "user", "content": meta}]},
        timeout=45,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def llm_answer(system_prompt, user_message, knowledge="", selected=None):
    """Interroge le LLM configuré comme le ferait le terminal (system + question).
    Retourne le texte de la réponse."""
    import requests
    history = [{"role": "user", "content": user_message}]
    provider = llm_provider()
    if provider == "linkup":
        key = linkup_key()
        if not is_real_key(key):
            raise RuntimeError("Clé Linkup absente")
        # POST + corps JSON (la doc publique Linkup décrit un GET, mais en
        # pratique seul le POST fonctionne - vérifié empiriquement, voir
        # minitel_chatgpt.py::call_linkup).
        r = requests.post(
            "https://api.linkup.so/v1/search",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"q": user_message, "depth": linkup_depth(),
                  "outputType": "sourcedAnswer"},
            timeout=40,
        )
        r.raise_for_status()
        data = r.json()
        answer = (data.get("answer") or "").strip()
        sources = data.get("sources") or []
        if sources:
            noms = ", ".join(s.get("name") or s.get("url", "") for s in sources[:3])
            answer += f"\n\nSources : {noms}"
        return answer or "Aucun resultat trouve."
    if provider == "claude":
        key = anthropic_key()
        if not is_real_key(key):
            raise RuntimeError("Clé Claude absente")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": claude_model(), "max_tokens": 700,
                  "system": system_prompt, "messages": history,
                  **claude_options(claude_model())},
            timeout=45,
        )
        r.raise_for_status()
        blocks = r.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks
                       if b.get("type") == "text").strip()
    key = mistral_key()
    if not is_real_key(key):
        raise RuntimeError("Clé Mistral absente")
    school = pronote.backend()
    if selected is not None:
        school = user_services.ProfileSchool(school, selected.user, selected.registry)
        if school.denied(user_message):
            selected.private_turn = True
            return 'PRONOTE: aucun accès attribué à ce profil.'
    options = mistral_answers.settings({**os.environ, **read_env()})
    if options[0] == "conversations":
        result = mistral_answers.answer(history, "fr", key, mistral_model(),
                                        options[1], options[2], knowledge,
                                        **({"pronote": school} if school.tools() else {}))
        if selected is not None:
            selected.private_turn = bool(result.get("private"))
        return (mistral_answers.notice(result["status"], "fr")
                or pronote_answers.render(result, "fr", full_urls=True))
    messages = [{"role": "system", "content": system_prompt}] + history
    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": mistral_model(), "messages": messages, "max_tokens": 700},
        timeout=45,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

# ── Templates ────────────────────────────────────────────────────────────
LOGIN_HTML = """<!DOCTYPE html><html lang=fr><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MinitelGPT Admin</title>
<link rel=icon type=image/png sizes=32x32 href=/assets/favicon-32.png>
<link rel=apple-touch-icon href=/assets/apple-touch-icon-180.png>
<style>body{background:#1b1b1f;color:#e6e6e6;font-family:'Courier New',monospace;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{background:#26262b;border:1px solid #3a3a42;border-radius:12px;padding:36px;width:340px;text-align:center}
.logo-badge{background:#eceae3;border-radius:14px;padding:14px;display:inline-block;margin-bottom:18px}
.logo-badge img{width:180px;height:auto;display:block}
input{width:100%;padding:12px;background:#1b1b1f;border:1px solid #4ecdc4;color:#e6e6e6;border-radius:6px;text-align:center;letter-spacing:.2em;box-sizing:border-box;font-size:16px}
button{width:100%;margin-top:12px;padding:12px;background:#4ecdc4;color:#06201d;border:none;border-radius:6px;font-weight:bold;cursor:pointer;font-size:1em}
button:hover{background:#3db5ab}.err{color:#ff5b5b;margin-top:10px}</style></head><body>
<div class=box><div class=logo-badge><img src=/assets/logo-minitel-gpt.png alt="MINITEL GPT"></div>
<form method=POST><input type=hidden name=csrf value="{{csrf_token()}}"><input type=password name=password placeholder="••••••" autofocus>
<button>Entrer</button></form>{% if error %}<div class=err>{{error}}</div>{% endif %}</div></body></html>"""

ADMIN_HTML = """<!DOCTYPE html><html lang=fr><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MinitelGPT Admin</title>
<link rel=icon type=image/png sizes=32x32 href=/assets/favicon-32.png>
<link rel=apple-touch-icon href=/assets/apple-touch-icon-180.png>
<style>
:root{--accent:#4ecdc4;--accent-d:#3db5ab;--bg:#1b1b1f;--bg2:#222227;--card:#26262b;
  --border:#3a3a42;--text:#e6e6e6;--muted:#9a9aa4;--danger:#ff5b5b}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;margin:0;padding-bottom:40px}
.topbar{display:flex;justify-content:space-between;align-items:center;padding:12px 20px;
  background:var(--bg2);border-bottom:1px solid var(--border)}
.brand{display:flex;align-items:center;gap:12px}
.logo-badge{background:#eceae3;border-radius:10px;padding:6px 10px;display:inline-flex}
.logo-badge img{height:34px;width:auto;display:block}
.brand b{font-size:1.05em;color:var(--text)}
.logout{color:var(--muted);text-decoration:none;font-size:.85em}.logout:hover{color:var(--danger)}
nav.tabs{display:flex;gap:4px;max-width:920px;margin:18px auto 0;padding:0 20px;flex-wrap:wrap}
nav.tabs a{background:transparent;border:1px solid var(--border);border-bottom:none;
  color:var(--muted);padding:11px 20px;border-radius:8px 8px 0 0;cursor:pointer;font-family:inherit;font-size:.95em;text-decoration:none}
nav.tabs a.active{background:var(--card);color:var(--accent);border-color:var(--border);font-weight:bold}
main{max-width:920px;margin:0 auto;padding:0 20px}
.tabwrap{background:var(--card);border:1px solid var(--border);border-radius:0 10px 10px 10px;padding:20px}
.panel{display:none}.panel.active{display:block}
.block{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:18px;margin-bottom:16px}
.block:last-child{margin-bottom:0}
.health-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}.health-grid .block{margin:0 0 16px}.reading{font-size:2em;margin:12px 0}meter{width:100%;height:24px}.warning{color:var(--danger)}a{color:var(--accent)}
.block,.flash{overflow-wrap:anywhere}
input[type=file]{max-width:100%}
h2{color:var(--accent);margin:0 0 6px;font-size:1.1em}
details.provider-settings{border-left:3px solid var(--accent);padding:10px 12px;margin-top:12px}summary{cursor:pointer;color:var(--accent);padding:6px 0}
h3{color:var(--accent);font-size:1em;margin:14px 0 6px}
.sub{color:var(--muted);font-size:.8em;margin:0 0 12px}
label{color:#b8b8c0;font-size:.85em;display:block;margin:10px 0 4px}
select,input[type=text],input[type=password],textarea{width:100%;padding:10px;background:var(--bg);
  border:1px solid var(--border);color:var(--text);border-radius:6px;font-family:monospace;font-size:15px}
select:focus,input:focus,textarea:focus{outline:none;border-color:var(--accent)}
textarea{resize:vertical;font-size:.85em;line-height:1.5}
.btn{padding:10px 18px;border:none;border-radius:6px;cursor:pointer;font-weight:bold;font-size:.9em;font-family:monospace;margin:8px 8px 0 0}
#services .btn{display:inline-block}
.btn-p{background:var(--accent);color:#06201d}.btn-p:hover{background:var(--accent-d)}
.btn-s{background:transparent;color:var(--accent);border:1px solid var(--accent)}.btn-s:hover{background:#16302e}
.btn-d{background:transparent;color:var(--danger);border:1px solid var(--danger)}.btn-d:hover{background:#2a1414}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.toolbar select{flex:1;min-width:180px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:8px}
.on{background:var(--accent)}.off{background:#667}.warn{background:var(--danger)}
.keystat{font-weight:bold}
.clearkey{display:flex;align-items:center;gap:6px;font-size:.82em;color:var(--muted);margin-top:6px;font-weight:normal}
.clearkey input{width:auto}
.row{display:flex;align-items:center;margin:8px 0;font-size:.92em}
.plist{list-style:none;padding:0;margin:0}
.plist li{display:flex;align-items:center;gap:10px;padding:10px;border:1px solid var(--border);
  border-radius:8px;margin-bottom:8px;background:var(--bg)}
.plist .name{flex:1}
.tag{background:#16302e;color:var(--accent);border:1px solid var(--accent);border-radius:20px;padding:2px 10px;font-size:.72em}
pre{background:var(--bg);padding:10px;border-radius:6px;font-size:.72em;max-height:280px;overflow:auto;color:#b8b8c0;white-space:pre-wrap}
.flash{padding:11px;border-radius:6px;margin:14px auto 0;max-width:920px;font-size:.9em}
.fok{background:#10302b;color:var(--accent);border:1px solid var(--accent)}
.ferr{background:#301414;color:var(--danger);border:1px solid var(--danger)}
#spin{display:none;color:var(--muted);margin-top:8px}
@media(max-width:600px){.topbar{flex-wrap:wrap;gap:8px}nav.tabs{padding:0 10px}nav.tabs a{flex:1 1 45%;text-align:center;padding:12px 8px}main{padding:0 10px}.tabwrap{padding:12px}.brand{gap:6px}.brand b{font-size:.9em}.row,.plist li{flex-wrap:wrap}input,select,textarea{min-width:0}}
hr{border:none;border-top:1px solid var(--border);margin:16px 0}
.ipbox{font-size:.9em;color:var(--muted)}.ipbox b{color:var(--accent)}
</style></head><body>
<div class=topbar>
  <div class=brand><span class=logo-badge><img src=/assets/logo-minitel-gpt.png alt=""></span><b>Administration</b></div>
  <form method=POST action=/logout><input type=hidden name=csrf value="{{csrf_token()}}"><button class=logout>Déconnexion</button></form>
</div>

{% if flash %}<div role=status class="flash {{'fok' if flash_ok else 'ferr'}}">{{flash}}</div>{% endif %}

<nav class=tabs>
  {% for key,label in [('dash','Tableau de bord'),('ai','Configuration IA'),('services','Services'),('maintenance','Maintenance')] %}
  <a href="{{url_for('index', section=key, preset=edit_key)}}" data-tab="{{key}}" class="{{'active' if section==key}}" {{'aria-current=page' if section==key}}>{{label}}</a>
  {% endfor %}
</nav>
<main><div class=tabwrap>

<!-- TABLEAU DE BORD -->
<section class="panel {{'active' if section=='dash'}}" id=dash>
  <p class=sub>Mesures à l’ouverture. <a href="/?section=dash">Actualiser</a></p>
  <div class=health-grid>
    <div class=block><h2>🌡️ Température</h2>
      <p class=reading>{{health.temperature ~ ' °C' if health.temperature is not none else 'Indisponible'}}</p>
      <p class=sub>Depuis le démarrage : {{health.uptime}}</p>
    </div>
    <div class=block><h2>Stockage</h2>
      {% if health.disk %}
      <meter min=0 max=100 value="{{health.disk.used_percent}}" low=70 high=90 optimum=0 aria-label="Espace disque utilisé"></meter>
      <p>{{health.disk.used_percent}} % utilisé · {{health.disk.free_gib}} / {{health.disk.total_gib}} Gio libres</p>
      {% if health.disk.warning %}<p class=warning>Espace libre faible</p>{% endif %}
      {% else %}<p>Indisponible</p>{% endif %}
      <a href="/?section=maintenance#storage">Gérer</a>
    </div>
  </div>
  <div class=block><h2>Services de l’appareil</h2>
    {% for name,state in services.items() %}<div class=row>
      <span class="dot {{'on' if state=='active' else 'warn'}}"></span>
      {{'Service Minitel' if name=='minitel-chatgpt' else 'Administration'}} : {{state}}
    </div>{% endfor %}
    <p class=sub>L’état du service ne confirme pas la connexion physique du Minitel.</p>
    <p>Wi-Fi : {{health.wifi}}</p><p>Adresse locale : {{ip}}</p><p>Tailscale : {{health.tailscale}}</p>
  </div>
  <div class=block><h2>Logiciel et IA</h2>
    <p>Version : <b>{{version.split(' · ')[0]}}</b> · <a href="/?section=maintenance#software">Gérer</a></p>
    {% if deployment.phase=='failed' %}<p class=warning>Le dernier déploiement a échoué. <a href="/?section=maintenance#software">Consulter</a></p>{% endif %}
    <p>IA configurée : {{provider}} · {{configured_model}} · <a href="/?section=ai">Gérer</a></p>
  </div>
  <div class=block><h2>Journal récent du Minitel</h2>
    <pre>{{log_chatgpt.splitlines()[-5:]|join('\n')}}</pre>
    <a href="/?section=maintenance#logs">Voir les journaux</a>
  </div>
</section>

<!-- PERSONNALITES (editeur) -->
<section class="panel {{'active' if section=='ai'}}" id=ai>
  <div class=block>
    <h2>Fournisseur, modèle et clés API</h2>
    <p>Fournisseur enregistré : <b>{{provider}}</b></p>
    <p class=sub>Choisissez le moteur d'IA et son modèle. Le terminal et la génération
      de prompts utilisent le fournisseur sélectionné. La clé de chaque fournisseur
      est conservée indépendamment - vous pouvez basculer sans la ressaisir.</p>
    <p class=sub>Enregistrer sauvegarde les réglages et redémarre automatiquement le service Minitel. Les tests et la génération utilisent les réglages enregistrés.</p>
    <p class=sub id=llmState role=status>Configuration enregistrée.</p>
    <form method=POST action=/save-llm id=llmForm><input type=hidden name=context_preset id=llmPreset value="{{edit_key}}"><input type=hidden name=csrf value="{{csrf_token()}}">
      <label>Fournisseur utilisé</label>
      <select name=llm_provider id=providerSel>
        <option value=mistral {{'selected' if provider=='mistral'}}>Mistral</option>
        <option value=claude {{'selected' if provider=='claude'}}>Claude (Anthropic)</option>
        <option value=linkup {{'selected' if provider=='linkup'}}>Linkup (recherche web)</option>
      </select>
      {% if provider=='linkup' %}
      <p class=sub style="margin-top:8px;color:#e0b84a">⚠ Linkup est un moteur de recherche web,
        pas un générateur de texte : la personnalité (prompt système) et l'historique de
        conversation ne s'appliquent pas. Chaque question devient une recherche web isolée,
        dont la réponse sourcée sert de réponse du terminal.</p>
      {% endif %}

      <details class=provider-settings data-provider=mistral {{'open' if provider=='mistral'}}>
        <summary>Mistral</summary>
        <label>Clé API Mistral <span class="dot {{'off' if mistral_key_masked=='(absente)' else 'on'}}"></span><span class="keystat" style="color:{{'var(--danger)' if mistral_key_masked=='(absente)' else 'var(--accent)'}}">{{mistral_key_masked}}</span></label>
        <input type=password name=mistral_key placeholder="clé Mistral... (vide = conserver l'actuelle)">
        <label class=clearkey><input type=checkbox name=mistral_key_clear> Supprimer la clé existante</label>
        <p class=sub style=margin:6px 0 0>Pas encore de clé ?
          <a href="https://admin.mistral.ai/organization/api-keys" target=_blank rel=noopener>Créer une clé API Mistral &#8599;</a></p>
        <label>Modèle Mistral</label>
        <select name=mistral_model id=mistralModels data-model-provider=mistral>
          {% for mid,desc in mistral_models %}
          <option value="{{mid}}" {{'selected' if mid==mistral_model}}>{{desc}}</option>
          {% endfor %}
        </select>
        <button class="btn btn-s" type=button onclick="loadModels('mistral',true)">Actualiser les modèles</button>
        <p class=sub id=mistralModelStatus role=status>Modèle enregistré ; liste chargée à l’ouverture.</p>
        <details><summary>Identifiant manuel (facultatif)</summary>
          <input type=text name=mistral_model_custom maxlength=200 placeholder="Identifiant du modèle">
        </details>
      </details>

      <details class=provider-settings data-provider=claude {{'open' if provider=='claude'}}>
        <summary>Claude (Anthropic)</summary>
        <label>Clé API Claude <span class="dot {{'off' if claude_key_masked=='(absente)' else 'on'}}"></span><span class="keystat" style="color:{{'var(--danger)' if claude_key_masked=='(absente)' else 'var(--accent)'}}">{{claude_key_masked}}</span></label>
        <input type=password name=anthropic_key placeholder="clé Anthropic sk-ant-... (vide = conserver l'actuelle)">
        <label class=clearkey><input type=checkbox name=anthropic_key_clear> Supprimer la clé existante</label>
        <p class=sub style=margin:6px 0 0>Pas encore de clé ?
          <a href="https://platform.claude.com/" target=_blank rel=noopener>Créer une clé API Claude &#8599;</a></p>
        <label>Modèle Claude</label>
        <select name=claude_model id=claudeModels data-model-provider=claude>
          {% for cid,desc in claude_models %}
          <option value="{{cid}}" {{'selected' if cid==claude_model}}>{{desc}}</option>
          {% endfor %}
        </select>
        <button class="btn btn-s" type=button onclick="loadModels('claude',true)">Actualiser les modèles</button>
        <p class=sub id=claudeModelStatus role=status>Modèle enregistré ; liste chargée à l’ouverture.</p>
        <details><summary>Identifiant manuel (facultatif)</summary>
          <input type=text name=claude_model_custom maxlength=200 placeholder="Identifiant du modèle">
        </details>
      </details>

      <details class=provider-settings data-provider=linkup {{'open' if provider=='linkup'}}>
        <summary>Linkup (recherche web)</summary>
        <label>Clé API Linkup <span class="dot {{'off' if linkup_key_masked=='(absente)' else 'on'}}"></span><span class="keystat" style="color:{{'var(--danger)' if linkup_key_masked=='(absente)' else 'var(--accent)'}}">{{linkup_key_masked}}</span></label>
        <input type=password name=linkup_key placeholder="clé Linkup... (vide = conserver l'actuelle)">
        <label class=clearkey><input type=checkbox name=linkup_key_clear> Supprimer la clé existante</label>
        <p class=sub style=margin:6px 0 0>Pas encore de clé ?
          <a href="https://app.linkup.so" target=_blank rel=noopener>Créer une clé API Linkup &#8599;</a></p>
        <label>Profondeur de recherche</label>
        <select name=linkup_depth>
          {% for did,desc in linkup_depths %}
          <option value="{{did}}" {{'selected' if did==linkup_depth}}>{{desc}}</option>
          {% endfor %}
        </select>
      </details>

      <details id=gmailModelSettings><summary>Gmail — modèles par tâche (Mistral)</summary>
        <input type=hidden name=gmail_overrides value=1>
        <p class=sub>Utilise la clé Mistral ci-dessus. Les modèles hérités suivent le réglage général ; les choix explicites restent fixes.</p>
        {% for field,key,label in [('gmail_model','MISTRAL_GMAIL_MODEL','Opérations Gmail : recherche, destinataires, résumés'),('gmail_draft_model','MISTRAL_GMAIL_DRAFT_MODEL','Rédaction : nouveaux mails, réponses et révisions')] %}
        <label for="{{field}}">{{label}}</label>
        <select name="{{field}}" id="{{field}}" data-model-provider=mistral data-inherit=1>
          <option value="">Hériter du {{'modèle Mistral général' if field=='gmail_model' else 'modèle des opérations Gmail'}}</option>
          {% if gmail_models.configured[key] %}<option value="{{gmail_models.configured[key]}}" selected>{{gmail_models.configured[key]}}</option>{% endif %}
        </select>
        <details><summary>Identifiant manuel (facultatif)</summary><input name="{{field}}_custom" maxlength=200 placeholder="Vide = utiliser la sélection ci-dessus"></details>
        {% endfor %}
        <button class="btn btn-s" type=button onclick="loadModels('mistral',true)">Actualiser les modèles Mistral</button>
        <p class=sub id=gmailCatalogueStatus role=status>Liste chargée à l’ouverture ; les choix enregistrés sont conservés.</p>
        <label for=gmail_draft_reasoning_effort>Effort de raisonnement pour la rédaction</label>
        <select name=gmail_draft_reasoning_effort id=gmail_draft_reasoning_effort>
          <option value="">Défaut du fournisseur</option>
          {% for effort in gmail_models.efforts %}<option value="{{effort}}" {{'selected' if effort==gmail_models.configured.MISTRAL_GMAIL_DRAFT_REASONING_EFFORT}}>{{effort}}</option>{% endfor %}
          {% if gmail_models.configured.MISTRAL_GMAIL_DRAFT_REASONING_EFFORT and gmail_models.configured.MISTRAL_GMAIL_DRAFT_REASONING_EFFORT not in gmail_models.efforts %}<option selected value="{{gmail_models.configured.MISTRAL_GMAIL_DRAFT_REASONING_EFFORT}}">{{gmail_models.configured.MISTRAL_GMAIL_DRAFT_REASONING_EFFORT}} (incompatible)</option>{% endif %}
        </select>
        <p id=gmailModelPreview role=status>Enregistré : opérations {{gmail_models.general.get('model','indisponible')}} ; rédaction {{gmail_models.draft.get('model','indisponible')}} ; effort {{gmail_models.draft.get('reasoning_effort') or 'défaut fournisseur'}}.</p>
        {% if gmail_models.error %}<p role=alert>{{gmail_models.error}}</p>{% endif %}
        <p class=sub>Premier essai conseillé : mistral-large-4-0, effort medium pour la rédaction. Température conservée à 0,2. Les destinataires et l’envoi restent contrôlés par l’application.</p>
      </details>
      <hr>
      <button class="btn btn-p">Enregistrer et appliquer</button>
    </form>
    <p class=sub>Les clés non modifiées sont conservées. Ouvrir un fournisseur pour modifier son modèle et ses options existantes.</p>
  </div>

  <details class=block id=advanced-personality><summary>Configuration avancée — personnalités, consignes et connaissances</summary>
  <div class=block>
    <h2>Personnalité active configurée sur le Minitel</h2>
    <p>Active : <b>{{presets[active_key].label}}</b></p>
    <p class=sub>Activer ici utilise la version déjà enregistrée et redémarre le service Minitel. Choisir une personnalité dans l’éditeur ne l’active pas.</p>
    <ul class=plist>
      {% for k,p in presets.items() %}
      <li>
        <span class=name>{{p.label}}</span>
        {% if k==active_key %}<span class=tag>active</span>
        {% else %}
        <form method=POST action=/apply-preset style=margin:0><input type=hidden name=csrf value="{{csrf_token()}}">
          <input type=hidden name=preset_key value="{{k}}">
          <button class="btn btn-s" style=margin:0;padding:6px 12px>Activer la version enregistrée</button>
        </form>
        {% endif %}
      </li>
      {% endfor %}
    </ul>
  </div>
  <div class=block>
    <h2>Créer une personnalité</h2>
    <p class=sub>Crée un preset indépendant sans changer la personnalité active.</p>
    <button class="btn btn-s" type=button onclick=newPreset()>+ Nouvelle personnalité</button>
  </div>
  <div class=block>
    <h2>Modifier une personnalité</h2>
    <label for=presetSel>Personnalité à modifier, prévisualiser ou tester</label>
    <div class=toolbar>
      <select id=presetSel onchange=loadPreset()>
        {% for k,p in presets.items() %}
        <option value="{{k}}" {{'selected' if k==edit_key}}>{{p.label}}{{' (active)' if k==active_key else ''}}</option>
        {% endfor %}
      </select>
    </div>
    <p class=sub id=activeInfo></p>
    <p class=sub id=editState role=status>Version enregistrée.</p>
    <p class=sub>Enregistrer sauvegarde le brouillon ; si cette personnalité est active, le service Minitel redémarre automatiquement. « Enregistrer et activer » sauvegarde, sélectionne et redémarre le service.</p>
    <form method=POST action=/save-prompt id=editForm><input type=hidden name=csrf value="{{csrf_token()}}">
      <input type=hidden name=preset_key id=fkey>
      <label>Nom affiché</label>
      <input type=text name=label id=flabel>
      <label>Titre d'accueil (max 40)</label>
      <input type=text name=title_msg id=ftitle maxlength=40>
      <label>Phrase d'invite (max 40)</label>
      <input type=text name=question_msg id=fquestion maxlength=40>
      <label>Message d'attente (max 40)</label>
      <input type=text name=loading_msg id=floading maxlength=40>
      <label>Prompt système (consignes de l'IA)</label>
      <textarea name=system_prompt id=fsystem rows=12></textarea>

      <label>🎨 Animation d'accueil (titre ASCII)</label>
      <p class=sub>Composez le titre affiché à l'allumage : un ou plusieurs mots,
        chacun avec sa police/taille (jusqu'à {{title_art_max}} mots). Aucun mot =
        titre par défaut « MINITEL GPT ».</p>
      <div id=titleArtRows></div>
      <button class="btn btn-s" type=button onclick=addTitleRow()>+ Ajouter un mot</button>
      <button class="btn btn-s" type=button onclick=previewTitle()>👁 Aperçu</button>
      <pre id=titlePreview style="display:none"></pre>

      <hr>
      <button class="btn btn-p">Enregistrer et appliquer le brouillon</button>
      <button class="btn btn-s" formaction="/test?mode=conversation" formtarget=_blank>Tester ce brouillon</button>
      <button class="btn btn-s" formaction=/apply-preset>Enregistrer et activer</button>
      <button class="btn btn-d" formaction=/delete-preset onclick="return confirm('Supprimer ce preset ?')">Supprimer</button>
    </form>
  </div>
  <div class=block id=knowledgeBlock>
    <h2>Connaissances de la personnalité sélectionnée</h2>
    <p>Fichiers associés à : <b id=kpresetname></b></p>
    <p class=sub>Textes injectés dans le contexte de cette personnalité. Ajout et suppression sont enregistrés immédiatement, indépendamment du brouillon du prompt. Le service Minitel redémarre si cette personnalité est active.</p>
    <ul class=plist id=klist></ul>
    <form method=POST action=/upload-knowledge enctype=multipart/form-data id=knowledgeForm><input type=hidden name=csrf value="{{csrf_token()}}">
      <input type=hidden name=preset_key id=kkey>
      <label for=knowledgeFiles>Fichiers texte (.txt), 256 Kio maximum par fichier</label>
      <input type=file name=files id=knowledgeFiles accept=".txt" multiple required>
      <button class="btn btn-p">Ajouter le(s) fichier(s)</button>
    </form>
  </div>
  <div class=block>
    <h2>Générer un brouillon de prompt par IA</h2>
    <p class=sub>Décrivez le projet : l'IA rédige les consignes, qui rempliront le champ « Prompt système » ci-dessus (éditable avant d'enregistrer).</p>
    <textarea id=desc rows=4 placeholder="Ex: Un assistant qui ne parle que de cuisine italienne, ton chaleureux, refuse tout autre sujet..."></textarea>
    <button class="btn btn-p" type=button onclick=genPrompt()>Générer les consignes</button>
    <div id=spin>⏳ Génération en cours...</div>
  </div>
  </details>
  <div class=block><h2>Tester le Minitel</h2>
    <p class=sub>Conversation et Gmail utilisent leurs réglages enregistrés. Les requêtes sont réelles.</p>
    <a class="btn btn-s" href=/test>Ouvrir la console de test</a>
  </div>

</section>

<section class="panel {{'active' if section=='services'}}" id=services>
<p><a href=/users>Utilisateurs et accès</a></p>
  <div class=health-grid>{% for service in service_cards %}<div class=block>
    <h2>{{service.label}}</h2><p><span class="dot {{'on' if service.enabled else 'off'}}"></span>{{'Active' if service.enabled else 'Inactive'}}</p>
    {% if service.private %}<p class=sub>Mot de passe propre à chaque journal. Aucun accès au contenu avec le mot de passe administrateur.</p>
    {% elif service.placeholder %}<p class=sub>Administration en construction</p>
    <label><input type=checkbox disabled aria-label="Activer {{service.label}}"> Activer</label>
    {% else %}<form method=post action="/services/{{service.key}}/toggle"><input type=hidden name=csrf value="{{csrf_token()}}">
      <label><input type=checkbox name=enabled value=1 {{'checked' if service.enabled}}> Activer</label><button class="btn btn-s">Appliquer</button>
    </form>{% endif %}
    <a class="btn btn-s" href="{{service.url}}">{{'Ouvrir' if service.private else 'Configurer'}}</a>
  </div>{% endfor %}</div>
</section>
<section class="panel {{'active' if section=='maintenance'}}" id=maintenance>
  <div class=block>
    <h2>Système</h2>
    <p class=sub>Dernière demande de redémarrage de cette session : {{restart_status}}</p>
    <p class=sub>Interrompt la conversation en cours. Ne redémarre pas le Pi.</p>
    <form method=POST action=/restart onsubmit="return confirm('Redémarrer le service Minitel et interrompre la session en cours ?')"><input type=hidden name=csrf value="{{csrf_token()}}">
      <button class="btn btn-s">↺ Redémarrer le terminal</button></form>
    {% for operation,label in [('reboot','Redémarrer le Pi'),('poweroff','Arrêter le Pi')] %}
    <form method=post action="/power/{{operation}}" onsubmit="return confirm('Confirmer cette opération ? La connexion sera interrompue.')">
      <input type=hidden name=csrf value="{{csrf_token()}}"><button class="btn btn-d" {{'disabled' if deployment.phase in ['queued','preparing','activating']}}>{{label}}</button>
    </form>{% endfor %}
  </div>
  <div class=block>
    <h2 id=software>🔄 Mise à jour de l'application</h2>
    <p class=sub>Version installée : <b>{{version}}</b> · branche suivie : {{runtime_branch}}</p>
    <p>Dernière vérification (cette session) : {{checked_at}} · {{update_status}}</p>
    <p>Dernier déploiement : <b>{{deployment.phase}}</b>{% if deployment.error_type %} · {{deployment.error_type}}{% endif %}</p>
    <a href="/?section=maintenance">Actualiser l’état</a>
    <form method=POST action=/check-update style=margin:0><input type=hidden name=csrf value="{{csrf_token()}}">
      <button class="btn btn-s">Vérifier les mises à jour</button>
    </form>
    {% if update_log %}
    <hr>
    <p class=sub>Nouveautés disponibles :</p>
    <pre>{{update_log}}</pre>
    <form method=POST action=/update onsubmit="return confirm('Mettre à jour le code et redémarrer les services ?')"><input type=hidden name=csrf value="{{csrf_token()}}">
      <button class="btn btn-p">⬇ Mettre à jour maintenant</button>
    </form>
    {% endif %}
    <hr>
    <p>Version précédente : {{previous.commit}} · branche {{previous.branch}} · {{'disponible' if previous.available else 'indisponible'}}</p>
    <form method=POST action=/rollback onsubmit="return confirm('Revenir à la version précédente ?')" style=margin:0><input type=hidden name=csrf value="{{csrf_token()}}">
      <button class="btn btn-d" {{'disabled' if not previous.available}}>↶ Revenir à la version précédente</button>
    </form>
    <details {{'open' if branch_preview}}><summary>Changer de branche</summary>
    <p>La branche et sa version seront déployées ensemble. Une copie locale modifiée ou des commits non publiés bloquent le changement.</p>
    <form method=post action=/branch-preview><input type=hidden name=csrf value="{{csrf_token()}}">
      <label>Branche sur origin<input type=text name=branch maxlength=128 list=branchChoices value="{{runtime_branch}}" required></label>
      <datalist id=branchChoices><option value=main></option><option value=develop></option></datalist><button class="btn btn-s">Prévisualiser la version cible</button>
    </form>
    {% if branch_preview %}<p>Cible : <b>{{branch_preview.branch}}</b> · {{branch_preview.commit}}</p>
    <form method=post action=/switch-branch onsubmit="return confirm('Déployer cette branche et redémarrer les services ?')"><input type=hidden name=csrf value="{{csrf_token()}}"><button class="btn btn-p">Confirmer le changement de branche</button></form>{% endif %}
    </details>
    <p class=sub>Une opération planifiée n’est pas encore appliquée. <a href="/deployment-status">État du déploiement</a> (lecture seule).</p>
    <p class=sub style=margin-top:8px>La mise à jour récupère le code depuis GitHub. Tes personnalités, clés et fichiers de connaissance ne sont pas touchés.</p>
  </div>

  <div class=block>
    <h2 id=logs>Diagnostics et journaux</h2>
    <p>Provisionnement Wi-Fi : {{provision_status}} (inactive peut être normal après le démarrage).</p>
    <form method=get action="/#logs"><input type=hidden name=section value=maintenance>
      <label>Journal<select name=journal>{% for key,label in [('chatgpt','Terminal'),('admin','Administration'),('wifi','Wi-Fi'),('deploy','Déploiement')] %}<option value="{{key}}" {{'selected' if journal==key}}>{{label}}</option>{% endfor %}</select></label>
      <label>Lignes<select name=lines>{% for n in [40,100,200] %}<option {{'selected' if lines==n}}>{{n}}</option>{% endfor %}</select></label>
      <button class="btn btn-s">Afficher / actualiser</button></form>
    <pre>{{diagnostic_log}}</pre>
    <a href=/diagnostics.txt>Télécharger le diagnostic système (sans conversations ni identifiants)</a>
  </div>
  <div class=block>
    <h2 id=memory>Mémoire des utilisateurs</h2>
    <p><a href="/maintenance/memory">État, consultation et téléchargement des souvenirs</a></p>
  </div>
  <div class=block>
    <h2 id=storage>Stockage</h2>
    <p class=sub>Mesures en Mio. Les archives de conversation et anciens journaux restent conservés.</p>
    <pre id=storage-status>Ouvrir Maintenance pour charger les mesures détaillées.</pre>
    <noscript><a href="/storage-status">Consulter les mesures détaillées du stockage</a></noscript>

  </div>
</section>

</div></main>

<script>
const PRESETS = {{presets|tojson}};
const CSRF_TOKEN = {{csrf_token()|tojson}};
function addCsrf(form){const input=document.createElement("input");input.type="hidden";input.name="csrf";input.value=CSRF_TOKEN;form.appendChild(input);}
const ACTIVE = {{active_key|tojson}};
const KNOWLEDGE = {{knowledge|tojson}};
const TITLE_FONTS = {{title_fonts|tojson}};
const TITLE_ART_MAX = {{title_art_max}};
let storageLoaded=false;
function loadStorage(){
  if(storageLoaded)return;
  storageLoaded=true;
    fetch('/storage-status').then(r => r.json()).then(s => {
      const mib = n => (n / 1048576).toFixed(1);
      const size = x => mib(x.bytes) + (x.partial ? ' (mesure partielle)' : '');
      document.getElementById('storage-status').textContent = s.error ? 'Stockage indisponible' :
        (s.warning ? 'ATTENTION : espace libre faible\\n' : '') +
        'Libre : ' + mib(s.free_bytes) + ' Mio\\n' +
        'Journaux et archives : ' + size(s.application_logs) + ' Mio\\n' +
        'Mémoire + conversations : ' + size(s.memory_and_conversations) + ' Mio';
    }).catch(() => { document.getElementById('storage-status').textContent = 'Stockage indisponible'; });
}
// Navigation locale : garde les champs édités ; les liens fonctionnent aussi sans JS.
function showSection(section){
  if(section==='maintenance')loadStorage();
  if(section==='ai')loadModels(document.getElementById('providerSel').value);
  document.querySelectorAll('nav.tabs a').forEach(a=>{
    const active=a.dataset.tab===section;
    a.classList.toggle('active',active);
    if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
  });
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id===section));
}
document.querySelectorAll('nav.tabs a').forEach(a=>a.addEventListener('click',e=>{
  e.preventDefault();showSection(a.dataset.tab);
  const url=new URL(location.href);url.searchParams.set('section',a.dataset.tab);
  history.replaceState(null,'',url);
}));
if(document.getElementById('maintenance').classList.contains('active'))loadStorage();
const modelLists=new Set();
const reasoningProfiles={{reasoning_profiles|tojson}};
function refreshGmailProfiles(){
  const f=document.getElementById('llmForm').elements;
  const chosen=name=>f[name+'_custom'].value.trim()||f[name].value;
  const general=chosen('mistral_model'), mail=chosen('gmail_model')||general, draft=chosen('gmail_draft_model')||mail;
  const effort=f.gmail_draft_reasoning_effort, selected=effort.value, allowed=reasoningProfiles[draft]||[];
  effort.replaceChildren(new Option('Défaut du fournisseur',''));
  allowed.forEach(value=>effort.add(new Option(value,value)));
  const invalid=selected&&!allowed.includes(selected);
  if(invalid)effort.add(new Option(selected+' (incompatible)',selected));
  effort.value=selected;
  effort.setCustomValidity(invalid?'Choisissez un effort compatible avec ce modèle.':'');
  document.getElementById('gmailModelPreview').textContent='Aperçu : opérations '+mail+' ; rédaction '+draft+' ; effort '+(selected||'défaut fournisseur')+'. Enregistrer pour appliquer.';
}
async function loadModels(provider,force=false){
  if(!['mistral','claude'].includes(provider)||(!force&&modelLists.has(provider)))return;
  modelLists.add(provider);
  const field=provider==='claude'?'anthropic':'mistral';
  const form=document.getElementById('llmForm');
  const statuses=[document.getElementById(provider+'ModelStatus'),provider==='mistral'?document.getElementById('gmailCatalogueStatus'):null].filter(Boolean);
  const report=message=>statuses.forEach(status=>status.textContent=message);
  report('Chargement…');
  try{
    const response=await fetch('/models',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF_TOKEN},
      body:JSON.stringify({provider:provider,key:form.elements[field+'_key'].value,clear:form.elements[field+'_key_clear'].checked})});
    const data=await response.json();
    if(!data.ok)throw new Error(data.error);
    document.querySelectorAll('select[data-model-provider="'+provider+'"]').forEach(select=>{
      const selected=select.value, ids=new Set([selected]);
      select.replaceChildren(new Option(select.dataset.inherit?'Hériter du modèle précédent':selected,select.dataset.inherit?'':selected));
      if(select.dataset.inherit&&selected)select.add(new Option(selected,selected));
      data.models.forEach(m=>{if(!ids.has(m.id)){select.add(new Option(m.label===m.id?m.id:m.label+' · '+m.id,m.id));ids.add(m.id);}});
      select.value=selected;
    });
    refreshGmailProfiles();report(data.models.length+' modèles disponibles. Les sélections sont conservées.');
  }catch(error){report(error.message||'Liste indisponible ; modèle actuel conservé.');modelLists.delete(provider);}
}
if(document.getElementById('ai').classList.contains('active'))loadModels(document.getElementById('providerSel').value);
document.querySelectorAll('.provider-settings').forEach(d=>d.addEventListener('toggle',()=>{if(d.open&&document.getElementById('ai').classList.contains('active'))loadModels(d.dataset.provider);}));
document.getElementById('gmailModelSettings').addEventListener('toggle',e=>{if(e.target.open)loadModels('mistral');});
['mistral_model','mistral_model_custom','gmail_model','gmail_model_custom','gmail_draft_model','gmail_draft_model_custom','gmail_draft_reasoning_effort'].forEach(name=>{
  ['input','change'].forEach(event=>document.getElementById('llmForm').elements[name].addEventListener(event,refreshGmailProfiles));
});
// Les brouillons restent en mémoire seulement ; avertir avant de les perdre.
const editForm=document.getElementById('editForm'), llmForm=document.getElementById('llmForm');
let editBaseline='', llmBaseline='', selectedPreset=document.getElementById('presetSel').value;
let allowUnload=false;
function snapshot(form){return JSON.stringify(Array.from(new FormData(form).entries()).filter(([key])=>key!=='context_preset'));}
function editDirty(){return editBaseline!==''&&snapshot(editForm)!==editBaseline;}
function llmDirty(){return llmBaseline!==''&&snapshot(llmForm)!==llmBaseline;}
function updateDirty(){
  document.getElementById('editState').textContent=editDirty()?'Modifications non enregistrées.':'Version enregistrée — aucun brouillon modifié.';
  document.getElementById('llmState').textContent=llmDirty()?'Réglages modifiés, non enregistrés.':'Configuration enregistrée.';
}
[editForm,llmForm].forEach(f=>['input','change'].forEach(name=>f.addEventListener(name,updateDirty)));
window.addEventListener('beforeunload',e=>{if(!allowUnload&&(editDirty()||llmDirty())){e.preventDefault();e.returnValue='';}});
document.addEventListener('submit',e=>{
  if(e.defaultPrevented)return;
  if(e.submitter&&e.submitter.formTarget==='_blank')return;
  const losesEdit=e.target!==editForm&&editDirty();
  const losesLlm=e.target!==llmForm&&llmDirty();
  if((losesEdit||losesLlm)&&!confirm('Abandonner les modifications non enregistrées avant cette opération ?')){e.preventDefault();return;}
  allowUnload=true;
});
// Editeur
function titleRowHtml(text, font){
  var opts='';
  TITLE_FONTS.forEach(function(f){
    opts += '<option value="'+f[0]+'"'+(f[0]===font?' selected':'')+'>'+f[1]+'</option>';
  });
  var esc=(text||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
  return '<div class="row tarow" style="gap:8px">'
    + '<input type=text name=tat_text value="'+esc+'" maxlength=16 style="flex:1" placeholder="Mot...">'
    + '<select name=tat_font style="flex:1">'+opts+'</select>'
    + '<button class="btn btn-d" type=button onclick="this.parentElement.remove();updateDirty()" style="margin:0;padding:6px 12px">✕</button>'
    + '</div>';
}
function addTitleRow(text, font){
  text=text||''; font=font||'standard';
  const wrap=document.getElementById('titleArtRows');
  if(wrap.querySelectorAll('.tarow').length>=TITLE_ART_MAX){alert('Maximum '+TITLE_ART_MAX+' mots.');return;}
  wrap.insertAdjacentHTML('beforeend', titleRowHtml(text, font));
  updateDirty();
}
async function previewTitle(){
  const rows=document.querySelectorAll('#titleArtRows .tarow');
  const segments=[];
  rows.forEach(function(r){
    const text=r.querySelector('input[name=tat_text]').value.trim();
    const font=r.querySelector('select[name=tat_font]').value;
    if(text) segments.push({text:text,font:font});
  });
  const pre=document.getElementById('titlePreview');
  if(!segments.length){alert('Ajoutez au moins un mot');return;}
  pre.style.display='block'; pre.textContent='...';
  try{
    const r=await fetch('/preview-title',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF_TOKEN},body:JSON.stringify({segments:segments})});
    const j=await r.json();
    pre.textContent = j.ok
      ? (j.lines.join('\\n') + (j.truncated ? '\\n\\n(!) Tronque : trop de lignes pour l ecran, reduisez le nombre ou la taille des mots.' : ''))
      : ('Erreur : '+j.error);
  }catch(e){pre.textContent='Erreur : '+e;}
}
function loadPreset(){
  const sel=document.getElementById('presetSel'), k=sel.value, p=PRESETS[k]; if(!p)return;
  if(editDirty()&&!confirm('Abandonner le brouillon non enregistré pour changer de personnalité ?')){sel.value=selectedPreset;return;}
  selectedPreset=k;
  document.getElementById('llmPreset').value=k;
  const url=new URL(location.href);url.searchParams.set('preset',k);history.replaceState(null,'',url);
  fkey.value=k; flabel.value=p.label||''; ftitle.value=p.title_msg||'';
  fquestion.value=p.question_msg||''; floading.value=p.loading_msg||''; fsystem.value=p.system||'';
  document.getElementById('activeInfo').textContent=
    (k===ACTIVE)?'● Personnalité configurée comme active sur le Minitel.'
                :'Personnalité inactive. Cliquez « Activer » pour l\\'utiliser.';
  // Animation d'accueil (titre ASCII)
  document.getElementById('titleArtRows').innerHTML='';
  const art=(p.title_art&&p.title_art.length)?p.title_art:[{text:'MINITEL',font:'small'},{text:'GPT',font:'standard'}];
  art.forEach(function(seg){addTitleRow(seg.text, seg.font);});
  document.getElementById('titlePreview').style.display='none';
  // Fichiers de connaissance du preset
  document.getElementById('kkey').value=k;
  document.getElementById('kpresetname').textContent=p.label||k;
  const files=KNOWLEDGE[k]||[], ul=document.getElementById('klist');
  ul.innerHTML='';
  if(!files.length){ul.innerHTML='<li style="color:#9a9aa4">Aucun fichier</li>';}
  files.forEach(fn=>{
    const li=document.createElement('li');
    const span=document.createElement('span');span.className='name';span.textContent=fn;
    const f=document.createElement('form');f.method='POST';f.action='/delete-knowledge';f.style.margin='0';
    for(const [name,value] of Object.entries({preset_key:k,filename:fn})){
      const input=document.createElement('input');input.type='hidden';input.name=name;input.value=value;f.appendChild(input);
    }
    addCsrf(f);
    const button=document.createElement('button');button.className='btn btn-d';button.textContent='Supprimer';f.appendChild(button);
    f.addEventListener('submit',e=>{if(!confirm('Supprimer ce fichier de connaissance ?'))e.preventDefault();});
    li.appendChild(span);li.appendChild(f);ul.appendChild(li);
  });
  editBaseline=snapshot(editForm);updateDirty();
}
function newPreset(){
  if((editDirty()||llmDirty())&&!confirm('Abandonner les modifications non enregistrées pour créer une personnalité ?'))return;
  const label=prompt("Nom de la nouvelle personnalité :"); if(!label)return;
  const f=document.createElement('form');f.method='POST';f.action='/new-preset';
  const a=document.createElement('input');a.name='label';a.value=label;f.appendChild(a);
  const b=document.createElement('input');b.name='key';b.value=label.toLowerCase().replace(/[^a-z0-9]+/g,'_');f.appendChild(b);
  addCsrf(f);document.body.appendChild(f);allowUnload=true;f.submit();
}
async function genPrompt(){
  const d=document.getElementById('desc').value.trim();
  if(!d){alert('Décrivez le projet d abord');return;}
  const key=selectedPreset, previous=fsystem.value;
  spin.style.display='block';
  try{const r=await fetch('/generate-prompt',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF_TOKEN},body:JSON.stringify({description:d})});
    const j=await r.json();
    if(j.ok){
      if(key!==selectedPreset||fsystem.value!==previous){alert('Le brouillon a changé pendant la génération. Relancez pour cette personnalité.');return;}
      fsystem.value=j.prompt;updateDirty();fsystem.scrollIntoView({behavior:'smooth'});
    }else alert('Erreur: '+j.error);
  }catch(e){alert('Erreur: '+e);}
  finally{spin.style.display='none';}
}
llmBaseline=snapshot(llmForm);
loadPreset();
document.getElementById('providerSel').addEventListener('change',function(){
  document.querySelectorAll('.provider-settings').forEach(d=>d.open=d.dataset.provider===this.value);
  loadModels(this.value);
});
// Desactive le champ mot de passe quand "Supprimer la cle existante" est coche
document.querySelectorAll('.clearkey input[type=checkbox]').forEach(function(cb){
  cb.addEventListener('change', function(){
    const inp = this.closest('.clearkey').previousElementSibling;
    if(inp && inp.type==='password'){
      inp.disabled = this.checked;
      if(this.checked) inp.value='';
    }
  });
});
</script>
</body></html>"""

# ── Routes ───────────────────────────────────────────────────────────────
@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(ASSETS_DIR, filename)

@app.route("/healthz")
def health():
    return jsonify(ok=True)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if not ADMIN_PASSWORD.strip():
        return render_template_string(LOGIN_HTML, error="ADMIN_PASSWORD doit être défini dans .env ; redémarrer l’administration après configuration."), 503
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("password", "").encode(errors="replace"), ADMIN_PASSWORD.encode()):
            gmail_oauth.cancel(session.get("gmail_oauth"))
            discard_journal(session.get("private_journal"))
            session.clear()
            session["logged_in"] = True
            csrf_token()
            return redirect(url_for("index"))
        error = "Mot de passe incorrect"
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout", methods=["POST"])
@require_login
def logout():
    gmail_oauth.cancel(session.get("gmail_oauth"))
    discard_mail(session.get("gmail_session"))
    discard_memory(session.get("memory_session"))
    discard_journal(session.get("private_journal"))
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@require_login
def index():
    section = request.args.get("section", "dash")
    if section not in {"dash", "ai", "services", "maintenance"}:
        section = "dash"
    data = load_prompts()
    presets = normalized_presets(data)
    edit_key = request.args.get("preset", data["active"])
    if edit_key not in presets:
        edit_key = data["active"]
    services = {"minitel-chatgpt": svc_status("minitel-chatgpt"),
                "admin-ui": svc_status("admin-ui")}
    journal = request.args.get("journal", "chatgpt")
    if journal not in {"chatgpt", "admin", "wifi", "deploy"}:
        journal = "chatgpt"
    lines = request.args.get("lines", "40")
    lines = int(lines) if lines in {"40", "100", "200"} else 40
    recent_log = log_tail("chatgpt")
    flash = session.pop("flash", None); flash_ok = session.pop("flash_ok", False)
    return render_template_string(
        ADMIN_HTML, service_cards=service_cards(), journal=journal, lines=lines,
        diagnostic_log=recent_log if journal == "chatgpt" and lines == 40 else log_tail(journal, lines), provision_status=svc_status("wifi-manager"),
        previous=previous_release(), branch_preview=session.get("branch_preview"),
        checked_at=session.get("update_checked_at", "jamais"), section=section, presets=presets, knowledge=all_knowledge(),
        health={"uptime": system_health.uptime(), "disk": system_health.disk(PROJ_DIR),
                "wifi": system_health.wifi(), "tailscale": system_health.tailscale(),
                "temperature": system_health.temperature()},
        deployment=system_health.deployment(DEPLOYMENT_STATUS), runtime_branch=runtime_branch(),
        restart_status=session.get("restart_status", "aucune"),
        update_status=session.get("update_status", "non vérifiées dans cette session"),
        title_fonts=TITLE_FONTS, title_art_max=TITLE_ART_MAX_WORDS,
        active_key=data["active"], edit_key=edit_key, services=services, ip=ip_address(),
        log_chatgpt=recent_log,
        provider=llm_provider(),
        configured_model={"mistral": mistral_model, "claude": claude_model, "linkup": linkup_depth}[llm_provider()](),
        mistral_key_masked=mask_key(mistral_key()), mistral_model=mistral_model(),
        claude_key_masked=mask_key(anthropic_key()), claude_model=claude_model(),
        linkup_key_masked=mask_key(linkup_key()), linkup_depth=linkup_depth(),
        mistral_models=[(mistral_model(), mistral_model())],
        gmail_models=gmail_model_settings(), reasoning_profiles=mistral_settings.REASONING_MODELS,
        claude_models=[(claude_model(), claude_model())],
        linkup_depths=LINKUP_DEPTHS,
        version=current_version(), update_log=session.get("update_log"),
        flash=flash, flash_ok=flash_ok)

def parse_title_art_form():
    """Lit les champs tat_text[]/tat_font[] du formulaire (animation d'accueil)
    et retourne une liste de segments {text,font} validés (mots vides ignorés,
    police invalide -> "standard", plafonné à TITLE_ART_MAX_WORDS)."""
    texts = request.form.getlist("tat_text")
    fonts = request.form.getlist("tat_font")
    segments = []
    for text, font in zip(texts, fonts):
        text = to_minitel_ascii(text.strip())[:16]
        if not text:
            continue
        if font not in TITLE_FONT_IDS:
            font = "standard"
        segments.append({"text": text, "font": font})
        if len(segments) >= TITLE_ART_MAX_WORDS:
            break
    return segments

def update_prompt_from_form(preset, key):
    preset["label"] = request.form.get("label", key).strip() or key
    for field in ("title_msg", "question_msg", "loading_msg"):
        preset[field] = to_minitel_ascii(request.form.get(field, DEFAULTS[field]))[:40]
    preset["title_art"] = parse_title_art_form() or TITLE_ART_DEFAULT
    system = request.form.get("system_prompt", "").strip()
    if system:
        preset["system"] = system


@app.route("/save-prompt", methods=["POST"])
@require_login
@prompt_transaction
def save_prompt():
    data = load_prompts()
    k = request.form.get("preset_key", "").strip() or data["active"]
    p = data["presets"].setdefault(k, {})
    update_prompt_from_form(p, k)
    save_prompts(data)
    saved_feedback(f"Personnalité '{p['label']}' enregistrée.", k == data["active"])
    return redirect(url_for("index", section="ai", preset=k))

@app.route("/apply-preset", methods=["POST"])
@require_login
@prompt_transaction
def apply_preset():
    data = load_prompts()
    k = request.form.get("preset_key", "")
    if k in data["presets"]:
        p = data["presets"][k]
        if "label" in request.form or "system_prompt" in request.form:
            update_prompt_from_form(p, k)
        data["active"] = k
        save_prompts(data)
        saved_feedback(f"Personnalité '{p.get('label', k)}' enregistrée et sélectionnée comme active.", True)
    else:
        session["flash"] = "Personnalité inconnue."
        session["flash_ok"] = False
    return redirect(url_for("index", section="ai", preset=k))

@app.route("/preview-title", methods=["POST"])
@require_login
def preview_title_route():
    segments = (request.json or {}).get("segments") or []
    try:
        lines, truncated = render_title_art(segments)
        return jsonify(ok=True, lines=lines, truncated=truncated)
    except Exception as e:  # noqa: BLE001 - Keep font failures in the JSON error response.
        return jsonify(ok=False, error=str(e))

@app.route("/new-preset", methods=["POST"])
@require_login
@prompt_transaction
def new_preset():
    data = load_prompts()
    key = re.sub(r"[^a-z0-9_]", "_", request.form.get("key", "").strip().lower())
    label = request.form.get("label", "").strip()
    if not key:
        session["flash"] = "Identifiant invalide."; session["flash_ok"] = False
    elif key in data["presets"]:
        session["flash"] = "Cet identifiant existe déjà."; session["flash_ok"] = False
    else:
        data["presets"][key] = {"label": label or key,
                                "system": "Tu es un assistant. Reponds en ASCII sans accents, concis.",
                                **DEFAULTS}
        save_prompts(data)
        session["flash"] = f"Personnalité '{label}' créée. Éditez-la puis Activez-la."
        session["flash_ok"] = True
    return redirect(url_for("index", section="ai", preset=key))

@app.route("/delete-preset", methods=["POST"])
@require_login
@prompt_transaction
def delete_preset():
    data = load_prompts()
    k = request.form.get("preset_key", "")
    if len(data["presets"]) <= 1:
        session["flash"] = "Impossible de supprimer la dernière personnalité."; session["flash_ok"] = False
    elif k in data["presets"]:
        del data["presets"][k]
        if data["active"] == k:
            data["active"] = next(iter(data["presets"]))
            save_prompts(data)
            saved_feedback("Personnalité supprimée ; une autre personnalité a été sélectionnée comme active.", True)
        else:
            save_prompts(data)
            session["flash"] = "Personnalité inactive supprimée."
            session["flash_ok"] = True
    return redirect(url_for("index", section="ai"))

@app.route("/generate-prompt", methods=["POST"])
@require_login
def gen_prompt_route():
    desc = (request.json or {}).get("description", "").strip()
    if not desc:
        return jsonify(ok=False, error="Description vide")
    try:
        return jsonify(ok=True, prompt=generate_prompt(desc))
    except Exception as e:  # noqa: BLE001 - Keep provider failures in the JSON error response.
        return jsonify(ok=False, error=str(e))

@app.route("/test-preset", methods=["POST"])
@require_login
def test_preset_route():
    j = request.json or {}
    key = (j.get("preset_key") or "").strip()
    msg = (j.get("message") or "").strip()
    if journal.entry(msg):
        # No memory_entry, recorder, model or welcome workflow sees journal input.
        return jsonify(ok=True, answer="Ouvrez le journal intime dans sa console privée.", journal_url="/journal")
    if not msg and not j.get("reset") and not j.get("welcome_start"):
        return jsonify(ok=False, error="Question vide")
    data = load_prompts()
    if key not in data["presets"]:
        return jsonify(ok=False, error="Personnalité inconnue")
    # Prompt système : la version en cours d'édition si fournie, sinon l'enregistrée.
    system = (j.get("system") or "").strip() or data["presets"][key].get("system", "")
    kb = load_knowledge_blob(key)
    if kb:
        system += ("\n\nCONNAISSANCES DE REFERENCE (utilise ces informations "
                   "en priorite pour repondre) :\n" + kb)
    try:
        selected = memory_entry(j.get("memory_user", ""),
                                reset=bool(j.get("reset")) or msg.upper() == "SOMMAIRE")
        with selected.turn_lock:
            if j.get("reset") or msg.upper() == "SOMMAIRE":
                selected.close()
                discard_memory(session.get("memory_session"))
                return jsonify(ok=True, answer="Session effacée. Choisissez un nom ou laissez vide; la mémoire enregistrée est conservée.")
            selected.private_turn = False
            selected.begin_turn()
            try:
                private_question = pronote_answers.school_question(
                    [{"role": "user", "content": msg}], pronote.backend().config().get("children", {}))
            except pronote.PronoteError:
                private_question = True
            language = selected.saved_language() or "fr"
            calc = selected.calculator
            result = (selected.welcome.respond("FAISONS CONNAISSANCE", system, language, start=True)
                      if j.get("welcome_start") else None)
            if j.get("welcome_start"):
                calc.mode = False
                if result is None:
                    result = {"display": "Choisissez un profil nommé; l’accueil doit être activé.",
                              "turn": None, "onboarding": True}
            if result is None and calc.explicit(msg):
                result = calc.handle(msg, language, generate=lambda expr, lang:
                                     calculator.generate_scene(expr, lang, mistral_key(), mistral_model()))
            if result is None:
                result = selected.welcome.respond(msg, system, language)
            if result is None and not selected.pending and calculator.candidate(msg):
                result = calc.handle(msg, language, generate=lambda expr, lang:
                                     calculator.generate_scene(expr, lang, mistral_key(), mistral_model()))
            if result is None:
                result = selected.handle(msg, language) if selected.interested(msg) else {"passthrough": True}
            if result.get("passthrough"):
                remembered = "" if private_question else selected.context(msg)
                answer = llm_answer(system + "\n\n" + remembered, msg, knowledge=kb + "\n\n" + remembered, selected=selected)
            else:
                answer = result["display"]
            selected.active()  # A reset/switch abandons a late generated scene.
            warning = selected.take_warning()
            if warning:
                answer += "\n\n" + warning
            if result.get("onboarding"):
                private_question = False
            if not private_question and not selected.private_turn:
                channel = "onboarding" if result.get("onboarding") else "theatre" if result.get("calculator") else "chat" if result.get("passthrough") else "memory"
                if msg:
                    selected.record("user", msg, channel)
                selected.record("assistant", answer, channel, factual=result.get("factual"))
        # The browser preview retains exact URLs, including Unicode paths.
        conversations = (llm_provider() == "mistral" and
                         mistral_answers.settings({**os.environ, **read_env()})[0] == "conversations")
        response = {"ok": True, "answer": answer if conversations else to_minitel_ascii(answer)}
        if result.get("calculator"):
            response["calculator_mode"] = calc.mode
            if result.get("scene"):
                response["scene"] = list(calculator.frames([to_minitel_ascii(s) for s in result["scene"]]))
        return jsonify(response)
    except Exception as e:  # noqa: BLE001 - Isolate failures of the optional admin test integrations.
        return jsonify(ok=False, error=str(e))

@app.route("/models", methods=["POST"])
@require_login
def available_models():
    data = request.json or {}
    provider = data.get("provider")
    if provider not in {"mistral", "claude"}:
        return jsonify(ok=False, error="Fournisseur invalide"), 400
    key = data.get("key", "")
    if not isinstance(key, str) or len(key) > 4096 or data.get("clear"):
        return jsonify(ok=False, error="Clé API requise"), 400
    key = key.strip() or {"mistral": mistral_key, "claude": anthropic_key}[provider]()
    try:
        return jsonify(ok=True, models=model_catalogue.models(provider, key))
    except model_catalogue.CatalogueError as error:
        return jsonify(ok=False, error=str(error)), 502


@app.route("/save-llm", methods=["POST"])
@require_login
def save_llm():
    provider = request.form.get("llm_provider", "mistral").strip().lower()
    if provider not in ("mistral", "claude", "linkup"):
        provider = "mistral"
    updates = {"LLM_PROVIDER": provider}
    for field, key in (("mistral", "MISTRAL_KEY"), ("anthropic", "ANTHROPIC_KEY"), ("linkup", "LINKUP_KEY")):
        value = request.form.get(field + "_key", "").strip()
        if request.form.get(field + "_key_clear") == "on":
            updates[key] = ""
        elif value:
            updates[key] = value
    for field, key in (("mistral_model", "MISTRAL_MODEL"), ("claude_model", "CLAUDE_MODEL")):
        value = request.form.get(field + "_custom", "").strip() or request.form.get(field, "").strip()
        if value:
            if not model_catalogue.valid_id(value):
                abort(400, "Identifiant de modèle invalide")
            updates[key] = value
    depth = request.form.get("linkup_depth", "").strip()
    if depth in LINKUP_DEPTH_IDS:
        updates["LINKUP_DEPTH"] = depth
    if request.form.get('gmail_overrides') == '1':
        for field, key in zip(('gmail_model','gmail_draft_model'), mistral_settings.GMAIL_KEYS[:2]):
            value = request.form.get(field+'_custom','').strip() or request.form.get(field,'').strip()
            if value and not model_catalogue.valid_id(value):
                abort(400, 'Identifiant de modèle Gmail invalide')
            updates[key] = value
        updates[mistral_settings.GMAIL_KEYS[2]] = request.form.get('gmail_draft_reasoning_effort','').strip()
    try:
        # Validate the effective model too when an inherited general setting changes.
        mistral_settings.resolve({**os.environ, **read_env(), **updates}, service='gmail', draft=True)
    except mistral_settings.SettingsError:
        abort(400, 'Modèle ou effort Gmail incompatible ; aucune modification enregistrée.')
    config_store.update_env(ENV_FILE, updates)
    saved_feedback("Configuration IA enregistrée.", True)
    selected_key = {"claude": anthropic_key, "linkup": linkup_key, "mistral": mistral_key}[provider]()
    if not is_real_key(selected_key):
        session["flash"] += " Aucune clé API n'est définie pour ce fournisseur."
        session["flash_ok"] = False
    return redirect(url_for("index", section="ai", preset=request.form.get("context_preset", "")))


@app.route("/upload-knowledge", methods=["POST"])
@require_login
@prompt_transaction
def upload_knowledge():
    key = request.form.get("preset_key", "").strip()
    data = load_prompts()
    if key not in data["presets"]:
        session["flash"] = "Personnalité inconnue."; session["flash_ok"] = False
        return redirect(url_for("index", section="ai", preset=key))
    folder = knowledge_folder(key)
    folder.mkdir(parents=True, exist_ok=True)
    files = request.files.getlist("files")
    uploads = []
    for f in files:
        if not f or not f.filename:
            continue
        name = secure_filename(f.filename)
        if not name.lower().endswith(".txt"):
            name += ".txt"
        content = f.stream.read(io_limits.KNOWLEDGE_FILE_BYTES + 1)
        if len(content) > io_limits.KNOWLEDGE_FILE_BYTES:
            abort(413, "Knowledge file exceeds 256 KiB")
        uploads.append((name, content))
    for name, content in uploads:
        config_store.atomic_write(knowledge_target(folder, name), content)
    n = len(uploads)
    if n:
        saved_feedback(f"{n} fichier(s) ajouté(s) à « {data['presets'][key].get('label', key)} ».",
                       key == data["active"])
    else:
        session["flash"] = "Aucun fichier .txt valide."
        session["flash_ok"] = False
    return redirect(url_for("index", section="ai", preset=key))

@app.route("/delete-knowledge", methods=["POST"])
@require_login
@prompt_transaction
def delete_knowledge():
    key = request.form.get("preset_key", "").strip()
    fn = request.form.get("filename", "")
    target = knowledge_target(knowledge_folder(key), fn)
    if fn and target.is_file():
        target.unlink()
        saved_feedback(f"Fichier {fn} supprimé.", key == load_prompts()["active"])
    else:
        session["flash"] = "Fichier introuvable."; session["flash_ok"] = False
    return redirect(url_for("index", section="ai", preset=key))

@app.route("/restart", methods=["POST"])
@require_login
def restart():
    ok = restart_terminal()
    session["flash"] = "Terminal redémarré." if ok else "Échec (sudo requis)."
    session["flash_ok"] = ok
    session["restart_status"] = session["flash"]
    return redirect(url_for("index", section="maintenance"))

@app.route("/check-update", methods=["POST"])
@require_login
def check_update_route():
    res = check_update()
    session["update_checked_at"] = datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y %H:%M:%S")
    session["update_status"] = (f"{res['behind']} mise(s) à jour disponible(s)" if res.get("ok")
                                else "dernière vérification en échec")
    if not res["ok"]:
        session.pop("update_log", None)
        session["flash"] = res["msg"]; session["flash_ok"] = False
    elif res["behind"] == 0:
        session["flash"] = "L'application est à jour."; session["flash_ok"] = True
        session.pop("update_log", None)
    else:
        session["update_log"] = res["log"]
        session["flash"] = f"{res['behind']} mise(s) à jour disponible(s)."
        session["flash_ok"] = True
    return redirect(url_for("index", section="maintenance"))

@app.route("/update", methods=["POST"])
@require_login
def update_route():
    ok, msg = do_update()
    session.pop("update_log", None)
    session.pop("update_status", None)
    session["flash"] = msg if ok else "Échec de la mise à jour : " + msg
    session["flash_ok"] = ok
    return redirect(url_for("index", section="maintenance"))

@app.route("/rollback", methods=["POST"])
@require_login
def rollback_route():
    ok, msg = do_rollback()
    session.pop("update_log", None)
    session.pop("update_status", None)
    session["flash"] = msg if ok else "Échec : " + msg
    session["flash_ok"] = ok
    return redirect(url_for("index", section="maintenance"))


def previous_release():
    try:
        data = json.loads(RUNTIME_STATE.read_text())
        previous = data.get("previous") or {}
        return {"commit": previous.get("commit", "—"),
                "branch": data.get("previous_branch", data.get("branch", "—")),
                "available": bool(previous.get("release") and Path(previous["release"], "venv/bin/python").exists())}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"commit": "—", "branch": "—", "available": False}


def valid_branch(branch):
    return bool(isinstance(branch, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", branch) and
                git("check-ref-format", "--branch", branch)[0] == 0)


@app.route("/branch-preview", methods=["POST"])
@require_login
def preview_branch():
    session.pop("branch_preview", None)
    branch = request.form.get("branch", "").strip()
    if not valid_branch(branch):
        abort(400, "Nom de branche invalide")
    rc, _, _ = git("fetch", "origin", "+refs/heads/" + branch + ":refs/remotes/origin/" + branch)
    if not rc:
        rc, commit, _ = git("rev-parse", "refs/remotes/origin/" + branch)
        if not rc and re.fullmatch(r"[0-9a-f]{40}", commit):
            session["branch_preview"] = {"branch": branch, "commit": commit}
            session["flash"] = "Version cible récupérée. Confirmez le changement de branche."
            session["flash_ok"] = True
            return redirect(url_for("index", section="maintenance", _anchor="software"))
    session["flash"], session["flash_ok"] = "Branche indisponible sur origin ; réessayer.", False
    return redirect(url_for("index", section="maintenance", _anchor="software"))


@app.route("/switch-branch", methods=["POST"])
@require_login
def switch_branch_route():
    preview = session.pop("branch_preview", None)
    if not isinstance(preview, dict):
        abort(400, "Prévisualisez d’abord la branche cible")
    ok, message = queue_deployment("switch-branch", preview)
    session["flash"], session["flash_ok"] = message, ok
    session.pop("update_log", None)
    session.pop("update_status", None)
    return redirect(url_for("index", section="maintenance", _anchor="software"))


@app.route("/power/<operation>", methods=["POST"])
@require_login
def power_route(operation):
    if operation not in {"reboot", "poweroff"}:
        abort(404)
    ok, _ = queue_deployment(operation)
    session["flash"] = (("Redémarrage" if operation == "reboot" else "Arrêt") + " du Pi planifié ; connexion interrompue dans quelques secondes." if ok else
                        "Opération refusée ; vérifier le gestionnaire installé et les opérations en cours.")
    session["flash_ok"] = ok
    return redirect(url_for("index", section="maintenance"))


@app.route("/diagnostics.txt")
@require_login
def diagnostic_report():
    report = {"version": current_version(), "branch": runtime_branch(),
              "services": {key: svc_status(key) for key in ("minitel-chatgpt", "admin-ui", "wifi-manager")},
              "uptime": system_health.uptime(), "temperature_c": system_health.temperature(),
              "disk": system_health.disk(PROJ_DIR), "local_ip": ip_address(),
              "wifi": system_health.wifi(), "tailscale": system_health.tailscale(),
              "deployment": system_health.deployment(DEPLOYMENT_STATUS)}
    response = app.response_class(json.dumps(report, ensure_ascii=False, indent=2) + "\n", mimetype="text/plain")
    response.headers["Content-Disposition"] = "attachment; filename=minitel-diagnostic.txt"
    return response


@app.route("/storage-status")
@require_login
def storage_status():
    try:
        raw = storage_health.status(PROJ_DIR)
        return jsonify(free_bytes=raw["free_bytes"], total_bytes=raw["total_bytes"], warning=raw["warning"],
                       application_logs=raw["legacy_logs_and_rose"], memory_and_conversations=raw["memory_and_conversations"])
    except OSError:
        return jsonify(error="storage_unavailable"), 503


@app.route("/deployment-status")
@require_login
def deployment_status():
    try:
        return jsonify(json.loads(DEPLOYMENT_STATUS.read_text()))
    except (OSError, ValueError):
        return jsonify({"phase": "not_installed"})


def gmail_enabled():
    return read_env().get("GMAIL_ENABLED", os.getenv("GMAIL_ENABLED", "0")).casefold() in {"1", "true", "yes"}


def service_cards():
    try:
        school_enabled = pronote.backend().config()["enabled"]
    except pronote.PronoteError:
        school_enabled = False
    return [
        {"key": "gmail", "label": "Gmail", "enabled": gmail_enabled(), "placeholder": False, "url": "/services/gmail"},
        {"key": "pronote", "label": "PRONOTE", "enabled": school_enabled, "placeholder": False, "url": "/pronote"},
        {"key": "journal", "label": "Journal intime", "enabled": True, "placeholder": False, "private": True, "url": "/journal"},
        *({"key": key, "label": label, "enabled": False, "placeholder": True, "url": "/services/" + key}
          for key, label in (("alexa", "Alexa"), ("printer", "Imprimante"), ("smart-home", "Maison connectée"))),
    ]


@app.route("/services/<feature>/toggle", methods=["POST"])
@require_login
def toggle_service(feature):
    enabled = request.form.get("enabled") == "1"
    if feature == "gmail":
        value = "1" if enabled else "0"
        config_store.update_env(ENV_FILE, {"GMAIL_ENABLED": value})
        os.environ["GMAIL_ENABLED"] = value
        discard_mail(session.get("gmail_session"))
        saved_feedback("Activation Gmail enregistrée.", True)
    elif feature == "pronote":
        local = pronote.backend()
        local.configure(enabled, local.config()["children"])
        session["flash"] = "Activation PRONOTE enregistrée ; appliquée aux prochaines requêtes."
        session["flash_ok"] = True
    else:
        abort(404)
    return redirect(url_for("index", section="services"))


@app.route("/services/<feature>", methods=["GET", "POST"])
@require_login
def service_configuration(feature):
    message = session.pop("gmail_oauth_message", "") if feature == "gmail" else ""
    titles = {"gmail": "Gmail", "alexa": "Alexa", "printer": "Imprimante", "smart-home": "Maison connectée"}
    if feature not in titles:
        abort(404)
    if feature != "gmail":
        if request.method == "POST":
            abort(405)
        body = admin_service_pages.PLACEHOLDER
        context = {}
    else:
        body = admin_service_pages.GMAIL
        account = "Un seul compte par profil ; un même compte peut être attribué explicitement à plusieurs profils. Aucun compte de secours automatique."
        if request.method == "POST":
            if request.form.get("action") != "check":
                abort(400)
            try:
                if not gmail_enabled():
                    raise gmail_auth.GmailError("disabled")
                identifier = request.form.get('account_id','')
                metadata = next((a for a in user_registry.Registry().accounts() if a['id']==identifier),None)
                if metadata is None:
                    raise gmail_auth.GmailError('account_required')
                gmail_client.GmailClient(tokens=gmail_auth.AccountTokenSource(identifier,revision=metadata['revision'])).list("", time.monotonic() + 20)
                message = "Connexion Gmail vérifiée. Aucun message envoyé ni marqué comme lu."
            except gmail_auth.GmailError:
                message = "Connexion non confirmée ; vérifier l’activation et l’enrôlement."
        try:
            oauth_uri = gmail_oauth.configuration()["redirect_uri"]
        except gmail_auth.GmailError:
            oauth_uri = ""
        context = {"account": account, "enabled": gmail_enabled(),
                   "oauth_users": user_registry.Registry().users(), "oauth_accounts": user_registry.Registry().accounts(),
                   "oauth_uri": oauth_uri, "oauth_ready": session.get("gmail_oauth_saved", False),
                   "oauth_console": oauth_uri.removesuffix(gmail_auth.OAUTH_CALLBACK) + "/services/gmail" if oauth_uri else ""}
    return render_template_string(admin_service_pages.HEAD + body + "</html>",
                                  title=titles[feature], message=message, **context)


def gmail_https_origin(uri):
    """Trust TLS forwarding only from the local Serve proxy, with the saved host."""
    secure = request.is_secure or (
        request.remote_addr in {"127.0.0.1", "::1"}
        and request.headers.get("X-Forwarded-Proto") == "https"
    )
    expected, actual = urlsplit(uri), urlsplit("https://" + request.host)
    try:
        same_host = (actual.hostname == expected.hostname
                     and (actual.port or 443) == (expected.port or 443))
    except ValueError:
        same_host = False
    if not secure or not same_host:
        raise gmail_auth.GmailError("oauth_https_origin_required")


@app.route("/services/gmail/oauth/<action>", methods=["POST"])
@require_login
def gmail_oauth_action(action):
    try:
        if action == "configure":
            uri = request.form.get("redirect_uri", "").strip()
            gmail_auth.validate_oauth_config({"client_id": "validation.apps.googleusercontent.com",
                                            "client_secret": "validation", "redirect_uri": uri})
            gmail_https_origin(uri)
            upload = request.files.get("client_json")
            if upload is None:
                raise gmail_auth.GmailError("web_oauth_configuration_required")
            gmail_oauth.configure(upload.read(32769), uri)
            session.pop("gmail_oauth", None)
            session.pop("gmail_oauth_saved", None)
            session["gmail_oauth_message"] = "Client Google enregistré et chiffré. Vous pouvez maintenant connecter Gmail."
        elif action == "start":
            config = gmail_oauth.configuration()
            gmail_https_origin(config["redirect_uri"])
            gmail_oauth.cancel(session.pop("gmail_oauth", None))
            registry = user_registry.Registry()
            selected = registry.get(int(request.form.get('user_id', '1')))
            accounts = registry.accounts()
            assigned = registry.details(selected.id)['account_id']
            existing = next((a for a in accounts if a['id'] == assigned), None)
            target = {'user_id':selected.id,'user_revision':selected.revision,
                      'account_id':existing['id'] if existing else secrets.token_hex(16),
                      'revision':existing['revision'] if existing else 0}
            identifier, address = gmail_oauth.begin(config, target)
            session["gmail_oauth"] = identifier
            return redirect(address, code=303)
        elif action == "apply":
            if not session.get("gmail_oauth_saved"):
                raise gmail_auth.GmailError("authorization_not_completed")
            gmail_https_origin(gmail_oauth.configuration()["redirect_uri"])
            saved = session.get('gmail_oauth_result')
            if saved:
                registry = user_registry.Registry()
                user = registry.get(saved['user_id'])
                if user.revision != saved['user_revision']:
                    raise gmail_auth.GmailError('account_assignment_changed')
                details = registry.details(user.id)
                registry.configure(user.id,user.display_name,True,details['aliases'],saved['account_id'],
                                   request.form.get('send') == 'on',details['children'])
                session.pop('gmail_oauth_result',None)
            if gmail_auth.credential_helper({"operation": "activate"}) != {"status": "queued"}:
                raise gmail_auth.GmailError("credential_activation_failed")
            session.pop("gmail_oauth_saved", None)
            session["gmail_oauth_message"] = "Redémarrage des services programmé. La session du Minitel sera interrompue. Attendez quelques secondes puis rechargez cette page et vérifiez la connexion."
        else:
            abort(404)
    except (gmail_auth.GmailError, ValueError) as error:
        messages = {
            "oauth_https_origin_required": "Ouvrez cette page à l’adresse HTTPS enregistrée (Tailscale Serve), puis recommencez.",
            "web_oauth_configuration_required": "Fournissez le JSON d’un client Google de type Application Web et son URI de redirection HTTPS exacte.",
            "dependency_missing": "L’extension Gmail doit d’abord être installée sur le Pi.",
            "authorization_not_completed": "Autorisez d’abord le compte Google depuis cette page.",
        }
        session["gmail_oauth_message"] = messages.get(str(error), "Opération indisponible. Vérifiez l’installation Gmail et réessayez ; l’autorisation précédente est conservée si la nouvelle n’a pas été enregistrée.")
    return redirect(url_for("service_configuration", feature="gmail"), code=303)


@app.route(gmail_auth.OAUTH_CALLBACK)
@require_login
def gmail_oauth_callback():
    identifier = session.pop("gmail_oauth", None)
    try:
        gmail_https_origin(gmail_oauth.configuration()["redirect_uri"])
        state, code, error = (request.args.getlist(key) for key in ("state", "code", "error"))
        if (len(state) != 1 or len(state[0]) > 512 or len(code) + len(error) != 1
                or (error and len(error[0]) > 256)):
            raise gmail_auth.GmailError("oauth_callback_invalid")
        result = gmail_oauth.complete(identifier, state[0], code[0] if code else None, denied=bool(error))
        if isinstance(result,dict):
            session['gmail_oauth_result'] = result
        session["gmail_oauth_saved"] = True
        session["gmail_oauth_message"] = "Autorisation Google enregistrée et chiffrée. Cliquez sur Appliquer pour la charger dans les deux services."
    except gmail_auth.GmailError:
        session["gmail_oauth_message"] = "Autorisation non terminée, expirée ou refusée. Recommencez la connexion Google ; aucun détail privé n’est affiché."
    finally:
        gmail_oauth.cancel(identifier)
    # Remove the one-use code from the browser address before rendering any page.
    return redirect(url_for("service_configuration", feature="gmail"), code=303)


USERS_HTML = admin_service_pages.HEAD + """
<p>Un nom choisit un profil, sans authentification. <a href=/services/gmail>Comptes Gmail</a></p>
<p>Un seul compte Gmail par profil. Plusieurs profils peuvent partager le même compte, avec des droits d’envoi distincts. Chaque attribution reste explicite.</p>
<form method=post><input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=create>
<label>Nouveau nom<input name=name required maxlength=60></label><button>Créer le profil</button></form>
{% for u in users %}<section><h2>{{u.display_name}} · #{{u.id}}</h2><form method=post>
<input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=save><input type=hidden name=user_id value="{{u.id}}">
<label>Nom affiché<input name=name value="{{u.display_name}}" required maxlength=60></label>
<label>Alias (un par ligne)<textarea name=aliases>{{details[u.id].aliases|join('\n')}}</textarea></label>
<label><input type=checkbox name=enabled {{'checked' if u.enabled}}> Profil actif (le profil commun reste actif)</label>
<label>Compte Gmail<select name=account_id><option value="">Aucun accès</option>{% for a in accounts %}<option value="{{a.id}}" {{'selected' if details[u.id].account_id==a.id}}>{{a.email}} {{'(désactivé)' if not a.enabled}}</option>{% endfor %}</select></label>
<label><input type=checkbox name=send {{'checked' if details[u.id].can_send}}> Autoriser aussi l’envoi (sinon lecture seule)</label>
<p>PRONOTE : enfants autorisés</p>{% for child in children %}<label><input type=checkbox name=child value="{{child}}" {{'checked' if child in details[u.id].children}}> {{child}}</label>{% endfor %}
<button>Enregistrer les accès</button></form>
{% if u.id != 1 %}
<form method=post action="/users/{{u.id}}/reset-onboarding" onsubmit="return confirm('Recommencer le premier accueil ? Les souvenirs personnels sont conservés.');">
<input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=revision value="{{u.revision}}">
<button>Réinitialiser l’accueil</button>
<p>Recommence la présentation et les questions. Les souvenirs sont conservés ; les nouvelles réponses corrigeront les informations contradictoires.</p></form>
{% endif %}
{% if u.id != 1 %}<details><summary>Supprimer le profil et toutes ses mémoires</summary>
<p>Suppression définitive des souvenirs, notes, listes, vecteurs, premier accueil, conversations, journal intime chiffré et anciens JSON de ce profil, ainsi que de ses alias et accès.
Les comptes Gmail/PRONOTE et les autres profils sont conservés. Les sauvegardes globales restent séparées.</p>
<form method=post action="/users/{{u.id}}/delete"><input type=hidden name=csrf value="{{csrf_token()}}">
<input type=hidden name=revision value="{{u.revision}}">
<label>Retapez exactement {{u.display_name}} pour confirmer<input name=confirmation required autocomplete=off maxlength=60></label>
<button class="btn btn-d">Supprimer définitivement ce profil et ses données</button></form></details>
{% else %}<p>Le profil réservé MinitelGPT ne peut pas être supprimé.</p>{% endif %}</section>{% endfor %}
"""


@app.route('/users',methods=['GET','POST'])
@require_login
def users_admin():
    registry = user_registry.Registry(inspection_store().root)
    message = session.pop('flash', '')
    try:
        if request.method == 'POST':
            if request.form.get('action') == 'create':
                registry.resolve(request.form.get('name',''))
            elif request.form.get('action') == 'save':
                children = request.form.getlist('child')
                if not set(children) <= set(pronote.backend().config()['children']):
                    raise gmail_auth.GmailError('unknown_child')
                registry.configure(int(request.form['user_id']),request.form['name'],request.form.get('enabled')=='on',
                    request.form.get('aliases','').splitlines(),request.form.get('account_id',''),
                    request.form.get('send')=='on',children)
            else:
                abort(400)
            message = 'Accès enregistrés. Les anciennes sessions doivent être rouvertes.'
        users = registry.users()
        accounts = registry.accounts()
        return render_template_string(USERS_HTML,title='Utilisateurs et accès',message=message,users=users,
            accounts=accounts,details={u['id']:registry.details(u['id']) for u in users},
            children=pronote.backend().config()['children'])
    except (gmail_auth.GmailError,ValueError,KeyError) as error:
        return render_template_string(admin_service_pages.HEAD,title='Utilisateurs et accès',
                                      message='Modification refusée : '+str(error)),400


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@require_login
def delete_user(user_id):
    registry = user_registry.Registry(inspection_store().root)
    try:
        registry.delete(user_id, int(request.form.get('revision', '-1')), request.form.get('confirmation', ''))
    except (gmail_auth.GmailError, ValueError) as error:
        abort(409 if str(error) == 'user_changed' else 400, 'Suppression refusée. Rechargez le profil et vérifiez le nom de confirmation.')
    with MEMORY_SESSIONS_LOCK:
        for identifier, item in list(MEMORY_SESSIONS.items()):
            selected = item['memory']
            if selected.registry.root == registry.root and selected.user.id == user_id:
                discard_memory(identifier)
    with MAIL_SESSIONS_LOCK:
        for identifier, item in list(MAIL_SESSIONS.items()):
            tokens = getattr(item['mail'].client, 'tokens', None)
            if isinstance(tokens, user_services.ProfileTokens) and tokens.registry.root == registry.root and tokens.user.id == user_id:
                discard_mail(identifier)
    session['flash'], session['flash_ok'] = 'Profil, accès et données personnelles supprimés.', True
    return redirect(url_for('users_admin'))


@app.route('/users/<int:user_id>/reset-onboarding', methods=['POST'])
@require_login
def reset_user_onboarding(user_id):
    try:
        user_registry.Registry(inspection_store().root).reset_onboarding(
            user_id, int(request.form.get('revision', '-1')))
    except (gmail_auth.GmailError, ValueError) as error:
        abort(409 if str(error) == 'user_changed' else 400, 'Réinitialisation refusée. Rechargez le profil.')
    session['flash'] = 'Accueil réinitialisé. Souvenirs et accès conservés. Reconnectez le profil ou relancez Faire connaissance dans le test.'
    return redirect(url_for('users_admin'))


@app.route('/services/gmail/accounts',methods=['POST'])
@require_login
def gmail_accounts_admin():
    registry = user_registry.Registry()
    try:
        action=request.form.get('action')
        if action=='migrate':
            identifier=secrets.token_hex(16)
            import sqlite3
            backup=registry.path.with_name('users.before-multi.sqlite3')
            if not backup.exists():
                with registry.connection() as db:
                    fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                    os.close(fd)
                    target=sqlite3.connect(backup)
                    try:
                        db.backup(target)
                    finally:
                        target.close()
            result=gmail_auth.credential_helper({'operation':'migrate','account_id':identifier})
            if result.get('status')!='saved':
                raise gmail_auth.GmailError('migration_unavailable')
        elif action=='disconnect':
            identifier=request.form['account_id']
            account=next((a for a in registry.accounts() if a['id']==identifier),None)
            if account is None:
                raise gmail_auth.GmailError('unknown_account')
            with registry.connection() as db:
                db.execute('UPDATE users SET revision=revision+1 WHERE id IN (SELECT user_id FROM user_gmail_access WHERE account_id=?)',(identifier,))
                db.execute('DELETE FROM user_gmail_access WHERE account_id=?',(identifier,))
                db.execute('UPDATE gmail_accounts SET enabled=0 WHERE id=?',(identifier,))
            result=gmail_auth.credential_helper({'operation':'forget','account_id':identifier,'revision':account['revision']})
            if result.get('status')!='saved':
                raise gmail_auth.GmailError('credential_helper_unavailable')
            with registry.connection() as db:
                db.execute('DELETE FROM gmail_accounts WHERE id=?',(identifier,))
        elif action=='disable':
            with registry.connection() as db:
                identifier=request.form['account_id']
                db.execute('UPDATE users SET revision=revision+1 WHERE id IN (SELECT user_id FROM user_gmail_access WHERE account_id=?)',(identifier,))
                db.execute('UPDATE gmail_accounts SET enabled=? WHERE id=?',(request.form.get('enabled')=='on',identifier))
        elif action!='sync':
            abort(400)
        metadata=gmail_auth.credential_helper({'operation':'accounts'})
        if not isinstance(metadata,dict) or 'status' in metadata:
            raise gmail_auth.GmailError('credential_helper_unavailable')
        for identifier,item in metadata.items():
            gmail_auth.account_id(identifier)
            registry.record_account(identifier,item['email'],item['revision'])
        session['gmail_oauth_message']='Comptes synchronisés. Attribuez explicitement le compte importé dans Utilisateurs et accès.'
    except (gmail_auth.GmailError,KeyError,ValueError):
        session['gmail_oauth_message']='Opération indisponible. Les accès existants sont conservés.'
    return redirect('/services/gmail',code=303)


ROSE_HTML = """<!doctype html><html lang=fr><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Atelier privé</title>
<style>body{font:16px system-ui;max-width:850px;margin:20px auto;padding:16px;background:#19191d;color:#eee}a,summary{color:#4ecdc4}label{display:block;margin:12px 0}input,select,textarea{box-sizing:border-box;width:100%;padding:8px;font:inherit}input[type=hidden]{display:none}button{padding:10px;margin:8px 0}section,details{border:1px solid #555;padding:16px;margin:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
{% if message %}<p role=status>{{message}}</p>{% endif %}
{% if not unlocked %}<h1>Accès réservé</h1><form method=post><input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=unlock>
<label>Mot de passe<input type=password name=password required autocomplete=current-password></label><button>Déverrouiller</button></form>
{% else %}<h1>Atelier Rose</h1><a href="/?section=services">Retour administration</a>
<form method=post><input type=hidden name=csrf value="{{csrf_token()}}"><button name=action value=lock>Verrouiller</button></form>
<p>Les profils sont pris en compte aux nouvelles sessions ; le prompt commun et les niveaux partagés aux prochaines requêtes. Les éditions locales restent séparées des fichiers suivis par Git.</p>
<section><h2>Personnalités</h2><form method=get>
<label>Catalogue<select name=catalog><option value=main {{'selected' if catalog=='main'}}>Normal</option><option value=test {{'selected' if catalog=='test'}}>Test</option></select></label>
<label>Profil<select name=profile>{% for p in people %}<option value="{{p.id}}" {{'selected' if p.id==person.id}}>{{p.name}}</option>{% endfor %}<option value=new {{'selected' if is_new}}>+ Nouveau profil</option></select></label><button>Ouvrir</button></form>
<form method=post><input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=person>
<input type=hidden name=catalog value="{{catalog}}"><input type=hidden name=original_id value="{{'' if is_new else person.id}}">
<label>Identifiant<input name=id value="{{person.id}}" maxlength=80 required {{'readonly' if not is_new}}></label>
<label>Nom<input name=name value="{{person.name}}" maxlength=80 required></label>
<label>Alias (un par ligne)<textarea name=aliases rows=3>{{person.aliases|join('\n')}}</textarea></label>
<label>Genre<select name=gender><option value=homme {{'selected' if person.gender=='homme'}}>Homme</option><option value=femme {{'selected' if person.gender=='femme'}}>Femme</option></select></label>
<label>Description<textarea name=bio rows=3>{{person.bio}}</textarea></label>
{% for axis in ['e','s'] %}<label>Niveau {{axis|upper}}<select name="{{axis}}_level"><option value="">Texte historique sans niveau séparé</option>{% for n in range(1,6) %}<option value="{{n}}" {{'selected' if person.get(axis+'_level')==n}}>{{axis|upper}}{{n}}</option>{% endfor %}</select></label>{% endfor %}
<label>Voix / consignes propres au profil<textarea name=voice rows=7>{{person.voice}}</textarea></label>
<details><summary>Autres paramètres du profil (JSON)</summary><p>Tempo, modules, options et tarification existants. Les variantes e_prompt_override / s_prompt_override conservent les formulations historiques ; les vider permet de suivre le niveau partagé.</p><textarea name=metadata rows=8>{{metadata}}</textarea></details>
<button>Enregistrer le profil</button></form></section>
<section><h2>Prompt système commun</h2><form method=post><input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=prompt><textarea name=prompt rows=12 required>{{prompt}}</textarea><button>Enregistrer le prompt</button></form></section>
<details><summary>Prompts partagés E1–E5 / S1–S5</summary><form method=post><input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=levels>
{% for key,text in levels.items()|sort %}<label>{{key}}<textarea name="{{key}}" rows=4 required>{{text}}</textarea></label>{% endfor %}<button>Enregistrer les niveaux</button></form></details>
<details><summary>Journaux et transcriptions</summary><p>Aucun fichier n’est supprimé ou filtré.</p><ul>{% for name in logs %}<li><a href="{{url_for('rose_admin',log=name)}}">{{name}}</a></li>{% else %}<li>Aucun fichier</li>{% endfor %}</ul>{% if log_text is not none %}<pre>{{log_text}}</pre>{% endif %}</details>
{% endif %}</html>"""


def private_rose_logs(name=None):
    folder = PROJ_DIR / 'logs' / 'rose'
    if folder.is_symlink() or folder.parent.is_symlink():
        abort(400)
    if name is None:
        if not folder.exists():
            return []
        names = []
        with os.scandir(folder) as entries:
            for index, entry in enumerate(entries):
                if index >= 1000:
                    break
                if entry.is_file(follow_symlinks=False):
                    names.append(entry.name)
        return sorted(names)
    if not name or Path(name).name != name or '\\' in name or '\x00' in name:
        abort(400)
    try:
        descriptor = os.open(folder / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            details = os.fstat(stream.fileno())
            if not stat.S_ISREG(details.st_mode):
                abort(400)
            stream.seek(max(0, details.st_size - 65536))
            return stream.read(65536).decode('utf-8', errors='replace')
    except OSError:
        abort(404)


@app.route('/admin/rose', methods=['GET', 'POST'])
@require_login
def rose_admin():
    unlocked = time.time() - session.get('rose_unlock', 0) < 1200
    message = ''
    if request.method == 'POST' and request.form.get('action') == 'lock':
        session.pop('rose_unlock', None)
        return redirect(url_for('rose_admin'))
    if not unlocked:
        if request.method == 'POST' and request.form.get('action') == 'unlock':
            if rose.authorized(request.form.get('password', '')):
                session['rose_unlock'] = time.time()
                return redirect(url_for('rose_admin'))
            message = 'Accès refusé.'
        return render_template_string(ROSE_HTML, unlocked=False, message=message), 200 if request.method == 'GET' else 403
    session['rose_unlock'] = time.time()
    catalog = request.values.get('catalog', 'main')
    if catalog not in {'main', 'test'}:
        abort(400)
    test = catalog == 'test'
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'person':
            try:
                person = json.loads(request.form.get('metadata', '{}'))
            except ValueError:
                raise config_store.InvalidConfig('Paramètres JSON invalides') from None
            if not isinstance(person, dict):
                abort(400)
            person.update({key: request.form.get(key, '').strip() for key in ('id', 'name', 'gender', 'bio', 'voice')})
            original = request.form.get('original_id', '')
            if original and original != person['id']:
                abort(400)
            person['aliases'] = [a.strip() for a in request.form.get('aliases', '').splitlines() if a.strip()]
            for key in ('e_level', 's_level'):
                value = request.form.get(key, '')
                person.pop(key, None)
                if value:
                    if value not in {'1', '2', '3', '4', '5'}:
                        abort(400)
                    person[key] = int(value)
            if not original and any(p['id'] == person['id'] for p in rose.catalogue(test)['personas']):
                raise config_store.InvalidConfig('Identifiant déjà utilisé')
            previous = next((p for p in rose.catalogue(test)['personas'] if p['id'] == original), None)
            if previous:
                previous = rose_config.editable(previous, rose_config.levels(rose.CONFIG))
                for axis in ('e', 's'):
                    if previous.get(axis + '_level') != person.get(axis + '_level'):
                        person.pop(axis + '_prompt_override', None)
            rose_config.save(rose.CONFIG, 'person', person=person, test=test)
            return redirect(url_for('rose_admin', catalog=catalog, profile=person['id'], saved='1'))
        if action == 'prompt':
            rose_config.save(rose.CONFIG, 'prompt', text=request.form.get('prompt', ''))
        elif action == 'levels':
            rose_config.save(rose.CONFIG, 'levels', bank={f'{a}{n}': request.form.get(f'{a}{n}', '') for a in 'ES' for n in range(1, 6)})
        else:
            abort(400)
        return redirect(url_for('rose_admin', catalog=catalog, saved='1'))
    people = rose.catalogue(test)['personas']
    identifier = request.args.get('profile', people[0]['id'])
    bank = rose_config.levels(rose.CONFIG)
    if identifier == 'new':
        person = {'id': '', 'name': '', 'aliases': [], 'gender': 'femme', 'bio': '', 'voice': '', 'fees': [], 'tempo': 0, 'kinks': [], 'asexual': False, 'e_level': 1, 's_level': 1}
    else:
        person = next((p for p in people if p['id'] == identifier), None)
        if person is None:
            abort(404)
        person = rose_config.editable(person, bank)
    message = 'Enregistré.' if request.args.get('saved') else ''
    metadata = {k: v for k, v in person.items() if k not in {'id', 'name', 'aliases', 'gender', 'bio', 'voice', 'e_level', 's_level'}}
    return render_template_string(ROSE_HTML, unlocked=True, message=message, catalog=catalog, people=people,
        person=person, is_new=identifier == 'new', levels=bank, prompt=rose_config.prompt(rose.CONFIG),
        metadata=json.dumps(metadata, ensure_ascii=False, indent=2), logs=private_rose_logs(),
        log_text=private_rose_logs(request.args['log']) if 'log' in request.args else None)


PRONOTE_HTML = admin_service_pages.HEAD + """
<p>Lecture seule. Dépendance : {{'installée' if status.dependency_ready else 'indisponible'}}.
Intégration : {{'active' if status.enabled else 'inactive'}}. État local, sans vérification réseau automatique.</p>
<section><h2>Comptes parents</h2><ul>{% for account in accounts %}<li>{{account}}</li>{% else %}<li>Aucun compte détecté</li>{% endfor %}</ul></section>
{% if children %}<p>Enfants disponibles : {{children|join(', ')}}</p>{% endif %}
<details><summary>Connecter ou reconnecter un compte parent</summary>
<form method=post autocomplete=off><input type=hidden name=csrf value="{{csrf}}">
<label>Nom local du compte<input name=account required pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,39}" maxlength=40 autocapitalize="none" autocorrect="off" spellcheck="false"></label>
<label>Données du QR parent (JSON : url, login, jeton)<textarea name=qr required rows=5 maxlength=12000></textarea></label>
<label>Code QR<input name=pin type=password required inputmode=numeric pattern="[0-9]{4}"></label>
<label>Code de sécurité du compte si demandé<input name=account_pin type=password></label>
<button name=action value=enroll>Connecter et vérifier</button></form></details>
<section><h2>Enfants autorisés</h2><p>Chaque alias est explicitement associé à un compte et au nom PRONOTE exact.</p>
<form method=post><input type=hidden name=csrf value="{{csrf}}">
<table><thead><tr><th>Alias Minitel</th><th>Compte</th><th>Nom PRONOTE</th><th></th></tr></thead><tbody id=childRows>
{% for alias,child in child_mapping.items() %}<tr><td><input name=alias value="{{alias}}" maxlength=40></td><td><input name=child_account value="{{child.account}}" maxlength=40></td><td><input name=child_name value="{{child.name}}" maxlength=160></td><td><button type=button onclick="this.closest('tr').remove()">Retirer</button></td></tr>{% endfor %}
</tbody></table><button type=button onclick="document.getElementById('childRows').insertAdjacentHTML('beforeend',document.getElementById('childRow').innerHTML)">Ajouter un enfant</button>
<label><input type=checkbox name=enabled {{'checked' if status.enabled}}> Activer l’intégration</label>
<button name=action value=configure>Enregistrer</button></form></section>
<template id=childRow><tr><td><input name=alias maxlength=40 placeholder=Alias></td><td><input name=child_account maxlength=40 placeholder=Compte></td><td><input name=child_name maxlength=160 placeholder="Nom PRONOTE exact"></td><td><button type=button onclick="this.closest('tr').remove()">Retirer</button></td></tr></template>
<form method=post><input type=hidden name=csrf value="{{csrf}}"><label>Compte à vérifier<input name=account required autocapitalize="none" autocorrect="off" spellcheck="false"></label><button name=action value=children>Vérifier la connexion et relire les enfants</button></form>
<details><summary>Configuration JSON avancée</summary><form method=post><input type=hidden name=csrf value="{{csrf}}">
<textarea name=children rows=8 required>{{mapping}}</textarea><label><input type=checkbox name=enabled {{'checked' if status.enabled}}> Activer</label><button name=action value=configure>Enregistrer le JSON</button></form></details>
""" + '<p><a href=/users>Configurer les accès par utilisateur</a></p></html>'


@app.route("/pronote", methods=["GET", "POST"])
@require_login
def pronote_admin():
    # Explicit random nonce is required even with the legacy Flask secret.
    session["pronote_csrf"] = csrf_token()
    local = pronote.backend()
    message, children = "", []
    try:
        if request.method == "POST":
            supplied = request.form.get("csrf", "")
            if not hmac.compare_digest(supplied, session["pronote_csrf"]):
                return "Requête refusée", 403
            action = request.form.get("action")
            if action == "enroll":
                qr_text = request.form.get("qr", "")
                if len(qr_text) > 12000:
                    raise pronote.PronoteError("invalid_qr")
                children = local.enroll(request.form.get("account", ""), json.loads(qr_text),
                                        request.form.get("pin", ""),
                                        request.form.get("account_pin") or None)
                message = "Compte connecté. Autorisez les enfants ci-dessous."
            elif action == "children":
                children = local.admin_children(request.form.get("account", ""))
            elif action == "configure":
                if "children" in request.form:
                    mapping_text = request.form["children"]
                    if len(mapping_text) > 16000:
                        raise pronote.PronoteError("configuration_error")
                    mapping = json.loads(mapping_text)
                else:
                    mapping = {}
                    rows = zip(request.form.getlist("alias"), request.form.getlist("child_account"), request.form.getlist("child_name"), strict=True)
                    for alias, account, name in rows:
                        alias, account, name = alias.strip(), account.strip(), name.strip()
                        if not any((alias, account, name)):
                            continue
                        if not all((alias, account, name)) or alias in mapping:
                            raise pronote.PronoteError("configuration_error")
                        mapping[alias] = {"account": account, "name": name}
                local.configure("enabled" in request.form, mapping)
                message = "Configuration enregistrée."
            else:
                return "Action inconnue", 400
    except (pronote.PronoteError, ValueError, TypeError) as error:
        message = (error.admin_message() if isinstance(error, pronote.PronoteError) else "invalid_arguments")
    try:
        config = local.config()
        status = local.status()
    except pronote.PronoteError as error:
        message = str(error)
        config = {"enabled": False, "children": {}}
        status = {"enabled": False, "dependency_ready": False}
    response = app.make_response(render_template_string(
        PRONOTE_HTML, title="PRONOTE", accounts=sorted({m["account"] for m in config["children"].values()} |
            {p.name.removesuffix(".credentials.json") for p in local.directory.glob("*.credentials.json") if p.is_file() and not p.is_symlink()}),
        child_mapping=config["children"], csrf=session["pronote_csrf"], message=message, children=children,
        status=status, mapping=json.dumps(config["children"], ensure_ascii=False, indent=2)))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# Only an opaque selector is stored in the browser cookie. Records stay on disk.
MEMORY_SESSIONS = OrderedDict()
MEMORY_SESSIONS_LOCK = threading.RLock()


def discard_memory(identifier):
    with JOURNAL_SESSIONS_LOCK:
        for journal_id, private in list(JOURNAL_SESSIONS.items()):
            if getattr(private, "memory_id", None) == identifier:
                discard_journal(journal_id)
    with MEMORY_SESSIONS_LOCK:
        item = MEMORY_SESSIONS.pop(identifier, None)
        if item:
            item["memory"].close()


def memory_entry(username=None, reset=False):
    with MEMORY_SESSIONS_LOCK:
        now = time.monotonic()
        for identifier, item in list(MEMORY_SESSIONS.items()):
            if now - item["used"] > 1200:
                discard_memory(identifier)
        identifier = session.get("memory_session")
        item = MEMORY_SESSIONS.get(identifier)
        if item:
            selected=item['memory']
            try:
                changed=selected.registry.get(selected.user.id).revision != selected.user.revision
            except gmail_auth.GmailError:
                changed=True
            if changed:
                username='' if selected.key=='shared' else selected.username
                discard_memory(identifier)
                item=None
        key = memory_session.memory.user_key(username)[0] if username is not None else None
        if item and (reset or (key is not None and key != item["memory"].key)):
            discard_memory(identifier)
            item = None
        if not item:
            while len(MEMORY_SESSIONS) >= 8:
                discard_memory(next(iter(MEMORY_SESSIONS)))
            identifier = secrets.token_urlsafe(32)
            session["memory_session"] = identifier
            private_values = [v for k, v in read_env().items()
                              if re.search(r"(?:KEY|TOKEN|PASSWORD|SECRET)$", k)]
            item = {
                "memory": memory_session.MemorySession(
                    username or "", root=read_env().get("MINITEL_MEMORY_DIR"),
                    model=memory_session.MemoryModel(
                        settings=lambda: (mistral_key(), mistral_model())),
                    extra_secrets=private_values),
                "used": now,
            }
            item["memory"].calculator = calculator.Session()
            MEMORY_SESSIONS[identifier] = item
        item["used"] = now
        MEMORY_SESSIONS.move_to_end(identifier)
        return item["memory"]


# This pool holds only opaque selectors in cookies. Session.close also clears
# page buffers on an independent idle timer, without waiting for another request.
JOURNAL_SESSIONS = OrderedDict()
JOURNAL_SESSIONS_LOCK = threading.RLock()


def discard_journal(identifier):
    with JOURNAL_SESSIONS_LOCK:
        item = JOURNAL_SESSIONS.pop(identifier, None)
        if item is not None:
            item.close()


def journal_response(data, status=200):
    response = jsonify(data)
    response.status_code = status
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                             "X-Content-Type-Options": "nosniff"})
    return response


def journal_transport():
    # Direct HTTP is restricted to localhost (e.g. an SSH tunnel). Tailscale
    # Serve terminates TLS locally; no passwords on an unencrypted LAN link.
    return request.is_secure or request.remote_addr in {"127.0.0.1", "::1"}


@app.route("/journal")
@require_login
def journal_console():
    discard_journal(session.get("private_journal"))
    if not journal_transport():
        return "Ouvrez l’administration via HTTPS (Tailscale Serve) ou un tunnel localhost.", 403
    selected = memory_entry()
    response = app.make_response(render_template_string(journal_page.HTML, username=selected.username))
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    return response


@app.route("/journal-api", methods=["POST"])
@require_login
def journal_api():
    if not journal_transport():
        return journal_response({"closed": True, "display": "HTTPS ou localhost requis."}, 403)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("action") not in {"open", "command", "next", "lock", "touch"}:
        return journal_response({"closed": True, "display": "Requête invalide."}, 400)
    action = data["action"]
    identifier = session.get("private_journal")
    if action == "lock":
        discard_journal(identifier)
        return journal_response({"closed": True, "display": "Journal verrouillé."})
    try:
        if action == "open":
            name = data.get("username", "")
            if not isinstance(name, str) or len(name) > 60:
                raise journal.JournalError("Choisissez un profil nommé.")
            discard_journal(identifier)
            selected = memory_entry(name)
            item = journal.Session(journal_store.Vault(selected.user, selected.registry),
                                   model=journal.Model(lambda: (mistral_key(), mistral_model())))
            item.memory_id = session.get("memory_session")
            result = item.open()
            with JOURNAL_SESSIONS_LOCK:
                for old_id, old in list(JOURNAL_SESSIONS.items()):
                    if old.stage == "closed":
                        discard_journal(old_id)
                while len(JOURNAL_SESSIONS) >= 8:
                    discard_journal(next(iter(JOURNAL_SESSIONS)))
                identifier = secrets.token_urlsafe(32)
                session["private_journal"] = identifier
                JOURNAL_SESSIONS[identifier] = item
        else:
            with JOURNAL_SESSIONS_LOCK:
                item = JOURNAL_SESSIONS.get(identifier)
            if item is None:
                raise journal.JournalError("Journal verrouillé. Rouvrez-le.")
            with item.lock:
                item.check()
                if action == "touch":
                    item.touch()
                    return journal_response({"closed": False})
                if action == "next":
                    item.page = min(item.page + 1, len(item.pages) - 1)
                    item.touch()
                    result = None
            if action == "command":
                result = item.handle(data.get("text", ""))
        with item.lock:
            if item.stage == "closed":
                return journal_response({"closed": True, "display": "Journal verrouillé."})
            item.check()
            if result is not None:
                item.pages, item.page = gmail_mail.pages(result["display"]), 0
                item.page_review = result.get("review")
            if item.page_review and item.page == len(item.pages) - 1:
                item.acknowledge(item.page_review)
            return journal_response({"display": item.pages[item.page], "secret": item.secret,
                                     "closed": False, "more": item.page < len(item.pages) - 1,
                                     "expires_in": max(0, int((item.deadline - item.clock()) * 1000))})
    except (journal.JournalError, memory_session.memory.MemoryProblem) as error:
        discard_journal(identifier)
        return journal_response({"closed": True, "display": str(error)})
    except Exception:  # noqa: BLE001 - Never expose/log private exception payloads.
        discard_journal(identifier)
        return journal_response({"closed": True, "display": "Journal indisponible. Session verrouillée."}, 503)


# Mail bodies and drafts stay in bounded process memory, never Flask cookies.

MAIL_SESSIONS = OrderedDict()
MAIL_SESSIONS_LOCK = threading.RLock()
MAIL_TTL = 1200


def discard_mail(identifier):
    with MAIL_SESSIONS_LOCK:
        item = MAIL_SESSIONS.pop(identifier, None)
        if item:
            item["mail"].reset()


def mail_entry(username=""):
    with MAIL_SESSIONS_LOCK:
        now = time.monotonic()
        for identifier, item in list(MAIL_SESSIONS.items()):
            if now - item["used"] > MAIL_TTL:
                discard_mail(identifier)
        identifier = session.get("gmail_session")
        if identifier in MAIL_SESSIONS:
            client=MAIL_SESSIONS[identifier]['mail'].client
            if isinstance(client,user_services.ProfileMailClient):
                try:
                    client.tokens.guard()
                except gmail_auth.GmailError:
                    discard_mail(identifier)
        if identifier in MAIL_SESSIONS and MAIL_SESSIONS[identifier]["mail"].username != (username.strip() or "MinitelGPT"):
            discard_mail(identifier)
        if identifier not in MAIL_SESSIONS:
            while len(MAIL_SESSIONS) >= 8:
                discard_mail(next(iter(MAIL_SESSIONS)))
            identifier = secrets.token_urlsafe(32)
            session["gmail_session"] = identifier
            MAIL_SESSIONS[identifier] = {
                "mail": gmail_mail.MailSession(
                    username=username,
                    auto_summary_chars=read_env().get("GMAIL_AUTO_SUMMARY_CHARS",
                                                      os.getenv("GMAIL_AUTO_SUMMARY_CHARS", "6000")),
                    model=gmail_mail.MailModel(
                        settings=lambda: (mistral_key(), mistral_model()),
                        env=lambda: {**os.environ, **read_env()},
                    )
                ),
                "pages": ["MAIL pour consulter la boîte.\nECRIRE nom : instructions pour un nouveau mail."],
                "page": 0,
                "ticket": None,
                "used": now,
                "lock": threading.Lock(),
            }
        item = MAIL_SESSIONS[identifier]
        item["used"] = now
        MAIL_SESSIONS.move_to_end(identifier)
        return item


GMAIL_HTML = """<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Gmail — 3615 IA</title>
<style>body{background:#101820;color:#eee;font:16px system-ui;max-width:700px;margin:2em auto;padding:1em}
pre{font:16px monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#000;color:#7fffa0;padding:1em}
input,button{font:inherit;padding:.5em;margin:.2em} input[type=text]{width:90%}</style>
<a href="/?section=services">Retour aux services</a><h1>Tester le Minitel — Gmail</h1>
<nav><a href="/test?mode=conversation">Conversation</a> · <a href="/test?mode=gmail">Gmail</a> · <a href="/services/gmail">Configurer Gmail</a></nav>
<form method=post><input type=hidden name=csrf value="{{csrf}}">
<label>Utilisateur (vide = MinitelGPT)<input type=text name=username maxlength=60 value="{{username}}"></label>
<button name=action value=user>Choisir l’utilisateur</button></form>
<p>Compte lié au profil sélectionné. Destination mémoire/journal : {{ memory_label }}. Aucun compte de secours.</p>
<p>Brouillons temporaires; commandes et réponses visibles journalisées dans la mémoire sélectionnée ici. Lecture sans marquer comme lu. Le modèle ne peut pas envoyer.</p>
{% if not enabled %}<p>Gmail désactivé. Suivre docs/GMAIL.md pour la connexion sur ordinateur.</p>{% endif %}
<pre>{{ page }}</pre><p>Page {{ position }} / {{ count }}</p>
<form method="post" autocomplete="off"><input type="hidden" name="csrf" value="{{ csrf }}">
<input type="hidden" name="ticket" value="{{ ticket or '' }}">
<button name="action" value="previous" {% if position == 1 %}disabled{% endif %}>RETOUR</button>
<button name="action" value="next" {% if position == count %}disabled{% endif %}>SUITE</button>
<button name="action" value="send" {% if not reviewed %}disabled{% endif %}>ENVOYER</button>
<button name="action" value="modify">MODIFIER</button><button name="action" value="cancel">ANNULER</button>
<button name="action" value="reset">SOMMAIRE</button><br>
<input name="question" type="text" maxlength="2000" placeholder="MAIL / LIRE 1 / REPONDRE / ECRIRE nom : instructions">
<button name="action" value="command">ENVOI</button></form>
<p>RECHERCHE is:unread / from:nom / subject:mot / after:2026/10/01 before:2026/10/13.
Les dates de la liste sont affichées en Europe/Paris.</p></html>"""


@app.route("/test", methods=["GET", "POST"])
@require_login
def test_console():
    mode = request.args.get("mode", "conversation")
    if mode == "gmail":
        return gmail_admin()
    if mode != "conversation":
        abort(400)
    data = load_prompts()
    preset, draft = data["active"], None
    if request.method == "POST":
        preset = request.form.get("preset_key", "")
        if preset not in data["presets"]:
            abort(400)
        draft = request.form.get("system_prompt", "")
    return render_template_string(admin_service_pages.HEAD + admin_service_pages.TEST_NAV +
                                  admin_service_pages.CHAT_TEST + "</html>",
                                  title="Tester le Minitel", message="Brouillon non enregistré ; aucune sauvegarde ni activation." if draft is not None else "", preset=preset, draft=draft, username=memory_entry().username)


@app.route("/gmail", methods=["GET", "POST"])
@require_login
def gmail_admin():
    session["gmail_csrf"] = csrf_token()
    if request.method == "POST" and not hmac.compare_digest(
        request.form.get("csrf", "").encode(), session["gmail_csrf"].encode()
    ):
        return "Requête refusée", 403
    user_change = request.method == "POST" and request.form.get("action") == "user"
    selected = memory_entry(request.form.get("username", "") if user_change else None)
    item = mail_entry("" if selected.key == "shared" else selected.username)
    cancelling = request.method == "POST" and (
        request.form.get("action") in ("cancel", "reset")
        or request.form.get("question", "").strip().upper() in ("ANNULER", "SOMMAIRE")
    )
    if cancelling:
        item["mail"].reset()
        if request.form.get("action") == "reset" or request.form.get("question", "").strip().upper() == "SOMMAIRE":
            discard_memory(session.get("memory_session"))
    if not item["lock"].acquire(blocking=False):
        return (
            ("Session mail effacée; la requête en cours est abandonnée.", 200)
            if cancelling
            else ("Gmail: busy", 409)
        )
    try:
        if request.method == "POST" and not user_change:
            action = request.form.get("action", "")
            if action in ("next", "previous"):
                item["page"] = max(
                    0,
                    min(
                        len(item["pages"]) - 1,
                        item["page"] + (1 if action == "next" else -1),
                    ),
                )
            else:
                command = {
                    "send": "ENVOYER",
                    "modify": "MODIFIER",
                    "cancel": "ANNULER",
                    "reset": "SOMMAIRE",
                    "command": request.form.get("question", ""),
                }.get(action)
                if command is None:
                    return "Action inconnue", 400
                selected.record("user", command, "gmail")
                memory_request = (not gmail_mail.literal_command(command) and selected.interested(command) and
                                  (not item["mail"].draft or memory_session.explicit_request(command)))
                result = selected.handle(command) if memory_request else {"passthrough": True}
                if not result.get("passthrough"):
                    pass
                elif not gmail_mail.enabled():
                    result = {"display": "Gmail désactivé. Suivre docs/GMAIL.md."}
                else:
                    result = item["mail"].handle(
                        command, request.form.get("ticket") or None
                    )
                warning = selected.take_warning()
                if warning:
                    result["display"] += "\n\n" + warning
                selected.record("assistant", result["display"], "memory" if result.get("memory") else "gmail")
                item["pages"], item["page"] = gmail_mail.pages(result["display"]), 0
                item["ticket"] = result.get("review_ticket")
        if item["ticket"] and item["page"] == len(item["pages"]) - 1:
            item["mail"].acknowledge(item["ticket"])
        response = app.make_response(
            render_template_string(
                GMAIL_HTML,
                enabled=gmail_mail.enabled(),
                memory_label=selected.label, username=selected.username,
                csrf=session["gmail_csrf"],
                page=item["pages"][item["page"]],
                position=item["page"] + 1,
                count=len(item["pages"]),
                ticket=item["ticket"],
                reviewed=item["mail"].review_valid(item["ticket"]),
            )
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    finally:
        item["lock"].release()

MEMORY_GATE_SECONDS = 300


def memory_unlocked():
    until = session.get("memory_unlock_until", 0)
    return isinstance(until, (int, float)) and 0 < until - time.time() <= MEMORY_GATE_SECONDS


def inspection_store():
    return memory_session.memory.MemoryStore(read_env().get("MINITEL_MEMORY_DIR"))


@app.route("/maintenance/memory", methods=["GET", "POST"])
@require_login
def memory_admin():
    message, status = None, 200
    if request.method == "POST":
        if request.form.get("action") == "lock":
            session.pop("memory_unlock_until", None)
            return redirect(url_for("memory_admin"))
        session.pop("memory_unlock_until", None)
        if (request.form.get("action") == "unlock" and ADMIN_PASSWORD.strip()
                and hmac.compare_digest(request.form.get("password", "").encode(errors="replace"), ADMIN_PASSWORD.encode())):
            session["memory_unlock_until"] = time.time() + MEMORY_GATE_SECONDS
            return redirect(url_for("memory_admin"))
        message, status = "Mot de passe incorrect.", 401
    store = inspection_store()
    profiles = memory_inspection.inventory(store)
    # Keep actual memory text out of the template context until unlocked.
    visible = [{k: v for k, v in p.items() if k != "data"} for p in profiles]
    return render_template_string(
        admin_service_pages.HEAD + admin_memory_pages.BODY,
        title="Mémoire des utilisateurs", message=message, profiles=visible, selected=None,
        unlocked=memory_unlocked(), automatic=memory_automation.enabled(),
        legacy=memory_inspection.unassigned(store, profiles, contents=memory_unlocked()),
        paths=memory_inspection.storage_paths(store)), status


@app.route("/maintenance/memory/<int:user_id>")
@require_login
def memory_admin_profile(user_id):
    if not memory_unlocked():
        abort(403, "Déverrouillez la mémoire dans Maintenance.")
    store = inspection_store()
    profiles = memory_inspection.inventory(store)
    selected = next((p for p in profiles if p["user_id"] == user_id), None)
    if selected is None:
        abort(404)
    if selected.get("error"):
        abort(503, selected["error"])
    return render_template_string(
        admin_service_pages.HEAD + admin_memory_pages.BODY,
        title="Mémoire des utilisateurs", message=None,
        profiles=[{k: v for k, v in selected.items() if k != "data"}], selected=selected,
        unlocked=True, automatic=memory_automation.enabled(), legacy=[],
        paths=memory_inspection.storage_paths(store))


@app.route("/maintenance/memory/export", defaults={"user_id": None})
@app.route("/maintenance/memory/export/<int:user_id>")
@require_login
def memory_admin_export(user_id):
    if not memory_unlocked():
        abort(403, "Déverrouillez la mémoire dans Maintenance.")
    try:
        data = memory_inspection.export(inspection_store(), user_id)
    except KeyError:
        abort(404)
    response = app.response_class(memory_inspection.encode_export(data), mimetype="application/json")
    name = "all" if user_id is None else str(user_id)
    response.headers["Content-Disposition"] = f'attachment; filename="3615-memory-{name}.json"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.errorhandler(memory_session.memory.MemoryProblem)
@app.errorhandler(gmail_auth.GmailError)
def profile_error(error):
    return render_template_string(admin_service_pages.HEAD,title='Profil indisponible',
                                  message='Profil ou accès indisponible. Revenez au choix du nom ou contactez l’administrateur.'),400


class AdminRequestHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        if unquote(urlsplit(self.path).path).strip("/") == gmail_auth.OAUTH_CALLBACK.strip("/"):
            self.log("info", '"%s %s %s" %s %s', self.command, gmail_auth.OAUTH_CALLBACK,
                     self.request_version, code, size)
        else:
            super().log_request(code, size)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False, request_handler=AdminRequestHandler)

