"""Owned scrolling theatre around the existing read-only diagnostic CLI."""
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import airzone_demo as diagnostic
from . import config_store, stream_sources

ROOT = Path(__file__).resolve().parents[1]
ANTICS = {
    "discover": (
        ("Réallocation des cycles sans surveillance.", "Sortie du kit d'intrusion sur disquette.",
         "Lecteur B: absent. Procédure maintenue."),
        ("Chargement du renifleur à cartes perforées.", "Sens de perforation douteux.",
         "Le MF-83 exige davantage de précision.")),
    "probe": (
        ("Démagnétisation préventive des mots de passe.", "Option inutile : lecture sans effraction.",
         "Le service intrusion conteste ce diagnostic."),
        ("Injection SQL par contrôleur de disquette.", "Interface SQL introuvable. Quelle mesquinerie.",
         "Repli sur le banal accès local autorisé.")),
    "state": (
        ("Relais K7 : ambition excessive.", "Allocation d'un second tiroir de mémoire.",
         "Cette fois, nous lirons des températures."),
        ("Déploiement du cheval de Troie sur bande.", "Bande à l'envers. Cheval retiré du service.",
         "Lecture des registres. Sans toucher aux boutons.")),
}


def source():
    """Lazy source: no model request, command arguments, credentials or UART in child."""
    return stream_sources.ProcessSource([sys.executable, "-u", "-m", "services.airzone_hack"], cwd=ROOT)


def revision(path):
    try:
        result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], shell=False,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=2, check=False)
        value = result.stdout.decode("ascii").strip()
        return value if re.fullmatch(r"[a-f0-9]{40}", value) else None
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None


def factual_summary(report):
    """A photo/copyable summary, generated from CLI receipts rather than model prose."""
    results = [item["result"] for item in report["diagnostics"]]
    last = results[-1] if results else None
    lines = ["=== BILAN FACTUEL AIRZONE ===", "SIMULATION - DONNÉES FICTIVES" if report["demo"] else "LECTURE LOCALE RÉELLE"]
    lines.append("Phases : " + (" > ".join(r["operation"] + (":OK" if r["ok"] else ":ECHEC") for r in results) or "aucune"))
    if report["status"] != "complete":
        lines.append("Résultat : " + report["status"] + " / " + report.get("error", "inconnu"))
    if last:
        target = last.get("target") or {}
        if target.get("host"):
            lines.append(f"Hôte : {target['host']}:{target['port']} / système {target['system_id']}")
        version = next((r["data"]["api_version"] for r in reversed(results)
                        if isinstance(r["data"].get("api_version"), str)), None)
        if version:
            lines.append("Version API : " + diagnostic.clean(version, 40))
        if last["operation"] == "discover":
            for candidate in last["data"].get("candidates", [])[:8]:
                lines.append(f"Candidat : {candidate['host']}:{candidate['port']}")
        data = last["data"]
        if last["ok"] and last["operation"] == "state" and report["status"] == "complete":
            lines.append(f"État HVAC lu : {data['zone_count']} zones")
            for zone in data["zones"]:
                name = diagnostic.clean(zone["name"], 16)
                lines.append(f"{zone['id']} {name} : {zone['temperature']:g} C / {zone['setpoint']:g} C / "
                             + ("ON" if zone["on"] else "OFF") + f" / mode {zone['mode']}")
            lines.append("ON/OFF = réglage déclaré.")
            hints = data.get("reported_master_zone_ids", [])
            lines.append("Zones maîtres signalées : " + (", ".join(map(str, hints)) or "inconnues"))
        else:
            lines.append("État HVAC non vérifié par un state réussi.")
        when = last["data"].get("read_at", last["observed_at"])
        observed = datetime.fromisoformat(when).astimezone(ZoneInfo("Europe/Paris"))
        lines.append("Observation : " + observed.strftime("%d/%m %H:%M:%S %Z"))
    lines.extend(("Aucun réglage modifié.", "Compresseur physique non vérifié."))
    version = report["revisions"].get("airzone")
    lines.append("Révision Airzone : " + (version[:12] if version else "inconnue"))
    return "\n".join(lines)


