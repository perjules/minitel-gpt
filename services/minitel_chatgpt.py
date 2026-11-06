#!/usr/bin/env python3
"""
MINITEL GPT - service de chat années 80 sur Minitel.
Interface : SOMMAIRE → demarrage FR → connexion → saisie → reponse paginee.
Touches : ENVOI = valider, SUITE = page suivante, SOMMAIRE = nouvelle conversation.
SOMMAIRE réinitialise la conversation ; LOGOUT change d'utilisateur.
"""
import json
import logging
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
import unicodedata
from collections import deque
from pathlib import Path
from queue import Empty, Queue

import requests
from dotenv import load_dotenv

try:  # Support both systemd's direct script and package imports in tests.
    from . import (
        calculator,
        christmas_list,
        deployment,
        gmail_mail,
        io_limits,
        llm_streaming,
        mainframe_update,
        memory_session,
        mistral_answers,
        printing_session,
        pronote,
        pronote_answers,
        pronote_mode,
        stream_sources,
        terminal_diagnostics,
        terminal_display,
        terminal_link,
        terminal_scrolling,
        user_services,
        videotex,
    )
except ImportError:
    # Direct systemd entry point: keep one identity for exception/session classes.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from services import (
        calculator,
        christmas_list,
        deployment,
        gmail_mail,
        io_limits,
        llm_streaming,
        mainframe_update,
        memory_session,
        mistral_answers,
        printing_session,
        pronote,
        pronote_answers,
        pronote_mode,
        stream_sources,
        terminal_diagnostics,
        terminal_display,
        terminal_link,
        terminal_scrolling,
        user_services,
        videotex,
    )

try:
    from . import journal, journal_terminal, rose, rose_terminal, terminal_documents
except ImportError:
    # Direct systemd script: share the package identity with the rose adapter.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from services import (
        journal,
        journal_terminal,
        rose,
        rose_terminal,
        terminal_documents,
    )

