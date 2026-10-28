#!/opt/minitel-gpt/venv/bin/python -I
"""Root-owned fixed-purpose helper. No arguments, arbitrary paths or token logs."""

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

# This file and gmail_auth.py are copied into the same root-owned release.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gmail_auth import (
    GmailError,
    decrypt_info,
    decrypt_oauth_config,
    encrypt_info,
    encrypt_oauth_config,
    validate_info,
)


def activate():
    """Reload encrypted delivery for both services after the HTTP response."""
    with Path("/var/lib/minitel-gpt/runtime.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = Path("/var/lib/minitel-gpt/deployment.json")
        if status.exists() and json.loads(status.read_text()).get("phase") in {"queued", "preparing", "activating"}:
            raise GmailError("deployment_busy")
        decrypt_info()  # Refuse to change units without verified enrollment.
        for service in ("minitel-chatgpt", "admin-ui"):
            folder = Path(f"/etc/systemd/system/{service}.service.d")
            folder.mkdir(mode=0o755, parents=True, exist_ok=True)
            # Same credential drop-in as install_gmail.sh; no general systemd access.
            (folder / "gmail.conf").write_text(
                "[Unit]\nAfter=systemd-tmpfiles-setup.service\n[Service]\n"
                "Environment=GMAIL_ENABLED=1\n"
                "LoadCredentialEncrypted=gmail:/etc/credstore.encrypted/minitel-gmail\n"
                "PrivateMounts=yes\n"
            )
        for command in (
            ["systemctl", "daemon-reload"],
            ["systemd-run", "--quiet", "--collect", "--unit=minitel-gmail-activate",
             "--on-active=3s", "/usr/bin/systemctl", "restart", "minitel-chatgpt", "admin-ui"],
        ):
            result = subprocess.run(command, check=False, capture_output=True, timeout=10)
            if result.returncode:
                raise GmailError("credential_activation_failed")
    return {"status": "queued"}


def process(request):
    if not isinstance(request, dict):
        raise GmailError("invalid_request")
    with Path("/run/minitel-gmail-root.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if request == {"operation": "oauth_read"}:
            return decrypt_oauth_config()
        if set(request) == {"operation", "config"} and request["operation"] == "oauth_configure":
            encrypt_oauth_config(request["config"])
            return {"status": "saved"}
        if set(request) == {"operation", "info"} and request["operation"] == "enroll":
            info = validate_info(request["info"])
            configured = decrypt_oauth_config()
            if any(info[key] != configured[key] for key in ("client_id", "client_secret")):
                raise GmailError("oauth_configuration_changed")
            encrypt_info(info)
            return {"status": "saved"}
        if request == {"operation": "activate"}:
            return activate()
        info = decrypt_info()
        if request == {"operation": "read"}:
            return info
        if (
            set(request) != {"operation", "expected", "replacement"}
            or request["operation"] != "rotate"
            or not isinstance(request["replacement"], str)
            or not 0 < len(request["replacement"]) <= 4096
            or request["expected"] != info["refresh_token"]
        ):
            raise GmailError("invalid_rotation")
        info["refresh_token"] = request["replacement"]
        encrypt_info(info)
        return {"status": "saved"}


def main():
    try:
        if os.geteuid() != 0 or len(sys.argv) != 1 or sys.stdout.isatty():
            raise GmailError("invalid_invocation")
        request = sys.stdin.buffer.read(32769)
        if len(request) > 32768:
            raise GmailError("invalid_request")
        result = process(json.loads(request))
        sys.stdout.write(json.dumps(result))
    except (GmailError, ValueError, OSError, TypeError, subprocess.SubprocessError):
        # Never print exception text; upstream/JSON errors can include credentials.
        sys.stdout.write('{"status":"credential_helper_error"}')
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
