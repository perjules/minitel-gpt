"""Admin Gmail enrollment: bounded, one-use OAuth state kept only in RAM."""

import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict

import requests

try:
    from . import gmail_auth as auth
except ImportError:
    import gmail_auth as auth

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
PENDING = OrderedDict()
LOCK = threading.Lock()
LIFETIME = 600
MAX_PENDING = 8


def configuration():
    return auth.validate_oauth_config(auth.credential_helper({"operation": "oauth_read"}))


def configure(raw, redirect_uri):
    try:
        if len(raw) > 32768:
            raise ValueError
        web = json.loads(raw)["web"]
        if (web.get("auth_uri") not in {AUTH_URI, "https://accounts.google.com/o/oauth2/auth"}
                or web.get("token_uri") != auth.TOKEN_URI
                or not isinstance(web.get("redirect_uris"), list)
                or redirect_uri not in web.get("redirect_uris", [])):
            raise ValueError
        config = auth.validate_oauth_config({key: web[key] for key in ("client_id", "client_secret")} |
                                          {"redirect_uri": redirect_uri})
    except (ValueError, TypeError, KeyError, AttributeError):
        raise auth.GmailError("web_oauth_configuration_required") from None
    result = auth.credential_helper({"operation": "oauth_configure", "config": config})
    if result != {"status": "saved"}:
        raise auth.GmailError("credential_helper_unavailable")
    with LOCK:
        PENDING.clear()


def cancel(identifier):
    with LOCK:
        PENDING.pop(identifier, None)


def begin(config, target=None):
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        raise auth.GmailError("dependency_missing") from None
    config = auth.validate_oauth_config(config)
    state = secrets.token_urlsafe(32)
    flow = Flow.from_client_config(
        {"web": {"client_id": config["client_id"], "client_secret": config["client_secret"],
                 "auth_uri": AUTH_URI, "token_uri": auth.TOKEN_URI}},
        scopes=list(auth.SCOPES), state=state, autogenerate_code_verifier=True,
    )
    flow.redirect_uri = config["redirect_uri"]
    url, returned = flow.authorization_url(access_type="offline", prompt="consent select_account",
                                           include_granted_scopes="false")
    if not hmac.compare_digest(returned, state):
        raise auth.GmailError("oauth_state_error")
    identifier = secrets.token_urlsafe(32)
    now = time.monotonic()
    with LOCK:
        for key in list(PENDING):
            if now - PENDING[key][0] >= LIFETIME:
                del PENDING[key]
        if len(PENDING) >= MAX_PENDING:
            raise auth.GmailError("busy")
        PENDING[identifier] = (now, state, flow, target)
    return identifier, url


def complete(identifier, state, code, denied=False):
    with LOCK:
        entry = PENDING.get(identifier)
        if entry is None or time.monotonic() - entry[0] >= LIFETIME:
            PENDING.pop(identifier, None)
            raise auth.GmailError("oauth_session_expired")
        if not hmac.compare_digest(state.encode(), entry[1].encode()):
            raise auth.GmailError("oauth_state_error")
        del PENDING[identifier]  # One use, including denials and network failures.
    if denied:
        raise auth.GmailError("authorization_declined")
    if not isinstance(code, str) or not 0 < len(code) <= 8192:
        raise auth.GmailError("oauth_callback_invalid")
    try:
        target = entry[3]
        if target is not None:
            try:
                from .user_registry import Registry
            except ImportError:
                from user_registry import Registry
            if Registry().get(target['user_id']).revision != target['user_revision']:
                raise auth.GmailError('account_assignment_changed')
        flow = entry[2]
        flow.fetch_token(code=code, timeout=(3, 15))
        credentials = flow.credentials
        granted = credentials.granted_scopes if credentials.granted_scopes is not None else credentials.scopes
        if set(granted or []) != set(auth.SCOPES) or not credentials.refresh_token:
            raise auth.GmailError("required_scopes_or_refresh_token_missing")
        with requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": "Bearer " + credentials.token}, timeout=(3, 15),
            stream=True, allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise auth.GmailError("gmail_profile_unavailable")
            body = bytearray()
            for chunk in response.iter_content(4096):
                body.extend(chunk)
                if len(body) > 32768:
                    raise auth.GmailError("gmail_profile_unavailable")
            email = json.loads(body)["emailAddress"]
        info = auth.validate_info({"client_id": credentials.client_id,
                                   "client_secret": credentials.client_secret,
                                   "refresh_token": credentials.refresh_token,
                                   "token_uri": auth.TOKEN_URI, "scopes": list(auth.SCOPES),
                                   "email_address": email})
        payload = {"operation": "enroll", "info": info}
        if target is not None:
            payload.update(account_id=target['account_id'], revision=target['revision'])
        result = auth.credential_helper(payload)
        if result.get('status') != 'saved':
            raise auth.GmailError("credential_helper_unavailable")
        if target is not None:
            Registry().record_account(target['account_id'], email, result['revision'])
            return {'email': email, 'account_id':target['account_id'], 'user_id':target['user_id'],
                    'user_revision':Registry().get(target['user_id']).revision}
        return email
    except auth.GmailError:
        raise
    except Exception:  # noqa: BLE001 - OAuth exceptions can contain private token responses.
        raise auth.GmailError("oauth_or_network_error") from None
