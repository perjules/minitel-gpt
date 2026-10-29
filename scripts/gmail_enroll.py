#!/usr/bin/env python3
"""Computer-assisted desktop OAuth. Callback/logs/token persistence are bounded."""

import argparse
import hmac
import json
import os
import secrets
import stat
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.gmail_auth import (
    SCOPES,
    TOKEN_URI,
    GmailError,
    account_bundle,
    account_id,
    encrypt_info,
    save_accounts,
    systemd_preflight,
)

PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/oauth2callback"


def callback_server(state):
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Callback URL contains an authorization code; never log it.

        def do_GET(self):
            values = urlsplit(self.path)
            params = parse_qs(values.query)
            valid = (
                values.path == "/oauth2callback"
                and len(params.get("state", [])) == 1
                and hmac.compare_digest(params["state"][0].encode(), state.encode())
            )
            code = params.get("code", [])
            if valid and len(code) == 1 and 0 < len(code[0]) <= 8192:
                self.server.code = code[0]
                status, text = 200, "Authorization received. Return to the Pi terminal."
            elif valid and "error" in params:
                self.server.denied = True
                status, text = 400, "Authorization declined."
            else:
                status, text = 400, "Invalid callback."
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(text.encode())

    class BoundedServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            return connection, address

    server = BoundedServer(("127.0.0.1", PORT), Callback)
    server.timeout = 1
    server.code, server.denied = None, False
    return server


def load_client(path):
    path = Path(path).resolve()
    repo = Path(__file__).resolve().parents[1]
    if path.is_relative_to(repo):
        raise GmailError("oauth_configuration_must_be_outside_git")
    metadata = path.stat()
    if (
        metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or not stat.S_ISREG(metadata.st_mode)
    ):
        raise GmailError("oauth_configuration_permissions")
    if metadata.st_size > 32768:
        raise GmailError("oauth_configuration_error")
    data = json.loads(path.read_text())
    client = data.get("installed", {})
    if (
        client.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth"
        or client.get("token_uri") != TOKEN_URI
        or not client.get("client_id", "").endswith(".apps.googleusercontent.com")
        or not client.get("client_secret")
    ):
        raise GmailError("desktop_oauth_configuration_required")
    return data


def enroll(path, target=None):
    if os.geteuid() != 0:
        raise GmailError("run_as_root")
    print(json.dumps(systemd_preflight()))
    import requests
    from google_auth_oauthlib.flow import InstalledAppFlow

    data = load_client(path)
    state = secrets.token_urlsafe(32)
    flow = InstalledAppFlow.from_client_config(
        data, scopes=list(SCOPES), state=state, autogenerate_code_verifier=True
    )
    flow.redirect_uri = REDIRECT
    with callback_server(state) as server:
        url, returned_state = flow.authorization_url(
            access_type="offline", prompt="consent", include_granted_scopes="false"
        )
        if not hmac.compare_digest(returned_state, state):
            raise GmailError("oauth_state_error")
        # Authorization URL contains client ID/state/challenge, never access/refresh tokens.
        print("Open on the computer with the SSH tunnel running:\n" + url, flush=True)
        deadline = time.monotonic() + 300
        while server.code is None and not server.denied and time.monotonic() < deadline:
            server.handle_request()
        if server.denied or not server.code:
            raise GmailError("authorization_not_completed")
        flow.fetch_token(code=server.code, timeout=15)
    credentials = flow.credentials
    granted = set(credentials.granted_scopes or credentials.scopes or [])
    if granted != set(SCOPES) or not credentials.refresh_token:
        raise GmailError("required_scopes_or_refresh_token_missing")
    # Email address is needed for correct From, fetched under gmail.readonly.
    response = requests.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": "Bearer " + credentials.token},
        timeout=15,
    )
    response.raise_for_status()
    info = {
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "token_uri": TOKEN_URI,
        "scopes": list(SCOPES),
        "email_address": response.json()["emailAddress"],
    }
    # Same root lock as rotation; never stage a plaintext JSON file.
    import fcntl

    with Path("/run/minitel-gmail-root.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target is None:
            encrypt_info(info)
        else:
            account_id(target)
            data=account_bundle()
            existing=data['accounts'].get(target)
            if existing and existing['info']['email_address'].casefold()!=info['email_address'].casefold():
                raise GmailError('wrong_google_account')
            if any(k!=target and v['info']['email_address'].casefold()==info['email_address'].casefold() for k,v in data['accounts'].items()):
                raise GmailError('duplicate_account')
            data['accounts'][target]={'info':info,'revision':existing['revision']+1 if existing else 1}
            save_accounts(data)
    print(
        "Encrypted enrollment saved. Activate/restart both services; run the read-only probe."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "client_config", help="root-owned 0600 Desktop OAuth JSON outside the checkout"
    )
    parser.add_argument("--account-id", help="explicit account ID for multi-account enrollment; synchronize metadata in admin afterward")
    arguments = parser.parse_args()
    try:
        enroll(arguments.client_config, arguments.account_id)
    except GmailError as error:
        print("Enrollment failed: " + str(error), file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - sanitize OAuth errors which can contain tokens
        # Google/OAuth exceptions may include token response bodies.
        print(
            "Enrollment failed: oauth_or_network_error. Check setup and retry enrollment.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
