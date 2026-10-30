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
        return {"ok": False, "msg": "Échec de la vérification (Internet ?) : " + err}
    rc, behind, _ = run("rev-list", "--count", f"HEAD..origin/{branch}")
    if rc or not behind.isdigit():
        return {"ok": False, "msg": "Comparaison des versions indisponible ; réessayer la vérification."}
    rc, changes, _ = run("log", "-20", "--format=%s" if subjects_only else "--format=%h  %s",
                         f"HEAD..origin/{branch}")
    if rc:
        return {"ok": False, "msg": "Liste des mises à jour indisponible ; réessayer la vérification."}
    return {"ok": True, "behind": int(behind), "log": changes[:1600]}


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
        return {"ok": False}
    if status(deployment).get("phase") in BUSY:
        return {"ok": False, "busy": True}

    def run(*args):
        return git(project, *args, timeout=60 if args[0] == "fetch" else 5)

    for args, expected in ((('branch', '--show-current'), branch),
                           (('rev-parse', 'HEAD'), head),
                           (('status', '--porcelain', '--untracked-files=no'), '')):
        rc, value, _ = run(*args)
        if rc or value != expected:
            return {"ok": False}
    result = check_update(run, branch, subjects_only=True)
    if not result["ok"]:
        return {"ok": False}  # Raw Git diagnostics never reach the theatrical model.
    rc, target, _ = run("rev-parse", f"origin/{branch}")
    if rc or not re.fullmatch(r"[0-9a-f]{40}", target):
        return {"ok": False}
    if run("merge-base", "--is-ancestor", "HEAD", target)[0]:
        return {"ok": False}
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
