"""Local, read-only PRONOTE adapter. No credentials cross the tool boundary."""
import copy
import fcntl
import html
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
SDK_VERSION = "2.15.6"
CACHE_SECONDS = 600
STALE_SECONDS = 86400
MAX_ITEMS = 100
MAX_RESULT_CHARACTERS = 24000
LOCK = threading.Lock()
DEADLINE = ContextVar("pronote_deadline", default=0)
SAVE = ContextVar("pronote_save", default=None)
_CLIENT = None


class PronoteError(Exception):
    """Public, secret-free error code; never expose SDK exception messages."""


def today():
    return datetime.now(ZoneInfo("Europe/Paris")).date()


def slug(value):
    if not isinstance(value, str):
        raise PronoteError("invalid_account")
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", value):
        raise PronoteError("invalid_account")
    return value


def clean(value, limit=2000):
    text = html.unescape(re.sub(r"<[^>]*>", "", str(value or "")))
    text = re.sub(r"https?://\S+", "[link omitted]", text)
    return "".join(c for c in text if c in "\n\t" or ord(c) >= 32)[:limit]


def atomic_json(path, data):
    """Private directory, durable replacement, no half-written rotating token."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PronoteError("configuration_error") from None


def client_class():
    """Pin the small SDK hook that supplies network deadlines and blocks writes."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    try:
        if version("pronotepy") != SDK_VERSION:
            raise PronoteError("dependency_version")
        import pronotepy
        from pronotepy import clients, pronoteAPI
        from requests.adapters import HTTPAdapter
    except ImportError:
        raise PronoteError("dependency_missing") from None
    except PackageNotFoundError:
        raise PronoteError("dependency_missing") from None
    for name in ("pronotepy.clients", "pronotepy.pronoteAPI"):
        logging.getLogger(name).disabled = True

    class TimedAdapter(HTTPAdapter):
        def send(self, request, **kwargs):
            remaining = DEADLINE.get() - time.monotonic()
            if remaining <= 0:
                raise PronoteError("timeout")
            kwargs["timeout"] = min(10, remaining)
            return super().send(request, **kwargs)

    class TimedCommunication(pronoteAPI._Communication):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.session.mount("https://", TimedAdapter())
            self.session.mount("http://", TimedAdapter())

    clients._Communication = TimedCommunication

    class ReadOnlyParent(pronotepy.ParentClient):
        def __init__(self, *args, **kwargs):
            try:
                super().__init__(*args, **kwargs)
            except Exception:
                if hasattr(self, "communication"):
                    self.communication.session.close()
                raise

        def post(self, function_name, onglet=None, data=None):
            allowed = {"Identification", "Authentification", "ParametresUtilisateur",
                       "PageInfosPerso", "SecurisationCompteDoubleAuth",
                       "PageCahierDeTexte", "DernieresNotes"}
            if function_name not in allowed:
                raise PronoteError("read_only")
            # The SDK has already replaced password with the next token here.
            # Save before parsing user/child data can fail in the constructor.
            if function_name == "ParametresUtilisateur" and SAVE.get():
                SAVE.get()(self.export_credentials())
            post_data = {}
            if onglet:
                post_data["Signature"] = {"onglet": onglet, "membre": {
                    "N": self._selected_child.id, "G": 4}}
            if data:
                post_data["data"] = data
            # Deliberately no automatic refresh/retry with expired child IDs.
            return self.communication.post(function_name, post_data)

    _CLIENT = ReadOnlyParent
    return _CLIENT


