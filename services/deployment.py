"""Shared, unprivileged update preview and fixed privileged queue operation."""
import json
import os
import re
import signal
import subprocess
from pathlib import Path

RUNTIME_STATE = Path("/var/lib/minitel-gpt/runtime.json")
DEPLOYMENT_STATUS = Path("/var/lib/minitel-gpt/deployment.json")
BUSY = {"queued", "preparing", "activating"}

# Fixed diagnostics only: raw Git stderr can contain credential-bearing URLs.
PREFLIGHT_REASONS = {
    "runtime_state": ("Installation illisible ou incomplète.", "Installation state unreadable or incomplete."),
    "busy": ("Une maintenance est déjà en cours.", "Maintenance is already in progress."),
    "branch_read": ("Lecture de la branche impossible.", "Cannot read checkout branch."),
    "branch_mismatch": ("Branche différente de l'installation.", "Checkout and installed branches differ."),
    "head_read": ("Lecture de la révision impossible.", "Cannot read checkout revision."),
    "head_mismatch": ("Code et runtime ne correspondent pas.", "Checkout and installed revisions differ."),
    "status_read": ("État des fichiers inaccessible.", "Cannot check tracked files."),
    "dirty": ("Fichiers suivis modifiés. Vérifiez SSH.", "Tracked files modified. Check via SSH."),
    "fetch": ("Accès distant Git impossible ou expiré.", "Git remote access failed or timed out."),
    "comparison": ("Comparaison des révisions impossible.", "Cannot compare revisions."),
    "history": ("Historique des révisions inaccessible.", "Cannot read revision history."),
    "history_format": ("Historique des révisions illisible.", "Invalid revision history."),
    "history_changed": ("Historique modifié. Relancez MAJ.", "History changed. Run MAJ again."),
    "target_read": ("Révision distante introuvable.", "Cannot read remote revision."),
    "ancestry_read": ("Vérification de l'historique impossible.", "Cannot verify revision ancestry."),
    "diverged": ("Historique local divergent. Vérifiez SSH.", "Local history diverged. Check via SSH."),
}


def preflight_notice(result, language="fr"):
    """Explain a refused check without rendering arbitrary data as terminal text."""
    reason = result.get("reason")
    if result.get("busy"):
        reason = "busy"
    lines = PREFLIGHT_REASONS.get(reason)
    if lines is None:
        return ("Vérification indisponible.\nConsultez Maintenance dans la console." if language != "en" else
                "Update check unavailable.\nSee Maintenance in the admin console.")
    return lines[language == "en"] + ("\nCode : " if language != "en" else "\nCode: ") + reason


def git(project, *args, timeout=60):
    """Bound Git and its transport children; never run a shell."""
    process = None
    try:
        process = subprocess.Popen(
            ["git", "-C", str(project), *args], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        out, err = process.communicate(timeout=timeout)
        return process.returncode, out.strip(), err.strip()
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        return 1, "", "Git timeout"
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return 1, "", "Git unavailable"


def check_update(run, branch, *, subjects_only=False):
    """Compare against the installed branch; all writes remain in the helper."""
    rc, _, err = run("fetch", "origin", branch)
    if rc:
        return {"ok": False, "reason": "fetch", "msg": "Échec de la vérification (Internet ?) : " + err}
    rc, behind, _ = run("rev-list", "--count", f"HEAD..origin/{branch}")
    if rc or not behind.isdigit():
        return {"ok": False, "reason": "comparison", "msg": "Comparaison des versions indisponible ; réessayer la vérification."}
    rc, changes, _ = run("log", "-20", "--format=%H%x00%s" if subjects_only else "--format=%h  %s",
                         f"HEAD..origin/{branch}")
    if rc:
        return {"ok": False, "reason": "history", "msg": "Liste des mises à jour indisponible ; réessayer la vérification."}
    if subjects_only:
        commits = []
        for line in changes.splitlines():
            identifier, separator, subject = line.partition("\0")
            if not separator or not re.fullmatch(r"[0-9a-f]{40}", identifier):
                return {"ok": False, "reason": "history_format", "msg": "Liste des mises à jour illisible."}
            commits.append({"id": identifier, "subject": subject})
        if len(commits) != min(int(behind), 20):
            return {"ok": False, "reason": "history_changed", "msg": "La liste a changé ; réessayez la vérification."}
        return {"ok": True, "behind": int(behind), "commits": commits,
                "log": "\n".join(c["subject"] for c in commits)[:1600]}
    return {"ok": True, "behind": int(behind), "log": changes[:1600]}


def available(project, runtime=RUNTIME_STATE):
    """One bounded startup probe. None means unknown, never 'up to date'."""
    branch = status(runtime).get("branch")
    if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch):
        return None
    rc, current, _ = git(project, "branch", "--show-current", timeout=1)
    if rc or current != branch:
        return None
    if git(project, "fetch", "origin", branch, timeout=3)[0]:
        return None
    target = f"origin/{branch}"
    if git(project, "merge-base", "--is-ancestor", "HEAD", target, timeout=1)[0]:
        return None
    rc, count, _ = git(project, "rev-list", "--count", f"HEAD..{target}", timeout=1)
    return int(count) > 0 if not rc and count.isdigit() else None


