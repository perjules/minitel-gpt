#!/usr/bin/env python3
"""
MINITEL GPT - service de chat années 80 sur Minitel.
Interface : langue → demarrage fictif → SUITE → saisie → reponse paginee.
Touches : ENVOI = valider, SUITE = page suivante, SOMMAIRE = retour accueil.
Timeout 5 min sans action → retour sommaire.
"""
import json
import os
import sys
import time
import logging
import re
import subprocess
import textwrap
import unicodedata
from pathlib import Path
from dotenv import load_dotenv
import requests

# Translittération vers ASCII affichable sur Minitel (é→e, œ→oe, …) :
# évite les « ? » que produisait encode('ascii','replace').
_ASCII_REPL = {
    "œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "€": "EUR",
    "’": "'", "‘": "'", "“": '"', "”": '"', "«": '"', "»": '"',
    "–": "-", "-": "-", "…": "...", " ": " ", "·": ".", "•": "-",
}

def to_ascii(s: str) -> str:
    if not s:
        return s
    for k, v in _ASCII_REPL.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s)
    return s.encode("ascii", "ignore").decode("ascii")

load_dotenv(Path(__file__).parent.parent / ".env")

import serial

# ── Config ───────────────────────────────────────────────────────────────
def detect_port():
    for p in ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyAMA0", "/dev/serial0"]:
        if os.path.exists(p):
            return p
    return "/dev/ttyUSB0"

PORT = detect_port()
BAUD = 1200
COLS = 40
SCREEN_ROWS = 24
CONTENT_ROWS = 18          # lignes de contenu par page de réponse
IDLE_TIMEOUT = 300         # 5 min → retour sommaire

# ── Fournisseur d'IA (LLM) ───────────────────────────────────────────────
# LLM_PROVIDER = "mistral" (defaut), "claude" ou "linkup". La cle et le modele
# de chaque fournisseur sont independants ; on bascule sans perdre les autres.
PROVIDER = os.getenv("LLM_PROVIDER", "mistral").strip().lower()

MISTRAL_KEY = os.environ.get("MISTRAL_KEY", "")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"

# Claude (Anthropic) - appele en HTTP brut comme Mistral, sans le SDK (le Pi
# Zero ARMv6 evite les dependances lourdes a compiler).
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Linkup (recherche web agentique) - PAS un generateur de texte : pas de
# prompt systeme ni d'historique multi-tours, chaque question devient une
# recherche web isolee dont la reponse sourcee sert de "reponse" du terminal.
LINKUP_KEY = os.environ.get("LINKUP_KEY", "")
LINKUP_DEPTH = os.getenv("LINKUP_DEPTH", "standard")
LINKUP_URL = "https://api.linkup.so/v1/search"

# Modele/mode effectivement utilise (pour les logs)
MODEL = {"claude": CLAUDE_MODEL, "linkup": LINKUP_DEPTH}.get(PROVIDER, MISTRAL_MODEL)
PROMPTS_FILE = Path(__file__).parent.parent / "config" / "prompts.json"
PROMPTS_DEFAULT = Path(__file__).parent.parent / "config" / "prompts.default.json"
UI_FILE = Path(__file__).parent.parent / "config" / "terminal_ui.json"
UI = json.loads(UI_FILE.read_text(encoding="utf-8"))
BOOT_PROMPT_FILE = UI_FILE.with_name("boot_prompt.txt")
BOOT_EXAMPLES_FILE = UI_FILE.with_name("boot_examples.json")
BOOT_TIMEOUT = 8          # one attempt, then the static startup takes over


def ensure_prompts():
    """prompts.json est local (gitignoré) : si absent (1er lancement / après une
    mise à jour), on le crée depuis prompts.default.json fourni par le dépôt."""
    if not PROMPTS_FILE.exists() and PROMPTS_DEFAULT.exists():
        PROMPTS_FILE.write_text(PROMPTS_DEFAULT.read_text(encoding="utf-8"),
                                encoding="utf-8")


def call_mistral(system_prompt, history, timeout=30, require_complete=False):
    """Appelle l'API Mistral (chat completions) et retourne le texte de réponse."""
    messages = [{"role": "system", "content": system_prompt}] + history
    r = requests.post(
        MISTRAL_URL,
        headers={"Authorization": f"Bearer {MISTRAL_KEY}",
                 "Content-Type": "application/json"},
        json={"model": MISTRAL_MODEL, "messages": messages, "max_tokens": 700},
        timeout=timeout,
    )
    r.raise_for_status()
    choice = r.json()["choices"][0]
    if require_complete and choice.get("finish_reason") != "stop":
        raise ValueError("Incomplete Mistral startup response")
    text = choice["message"]["content"]
    return text if require_complete else text.strip()