class Pronote:
    def __init__(self, root=ROOT, factory=None):
        self.directory = Path(root) / "config" / "pronote"
        self.config_path = self.directory / "settings.json"
        self.factory = factory or client_class
        self.cache = {}

    def config(self):
        if not self.config_path.exists():
            return {"enabled": False, "children": {}}
        data = read_json(self.config_path)
        if (not isinstance(data, dict) or type(data.get("enabled")) is not bool
                or not isinstance(data.get("children"), dict)):
            raise PronoteError("configuration_error")
        if len(data["children"]) > 20:
            raise PronoteError("configuration_error")
        for alias, mapping in data["children"].items():
            if (not isinstance(alias, str) or not alias.strip() or len(alias) > 40
                    or any(ord(c) < 32 for c in alias) or not isinstance(mapping, dict)
                    or not isinstance(mapping.get("name"), str) or not 1 <= len(mapping["name"]) <= 160):
                raise PronoteError("configuration_error")
            mapping["account"] = slug(mapping.get("account"))
        return data

    def configure(self, enabled, children):
        if not isinstance(children, dict) or type(enabled) is not bool:
            raise PronoteError("configuration_error")
        candidate = {"enabled": enabled, "children": children}
        # Validate with exactly the same reader without mutating live settings.
        for alias, mapping in children.items():
            if (not isinstance(alias, str) or not alias.strip() or len(alias) > 40
                    or any(ord(c) < 32 for c in alias) or not isinstance(mapping, dict)
                    or not isinstance(mapping.get("name"), str) or not 1 <= len(mapping["name"]) <= 160):
                raise PronoteError("configuration_error")
            slug(mapping.get("account"))
        if len(children) > 20:
            raise PronoteError("configuration_error")
        candidate["children"] = {
            alias: {**mapping, "account": slug(mapping["account"])}
            for alias, mapping in children.items()
        }
        with self.lock("settings", time.monotonic() + 20):
            atomic_json(self.config_path, candidate)
        self.cache.clear()

    @contextmanager
    def lock(self, account, deadline):
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)
        if deadline <= time.monotonic():
            raise PronoteError("timeout")
        while not LOCK.acquire(timeout=max(0, min(.1, deadline - time.monotonic()))):
            if time.monotonic() >= deadline:
                raise PronoteError("busy")
        try:
            fd = os.open(self.directory / (slug(account) + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(fd, "w") as stream:
                while True:
                    try:
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise PronoteError("busy")
                        time.sleep(.05)
                yield
        finally:
            LOCK.release()

    def credentials_path(self, account):
        return self.directory / (slug(account) + ".credentials.json")

    @contextmanager
    def session(self, account, deadline, qr=None, pin=None, account_pin=None):
        with self.lock(account, deadline):
            deadline_token = DEADLINE.set(deadline)
            path = self.credentials_path(account)
            save_token = SAVE.set(lambda creds: atomic_json(path, creds))
            client = None
            try:
                factory = self.factory()
                if qr is not None:
                    client = factory.qrcode_login(qr, pin, str(uuid.uuid4()),
                                                  account_pin=account_pin,
                                                  device_name="Minitel 722079V", skip_2fa=True)
                    if not client.logged_in:
                        raise PronoteError("authentication_required")
                    atomic_json(path, client.export_credentials())
                    # Validate the token and device registration now. Save the QR
                    # token first so even a failed second login is recoverable.
                    client.communication.session.close()
                    client = None
                    client = factory.token_login(**read_json(path), account_pin=account_pin,
                                                 device_name="Minitel 722079V")
                else:
                    if not path.exists():
                        raise PronoteError("not_enrolled")
                    client = factory.token_login(**read_json(path))
                if not client.logged_in:
                    raise PronoteError("authentication_required")
                atomic_json(path, client.export_credentials())
                yield client
            finally:
                try:
                    if client is not None:
                        client.communication.session.close()
                    # PronotePy retains Period objects in a process-global set.
                    if _CLIENT is not None:
                        from pronotepy.dataClasses import Period
                        Period.instances.clear()
                finally:
                    SAVE.reset(save_token)
                    DEADLINE.reset(deadline_token)

    def enroll(self, account, qr, pin, account_pin=None):
        account = slug(account)
        required = {"url", "login", "jeton"}
        if (not isinstance(qr, dict) or not required.issubset(qr)
                or not all(isinstance(qr[key], str) and qr[key] for key in required)
                or not re.fullmatch(r"\d{4}", pin or "")):
            raise PronoteError("invalid_qr")
        # Real mobile QR codes include optional flags, e.g. avecPageConnexion.
        # Pass only the SDK's three connection fields; never change their bytes.
        qr = {key: qr[key] for key in required}
        url = urlsplit(qr["url"])
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or not url.path.endswith(("parent.html", "mobile.parent.html"))):
            raise PronoteError("invalid_parent_url")
        try:
            with self.session(account, time.monotonic() + 35, qr, pin, account_pin) as client:
                children = [child.name for child in client.children]
            self.cache.clear()
            return children  # ADMIN ONLY; names are not returned by public tools.
        except PronoteError:
            raise
        except Exception:  # noqa: BLE001 -- SDK exceptions must never expose secrets
            raise PronoteError("enrollment_failed") from None

    def admin_children(self, account):
        try:
            with self.session(account, time.monotonic() + 30) as client:
                return [child.name for child in client.children]
        except PronoteError:
            raise
        except Exception:  # noqa: BLE001 -- SDK exceptions must never expose secrets
            raise PronoteError("authentication_required") from None

    def status(self):
        config = self.config()
        try:
            dependency = version("pronotepy") == SDK_VERSION
        except PackageNotFoundError:
            dependency = False
        return {"enabled": config["enabled"], "dependency_ready": dependency,
                "children": list(config["children"]),
                "accounts_enrolled": bool(config["children"]) and all(self.credentials_path(m["account"]).exists()
                                         for m in config["children"].values())}

    def tools(self):
        config = self.config()
        if not config["enabled"]:
            return []
        aliases = list(config["children"])
        child = {"type": "string", "enum": aliases}
        definitions = [
            ("pronote_list_children", "List the allowed child aliases, never account identities.", {}),
            ("pronote_get_homework", "Read homework, inclusive ISO dates. Default today through 7 days. Max 31 days.",
             {"child": child, "from": {"type": "string"}, "to": {"type": "string"}}),
            ("pronote_get_grades", "Read published grades. Default current period; specify exact period name if known.",
             {"child": child, "period": {"type": "string"}}),
        ]
        return [{"type": "function", "function": {"name": name, "description": description,
                 "parameters": {"type": "object", "properties": properties,
                                "required": ["child"] if properties else [],
                                "additionalProperties": False}}}
                for name, description, properties in definitions]

    def execute(self, name, arguments, deadline=None):
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise PronoteError("invalid_arguments")
            config = self.config()
            if not config["enabled"]:
                raise PronoteError("disabled")
            if name == "pronote_list_children" and not arguments:
                return {"children": list(config["children"])}
            permitted = {"pronote_get_homework": {"child", "from", "to"},
                         "pronote_get_grades": {"child", "period"}}
            if name not in permitted or set(arguments) - permitted[name]:
                raise PronoteError("unsupported_tool")
            child = arguments.get("child")
            if not isinstance(child, str) or child not in config["children"]:
                raise PronoteError("unknown_child")
            args = {"child": child}
            if name == "pronote_get_homework":
                start_text = arguments.get("from", today().isoformat())
                if not isinstance(start_text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_text):
                    raise PronoteError("invalid_arguments")
                start = date.fromisoformat(start_text)
                end_text = arguments.get("to", (start + timedelta(days=7)).isoformat())
                if not isinstance(end_text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_text):
                    raise PronoteError("invalid_arguments")
                end = date.fromisoformat(end_text)
                if not 0 <= (end - start).days <= 30:
                    raise PronoteError("invalid_date_range")
                args.update({"from": start.isoformat(), "to": end.isoformat()})
            else:
                period = arguments.get("period", "current")
                if not isinstance(period, str) or not 1 <= len(period) <= 100:
                    raise PronoteError("invalid_period")
                args["period"] = period
            mapping = config["children"][child]
            credentials_path = self.credentials_path(mapping["account"])
            if not credentials_path.exists():
                raise PronoteError("not_enrolled")
            # Rotations keep uuid stable; a fresh QR enrollment changes it and
            # invalidates caches in other processes without storing school data.
            enrollment = read_json(credentials_path).get("uuid")
            cache_key = json.dumps([name, args, config, enrollment, today().isoformat()], sort_keys=True)
            cached = self.cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
                return {**copy.deepcopy(cached[1]), "cached": True}
            try:
                with self.session(mapping["account"], deadline or time.monotonic() + 30) as client:
                    matches = [c for c in client.children if c.name == mapping["name"]]
                    if len(matches) != 1:
                        raise PronoteError("child_unavailable")
                    client.set_child(matches[0])
                    if name == "pronote_get_homework":
                        values = sorted(client.homework(start, end), key=lambda item: item.date)
                        items = [{"date": h.date.isoformat(), "subject": clean(h.subject.name, 120),
                                  "description": clean(h.description), "done": bool(h.done)}
                                 for h in values[:MAX_ITEMS]]
                    else:
                        periods = client.periods
                        if args["period"] == "current":
                            try:
                                selected = client.current_period
                            except (KeyError, IndexError):
                                choices = [p for p in periods if p.start.date() <= today() <= p.end.date()]
                                if len(choices) != 1:
                                    return {"error": "select_period", "periods": [clean(p.name, 100) for p in periods]}
                                selected = choices[0]
                        else:
                            choices = [p for p in periods if p.name == args["period"]]
                            if len(choices) != 1:
                                return {"error": "select_period", "periods": [clean(p.name, 100) for p in periods]}
                            selected = choices[0]
                        values = sorted(selected.grades, key=lambda item: item.date, reverse=True)
                        items = [{"date": g.date.isoformat(), "subject": clean(g.subject.name, 120),
                                  "grade": clean(g.grade, 40), "out_of": clean(g.out_of, 40),
                                  "coefficient": clean(g.coefficient, 40), "comment": clean(g.comment, 300)}
                                 for g in values[:MAX_ITEMS]]
                        args["period"] = clean(selected.name, 100)
                truncated = len(values) > MAX_ITEMS
                while len(json.dumps(items, ensure_ascii=False)) > MAX_RESULT_CHARACTERS:
                    items.pop()
                    truncated = True
                if name == "pronote_get_homework":
                    truncated = truncated or any(len(clean(h.description, 2001)) > 2000 for h in values[:len(items)])
                result = {**args, "items": items, "truncated": truncated,
                          "retrieved_at": datetime.now(ZoneInfo("Europe/Paris")).isoformat(timespec="seconds"),
                          "cached": False, "stale": False}
                if len(self.cache) >= 64:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[cache_key] = (time.monotonic(), copy.deepcopy(result))
                return result
            except Exception as error:
                # Never substitute another child/date/period or return stale data
                # after authentication/allowlist failures.
                if (cached and time.monotonic() - cached[0] < STALE_SECONDS
                        and not isinstance(error, PronoteError)
                        and isinstance(error, requests.exceptions.RequestException)):
                    return {**copy.deepcopy(cached[1]), "cached": True, "stale": True,
                            "warning": "PRONOTE unavailable; cached data may have changed."}
                if isinstance(error, PronoteError):
                    raise
                raise PronoteError("unavailable") from None
        except PronoteError as error:
            return {"error": str(error)}
        except (ValueError, TypeError):
            return {"error": "invalid_arguments"}
        except Exception:  # noqa: BLE001 -- final secret-free tool boundary
            return {"error": "unavailable"}


_backend = Pronote()


def backend():
    return _backend