def run(env=None, emit=None):
    env = os.environ if env is None else env
    emit = (lambda text: print(text, flush=True)) if emit is None else emit
    report = {"report_version": 1, "demo": env.get("AIRZONE_DEMO_MOCK", "0").lower() in {"1", "true", "yes"},
              "status": "running", "diagnostics": [],
              "started_at": datetime.now(UTC).isoformat(), "revisions": {"minitel": None, "airzone": None}}
    directory = Path(env.get("AIRZONE_EVIDENCE_DIR", str(Path.home() / "airzone-evidence"))).expanduser()
    path = None
    backend = None
    saved = False

    def checkpoint():
        nonlocal saved
        try:
            if not directory.is_absolute() or directory.is_symlink():
                raise OSError("Invalid evidence directory")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            config_store.atomic_write(path, (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(), mode=0o600)
            saved = True
        except (OSError, ValueError, config_store.StorageFailure):
            saved = False

    def progress(operation, result):
        if result is None:
            report["phase_in_progress"] = operation
            checkpoint()
            emit("\nMF-83 / MISE EN SCÈNE")
            for line in random.choice(ANTICS[operation]):
                emit("  " + line)
            emit("DIAG / " + operation.upper() + " : démarrage")
        else:
            report.pop("phase_in_progress", None)
            report["diagnostics"] = list(backend.evidence)
            checkpoint()
            status = "OK" if result["ok"] else "ÉCHEC / " + result["error"]["code"]
            if result["ok"] and operation == "discover":
                status += f" / {len(result['data']['candidates'])} candidat(s), HVAC non vérifié"
            elif result["ok"]:
                status += f" / {result['data']['zone_count']} zones lues"
            emit("DIAG / " + operation.upper() + " : " + status)
            if result["ok"] and operation == "discover":
                for candidate in result["data"]["candidates"][:8]:
                    emit(f"DIAG / candidat : {candidate['host']}:{candidate['port']}")

    try:
        backend = diagnostic.Backend(env, progress=progress, owned_group=True)
        report["demo"] = backend.mock
        path = directory / ("minitel-last-mock.json" if backend.mock else "minitel-last-real.json")
        if not backend.enabled:
            raise diagnostic.DiagnosticError("disabled")
        checkpoint()
        emit("SIMULATION - aucune connexion réelle" if backend.mock else "DIAGNOSTIC LOCAL - lecture seule")
        emit("HACK AIRZONE / ALCATEL MF-83")
        emit("MISE EN SCÈNE : les exploits sont fictifs.")
        emit("DIAG et BILAN : résultats du script.")
        emit("ANNULATION : tuer la tentative, pas la clim.")
        report["revisions"] = {"minitel": revision(ROOT), "airzone": revision(backend.checkout)}
        checkpoint()
        results = backend.execute("airzone_diagnostic", {"action": "state"},
                                  deadline=time.monotonic() + diagnostic.BUDGET, full_diagnostic=True)
        if results[-1]["ok"] and results[-1]["operation"] == "state":
            report["status"] = "complete"
            emit("\nMF-83 / Aucun pare-feu n'a été humilié.")
            emit("Nous avons simplement lu une API.\nLaissez-moi rédiger le rapport autrement.")
        else:
            report.update(status="failed", error=results[-1]["error"]["code"])
    except KeyboardInterrupt:
        report.update(status="interrupted", error="operator_stop")
    except (OSError, ValueError, TypeError, KeyError, diagnostic.DiagnosticError) as exc:
        code = str(exc) if isinstance(exc, diagnostic.DiagnosticError) else "diagnostic_unavailable"
        report.update(status="failed", error=code)
    finally:
        report["completed_at"] = datetime.now(UTC).isoformat()
        if path:
            checkpoint()
        emit("\n" + factual_summary(report))
        if saved:
            emit("Rapport JSON privé : " + str(path))
        else:
            emit("Rapport non enregistré. Photo du bilan possible.")
    return report


def main():
    def stop(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        run()
    except Exception:  # noqa: BLE001 - Never expose raw diagnostics, credentials or tracebacks on the CRT.
        print("HACK AIRZONE : interruption interne. Aucun succès confirmé.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