def call_claude(system_prompt, history, timeout=30, require_complete=False):
    """Appelle l'API Claude (Anthropic Messages) et retourne le texte de réponse.
    Le prompt systeme est passe a part (champ `system`), pas dans `messages`."""
    r = requests.post(
        ANTHROPIC_URL,
        headers={"x-api-key": ANTHROPIC_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": CLAUDE_MODEL, "max_tokens": 700,
              "system": system_prompt, "messages": history,
              # Claude 5.x : thinking adaptatif par défaut -> effort bas pour
              # une réponse rapide ; Haiku 4.5 refuse ce paramètre.
              **({} if CLAUDE_MODEL.startswith("claude-haiku")
                 else {"output_config": {"effort": "low"}})},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    if require_complete and data.get("stop_reason") != "end_turn":
        raise ValueError("Incomplete Claude startup response")
    blocks = data.get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    return text if require_complete else text.strip()


def call_linkup(system_prompt, history):
    """Interroge Linkup (recherche web agentique) et retourne une reponse sourcee.
    Pas de prompt systeme ni d'historique pris en compte : seule la derniere
    question de l'utilisateur sert de requete de recherche.
    Note : la doc publique de Linkup decrit un GET avec parametres d'URL, mais
    en pratique seul un POST avec un corps JSON fonctionne (verifie empiriquement,
    le GET renvoie 404 "Cannot GET /v1/search")."""
    question = next((m["content"] for m in reversed(history)
                     if m.get("role") == "user"), "")
    r = requests.post(
        LINKUP_URL,
        headers={"Authorization": f"Bearer {LINKUP_KEY}",
                 "Content-Type": "application/json"},
        json={"q": question, "depth": LINKUP_DEPTH, "outputType": "sourcedAnswer"},
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


def call_llm(system_prompt, history):
    """Aiguille vers le fournisseur configure (LLM_PROVIDER)."""
    if PROVIDER == "claude":
        return call_claude(system_prompt, history)
    if PROVIDER == "linkup":
        return call_linkup(system_prompt, history)
    return call_mistral(system_prompt, history)


def get_ip():
    """IP locale (wlan0) pour l'affichage de l'accès admin."""
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout
        return out.split()[0] if out.split() else None
    except Exception:
        return None

# Journalisation : toujours sur la sortie standard (capturée par systemd/journald).
# Le fichier de log est un bonus : s'il n'est pas accessible (droits, FS plein…),
# on continue sans lui plutôt que de tuer le terminal. Un simple souci de log ne
# doit jamais empêcher l'affichage sur le Minitel.
_handlers = [logging.StreamHandler(sys.stdout)]
_LOG_FILE = Path(__file__).parent.parent / "logs" / "chatgpt.log"
try:
    _handlers.insert(0, logging.FileHandler(_LOG_FILE))
except Exception as _e:  # PermissionError, FileNotFoundError…
    print(f"[minitel-gpt] log fichier indisponible ({_e}), sortie standard seule",
          file=sys.stderr)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [minitel-gpt] %(levelname)s %(message)s",
    handlers=_handlers,
)
log = logging.getLogger(__name__)

# ── Codes Videotex ───────────────────────────────────────────────────────
ESC, SO, SI, RS, FF, CR, LF, SEP, BS = 0x1B,0x0E,0x0F,0x1E,0x0C,0x0D,0x0A,0x13,0x08
FG_WHITE  = bytes([ESC,0x47])
FG_CYAN   = bytes([ESC,0x46])
FG_YELLOW = bytes([ESC,0x43])
FG_GREEN  = bytes([ESC,0x42])
BG_BLACK  = bytes([ESC,0x50])
DBL_HEIGHT= bytes([ESC,0x4C])   # double hauteur
DBL_SIZE  = bytes([ESC,0x4F])   # double hauteur+largeur
SZ_NORMAL = bytes([ESC,0x4C-0x0C])  # 0x40 normal (placeholder)
# PRO2 START/STOP ROULEAU: scrolling within the 40-column Videotex screen.
SCROLL_ON = bytes([ESC, 0x3A, 0x69, 0x43])
SCROLL_OFF = bytes([ESC, 0x3A, 0x6A, 0x43])

# Touches de fonction Minitel (SEP + code)
K_ENVOI=0x41; K_RETOUR=0x42; K_REPET=0x43; K_GUIDE=0x44
K_ANNUL=0x45; K_SOMMAIRE=0x46; K_CORR=0x47; K_SUITE=0x48

# Minitel 2 en mode péri-informatique : les touches de fonction sont émises
# en VT100 (SS3 = "ESC O x") au lieu du Videotex "SEP + code". Mapping x → code.
SS3_MAP = {0x4D: K_ENVOI, 0x50: K_SOMMAIRE, 0x6E: K_SUITE, 0x6D: K_GUIDE,
           0x52: K_RETOUR, 0x6C: K_CORR, 0x51: K_ANNUL}

FALLBACK_PROMPT = (
    "Tu es MINITEL GPT. Reponds en francais, concis (max 30 lignes de 40 caracteres), "
    "ASCII sans accents ni emojis. Ne mentionne jamais que tu es une autre IA."
)


KNOWLEDGE_DIR = Path(__file__).parent.parent / "config" / "knowledge"
KNOWLEDGE_MAX_CHARS = 12000   # plafond du contexte injecté (coût/latence)


def load_knowledge(active_key):
    """Concatène les fichiers .txt de connaissance du preset (plafonné)."""
    folder = KNOWLEDGE_DIR / active_key
    if not folder.is_dir():
        return ""
    parts = []
    total = 0
    for f in sorted(folder.glob("*.txt")):
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            continue
        if not txt:
            continue
        parts.append(f"--- {f.name} ---\n{txt}")
        total += len(txt)
        if total >= KNOWLEDGE_MAX_CHARS:
            break
    blob = "\n\n".join(parts)
    return blob[:KNOWLEDGE_MAX_CHARS]


def load_preset():
    """Retourne (system, title_msg, question_msg, loading_msg, title_art).
    Le system inclut les fichiers de connaissance du preset s'il y en a.
    title_art est la liste de segments {text, font} qui composent l'animation
    ASCII d'accueil (voir admin, onglet Personnalites)."""
    try:
        ensure_prompts()
        data = json.load(open(PROMPTS_FILE))
        key = data["active"]
        p = data["presets"][key]
        system = p.get("system", FALLBACK_PROMPT)
        knowledge = load_knowledge(key)
        if knowledge:
            system += ("\n\nCONNAISSANCES DE REFERENCE (utilise ces informations "
                       "en priorite pour repondre) :\n" + knowledge)
        return (
            system,
            p.get("title_msg", "*** MINITEL GPT ***"),
            p.get("question_msg", "Posez votre question :"),
            p.get("loading_msg", "Consultation en cours..."),
            p.get("title_art") or TITLE_ART_DEFAULT,
        )
    except Exception as e:
        log.warning(f"prompts.json: {e}")
        return (FALLBACK_PROMPT, "*** MINITEL GPT ***",
                "Posez votre question :", "Consultation en cours...", TITLE_ART_DEFAULT)


# ── ASCII title (pyfiglet) ───────────────────────────────────────────────
# Chaque preset peut composer son propre titre d'accueil : une liste de
# segments {text, font}, chacun rendu via pyfiglet (voir admin, onglet
# Personnalites, pour la liste des polices proposees et l'apercu). A defaut
# (preset sans title_art, ou cree avant cette fonctionnalite), on retombe sur
# le titre historique "MINITEL" + "GPT".
TITLE_ART_DEFAULT = [{"text": "MINITEL", "font": "small"}, {"text": "GPT", "font": "standard"}]
TITLE_ART_MAX_HEIGHT = 12   # garde-fou ecran (24 lignes au total sur le Minitel)

def build_title(title_art=None):
    """Rend l'animation ASCII d'accueil a partir des segments {text, font}.
    Repli par segment si une police est invalide OU si son rendu ne produit
    aucun caractere ASCII visible (le paquet apt python3-pyfiglet, "+dfsg",
    inclut des polices - block, mono, braille, emboss... - qui dessinent en
    caracteres Unicode que to_ascii() supprime silencieusement : sans ce
    garde-fou, ces polices donnaient un titre vide). Repli global si pyfiglet
    est indisponible."""
    segments = title_art or TITLE_ART_DEFAULT
    try:
        from pyfiglet import Figlet
    except Exception as e:
        log.warning(f"pyfiglet indisponible: {e}")
        return ["", "  " + "   ".join(" ".join(s.get("text", "")) for s in segments), ""]
    lines = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        font = seg.get("font") or "standard"
        try:
            fig = Figlet(font=font, width=COLS)
            seg_lines = [ln[:COLS] for ln in fig.renderText(text).rstrip("\n").split("\n")
                         if ln.strip()]
            if seg_lines and not any(to_ascii(ln).strip() for ln in seg_lines):
                log.warning(f"pyfiglet police '{font}': rendu non-ASCII, ignore")
                continue
            lines.extend(seg_lines)
        except Exception as e:
            log.warning(f"pyfiglet police '{font}': {e}")
    if not lines:
        lines = ["", "  " + "   ".join(" ".join(s.get("text", "")) for s in segments), ""]
    return lines[:TITLE_ART_MAX_HEIGHT]


# ── Serial helpers ───────────────────────────────────────────────────────
class Term:
    def __init__(self):
        self.s = serial.Serial(PORT, BAUD, bytesize=7, parity="E",
                               stopbits=1, timeout=0.1)
        time.sleep(0.3)

    def w(self, data):
        if isinstance(data, str):
            data = to_ascii(data).encode("ascii", errors="replace")
        self.s.write(data)

    def clear(self):
        self.w(bytes([FF, RS]))
        time.sleep(0.2)

    def line(self, text=""):
        self.w(text[:COLS]); self.w(bytes([CR, LF]))

    def center(self, text):
        text = text[:COLS]
        self.w(" " * ((COLS - len(text)) // 2)); self.w(text); self.w(bytes([CR, LF]))

    def read_byte(self):
        b = self.s.read(1)
        return b[0] if b else None

    def read_key(self, timeout):
        """Lit une touche. Retourne ('char', c) ou ('fn', code) ou ('timeout', None)."""
        end = time.time() + timeout
        while time.time() < end:
            b = self.read_byte()
            if b is None:
                continue
            if b == SEP:
                # Mode Videotex (Minitel 1) : SEP + code touche
                code = self.read_byte()
                t2 = time.time() + 0.5
                while code is None and time.time() < t2:
                    code = self.read_byte()
                if code is None:
                    continue          # SEP parasite → ignorer, ne pas bloquer
                return ('fn', code)
            if b == ESC:
                # Mode péri-info (Minitel 2) : touche fonction en VT100 "ESC O x"
                b2 = self.read_byte()
                t2 = time.time() + 0.5
                while b2 is None and time.time() < t2:
                    b2 = self.read_byte()
                if b2 in (0x39, 0x3A, 0x3B):
                    # Consume complete PRO1/2/3 replies, including scroll ACKs.
                    # Their printable status bytes must not become user input.
                    for _ in range(b2 - 0x38):
                        reply = self.read_byte()
                        t3 = time.time() + 0.5
                        while reply is None and time.time() < t3:
                            reply = self.read_byte()
                        if reply is None:
                            break
                    continue
                if b2 == 0x4F:        # 'O' → séquence SS3
                    code = self.read_byte()
                    t3 = time.time() + 0.5
                    while code is None and time.time() < t3:
                        code = self.read_byte()
                    if code in SS3_MAP:
                        return ('fn', SS3_MAP[code])
                continue              # autre séquence ESC → ignorer
            return ('char', b)
        return ('timeout', None)


def wrap(text, width=COLS):
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        cur = ""
        for word in para.split():
            if len(cur) + len(word) + (1 if cur else 0) <= width:
                cur = (cur + " " + word).strip()
            else:
                out.append(cur)
                cur = word[:width]
        if cur:
            out.append(cur)
    return out


# ── Écrans ───────────────────────────────────────────────────────────────
def show_home(t: Term, title_lines, title_msg):
    t.clear()
    t.w(bytes([CR, LF]))
    t.w(FG_CYAN)
    for ln in title_lines:
        t.center(ln)
    t.line()
    t.w(FG_YELLOW)
    t.center(title_msg)
    t.line()
    t.w(FG_WHITE)
    t.center(UI["language_prompt"])
    t.center(UI["language_options"])
    t.center(UI["language_submit"])
    t.line()


def choose_language(text):
    """French is the silent fallback for empty or unrecognised input."""
    return "en" if (text or "").strip().lower() in ("en", "english") else "fr"


def session_prompt(system_prompt, language):
    """Keep the selected language out of conversation history."""
    name = "English" if language == "en" else "French"
    return system_prompt + (
        f"\n\nSESSION LANGUAGE: Reply in {name} unless the user explicitly "
        "requests another language. This overrides any default language above. "
        "Always refer to Minitel function keys by their French names: "
        "ENVOI, SUITE, RETOUR, SOMMAIRE, GUIDE, ANNULATION, CORRECTION, REPETITION."
    )


def validate_boot(text):
    """Reject controls/bad formats, then adapt text for a scrolling display."""
    if not isinstance(text, str):
        raise ValueError("Startup response is not text")
    text = text.replace("\r\n", "\n")
    if any(c != "\n" and not c.isprintable() for c in text):
        raise ValueError("Startup contains non-printable characters")
    text = to_ascii(text)
    if not text.strip():
        raise ValueError("Startup is empty")
    lines = text.strip("\n").split("\n")
    if "```" in text or any(line.lstrip().startswith(("#", "* ", "- ")) for line in lines):
        raise ValueError("Startup contains Markdown")
    if re.search(r"\b(press|push|appuyez|pressez|tapez)\b[^\n]*"
                 r"\b(ENVOI|SUITE|RETOUR|SOMMAIRE|GUIDE|ANNULATION|CORRECTION|REPETITION)\b", text, re.I):
        raise ValueError("Startup contains function-key instructions")
    rows = []
    for line in lines:
        rows.extend(textwrap.wrap(line, width=COLS - 1, replace_whitespace=False,
                                  break_on_hyphens=False) or [""])
    return rows


def prepare_boot(language):
    """One independent model call; its result never enters factual history."""
    fallback = list(UI[language]["boot"])
    if PROVIDER == "claude":
        generate, key = call_claude, ANTHROPIC_KEY
    elif PROVIDER == "mistral":
        generate, key = call_mistral, MISTRAL_KEY
    else:
        return fallback          # Linkup is a search service, not a boot writer
    if not key:
        return fallback
    try:
        prompt = BOOT_PROMPT_FILE.read_text(encoding="utf-8")
        examples = json.loads(BOOT_EXAMPLES_FILE.read_text(encoding="utf-8"))
        name = "English" if language == "en" else "French"
        request = f"Generate a new MF-83 startup in {name}. Reviewed examples:\n\n"
        request += "\n\n".join(example[language] for example in examples)
        text = generate(prompt, [{"role": "user", "content": request}],
                        timeout=BOOT_TIMEOUT, require_complete=True)
        rows = validate_boot(text)
        log.info("Generated fictional startup: %s lines", len(rows))
        return rows + ["", fallback[-1]]
    except Exception as e:
        log.warning("Startup generation failed (%s: %s); using static sequence",
                    type(e).__name__, e)
        return fallback


def show_boot(t: Term, language, lines=None):
    """Fictional diagnostics; only SUITE opens the question prompt."""
    t.clear()
    try:
        t.w(SCROLL_ON)
        t.w(FG_CYAN)
        for line in lines if lines is not None else UI[language]["boot"]:
            t.line(line)
        while True:
            kind, code = t.read_key(IDLE_TIMEOUT)
            if kind == 'timeout':
                return 'timeout'
            if kind == 'fn':
                if code == K_SUITE:
                    return 'done'
                if code == K_SOMMAIRE:
                    return 'sommaire'
                if code == K_GUIDE:
                    return 'guide'
    finally:
        # Restore page mode for the existing menus and paginated answers.
        t.w(SCROLL_OFF)


def show_question_prompt(t: Term, question_msg):
    t.clear()
    t.w(FG_WHITE)
    t.center(question_msg)
    t.line()


def show_guide(t: Term, language="fr"):
    """Écran d'aide affiché sur la touche GUIDE : adresse de l'interface d'admin."""
    ip = get_ip()
    t.clear()
    t.w(bytes([CR, LF, CR, LF]))
    t.w(FG_CYAN); t.center("=== ADMINISTRATION ===")
    t.w(bytes([CR, LF, CR, LF]))
    t.w(FG_WHITE)
    if ip:
        t.center(f"http://{ip}:8080")
    else:
        t.center(UI[language]["ip_unavailable"])
    t.w(bytes([CR, LF, CR, LF]))
    t.center(f"{UI[language]['password']} : {os.getenv('ADMIN_PASSWORD', 'mistral')}")
    t.w(bytes([CR, LF, CR, LF, CR, LF]))
    t.w(FG_CYAN); t.center(UI[language]["guide_return"])
    t.read_key(120)


def read_question(t: Term, allow_empty=False):
    """Lit une question. Retourne (texte, 'envoi') / (None,'sommaire') / (None,'timeout')."""
    t.w(FG_GREEN)
    t.w("> ")
    buf = []
    # Le Minitel fait l'écho local des frappes : on ne ré-écho PAS côté Pi.
    while True:
        kind, code = t.read_key(IDLE_TIMEOUT)
        if kind == 'timeout':
            return None, 'timeout'
        if kind == 'fn':
            if code == K_SOMMAIRE:
                return None, 'sommaire'
            if code == K_GUIDE:
                return None, 'guide'
            if code == K_ENVOI:
                if buf or allow_empty:
                    return "".join(buf), 'envoi'
            if code in (K_CORR, K_RETOUR):
                if buf:
                    buf.pop()
                    t.w(bytes([BS, 0x20, BS]))   # backspace destructif
            continue
        # caractère
        c = code
        if c in (CR, LF):
            if buf or allow_empty:
                return "".join(buf), 'envoi'
        elif c in (BS, 0x7F):
            if buf:
                buf.pop()
                t.w(bytes([BS, 0x20, BS]))
        elif 0x20 <= c <= 0x7E:
            buf.append(chr(c))       # pas d'écho (le Minitel l'affiche)


def show_response(t: Term, text: str, language="fr"):
    """Affiche la réponse en pages. Retourne 'sommaire' / 'done' / 'timeout'."""
    lines = wrap(text)
    pages = [lines[i:i+CONTENT_ROWS] for i in range(0, len(lines), CONTENT_ROWS)] or [[""]]
    for pidx, page in enumerate(pages):
        t.clear()
        t.w(FG_WHITE)
        for ln in page:
            t.line(ln)
        last = (pidx == len(pages) - 1)
        if not last:
            t.w(bytes([CR, LF]))
            t.w(FG_CYAN)
            t.center(UI[language]["next_page"])
            while True:
                kind, code = t.read_key(IDLE_TIMEOUT)
                if kind == 'timeout':
                    return 'timeout'
                if kind == 'fn':
                    if code == K_SUITE:
                        break
                    if code == K_SOMMAIRE:
                        return 'sommaire'
    return 'done'


# ── Boucle principale ────────────────────────────────────────────────────
def run():
    t = Term()
    log.info(f"Démarré sur {PORT} (fournisseur {PROVIDER}, modèle {MODEL})")

    while True:  # boucle sommaire
        # Recharger le preset à chaque retour au sommaire (prise en compte des édits)
        system_prompt, title_msg, question_msg, loading_msg, title_art = load_preset()
        title_lines = build_title(title_art)
        history = []
        show_home(t, title_lines, title_msg)
        choice, action = read_question(t, allow_empty=True)
        if action == 'guide':
            show_guide(t)
            continue
        if action == 'sommaire':
            continue
        language = choose_language(choice)
        system_prompt = session_prompt(system_prompt, language)
        if language == "en":
            question_msg = UI[language]["question"]
            loading_msg = UI[language]["loading"]
        t.w(bytes([CR, LF]))
        t.w(FG_CYAN)
        t.center(UI[language]["boot_wait"])
        # An unattended language-screen timeout must not spend an API call.
        boot_lines = (prepare_boot(language) if action == 'envoi'
                      else list(UI[language]["boot"]))  # retained for this session only
        result = show_boot(t, language, boot_lines)
        if result == 'guide':
            show_guide(t, language)
        if result != 'done':
            continue
        show_question_prompt(t, question_msg)

        while True:  # boucle conversation
            question, action = read_question(t)
            if action == 'guide':
                show_guide(t, language)
                break          # retour au sommaire après l'écran d'aide
            if action in ('sommaire', 'timeout'):
                break

            history.append({"role": "user", "content": question})
            log.info(f"Q: {question!r}")

            t.w(bytes([CR, LF]))
            t.w(FG_CYAN); t.line(""); t.center(loading_msg)
            try:
                answer = call_llm(system_prompt, history)
                history.append({"role": "assistant", "content": answer})
                answer = to_ascii(answer)
            except Exception as e:
                log.error(f"API: {e}")
                answer = UI[language]["connection_error"]

            result = show_response(t, answer, language)
            if result in ('sommaire', 'timeout'):
                break

            # Invite pour rebondir
            t.w(bytes([CR, LF]))
            t.w(FG_WHITE)
            t.center(UI[language]["follow_up"])
            t.w(bytes([CR, LF]))

            if len(history) > 20:
                history = history[-20:]


if __name__ == "__main__":
    run()