# Translittération vers ASCII affichable sur Minitel (é→e, œ→oe, …) :
# évite les « ? » que produisait encode('ascii','replace').
_ASCII_REPL = {
    "œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "€": "EUR",
    "’": "'", "‘": "'", "“": '"', "”": '"', "«": '"', "»": '"',
    "—": "-", "–": "-", "-": "-", "…": "...", " ": " ", "·": ".", "•": "-",
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
COLS = terminal_display.STANDARD.columns
SCREEN_ROWS = terminal_display.STANDARD.rows
CONTENT_ROWS = terminal_display.STANDARD.content_rows          # lignes de contenu par page de réponse
IDLE_TIMEOUT = 300         # 5 min → nouvelle conversation, même utilisateur
BOOT_CONTINUE_TIMEOUT = 30
BOOT_CONTINUE_PROMPT = "SUITE: continuer / connexion dans 30 s"

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
UPDATE_TIMEOUT = 20      # the longer maintenance procedure needs its own budget


def ensure_prompts():
    """prompts.json est local (gitignoré) : si absent (1er lancement / après une
    mise à jour), on le crée depuis prompts.default.json fourni par le dépôt."""
    if not PROMPTS_FILE.exists() and PROMPTS_DEFAULT.exists():
        PROMPTS_FILE.write_text(PROMPTS_DEFAULT.read_text(encoding="utf-8"),
                                encoding="utf-8")


def call_mistral(system_prompt, history, timeout=30, require_complete=False, *, max_tokens=700):
    """Appelle l'API Mistral (chat completions) et retourne le texte de réponse."""
    messages = [{"role": "system", "content": system_prompt}] + history
    r = requests.post(
        MISTRAL_URL,
        headers={"Authorization": f"Bearer {MISTRAL_KEY}",
                 "Content-Type": "application/json"},
        json={"model": MISTRAL_MODEL, "messages": messages, "max_tokens": max_tokens},
        timeout=timeout,
    )
    r.raise_for_status()
    choice = r.json()["choices"][0]
    if require_complete and choice.get("finish_reason") != "stop":
        raise ValueError("Incomplete Mistral startup response")
    text = choice["message"]["content"]
    return text if require_complete else text.strip()


def call_claude(system_prompt, history, timeout=30, require_complete=False, *, max_tokens=700):
    """Appelle l'API Claude (Anthropic Messages) et retourne le texte de réponse.
    Le prompt systeme est passe a part (champ `system`), pas dans `messages`."""
    r = requests.post(
        ANTHROPIC_URL,
        headers={"x-api-key": ANTHROPIC_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": CLAUDE_MODEL, "max_tokens": max_tokens,
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
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, check=False, timeout=5).stdout
        return out.split()[0] if out.split() else None
    except (OSError, subprocess.SubprocessError):
        return None

# One stream only: production systemd sends it to the bounded application journal.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [minitel-gpt] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── Codes Videotex ───────────────────────────────────────────────────────
ESC, SO, SI, RS, FF, CR, LF, SEP, BS = 0x1B,0x0E,0x0F,0x1E,0x0C,0x0D,0x0A,0x13,0x08
FG_WHITE  = bytes([ESC,0x47])
FG_CYAN   = bytes([ESC,0x46])
FG_YELLOW = bytes([ESC,0x43])
FG_GREEN  = bytes([ESC,0x42])
BG_BLACK  = bytes([ESC,0x50])
DBL_HEIGHT= bytes([ESC,0x4D])   # double hauteur
DBL_SIZE  = bytes([ESC,0x4F])   # double hauteur+largeur
SZ_NORMAL = bytes([ESC,0x4C])
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
    blob = "\n\n".join(parts)
    return blob[:KNOWLEDGE_MAX_CHARS]


def load_preset():
    """Retourne (system, title_msg, question_msg, loading_msg, title_art).
    Le system inclut les fichiers de connaissance du preset s'il y en a.
    title_art est la liste de segments {text, font} qui composent l'animation
    ASCII d'accueil (voir admin, onglet Personnalites)."""
    try:
        ensure_prompts()
        with PROMPTS_FILE.open(encoding="utf-8") as source:
            data = json.load(source)
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
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        log.warning(f"prompts.json: {e}")
        return (FALLBACK_PROMPT, "*** MINITEL GPT ***",
                "Posez votre question :", "Consultation en cours...", TITLE_ART_DEFAULT)


def factual_knowledge():
    """Keep the preset's reference files, without copying its personality prompt."""
    try:
        with PROMPTS_FILE.open(encoding="utf-8") as source:
            return load_knowledge(json.load(source)["active"])
    except (OSError, ValueError, KeyError):
        return ""


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
        from pyfiglet import Figlet, FigletError
    except ImportError as e:
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
        except (FigletError, OSError, ValueError, TypeError) as e:
            log.warning(f"pyfiglet police '{font}': {e}")
    if not lines:
        lines = ["", "  " + "   ".join(" ".join(s.get("text", "")) for s in segments), ""]
    return lines[:TITLE_ART_MAX_HEIGHT]


# ── Serial helpers ───────────────────────────────────────────────────────
KEYBOARD_STATUS = b"\x1b\x39\x72"
KEYBOARD_LOWER = b"\x1b\x3a\x69\x45"
KEYBOARD_UPPER = b"\x1b\x3a\x6a\x45"

class Term:
    geometry = terminal_display.STANDARD
    view = None

    def __init__(self, transport=None):
        self.geometry = terminal_display.STANDARD
        self.view = None
        self.prompt_history = terminal_display.PromptHistory()
        self._tx_until = 0.0
        self.link_activity = lambda: None
        if transport is not None:
            self.s = transport
        else:
            self.s = self.io(serial.Serial, detect_port(), BAUD, bytesize=7,
                             parity="E", stopbits=1, timeout=0.1, write_timeout=2)
            time.sleep(0.3)
        self._write_slack = getattr(self.s, "write_timeout", None)
        self.physical = isinstance(self.s, serial.SerialBase)
        self._input_bytes = deque()
        self._ss2 = bytearray()
        self._requested_input_case = os.getenv("MINITEL_INPUT_CASE", "lowercase")
        self._keyboard_original = None
        self._keyboard_changed = False
        self.input_case = "unconfirmed" if self.physical else "console"
        self._presence_enabled = isinstance(self.s, serial.SerialBase)
        self._presence_due = float("inf")
        self._presence_deadline = None
        self._presence_seen = False
        self._presence_misses = 0

    def keyboard_mode_status(self, command):
        """Consume a bounded mode reply, preserving other bytes as early input.

        Read the UART directly here: saved input must not be consumed again by
        a subsequent mode exchange. Never clear the UART or transform key bytes.
        """
        self.w(command)
        deadline = self.reply_deadline(1)
        reply = bytearray()
        while time.monotonic() < deadline:
            byte = self.io(self.s.read, 1)
            if not byte:
                time.sleep(.01)
                continue
            reply.extend(byte)
            if len(reply) + len(self._input_bytes) > io_limits.MAX_QUESTION_CHARS + 64:
                raise terminal_link.Unavailable("keyboard_typeahead_overflow")
            if len(reply) >= 4 and reply[-4:-1] == b"\x1b\x3a\x73" and 0x40 <= reply[-1] <= 0x7F:
                self._input_bytes.extend(reply[:-4])
                return reply[-1]
        self._input_bytes.extend(reply)
        return None

    def configure_keyboard(self):
        if not self.physical:
            return
        requested = self._requested_input_case
        if requested not in {"lowercase", "legacy"}:
            log.warning("terminal_keyboard invalid_configuration; requesting original behavior")
            requested = "legacy"
        status = self.keyboard_mode_status(KEYBOARD_STATUS)
        if status is not None:
            self._keyboard_original = bool(status & 8)
            self._keyboard_changed = True  # Before sending: partial writes can change the terminal.
            status = self.keyboard_mode_status(KEYBOARD_LOWER if requested == "lowercase" else KEYBOARD_UPPER)
            if status is not None and bool(status & 8) == (requested == "lowercase"):
                self.input_case = requested
                log.info("terminal_keyboard mode=%s confirmed", requested)
                return
        # No software case swap. Attempt the original mode if native setup is
        # unsupported or uncertain, and describe only what was acknowledged.
        status = self.keyboard_mode_status(KEYBOARD_UPPER)
        self.input_case = "legacy" if status is not None and not status & 8 else "unconfirmed"
        log.warning("terminal_keyboard requested=%s fallback=%s", requested, self.input_case)

    def restore_keyboard(self):
        if self._keyboard_changed and self._keyboard_original is not None:
            status = self.keyboard_mode_status(KEYBOARD_LOWER if self._keyboard_original else KEYBOARD_UPPER)
            if status is None or bool(status & 8) != self._keyboard_original:
                log.warning("terminal_keyboard restoration=unconfirmed; power-cycle if keyboard behaves oddly")
            else:
                self._keyboard_changed = False

    def start_presence(self):
        # SOMMAIRE proves the terminal is awake; do not configure a powered-off
        # terminal when the service first opens its USB adapter.
        if self.physical:
            self.configure_keyboard()
        self._presence_deadline = None
        self._presence_misses = 0
        self._presence_due = time.monotonic()

    def pause_presence(self):
        # A busy terminal may defer status replies while rendering the boot screen.
        self._presence_due = float("inf")
        self._presence_deadline = None
        self._presence_misses = 0

    def poll_presence(self):
        """STUM terminal status: PRO1 70 -> PRO2 71 status (no screen output).

        Three missed replies retire an established link. Terminals that never
        acknowledge status keep working, without mistaking inactivity for off.
        Console transports do not participate in this physical-terminal probe.
        """
        if not getattr(self, "_presence_enabled", False):
            return
        now = time.monotonic()
        if self._presence_deadline is not None and now >= self._presence_deadline:
            self._presence_deadline = None
            self._presence_misses += 1
            if self._presence_misses >= 3:
                if self._presence_seen:
                    raise terminal_link.Unavailable("terminal_status_lost")
                self._presence_enabled = False
                log.info("Terminal status unsupported; physical power detection disabled")
                return
        if self._presence_deadline is None and now >= self._presence_due:
            self.w(b"\x1b\x39\x70")
            self._presence_deadline = self.reply_deadline(1)
            self._presence_due = now + 2

    def presence_reply(self):
        self._presence_seen = True
        self._presence_misses = 0
        self._presence_deadline = None

    def keyboard_activity(self):
        if (getattr(self, "_presence_enabled", False)
                and self._presence_due != float("inf")):
            # A real key also proves liveness, including during slow page output.
            self._presence_deadline = None
            self._presence_misses = 0
            self._presence_due = time.monotonic() + 2

    def io(self, operation, *args, **kwargs):
        try:
            result = operation(*args, **kwargs)
        except OSError as error:
            # Class only: never expose arbitrary exception payloads in a loop.
            raise terminal_link.Unavailable(type(error).__name__) from None
        getattr(self, "link_activity", lambda: None)()
        return result

    def close(self):
        self.prompt_history.clear()
        try:
            self.restore_keyboard()
        except terminal_link.Unavailable:
            log.warning("terminal_keyboard restoration=unavailable")
        self._input_bytes.clear()
        self._ss2.clear()
        close = getattr(self.s, "close", None)
        if callable(close):
            try:
                close()
            except OSError:
                pass

    def w(self, data):
        if isinstance(data, str):
            data = videotex.encode("".join(char if char in videotex.GLYPHS else to_ascii(char)
                                         for char in unicodedata.normalize("NFC", data)))
        baud = getattr(self.s, "baudrate", None)
        started = time.monotonic() if isinstance(baud, int) and baud > 0 else None
        slack = getattr(self, "_write_slack", None)
        if started is not None and type(slack) in (int, float) and 0 < slack < float("inf"):
            # A full USB/driver buffer can block a healthy write while earlier
            # bytes drain at 120 bytes/s. Allow that backlog plus this payload,
            # then the original finite slack. Never retry a partly sent write.
            budget = self.reply_deadline(slack + len(data) * 10 / baud, now=started) - started
            if self.s.write_timeout != budget:
                self.io(setattr, self.s, "write_timeout", budget)
        written = self.io(self.s.write, data)
        if written is not None and written != len(data):
            raise terminal_link.Unavailable("short_write")
        if started is not None:
            # write() queues bytes; the USB adapter can still be transmitting
            # when the driver reports out_waiting == 0. Track all queued 7E1
            # bytes so protocol deadlines include the screen ahead of them.
            self._tx_until = max(started, getattr(self, "_tx_until", 0)) + len(data) * 10 / baud

    def reply_deadline(self, timeout, *, now=None):
        """One serial timing budget for routing ACKs and presence replies."""
        now = time.monotonic() if now is None else now
        queued = self.io(getattr, self.s, "out_waiting", 0)
        if not isinstance(queued, int) or queued < 0:
            queued = 0
        baud = getattr(self.s, "baudrate", BAUD)
        if not isinstance(baud, int) or baud <= 0:
            baud = BAUD
        return max(now, getattr(self, "_tx_until", 0), now + queued * 10 / baud) + timeout

    def waiting(self, text):
        if self.view is not None:
            self.view.busy(text)
        else:
            self.line()
            self.center(text)

    def clear(self):
        self._ss2.clear()
        self.view = None
        self.w(bytes([FF, RS]))
        time.sleep(0.2)

    def line(self, text=""):
        if self.view is not None:
            if text:
                self.view.status(text)
            return
        self.w(text[:self.geometry.width]); self.w(bytes([CR, LF]))

    def center(self, text):
        if self.view is not None:
            self.line(text)
            return
        text = text[:self.geometry.width]
        self.w(" " * ((self.geometry.columns - len(text)) // 2)); self.w(text); self.w(bytes([CR, LF]))

    def read_byte(self):
        if self._input_bytes:
            return self._input_bytes.popleft()
        b = self.io(self.s.read, 1)
        if not b:
            time.sleep(0.01)  # Also yield if a driver returns empty immediately.
        return b[0] if b else None

    def read_key(self, timeout):
        """Lit une touche. Retourne ('char', c) ou ('fn', code) ou ('timeout', None)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            terminal_link.check_worker(getattr(self, "_pending", None))
            self.poll_presence()
            b = self.read_byte()
            if b is None:
                continue
            if b == SEP:
                self._ss2.clear()
                # Mode Videotex (Minitel 1) : SEP + code touche
                code = self.read_byte()
                t2 = time.monotonic() + 0.5
                while code is None and time.monotonic() < t2:
                    code = self.read_byte()
                if code is None:
                    continue          # SEP parasite → ignorer, ne pas bloquer
                if code in (K_ENVOI, K_SUITE, K_SOMMAIRE, K_GUIDE, K_RETOUR, K_CORR, K_ANNUL, K_REPET):
                    self.keyboard_activity()
                return ('fn', code)
            if b == ESC:
                # Mode péri-info (Minitel 2) : touche fonction en VT100 "ESC O x"
                b2 = self.read_byte()
                t2 = time.monotonic() + 0.5
                while b2 is None and time.monotonic() < t2:
                    b2 = self.read_byte()
                if b2 in (0x39, 0x3A, 0x3B):
                    # Consume complete PRO1/2/3 replies, including scroll ACKs.
                    # Their printable status bytes must not become user input.
                    payload = []
                    for _ in range(b2 - 0x38):
                        reply = self.read_byte()
                        t3 = time.monotonic() + 0.5
                        while reply is None and time.monotonic() < t3:
                            reply = self.read_byte()
                        if reply is None:
                            break
                        payload.append(reply)
                    if b2 == 0x3A and len(payload) == 2 and payload[0] == 0x71:
                        self.presence_reply()
                    continue
                if b2 == 0x4F:        # 'O' → séquence SS3
                    code = self.read_byte()
                    t3 = time.monotonic() + 0.5
                    while code is None and time.monotonic() < t3:
                        code = self.read_byte()
                    if code in SS3_MAP:
                        self._ss2.clear()
                        self.keyboard_activity()
                        return ('fn', SS3_MAP[code])
                continue              # autre séquence ESC → ignorer
            if b == videotex.SS2:
                self._ss2[:] = bytes([b])
                continue
            if self._ss2:
                if b < 0x20 or b == 0x7F:
                    self._ss2.clear()  # Execute controls without swallowing an exit.
                else:
                    self._ss2.append(b)
                    if len(self._ss2) == 2 and b in videotex.ACCENTS:
                        continue  # A dead accent may precede its base by seconds.
                    char = videotex.decode(self._ss2)
                    self._ss2.clear()
                    self.keyboard_activity()
                    return "char", ord(char)
            if b in (CR, LF, BS, 0x7F) or 0x20 <= b <= 0x7E:
                self.keyboard_activity()
            return ('char', b)
        return ('timeout', None)

    def poll_key(self):
        """Check keys without an empty serial/curses read delaying each row."""
        self.poll_presence()
        if not self._input_bytes and self.io(getattr, self.s, "in_waiting", None) == 0:
            return "timeout", None
        return self.read_key(.001)


def wrap(text, width=COLS):
    out = []
    for para in text.split("\n"):
        # Break long URLs/code rather than dropping their tails. Indentation and
        # embedded whitespace remain meaningful; avoid the terminal's column-40
        # auto-wrap followed by an extra CR/LF.
        out.extend(textwrap.wrap(para, width=width - 1, replace_whitespace=False,
                                 break_on_hyphens=False) or [""])
    return out


def calculator_blocked(mail, memory):
    return bool((memory is not None and memory.pending) or
                (mail is not None and (mail.draft or mail.composition or mail.recipient_choices)))


def prompt_recall_allowed(memory, mail, calc, school, printer, *, onboarding_active=False):
    """Narrow ordinary-chat scope; no replay in feature or confirmation editors."""
    return not (onboarding_active or calc.mode or school.mode or printer.approval or getattr(memory, "pending", None)
                or (mail is not None and (mail.items or mail.selected or mail.draft
                                          or mail.composition or mail.recipient_choices)))


def call_answer(system_prompt, history, language, options, knowledge,
                mail=None, review_ticket=None, memory=None, calc=None, welcome_start=False):
    api, web_search, timeout = options
    welcome = getattr(memory, "welcome", None)
    if welcome_start:
        return (welcome.respond(None, system_prompt, language, timeout, start=True)
                or {"display": "", "turn": None, "onboarding": True})
    question = history[-1]["content"]
    if memory is not None and memory_session.household_command(question):
        return memory.handle(question, language, timeout)
    if isinstance(getattr(memory, "user", None), user_services.user_registry.User):
        christmas = christmas_list.route(memory, question, settings=lambda: (MISTRAL_KEY, MISTRAL_MODEL), timeout=timeout)
        if christmas is not None:
            return christmas
    if memory is not None and hasattr(memory, "begin_turn"):
        memory.begin_turn()
    if mail is not None and gmail_mail.enabled() and (gmail_mail.shortcut(question) or gmail_mail.literal_command(question)
                                                     or question.strip().upper() == "BROUILLON"):
        # Explicit mail instructions cannot be intercepted by welcome, memory,
        # school or calculator routing. Keep the user's actual line for approval.
        return {**mail.handle(question, review_ticket, timeout), "gmail": True}
    blocked = calculator_blocked(mail, memory)
    if calc is not None and calc.explicit(question):
        if isinstance(getattr(memory, "user", None), user_services.user_registry.User):
            memory.capture(question, "theatre")
        return calc.handle(question, language, generate=lambda expr, lang:
                           calculator.generate_scene(expr, lang, MISTRAL_KEY, MISTRAL_MODEL))
    if (isinstance(welcome, memory_session.onboarding.Session)
            and not (mail is not None and (mail.draft or mail.composition or mail.selected))):
        response = welcome.respond(question, system_prompt, language, timeout)
        if response is not None:
            return response
    explicit_memory = memory_session.explicit_request(question)
    note_request = re.match(r"^note\b", question, re.IGNORECASE)
    school = pronote.backend() if PROVIDER == "mistral" and api == "conversations" else None
    if school is not None and isinstance(getattr(memory,'user',None), user_services.user_registry.User):
        school = user_services.ProfileSchool(school, memory.user, memory.registry)
        if not explicit_memory and not note_request and '@' not in question and school.denied(question):
            return {'display':'PRONOTE: aucun accès attribué à ce profil.', 'turn':None}
    school_enabled = school is not None and bool(school.tools())
    school_request = (school_enabled and not explicit_memory and not note_request
                      and pronote_answers.school_record_request(question, school.config().get("children", {})))
    recipient_address = (mail is not None and mail.composition
                         and not mail.composition.get("instruction_needed")
                         and re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", question.strip()))
    if (memory is not None and memory.interested(question)
            and not (mail is not None and gmail_mail.literal_command(question))
            and not school_request and not recipient_address
            and not (mail is not None and mail.draft and mail.editing and not explicit_memory)
            and (mail is None or not mail.draft or explicit_memory or note_request)):
        # An ambiguous "note this" during reply editing must not silently save instead.
        if mail is not None and mail.draft and note_request:
            return {"display": "Ajouter au brouillon ou à la mémoire ? Pour mémoriser, dites RETIENS contenu.", "turn": None}
        response = memory.handle(question, language, timeout)
        if not response.get("passthrough"):
            return response
    if mail is not None and mail.interested(history[-1]["content"]):
        response = mail.handle(history[-1]["content"], review_ticket, timeout)
        if not response.get("passthrough"):
            return {**response, "gmail": True}
    if calc is not None and not blocked:
        if (not school_request and (calculator.candidate(question) or calc.mode)
                and isinstance(getattr(memory, "user", None), user_services.user_registry.User)):
            memory.capture(question, "theatre")
        result = calc.handle(question, language, generate=lambda expr, lang:
                             calculator.generate_scene(expr, lang, MISTRAL_KEY, MISTRAL_MODEL))
        if result is not None:
            return result
    if not school_request and isinstance(getattr(memory, "user", None), user_services.user_registry.User):
        memory.capture(question)
    remembered = memory.context(question) if memory is not None and not school_request else ""
    if remembered:
        knowledge = knowledge + "\n\n" + remembered
        system_prompt += "\n\n" + remembered
    session_context = (memory.session_instructions()
                       if isinstance(getattr(memory, "user", None), user_services.user_registry.User) else "")
    if session_context:
        system_prompt += "\n\nRUNTIME SESSION:\n" + session_context
    if PROVIDER == "mistral" and api == "conversations":
        result = mistral_answers.answer(history, language, MISTRAL_KEY,
                                        MISTRAL_MODEL, web_search, timeout, knowledge,
                                        **({"pronote": school} if school_enabled else {}),
                                        **({"session_context": session_context} if session_context else {}))
        if memory is not None:
            memory.active()
        if result.get("private") and isinstance(getattr(memory, "user", None), user_services.user_registry.User):
            memory.exclude_turn()
        notice = mistral_answers.notice(result["status"], language)
        if notice:
            # Operational notices are not successful factual turns. Do not
            # display an unverified current answer as a successful lookup.
            return {"display": notice, "turn": None}
        return {"display": pronote_answers.render(result, language), "turn": {
            "role": "assistant", "content": result["text"],
            "chunks": result["chunks"], "references": result["references"],
            **({"private": True} if result.get("private") else {}),
        }}
    # Explicit rollback and other providers retain their existing adapters.
    system_prompt += "\n\n" + mistral_answers.RECALL_PRESENTATION
    plain_history = [{"role": turn["role"], "content": turn["content"]} for turn in pronote_answers.public_history(history)]
    answer = call_llm(system_prompt, plain_history)
    return {"display": answer, "turn": {"role": "assistant", "content": answer}}


def start_answer(system_prompt, history, language, options, knowledge,
                 mail=None, review_ticket=None, memory=None, calc=None, welcome_start=False):
    """One daemon worker; no UART access. File operations use the scoped memory guard."""
    output = Queue(maxsize=1)

    def request():
        try:
            optional = {"mail": mail, "review_ticket": review_ticket} if mail is not None else {}
            if memory is not None:
                optional["memory"] = memory
            if calc is not None:
                optional["calc"] = calc
            if welcome_start:
                optional["welcome_start"] = True
            output.put((call_answer(system_prompt, history, language, options, knowledge, **optional), None))
        except Exception as error:  # noqa: BLE001 - Forward every worker failure to the UART owner.
            output.put((None, error))

    worker = threading.Thread(target=request, daemon=True)
    started = time.monotonic()
    worker.start()
    return worker, output, started


def start_pronote(request, school, language, options, knowledge):
    """Reuse the single-worker wait contract without persisting school turns."""
    output = Queue(maxsize=1)
    started = time.monotonic()

    def read():
        try:
            if "tool" in request:
                result = school.execute(request["tool"], request["arguments"], started + options[2])
                output.put(({"records": result}, None))
            elif PROVIDER == "mistral" and options[0] == "conversations":
                if not MISTRAL_KEY or MISTRAL_KEY == "...":
                    output.put(({"display": "Interpreteur IA indisponible. Utilisez AIDE."}, None))
                    return
                question = "PRONOTE pour " + request["child"] + ": " + request["fallback"]
                if request.get("context", {}).get("operation"):
                    question += "\nCurrent school view: " + json.dumps(request["context"], ensure_ascii=False)
                result = pronote_answers.answer([{"role": "user", "content": question}], language,
                                               MISTRAL_KEY, MODEL, False, options[2], knowledge, pronote_mode.ScopedBackend(school, request["child"]))
                output.put(({"display": pronote_answers.render(result, language)}, None))
            else:
                output.put(({"display": "Commande inconnue. AIDE pour les commandes."}, None))
        except Exception as error:  # noqa: BLE001 - Forward every worker failure to the UART owner.
            output.put((None, error))

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    return worker, output, started


def retour_requested(kind, code, typed):
    """Recognize a physical RETOUR or a submitted exit word during a wait.

    The bounded buffer belongs to that wait/page only. Other typed requests
    cannot trigger tools or become input to a later screen.
    """
    if kind == 'fn' and code == K_RETOUR:
        return True
    if (kind == 'fn' and code == K_ENVOI) or (kind == 'char' and code in (CR, LF)):
        command = "".join(typed).strip().upper()
        typed.clear()
        return command in ("RETOUR", "SORTIR", "QUITTER")
    if (kind == 'fn' and code == K_CORR) or (kind == 'char' and code in (BS, 0x7F)):
        if typed:
            typed.pop()
    elif kind == 'char' and videotex.input_character(code) and len(typed) < 40:
        typed.append(chr(code))
    return False


def wait_answer(t, pending, timeout, allow_retour=False):
    """Reset stops local waiting; provider work/billing may continue."""
    _, output, started = pending
    deadline = started + timeout
    typed = []
    while time.monotonic() < deadline:
        try:
            result, error = output.get_nowait()
        except Empty:
            kind, code = t.read_key(min(0.1, max(0, deadline - time.monotonic())))
            if allow_retour and retour_requested(kind, code, typed):
                return None, 'retour'
            if kind == 'fn' and code in (K_SOMMAIRE, K_GUIDE):
                return None, 'guide' if code == K_GUIDE else 'sommaire'
            continue
        if error:
            # Fixed diagnostic codes only; never log provider bodies or arbitrary
            # exception messages containing questions, tokens or school records.
            reason = {
                "Sources without observed search execution": "unverified_references",
                "Unexpected web search in private request": "private_web_output",
                "Expected one final assistant entry": "ambiguous_final",
                "Empty factual answer": "empty_answer",
                "Local tool call limit": "local_tool_limit",
            }.get(str(error), "unclassified")
            status = getattr(getattr(error, "response", None), "status_code", None)
            log.warning("answer status=api_error error=%s code=%s http_status=%s",
                        type(error).__name__, reason, status if type(status) is int else "-")
            return None, 'error'
        return result, 'done'
    log.warning("answer status=deadline elapsed=%.2f", time.monotonic() - started)
    return None, 'deadline'


def safe_display(text):
    """Readable ASCII output; one ? for unsupported glyphs, no input decoding."""
    symbols = {"−": "-", "µ": "u", "μ": "u", "°": "deg", "±": "+/-",
               "≤": "<=", "≥": ">=", "≠": "!=", "×": "x", "÷": "/", "Ω": "ohm",
               "→": "->", "←": "<-", "↔": "<->", "⇒": "=>", "⇐": "<=",
               "↑": "^", "↓": "v", "£": "GBP", "½": "1/2", "¼": "1/4", "¾": "3/4"}
    output = []
    for char in unicodedata.normalize("NFC", text):
        if char not in "\n\t" and (ord(char) < 32 or ord(char) == 127):
            output.append(f"\\x{ord(char):02x}")
        else:
            mapped = char if char in videotex.GLYPHS else symbols.get(char, to_ascii(char))
            # Keep unknown Unicode visible without filling the 39-column page
            # with diagnostic Unicode code-point strings. Source data remains unchanged.
            output.append(mapped or "?")
    return "".join(output)


# ── Écrans ───────────────────────────────────────────────────────────────
def show_home(t: Term, title_lines, title_msg, username=False):
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
    if username:
        t.center("Nom / name (vide / blank = MinitelGPT)")
        t.center("Sans mot de passe / no password")
        t.center("ENVOI: continuer / continue")
    else:
        t.center(UI["language_prompt"])
        t.center(UI["language_options"])
        t.center(UI["language_submit"])
    t.line()


def choose_language(text):
    """French is the silent fallback for empty or unrecognised input."""
    return "en" if (text or "").strip().lower() in ("en", "english") else "fr"


def select_memory_user(t, language="fr", title_lines=None, title_msg="3615 IA", preserve_display=False):
    """The name chooses a memory folder, not an authenticated identity."""
    while True:
        if preserve_display:
            t.line()
            t.line("Nom / name (vide / blank = MinitelGPT)")
            preserve_display = False
        else:
            show_home(t, title_lines or [], title_msg, username=True)
        name, action = read_question(t, allow_empty=True)
        if action in ('sommaire', 'guide'):
            return None, action
        try:
            return memory_session.MemorySession(name if action == 'envoi' else ""), action
        except memory_session.memory.MemoryProblem as error:
            t.line(safe_display(str(error)))
            t.center("ENVOI: choose a simple name" if language == "en" else "ENVOI: choisissez un nom simple")


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
        raise ValueError("Startup response is not text")  # noqa: TRY004 - Keep the validator's ValueError contract.
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
                 r"\b(ENVOI|SUITE|RETOUR|SOMMAIRE|GUIDE|ANNULATION|CORRECTION|REPETITION)\b", text, re.IGNORECASE):
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
        request = f"Generate a new ALCATEL MF-83 startup in {name}. Reviewed examples:\n\n"
        request += "\n\n".join(example[language] for example in examples)
        text = generate(prompt, [{"role": "user", "content": request}],
                        timeout=BOOT_TIMEOUT, require_complete=True)
        rows = validate_boot(text)
        log.info("Generated fictional startup: %s lines", len(rows))
        return rows + ["", fallback[-1]]
    except Exception as e:  # noqa: BLE001 - Generated startup is optional; retain the static fallback.
        log.warning("Startup generation failed (%s: %s); using static sequence",
                    type(e).__name__, e)
        return fallback


def show_boot(t: Term, language, lines=None):
    """Fictional diagnostics, then SUITE or 30 seconds opens login."""
    t.pause_presence()
    t.clear()
    try:
        t.w(SCROLL_ON)
        t.w(FG_CYAN)
        rows = list(lines if lines is not None else UI[language]["boot"])
        if rows and rows[-1] == UI[language]["boot"][-1]:
            rows[-1] = BOOT_CONTINUE_PROMPT
        for line in rows:
            t.line(line)
        deadline = time.monotonic() + BOOT_CONTINUE_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return 'done'
            kind, code = t.read_key(remaining)
            if kind == 'timeout':
                return 'done'
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
        t.start_presence()


def prepare_update_text(preview, language, *, procedure=False):
    """One optional call, independent of personal history and factual answers."""
    words = mainframe_update.TEXT[language]
    fallback = (words["procedure"] if procedure else
                mainframe_update.message_rows(preview, language, to_ascii))
    if not procedure and not preview["behind"]:
        return fallback
    generate, key = ((call_claude, ANTHROPIC_KEY) if PROVIDER == "claude" else
                     (call_mistral, MISTRAL_KEY))
    phase = "procedure" if procedure else "announcement"
    if PROVIDER not in {"mistral", "claude"} or not key or key == "...":
        log.info("update_theatre phase=%s status=fallback reason=provider_unconfigured", phase)
        return list(fallback)
    started = time.monotonic()
    try:
        prompt = mainframe_update.PROMPT.read_text(encoding="utf-8")
        text = generate(prompt, [{"role": "user", "content": (
            mainframe_update.request(preview, language, procedure=True) if procedure else
            mainframe_update.component_request(preview, language))}],
            timeout=UPDATE_TIMEOUT if procedure else BOOT_TIMEOUT, require_complete=True,
            **({"max_tokens": 1800} if procedure else {}))
        rows = (mainframe_update.validate(text, validate_boot, procedure=True) if procedure else
                mainframe_update.message_rows(preview, language, to_ascii,
                    mainframe_update.component_labels(text, len(preview["commits"]), to_ascii)))
        log.info("update_theatre phase=%s status=generated lines=%s elapsed=%.2f",
                 phase, len(rows), time.monotonic() - started)
        return rows
    except Exception as error:  # noqa: BLE001 - Theatre must never block maintenance.
        reason = ("timeout" if isinstance(error, requests.Timeout) else
                  "http_error" if isinstance(error, requests.RequestException) else
                  "prompt_io" if isinstance(error, OSError) else {
                      "Incomplete Mistral startup response": "incomplete_response",
                      "Incomplete Claude startup response": "incomplete_response",
                      "Invalid maintenance length": "line_count",
                      "Invalid maintenance vocabulary": "vocabulary",
                      "Invalid maintenance text": "invalid_text",
                      "Invalid component labels": "component_labels",
                      "Startup contains Markdown": "markdown",
                      "Startup contains non-printable characters": "control_characters",
                      "Startup contains function-key instructions": "key_instructions",
                  }.get(str(error), "invalid_response"))
        log.warning("update_theatre phase=%s status=fallback reason=%s error=%s elapsed=%.2f",
                    phase, reason, type(error).__name__, time.monotonic() - started)
        return list(fallback)


def start_update_request(language, preview=None):
    """The worker only fetches/reads and writes text to its private queue, never UART or sudo."""
    output = Queue(maxsize=1)

    def request():
        try:
            if preview is None:
                checked = deployment.preview(Path(__file__).resolve().parents[1])
                words = mainframe_update.TEXT[language]
                rows = (prepare_update_text(checked, language) if checked["ok"] else
                        [words["busy"] if checked.get("busy") else words["unavailable"]])
                result = {"display": "\n".join(rows), "preview": checked}
            else:
                result = {"rows": prepare_update_text(preview, language, procedure=True)}
            output.put((result, None))
        except Exception as error:  # noqa: BLE001 - Forward failures to the UART owner.
            output.put((None, error))

    worker = threading.Thread(target=request, daemon=True)
    started = time.monotonic()
    worker.start()
    return worker, output, started


def play_update(t, rows, preview, queued_at, language):
    """Paced theatre until restart; real failure/status loss always ends the show."""
    words = mainframe_update.TEXT[language]
    deadline = time.monotonic() + 30 * 60
    last_status = time.monotonic()
    next_probe = next_line = 0
    phase = None
    index = 0
    t.clear()
    t.w(SCROLL_ON)
    try:
        t.w(FG_CYAN)
        t.line(words["leaving"])
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_probe:
                state = deployment.status()
                stamp = state.get("time")
                fresh = type(stamp) is int and stamp >= queued_at
                current = state.get("phase") if fresh else None
                if current in deployment.BUSY | {"ready", "failed"}:
                    last_status = now
                    if current == "failed":
                        return "done", words["failed"]
                    if current == "ready":
                        commit = state.get("commit")
                        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
                            return "done", words["unknown"]
                        return "done", words["current" if commit == preview["head"] else "finished"]
                    if current != phase:
                        t.line(words[{"queued": "queued", "preparing": "staging",
                                      "activating": "activating"}[current]])
                        phase = current
                if now - last_status >= 10:
                    return "done", words["unknown"]
                next_probe = now + 1
            if now >= next_line:
                t.line(rows[index] if index < len(rows) else
                       mainframe_update.continuation(language, index - len(rows)))
                index += 1
                next_line = now + 2.5
            kind, code = t.read_key(0.2)
            if kind == "fn" and code in (K_SOMMAIRE, K_GUIDE):
                return ("sommaire" if code == K_SOMMAIRE else "guide"), ""
        return "done", words["unknown"]
    finally:
        t.w(SCROLL_OFF)


def terminal_update(t, language):
    """Only a physical, explicit confirmation can reach the existing update helper."""
    words = mainframe_update.TEXT[language]
    t.line()  # ENVOI leaves the cursor after the locally echoed command.
    t.line(words["checking"])
    pending = start_update_request(language)
    t._pending = pending
    result, action = wait_answer(t, pending, 120)
    if action in ("sommaire", "guide"):
        return action, pending
    if result is None:
        return show_response(t, words["unavailable"], language), pending
    action = show_response(t, result["display"], language)
    preview = result["preview"]
    if action != "done" or not preview["ok"] or not preview["behind"]:
        return action, pending
    while True:
        t.line(words["confirm"])
        answer, action = read_question(t, allow_empty=True)
        t.line()
        if action in ("sommaire", "guide"):
            return action, pending
        answer = (answer or "").strip().upper()
        if action == "timeout" or answer in {"NON", "NO", "ANNULER", "CANCEL"}:
            t.line(words["cancelled"])
            return "done", pending
        if answer in {"OUI", "YES"}:
            if isinstance(t.view, terminal_display.View) and len(t.view.seen) < len(t.view.pages):
                t.line("SUITE: lire le dossier avant de confirmer")
                continue
            break
        t.line(words["invalid"])
    t.line(words["preparing"])
    pending = start_update_request(language, preview)
    t._pending = pending
    result, action = wait_answer(t, pending, UPDATE_TIMEOUT + 10)
    if action in ("sommaire", "guide"):
        return action, pending
    rows = result["rows"] if result else words["procedure"]
    if result is None and pending[0].is_alive():
        # A timed-out preparation remains tracked; it can never launch an update later.
        return show_response(t, words["unavailable"], language), pending
    if not deployment.unchanged(Path(__file__).resolve().parents[1], preview):
        return show_response(t, words["unavailable"], language), pending
    t.line(words["launching"])
    queued_at = int(time.time())
    try:
        mainframe_update.arm_resume(queued_at)
    except (OSError, ValueError, mainframe_update.config_store.StorageFailure,
            memory_session.memory.MemoryProblem):
        mainframe_update.clear_resume()
        return show_response(t, words["unavailable"], language), pending
    ok, _ = deployment.queue_deployment("update")
    if not ok:
        mainframe_update.clear_resume()
        return show_response(t, words["failed"], language), pending
    action, message = play_update(t, rows, preview, queued_at, language)
    if message:
        mainframe_update.clear_resume()
        action = show_response(t, message, language)
    return action, pending


def wait_connection(t):
    """Leave the physical power-on screen alone until SOMMAIRE is pressed."""
    while True:
        kind, code = t.read_key(IDLE_TIMEOUT)
        if kind == 'fn' and code == K_SOMMAIRE:
            t.start_presence()
            return


def show_question_prompt(t: Term, question_msg):
    if t.view is None:
        terminal_display.View(t, question_msg).install()
    t.view.status("SOMMAIRE: reset / LOGOUT: connexion")


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
    t.center("Use your administration password" if language == "en" else
             "Utilisez votre mot de passe admin")
    t.w(bytes([CR, LF, CR, LF]))
    t.center("UPDATE / MAJ: mainframe maintenance" if language == "en" else
             "UPDATE / MAJ: maintenance du mainframe")
    t.line()
    t.w(FG_CYAN); t.center(UI[language]["guide_return"])
    t.read_key(120)


def read_memory_password(t):
    """No echo/record/model hooks. Drain oversized input through ENVOI."""
    buffer, overflow = [], False
    deadline = time.monotonic() + IDLE_TIMEOUT
    while time.monotonic() < deadline:
        kind, code = t.read_key(max(0, deadline - time.monotonic()))
        if kind == "timeout":
            return None, "timeout"
        if kind == "fn" and code in (K_SOMMAIRE, K_RETOUR, K_ANNUL, K_GUIDE):
            return None, "sommaire" if code == K_SOMMAIRE else "done"
        if (kind == "fn" and code == K_ENVOI) or kind == "char" and code in (CR, LF):
            return (None, "overflow") if overflow else ("".join(buffer), "submit")
        if kind == "fn" and code == K_CORR or kind == "char" and code in (BS, 0x7F):
            if buffer:
                buffer.pop()
        elif kind == "char" and videotex.input_character(code):
            if len(buffer) < 2000 and not overflow:
                buffer.append(chr(code))
            else:
                buffer.clear()
                overflow = True
    return None, "timeout"


def terminal_memory_unlock(t, selected):
    """Admin password is entered only after confirmed hardware echo suppression."""
    try:
        ticket = selected.begin_household_unlock()
    except memory_session.memory.MemoryProblem as error:
        return "done", str(error)
    expected = os.getenv("ADMIN_PASSWORD", "")
    if not expected.strip():
        selected.lock_household()
        return "done", "Configurez ADMIN_PASSWORD dans l'administration avant d'activer la mémoire."
    attempt = None
    try:
        with rose_terminal.hidden_echo(t):
            try:
                t.clear()
                t.line("MEMOIRE DU FOYER — ACCES ADMIN")
                t.line("Mot de passe admin (saisie masquee) :")
                t.line("RETOUR : annuler / ENVOI : valider")
                attempt, action = read_memory_password(t)
                accepted = action == "submit" and selected.unlock_household(attempt, expected, ticket)
            finally:
                attempt = None
                t.s.reset_input_buffer()
                t.clear()
    except rose_terminal.EchoUnavailable:
        selected.lock_household()
        return "done", "Masquage du clavier indisponible; mémoire verrouillée."
    if not accepted:
        selected.lock_household()
        return (action if action in {"sommaire", "timeout"} else "done"), "Mémoire verrouillée. Accès annulé ou refusé."
    return "done", ("Mémoire du foyer activée pour cette session.\n"
                    "RETIENS / SOUVIENS-TOI / OUBLIE. Faits et préférences lisibles par tous.\n"
                    "DESACTIVER MEMOIRE ou SOMMAIRE pour verrouiller. Apprentissage automatique désactivé.")


def read_question(t: Term, allow_empty=False, calculator_mode=False, navigation_mode=False, *, recall=False):
    if getattr(t, "view", None) is not None:
        try:
            return t.view.read(allow_empty=allow_empty, calculator_mode=calculator_mode,
                               navigation_mode=navigation_mode, recall=recall)
        except rose_terminal.EchoUnavailable:
            # Older/unknown firmware: preserve public conversation with the
            # original local-echo editor. Private readers still fail closed.
            page = list(t.view.pages[t.view.page])
            t.clear()
            for line in page:
                t.line(line)
    return read_question_legacy(t, allow_empty, calculator_mode, navigation_mode)


def read_question_legacy(t: Term, allow_empty=False, calculator_mode=False, navigation_mode=False):
    """Lit une question. Retourne (texte, 'envoi') / (None,'sommaire') / (None,'timeout')."""
    t.w(FG_GREEN)
    t.w("> ")
    buf = []
    overflow = False
    # Le Minitel fait l'écho local des frappes : on ne ré-écho PAS côté Pi.
    while True:
        kind, code = t.read_key(IDLE_TIMEOUT)
        if kind == 'timeout':
            return None, 'timeout'
        submitted = kind == 'fn' and code == K_ENVOI or kind == 'char' and code in (CR, LF)
        if overflow and submitted:
            t.line("Saisie trop longue (2000 max). Recommencez.")
            t.w("> ")
            buf.clear()
            overflow = False
            continue
        if kind == 'fn':
            if code == K_ANNUL:
                t.w(bytes([BS, 0x20, BS]) * len(buf))
                buf.clear()
                overflow = False
                continue
            if navigation_mode and code in (K_SUITE, K_RETOUR):
                return 'SUIVANT' if code == K_SUITE else 'PRECEDENT', 'envoi'
            if calculator_mode and code == K_RETOUR:
                return 'RETOUR', 'envoi'
            if code == K_SOMMAIRE:
                return None, 'sommaire'
            if code == K_GUIDE:
                return None, 'guide'
            if code == K_ENVOI and (buf or allow_empty):
                return "".join(buf), 'envoi'
            if code in (K_CORR, K_RETOUR) and buf:
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
        elif videotex.input_character(c):
            if len(buf) < io_limits.MAX_QUESTION_CHARS and not overflow:
                buf.append(chr(c))
            else:
                overflow = True


def show_response(t: Term, text: str, language="fr", allow_retour=False):
    """Install the complete result without blocking input on intermediate pages.

    read_question owns navigation/editing next. PRONOTE and CALC keep their
    RETOUR behavior through its explicit mode flags, not through paging.
    """
    terminal_display.View(t, text, language).install()
    return 'done'


def show_calculator_scene(t, steps, language):
    """Only the UART owner plays frames. Poll every beat; always restore page mode."""
    deadline = time.monotonic() + 90
    fast = False
    t.clear()
    t.w(SCROLL_ON)
    t.w(FG_WHITE)
    try:
        for row in calculator.frames([safe_display(step) for step in steps]):
            # Keyboard-aware delay; SUITE removes added pauses, RETOUR cancels.
            pause = time.monotonic() + (0.02 if fast else 0.22)
            while time.monotonic() < pause:
                kind, code = t.read_key(min(0.05, max(0, pause - time.monotonic())))
                if kind == 'fn':
                    if code in (K_SOMMAIRE, K_GUIDE, K_RETOUR):
                        flush = getattr(t.s, 'reset_output_buffer', None)
                        if callable(flush):
                            flush()
                        return {K_SOMMAIRE: 'sommaire', K_GUIDE: 'guide', K_RETOUR: 'retour'}[code]
                    if code == K_SUITE:
                        fast = True
                        break
            if time.monotonic() >= deadline:
                t.line(safe_display(calculator.content(language)["cancelled"]))
                return 'retour'
            t.line(row)
        return 'done'
    finally:
        t.w(SCROLL_OFF)


def execute_print(t, document, language):
    """Terminal owns display; BLE transport runs on worker."""
    if document is None:
        return "AUCUN DOCUMENT"
    t.clear()
    t.w(FG_CYAN)
    t.line("MF-83 / REPROGRAPHIE")
    t.line("")
    try:
        printing_session.print_with_progress(
            document, lambda line: t.line(safe_display(line)),
            track=lambda pending: setattr(t, "_pending", pending),
        )
    except (terminal_link.Unavailable, terminal_link.WorkerExpired):
        raise
    except Exception as exc:  # noqa: BLE001 - Optional printer failures must not end the terminal session.
        log.warning("Printing failed: %s", exc)
        return ("ECHEC REPROGRAPHIE: " if language == "fr" else "PRINT FAILURE: ") + str(exc)
    return "DOCUMENT MATERIALISE" if language == "fr" else "DOCUMENT PRINTED"


# ── Boucle principale ────────────────────────────────────────────────────
def run(term=None):
    t = term if term is not None else Term()
    t._session_resources = ()
    t._pending = None
    try:
        return run_session(t)
    finally:
        t.prompt_history.clear()
        # Invalidate review tickets and close memory guards even on a UART error.
        for cleanup in t._session_resources:
            owner = getattr(cleanup, "__self__", None)
            if isinstance(getattr(owner, "user", None), user_services.user_registry.User):
                cleanup(reason="disconnect")
            else:
                cleanup()
        if term is None:
            t.close()


def run_session(t):
    destination = getattr(t.s, "port", "console")
    log.info(f"Démarré sur {destination} (fournisseur {PROVIDER}, modèle {MODEL})")
    options = (mistral_answers.settings(os.environ) if PROVIDER == "mistral"
               else ("chat_completions", False, 60))
    pending = None
    mail = None
    memory = None
    printer = printing_session.Session()
    keep_bill = False
    username = None
    language = None

    if not mainframe_update.take_resume():
        wait_connection(t)
    else:
        t.start_presence()  # Deployment resume also restores the configured keyboard.
    t.clear()
    t.center(UI["fr"]["boot_wait"])
    update_available = deployment.available(Path(__file__).resolve().parents[1])
    boot_lines = prepare_boot("fr")
    if update_available:
        boot_lines = list(boot_lines)
        # Keep the continuation prompt last and the countdown unchanged.
        boot_lines[-1:-1] = ["MISES A JOUR DISPONIBLES - MAJ", ""]
    result = show_boot(t, "fr", boot_lines)
    if result == 'guide':
        show_guide(t, "fr")

    while True:  # boucle sommaire
        t.prompt_history.clear()
        if t.view is not None:
            t.clear()  # Retire loaded output/input at every session boundary.
        # Recharger le preset à chaque retour au sommaire (prise en compte des édits)
        system_prompt, title_msg, question_msg, loading_msg, title_art = load_preset()
        title_lines = build_title(title_art)
        if mail is not None:
            mail.reset()
        if memory is not None:
            if isinstance(getattr(memory, "welcome", None), memory_session.onboarding.Session):
                memory.welcome.pause()
            memory.close()
        memory = None
        mail = None
        review_ticket = None
        printer.reset()
        history = []
        knowledge = factual_knowledge()
        if username is None:
            memory, user_action = select_memory_user(t, title_lines=title_lines, title_msg=title_msg)
        else:
            # Retire the old guard to block late worker writes, retain identity only.
            try:
                memory = memory_session.MemorySession(username)
            except memory_session.memory.MemoryProblem:
                username = language = None
                continue
            user_action = 'envoi'
        t._session_resources = (memory.close,) if memory is not None else ()
        if user_action == 'guide':
            show_guide(t)
        if user_action in ('guide', 'sommaire'):
            continue
        if user_action != 'envoi':
            continue
        username = "" if memory.key == "shared" else memory.username
        mail = gmail_mail.MailSession(username=username, memory=memory) if gmail_mail.enabled() else None
        t._session_resources = (mail.reset, memory.close) if mail is not None else (memory.close,)
        language = language or ("fr" if memory.key == "shared" else memory.saved_language())
        action = user_action
        if language not in ("fr", "en"):
            show_home(t, title_lines, title_msg)
            choice, action = read_question(t, allow_empty=True)
            if action == 'guide':
                show_guide(t)
                continue
            if action == 'sommaire':
                continue
            language = choose_language(choice)
            if action == 'envoi':
                memory.remember_language(language)
        system_prompt = session_prompt(system_prompt, language)
        calc = calculator.Session()
        school_mode = pronote_mode.Session(user_services.ProfileSchool(pronote.backend(), memory.user, memory.registry))
        if language == "en":
            question_msg = UI[language]["question"]
            loading_msg = UI[language]["loading"]
        greeted = False
        if (isinstance(getattr(memory, "welcome", None), memory_session.onboarding.Session)
                and memory.welcome.should_greet() and not (pending is not None and pending[0].is_alive())):
            t.waiting(loading_msg)
            pending = start_answer(system_prompt, [], language, options, knowledge,
                                   memory=memory, welcome_start=True)
            t._pending = pending
            introduction, action = wait_answer(t, pending, options[2])
            if action in ("sommaire", "guide"):
                if action == "guide":
                    show_guide(t, language)
                continue
            if introduction and introduction["display"]:
                memory.record("assistant", introduction["display"], "onboarding")
                action = show_response(t, safe_display(introduction["display"]), language)
                if action != "done":
                    continue
                greeted = True
        if keep_bill or greeted:
            t.line()
            t.center(UI[language]["follow_up"])
            keep_bill = False
        else:
            show_question_prompt(t, question_msg)

        queued_question = None
        onboarding_active = greeted
        while True:  # boucle conversation
            recall_eligible = False
            if queued_question is not None:
                question, action = queued_question, 'envoi'
                queued_question = None
            else:
                recall_eligible = prompt_recall_allowed(memory, mail, calc, school_mode, printer,
                                                        onboarding_active=onboarding_active)
                question, action = read_question(t, calculator_mode=calc.mode or school_mode.mode,
                                                 navigation_mode=school_mode.grade_view, recall=recall_eligible)
            if action == 'envoi' and (question or '').strip().upper() == 'SOMMAIRE':
                action = 'sommaire'
            if action == 'guide':
                show_guide(t, language)
                break          # retour au sommaire après l'écran d'aide
            if action in ('sommaire', 'timeout'):
                break

            if (question or "").strip().upper() == "LOGOUT":
                action = 'logout'
                username = language = None
                break

            try:
                memory.active()
            except memory_session.memory.MemoryProblem:
                t.line('Profil modifié. Reconnectez-vous.')
                username = language = None
                break

            # Deterministic local tool: use the ordinary answer display path,
            # but never send the fixture to a model, memory, history or printer.
            if recall_eligible and terminal_diagnostics.unicode_command(question):
                diagnostic = terminal_diagnostics.test_unicode()
                show_response(t, diagnostic["display"], language)
                t.view.status("TEST UNICODE - local, sans API")
                continue

            if recall_eligible and terminal_diagnostics.scroll_command(question):
                diagnostic = terminal_diagnostics.test_scroll()
                result = terminal_scrolling.show(t, diagnostic["display"], language)
                if result == "guide":
                    show_guide(t, language)
                if result != "done":
                    action = result
                    break
                continue

            stream_test = recall_eligible and terminal_diagnostics.stream_command(question)
            stream_request = llm_streaming.command(question) if recall_eligible else None
            if stream_test or stream_request is not None:
                if pending is not None and pending[0].is_alive():
                    show_response(t, "Requête précédente en cours. Réessayez bientôt.", language)
                    continue
                if stream_test:
                    source = stream_sources.ProcessSource(
                        [sys.executable, "-u", "-m", "services.terminal_diagnostics", "--stream-demo"],
                        cwd=Path(__file__).resolve().parents[1])
                elif not stream_request["prompt"]:
                    show_response(t, "STREAM votre question\nSTREAM WEB votre recherche\nTEST STREAM : moniteur local", language)
                    continue
                elif PROVIDER != "mistral" or not MISTRAL_KEY or MISTRAL_KEY == "...":
                    show_response(t, "Flux API indisponible : Mistral doit être configuré.", language)
                    continue
                elif stream_request["web"] and (options[0] != "conversations" or not options[1]):
                    show_response(t, "Recherche web en flux désactivée dans la configuration.", language)
                    continue
                else:
                    # Isolated public prompt: no personal history, recall, local
                    # tools, memory writes or retry of a submitted paid request.
                    source = llm_streaming.mistral(
                        [{"role": "user", "content": stream_request["prompt"]}], language,
                        MISTRAL_KEY, MISTRAL_MODEL, api=options[0], web_search=stream_request["web"])
                result = stream_sources.show(t, source, language)
                if result == "guide":
                    show_guide(t, language)
                if result != "done":
                    action = result
                    break
                continue

            memory_control = memory_session.household_command(question)
            if memory_control:
                if memory_control == "lock":
                    memory.lock_household()
                    action, message = "done", "Mémoire du foyer verrouillée."
                elif pending is not None and pending[0].is_alive():
                    action, message = "done", "Requête précédente en cours. Réessayez bientôt."
                else:
                    action, message = terminal_memory_unlock(t, memory)
                if action in ("sommaire", "timeout"):
                    break
                action = show_response(t, safe_display(message), language)
                if action != "done":
                    break
                t.line(UI[language]["follow_up"])
                continue

            # Intercept before any ordinary model, history, transcript or tool.
            if journal.entry(question):
                if pending is not None and pending[0].is_alive():
                    t.line("Requête précédente en cours. Réessayez bientôt.")
                    continue
                action, journal_pending = journal_terminal.run(t, memory.user, memory.registry)
                if journal_pending is not None:
                    pending = journal_pending
                if action == "logout":
                    username = language = None
                    break
                if action == "sommaire":
                    break
                if action.startswith("error:"):
                    show_response(t, safe_display(action.removeprefix("error:")), language)
                    t.line(UI[language]["follow_up"])
                    continue
                show_question_prompt(t, question_msg)
                continue

            # Local maintenance commands: never send them to an LLM.
            maintenance = (question or "").strip().lower()
            if maintenance in ("mainframe --shutdown", "mainframe --reboot"):
                operation = "poweroff" if maintenance.endswith("--shutdown") else "reboot"
                t.line("MAINFRAME: " + operation.upper())
                t.line("Confirmer: taper OUI puis ENVOI")
                confirmation, confirm_action = read_question(t)
                if confirm_action == "envoi" and (confirmation or "").strip().upper() == "OUI":
                    t.line("Arret du systeme..." if operation == "poweroff"
                           else "Redemarrage du systeme...")
                    t.line("Veuillez patienter.")
                    try:
                        subprocess.Popen(["sudo", "-n", "/usr/bin/systemctl", operation],
                                         stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL,
                                         start_new_session=True)
                    except OSError:
                        log.exception("Maintenance command failed to start")
                        t.line("Commande indisponible")
                    return
                t.line("Commande annulee.")
                show_question_prompt(t, question_msg)
                continue

            if mainframe_update.command(question):
                if pending is not None and pending[0].is_alive():
                    t.line("Requete precedente en cours. Reessayez bientot.")
                    continue
                action, pending = terminal_update(t, language)
                if action in ("sommaire", "guide", "timeout"):
                    if action == "guide":
                        show_guide(t, language)
                    break
                # Preserve the maintenance outcome while taking fresh input.
                t.line()
                t.center(UI[language]["follow_up"])
                continue

            rose_test = rose.test_entry(question)
            rose_alias = "" if rose_test else rose.entry(question)
            if (rose_alias is not None and not calc.mode and not school_mode.mode
                    and not calculator_blocked(mail, memory)):
                if pending is not None and pending[0].is_alive():
                    rose_terminal.display(t, "Requête précédente en cours. Réessayez bientôt.")
                    continue
                result, rose_pending = rose_terminal.run(
                    t, rose_alias, rose.MistralModel(MISTRAL_KEY, MISTRAL_MODEL,
                                                   min(options[2], 30)),
                    timeout=min(options[2], 30), test=rose_test)
                if rose_pending is not None:
                    pending = rose_pending
                if result == "sommaire":
                    keep_bill = not rose_test
                    break
                # Preserve the final bill and the unchanged main language.
                t.line()
                t.center(UI[language]["follow_up"])
                continue

            direct = school_mode.prepare(question)
            if direct is not None:
                if "display" in direct:
                    answer = direct["display"]
                    if direct.get("print"):
                        if printing_session.installed():
                            document = printing_session.PrintDocument("3615 IA / PRONOTE", (printing_session.TextBlock(answer),))
                            answer = execute_print(t, document, language)
                        else:
                            answer = "SERVICE REPROGRAPHIE NON INSTALLE"
                elif pending is not None and pending[0].is_alive():
                    answer = "Requete precedente en cours. Reessayez bientot."
                else:
                    t.waiting("CONSULTATION PRONOTE")
                    pending = start_pronote(direct, school_mode.backend, language, options, knowledge)
                    t._pending = pending
                    response, action = wait_answer(t, pending, options[2], allow_retour=True)
                    if action in ("sommaire", "guide"):
                        if action == "guide":
                            show_guide(t, language)
                        break
                    if action == "retour":
                        school_mode.reset()
                        answer = "Retour a la conversation."
                    elif response and "records" in response:
                        answer = school_mode.receive(response["records"], direct["operation"])
                    elif response:
                        answer = response["display"]
                    else:
                        answer = "PRONOTE: " + ("timeout" if action == "deadline" else "unavailable")
                try:
                    memory.active()
                except memory_session.memory.MemoryProblem:
                    username = language = None
                    break
                answer = safe_display(answer)
                if school_mode.grade_view and not direct.get("print"):
                    # One test per physical page, even with unusually long
                    # names/comments. Full fields remain available with LIRE.
                    lines = wrap(answer)
                    if len(lines) > CONTENT_ROWS:
                        answer = "\n".join(lines[:CONTENT_ROWS - 2] + [
                            f"... LIRE {school_mode.grade_index + 1}: detail",
                            "SUITE:suiv / RETOUR:prec / QUITTER"])
                result = show_response(t, answer, language, allow_retour=True)
                if result in ("sommaire", "timeout", "guide"):
                    if result == "guide":
                        show_guide(t, language)
                    break
                if result == "retour":
                    school_mode.reset()
                if not school_mode.mode:
                    # An explicit exit ends school follow-ups as well as the
                    # local parser state. Keep unrelated public conversation.
                    history = pronote_answers.public_history(history)
                # Keep the result/error on screen while accepting the next
                # command, like ordinary conversation. The startup prompt
                # helper clears the screen and would erase this last page.
                t.w(bytes([CR, LF]))
                t.w(FG_WHITE)
                t.center("Matiere / LIRE n / IMPRIMER" if school_mode.grade_view else
                         "PRONOTE / AIDE / RETOUR" if school_mode.mode else UI[language]["follow_up"])
                t.w(bytes([CR, LF]))
                continue

            # A selected email is a document in its own right, not an LLM turn.
            # Always print its full body, never the preview or summary.
            if question.strip().upper() == "IMPRIMER" and mail is not None and mail.selected:
                if printing_session.installed():
                    document = printing_session.PrintDocument(
                        "3615 IA / COURRIER",
                        (printing_session.TextBlock(mail.selected_text()),),
                    )
                    message = execute_print(t, document, language)
                else:
                    message = "SERVICE REPROGRAPHIE NON INSTALLE"
                result = show_response(t, message, language)
                if result != "done":
                    break
                show_question_prompt(t, question_msg)
                continue

            # Only trusted keyboard input may trigger physical printing.
            print_intent = printing_session.intent(question)
            if printer.approval is not None:
                if print_intent in ("non", "annuler"):
                    printer.cancel()
                    message = "REQUISITION ANNULEE"
                elif print_intent == "oui":
                    document = printer.take_confirmed(question)
                    message = execute_print(t, document, language)
                else:
                    printer.cancel()
                    message = "REQUISITION ANNULEE"
                    print_intent = None
                if print_intent is not None:
                    result = show_response(t, message, language)
                    if result != "done":
                        break
                    show_question_prompt(t, question_msg)
                    continue
            if print_intent in ("direct", "confirm", "image"):
                if not printing_session.installed():
                    message = "SERVICE REPROGRAPHIE NON INSTALLE"
                elif print_intent == "image":
                    message = "IMPRESSION D'IMAGE: GENERATION NON CONFIGUREE"
                elif not printer.last_text:
                    message = "AUCUNE REPONSE A IMPRIMER"
                elif print_intent == "direct":
                    document = printer.prepare().document
                    printer.cancel()
                    message = execute_print(t, document, language)
                else:
                    printer.prepare()
                    message = "IMPRIMER LA DERNIERE REPONSE ? OUI / NON"
                result = show_response(t, message, language)
                if result != "done":
                    break
                show_question_prompt(t, question_msg)
                continue

            try:
                private_question = pronote_answers.school_question([{"role": "user", "content": question}],
                                                                   pronote.backend().config().get("children", {}))
                private_question = private_question and pronote.backend().config().get("enabled", False)
            except pronote.PronoteError:
                # A broken school configuration must not feed private input to learning.
                private_question = True
            calculator_request = calc.explicit(question) or (
                calculator.candidate(question) and not calculator_blocked(mail, memory)
                and not (isinstance(getattr(memory, "welcome", None), memory_session.onboarding.Session)
                         and memory.welcome.should_greet()))
            t.waiting(calculator.content(language)["loading"] if calculator_request else loading_msg)
            # Do not pile up paid calls while a locally abandoned HTTP request
            # is still running. Its isolated queue is discarded when it ends.
            response = None
            if pending is not None and pending[0].is_alive():
                answer = ("Previous request still finishing. Try again shortly." if language == "en"
                          else "Requete precedente en cours. Reessayez bientot.")
            else:
                candidate = history + [{"role": "user", "content": question}]
                optional = {"mail": mail, "review_ticket": review_ticket} if mail is not None else {}
                optional["memory"] = memory
                optional["calc"] = calc
                pending = start_answer(system_prompt, candidate, language, options, knowledge, **optional)
                t._pending = pending
                response, action = wait_answer(t, pending, options[2], allow_retour=calculator_request)
                if action == 'guide':
                    show_guide(t, language)
                    break
                if action == 'sommaire':
                    break
                answer = UI[language]["connection_error"]
                if action == 'deadline':
                    answer = ("Request timed out. Information unverified." if language == "en"
                              else "Delai depasse. Information non verifiee.")
                elif action == 'retour':
                    calc.mode = False
                    answer = calculator.content(language)["cancelled"]
                elif response is not None:
                    answer = response["display"]
                    if response["turn"] is not None:
                        history = mistral_answers.bound_history(candidate + [response["turn"]])

            if response is not None and response.get("turn") is not None:
                printer.remember(answer)

            if response is not None:
                onboarding_active = bool(response.get("onboarding"))
            warning = memory.take_warning()
            if warning:
                answer += "\n\n" + warning
            if response and response.get("onboarding"):
                private_question = False  # Voluntary profile answers, not PRONOTE records.
            channel = ("onboarding" if response and response.get("onboarding") else
                       "theatre" if response and response.get("calculator") else
                       "memory" if response and response.get("memory") else
                       "gmail" if response and (response.get("gmail") or response.get("review_ticket")) else "chat")
            if not private_question and not (response and (response.get("turn") or {}).get("private")):
                memory.record("user", question, channel)
            if response and response.get("calculator"):
                memory.record("assistant", answer, channel, factual=response.get("factual"))
            elif not (response and (response.get("turn") or {}).get("private")):
                memory.record("assistant", answer, channel, references=(response.get("turn") or {}).get("references", ()) if response else ())
            # Only completed ordinary chat turns enter recall. Feature inputs,
            # secrets, private records and queued document commands never do.
            if (recall_eligible and channel == "chat" and not private_question
                    and response and response.get("turn") and not response.get("christmas")
                    and not response["turn"].get("private")):
                t.prompt_history.add(question)
            mail_document = channel == "gmail" and mail is not None and bool(
                mail.items or mail.selected or mail.draft or mail.composition or mail.recipient_choices)
            if mail_document:
                ticket = response.get("review_ticket") if response else None
                if ticket:
                    review_ticket = ticket
                queued_question, result = terminal_documents.read(
                    t, answer, language, title="3615 IA / COURRIER",
                    reviewed=(lambda mail=mail, ticket=ticket: mail.acknowledge(ticket)) if ticket else None,
                )
            elif response and (response.get("memory") or response.get("christmas")):
                # Memory inventories and Christmas lists accept commands on every page, including
                # the last. Reuse mail's reader instead of trapping input in SUITE-only paging.
                queued_question, result = terminal_documents.read(
                    t, answer, language, title="3615 IA / NOEL" if response.get("christmas") else "3615 IA / MEMOIRE")
            else:
                result = (show_calculator_scene(t, response["scene"], language)
                          if response and response.get("scene") else
                          terminal_scrolling.show(t, answer, language) if response and response.get("scrolling") else
                          show_response(t, safe_display(answer), language))
            if response and response.get('scene') and result == 'done':
                show_response(t, safe_display(answer), language)
            if response and response.get('scene') and result != 'done':
                memory.record('event', 'Scene CALC interrompue: ' + result, 'session')
            if result == 'guide':
                show_guide(t, language)
                break
            if result == 'retour':
                calc.mode = False
                show_response(t, safe_display(calculator.content(language)["cancelled"]), language)
            if result in ('sommaire', 'timeout'):
                break

            if queued_question is not None:
                continue

            if mail is not None and response and response.get("review_ticket"):
                review_ticket = response["review_ticket"]
                mail.acknowledge(review_ticket)

            # Invite pour rebondir
            t.w(bytes([CR, LF]))
            t.w(FG_WHITE)
            t.center(calculator.content(language)["prompt"] if calc.mode else UI[language]["follow_up"])
            t.w(bytes([CR, LF]))

        if mail is not None:
            mail.reset()
        if memory is not None:
            if isinstance(getattr(memory, "welcome", None), memory_session.onboarding.Session):
                memory.welcome.pause()
            if isinstance(getattr(memory, "user", None), user_services.user_registry.User):
                memory.close(reason=action if action in {'sommaire', 'timeout', 'logout', 'guide'} else 'closed')
            else:
                memory.close()

def main():
    def stop(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        terminal_link.serve(Term, run)
    except KeyboardInterrupt:
        pass  # SIGTERM/Ctrl-C already ran session and terminal cleanup.
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
