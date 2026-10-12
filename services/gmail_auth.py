"""Gmail credentials: systemd delivery, memory-only renewal, encrypted rotation."""

import fcntl
import hmac
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
)
TOKEN_URI = "https://oauth2.googleapis.com/token"
CREDENTIAL_NAME = "gmail"
STORE = Path("/etc/credstore.encrypted/minitel-gmail")
HELPER = "/usr/local/libexec/minitel-gmail-credential"
LOCK_PATH = Path("/run/minitel-gmail/auth.lock")
INFO_FIELDS = {
    "client_id",
    "client_secret",
    "refresh_token",
    "token_uri",
    "scopes",
    "email_address",
}


class GmailError(Exception):
    """Fixed public code only, never an upstream diagnostic or credential."""


def enabled():
    return os.getenv("GMAIL_ENABLED", "0").casefold() in ("1", "true", "yes")


def validate_info(info):
    if (
        not isinstance(info, dict)
        or set(info) != INFO_FIELDS
        or not all(
            isinstance(info.get(k), str) and 0 < len(info[k]) <= 4096
            for k in INFO_FIELDS - {"scopes"}
        )
        or info["token_uri"] != TOKEN_URI
        or not isinstance(info["scopes"], list)
        or not all(isinstance(scope, str) for scope in info["scopes"])
        or len(info["scopes"]) != 2
        or set(info["scopes"]) != set(SCOPES)
        or not info["client_id"].endswith(".apps.googleusercontent.com")
        or not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", info["email_address"])
        or any(ord(c) < 32 for c in info["email_address"])
    ):
        raise GmailError("credential_configuration_error")
    return info


def systemd_preflight():
    """On the Pi, fail closed if encrypted service credentials aren't supported."""
    try:
        creds = subprocess.run(
            ["systemd-creds", "--version"], check=False, capture_output=True, timeout=5
        )
        manager = subprocess.run(
            ["systemctl", "show", "--property=Version", "--value"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        versions = [
            re.search(rb"\b(\d{3})\b", value.stdout) for value in (creds, manager)
        ]
        if (
            creds.returncode
            or manager.returncode
            or not all(versions)
            or any(int(value.group(1)) < 250 for value in versions)
            or not Path("/run/systemd/system").is_dir()
        ):
            raise GmailError("encrypted_credentials_unsupported")
        os_name = "unknown"
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                os_name = line.split("=", 1)[1].strip('"')
        return {
            "os": os_name,
            "systemd": int(versions[1].group(1)),
            "encrypted_credentials": True,
        }
    except (OSError, subprocess.SubprocessError):
        raise GmailError("encrypted_credentials_unsupported") from None


def encrypt_info(info, store=STORE):
    """Root provisioning. Input/output pipes only; persist ciphertext atomically."""
    validate_info(info)
    try:
        payload = json.dumps(info).encode()
        result = subprocess.run(
            ["systemd-creds", "encrypt", "--name=gmail", "--with-key=host", "-", "-"],
            input=payload,
            check=False,
            capture_output=True,
            timeout=15,
        )
        if result.returncode or not result.stdout:
            raise GmailError("credential_encryption_failed")
        # Verify encrypted bytes with the host key before replacing the old file.
        decoded = subprocess.run(
            ["systemd-creds", "decrypt", "--name=gmail", "-", "-"],
            input=result.stdout,
            check=False,
            capture_output=True,
            timeout=15,
        )
        if decoded.returncode or not hmac.compare_digest(
            decoded.stdout.rstrip(), payload
        ):
            raise GmailError("credential_encryption_failed")
        store = Path(store)
        store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".minitel-gmail-", dir=store.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(result.stdout)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, store)
            directory = os.open(store.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except (OSError, subprocess.SubprocessError):
        raise GmailError("credential_storage_failed") from None


def decrypt_info(store=STORE):
    try:
        result = subprocess.run(
            ["systemd-creds", "decrypt", "--name=gmail", str(store), "-"],
            check=False,
            capture_output=True,
            timeout=15,
        )
        if result.returncode or len(result.stdout) > 32768:
            raise GmailError("credential_unavailable")
        return validate_info(json.loads(result.stdout))
    except (OSError, ValueError, subprocess.SubprocessError):
        raise GmailError("credential_unavailable") from None


def credential_helper(request):
    """No credentials in argv, shell, logs or error responses. Pipes stay local."""
    try:
        result = subprocess.run(
            ["sudo", "-n", HELPER],
            input=json.dumps(request).encode(),
            check=False,
            capture_output=True,
            timeout=35,
        )
        if result.returncode or len(result.stdout) > 32768:
            raise GmailError("credential_helper_unavailable")
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise GmailError("credential_helper_unavailable") from None


class TokenSource:
    def __init__(
        self, credential_dir=None, helper=credential_helper, lock_path=LOCK_PATH
    ):
        self.credential_dir = credential_dir
        self.helper = helper
        self.lock_path = Path(lock_path)
        self.lock = threading.Lock()
        self.credentials = None
        self.email_address = None

    def delivered_info(self):
        directory = self.credential_dir or os.getenv("CREDENTIALS_DIRECTORY")
        if not directory:
            raise GmailError("reconnect_required")
        try:
            return validate_info(
                json.loads((Path(directory) / CREDENTIAL_NAME).read_text())
            )
        except (OSError, ValueError):
            raise GmailError("reconnect_required") from None

    @contextmanager
    def refresh_lock(self, deadline):
        if not self.lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise GmailError("busy")
        try:
            with self.lock_path.open("a") as stream:
                while True:
                    try:
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise GmailError("busy")
                        time.sleep(0.05)
                yield
        except OSError:
            raise GmailError("credential_helper_unavailable") from None
        finally:
            self.lock.release()

    def token(self, deadline):
        with self.refresh_lock(deadline):
            if self.credentials is not None and self.credentials.valid:
                return self.credentials.token
            # Require systemd-delivered credentials, never a plaintext fallback.
            self.delivered_info()
            info = validate_info(self.helper({"operation": "read"}))
            try:
                from google.auth.exceptions import RefreshError, TransportError
                from google.auth.transport.requests import Request
                from google.oauth2.credentials import Credentials
            except ImportError:
                raise GmailError("dependency_missing") from None
            credentials = Credentials.from_authorized_user_info(
                info, scopes=list(SCOPES)
            )
            auth_request = Request()

            def bounded_request(*args, **kwargs):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GmailError("timeout")
                kwargs["timeout"] = min(15, remaining)
                return auth_request(*args, **kwargs)

            try:
                credentials.refresh(bounded_request)
            except RefreshError:
                raise GmailError("reconnect_required") from None
            except TransportError:
                raise GmailError("unavailable") from None
            finally:
                auth_request.session.close()
            if credentials.granted_scopes is not None and set(
                credentials.granted_scopes
            ) != set(SCOPES):
                raise GmailError("reconnect_required")
            if credentials.refresh_token != info["refresh_token"]:
                rotated = self.helper(
                    {
                        "operation": "rotate",
                        "expected": info["refresh_token"],
                        "replacement": credentials.refresh_token,
                    }
                )
                if rotated.get("status") != "saved":
                    raise GmailError("credential_rotation_failed")
            self.email_address = info["email_address"]
            self.credentials = credentials
            return credentials.token