def status(path=DEPLOYMENT_STATUS):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def preview(project, runtime=RUNTIME_STATE, deployment=DEPLOYMENT_STATUS):
    """Terminal preflight is conservative; the root helper rechecks at launch."""
    state = status(runtime)
    branch = state.get("branch")
    active = state.get("active")
    head = active.get("commit") if isinstance(active, dict) else None
    if (not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
            or not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head)):
        return {"ok": False, "reason": "runtime_state"}
    if status(deployment).get("phase") in BUSY:
        return {"ok": False, "busy": True, "reason": "busy"}

    def run(*args):
        return git(project, *args, timeout=60 if args[0] == "fetch" else 5)

    for args, expected, failure, mismatch in (
            (('branch', '--show-current'), branch, 'branch_read', 'branch_mismatch'),
            (('rev-parse', 'HEAD'), head, 'head_read', 'head_mismatch'),
            (('status', '--porcelain', '--untracked-files=no'), '', 'status_read', 'dirty')):
        rc, value, _ = run(*args)
        if rc or value != expected:
            return {"ok": False, "reason": failure if rc else mismatch}
    result = check_update(run, branch, subjects_only=True)
    if not result["ok"]:
        return {"ok": False, "reason": result["reason"]}  # Never forward raw Git diagnostics.
    rc, target, _ = run("rev-parse", f"origin/{branch}")
    if rc or not re.fullmatch(r"[0-9a-f]{40}", target):
        return {"ok": False, "reason": "target_read"}
    rc = run("merge-base", "--is-ancestor", "HEAD", target)[0]
    if rc:
        return {"ok": False, "reason": "diverged" if rc == 1 else "ancestry_read"}
    return {**result, "branch": branch, "head": head, "target": target}


def queue_deployment(operation, payload=None):
    if operation not in {"update", "rollback", "switch-branch", "reboot", "poweroff"}:
        return False, "Opération inconnue."
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/local/sbin/minitel-runtime", operation],
            capture_output=True, text=True, timeout=15, check=False,
            input=json.dumps(payload) if payload is not None else None,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return False, "Gestionnaire uv indisponible ; relancer sudo bash install.sh."
    if result.returncode:
        return False, "Opération refusée ; consulter le journal minitel-deploy."
    return True, "Opération planifiée ; actualiser Maintenance pour suivre le résultat."


def unchanged(project, preview, runtime=RUNTIME_STATE, deployment=DEPLOYMENT_STATUS):
    """Recheck the displayed installation immediately before requesting an update."""
    state = status(runtime)
    active = state.get("active")
    if (state.get("branch") != preview["branch"]
            or not isinstance(active, dict) or active.get("commit") != preview["head"]
            or status(deployment).get("phase") in BUSY):
        return False
    for args, expected in ((('branch', '--show-current'), preview['branch']),
                           (('rev-parse', 'HEAD'), preview['head']),
                           (('status', '--porcelain', '--untracked-files=no'), '')):
        rc, value, _ = git(project, *args, timeout=5)
        if rc or value != expected:
            return False
    return True


if __name__ == "__main__":
    # Can be extracted with git show and run before changing the live checkout.
    import argparse

    parser = argparse.ArgumentParser(description="Check terminal MAJ prerequisites; never deploy.")
    parser.add_argument("project", type=Path)
    print(json.dumps(preview(parser.parse_args().project), ensure_ascii=True, indent=2))
