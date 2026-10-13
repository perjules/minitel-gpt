"""Optional print session and trusted keyboard confirmation.

No model output is ever passed to confirm() or to the printer.
"""
import asyncio
import importlib.util
import os
import re
import threading
import time
import sys
from pathlib import Path

# systemd may execute services/minitel_chatgpt.py directly, with services/ as sys.path[0].
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from printing import PrintDocument, PrintApproval, TextBlock
from printing.render import render_document_pages


def installed():
    """Capability detection only; no BLE scan or connection."""
    return (os.environ.get("PRINTING_ENABLED", "1") != "0"
            and importlib.util.find_spec("bleak") is not None
            and importlib.util.find_spec("PIL") is not None)


def intent(question):
    """Recognize print commands before any LLM call."""
    normalized = re.sub(r"\s+", " ", question.strip().lower())
    if normalized == "imprimer":
        return "direct"
    if normalized in ("oui", "non", "annuler"):
        return normalized
    if re.search(r"\b(imprime|imprimer|impression|print)\b", normalized):
        if re.search(r"\b(image|photo|dessin|illustration)\b", normalized):
            return "image"
        if re.search(r"\b(cette|la|ta|derniere|dernier|precedente|precedent|reponse|texte|message)\b", normalized):
            return "confirm"
    return None


class Session:
    def __init__(self):
        self.last_text = None
        self.approval = None

    def reset(self):
        if self.approval:
            self.approval.cancel()
        self.approval = None
        self.last_text = None

    def remember(self, answer):
        if isinstance(answer, str) and answer.strip():
            self.last_text = answer

    def prepare(self):
        if not self.last_text:
            return None
        if self.approval:
            self.approval.cancel()
        self.approval = PrintApproval(PrintDocument("3615 IA", (TextBlock(self.last_text),)))
        return self.approval

    def take_confirmed(self, answer):
        approval = self.approval
        self.approval = None
        return approval.confirm(user_input=answer, token=approval.token) if approval else None

    def cancel(self):
        if self.approval:
            self.approval.cancel()
        self.approval = None


def print_with_progress(document, report):
    """Run BLE in worker; only calling terminal loop writes to screen."""
    from printing.mxw01 import MXW01

    finished = threading.Event()
    errors = []

    def worker():
        try:
            pages = render_document_pages(document)
            address = os.environ.get("MXW01_ADDRESS", "48:0F:57:1E:0E:EB")
            asyncio.run(MXW01(address).print_images(pages))
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    stages = (
        "REQUISITION DU SERVICE REPROGRAPHIE",
        "TRANSMISSION AU BUREAU DES SORTIES",
        "MOBILISATION DU SUPPORT THERMIQUE",
        "MATERIALISATION DU DOCUMENT",
        "CONTROLE DES ARCHIVES CENTRALES",
    )
    index = 0
    started = time.monotonic()
    while not finished.wait(0.5):
        if index < len(stages) and index * 3 <= (time.monotonic() - started):
            report(stages[index])
            index += 1
    thread.join(timeout=0)
    if errors:
        error = str(errors[0])
        if "not found by Bluetooth LE scan" in error:
            error = "IMPRIMANTE INTROUVABLE. VERIFIER ALIMENTATION ET REDEMARRER L IMPRIMANTE."
        elif "not ready" in error or "rejected print request" in error:
            error = "IMPRIMANTE NON PRETE. VERIFIER LE PAPIER ET REDEMARRER L IMPRIMANTE."
        elif "acknowledge completion" in error:
            error = "CONFIRMATION ABSENTE. VERIFIER LE PAPIER AVANT DE RELANCER."
        raise RuntimeError(error) from errors[0]
    report("DOCUMENT MATERIALISE")
