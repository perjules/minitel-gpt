#!/opt/minitel-gpt/venv/bin/python -I
"""Root-owned fixed-purpose helper. No arguments, arbitrary paths or token logs."""

import fcntl
import json
import os
import sys
from pathlib import Path

# This file and gmail_auth.py are copied into the same root-owned release.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gmail_auth import GmailError, decrypt_info, encrypt_info


def process(request):
    if not isinstance(request, dict):
        raise GmailError("invalid_request")
    with Path("/run/minitel-gmail-root.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
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
    except (GmailError, ValueError, OSError, TypeError):
        # Never print exception text; upstream/JSON errors can include credentials.
        sys.stdout.write('{"status":"credential_helper_error"}')
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
