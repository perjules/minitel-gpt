"""Bounded HTTP calls to the configured Aizone controller."""
import json
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

import requests

MAX_BODY = 128 * 1024
REQUEST_TIMEOUT = 10


class AirzoneError(ValueError):
    pass


def observation(state):
    try:
        value = datetime.fromisoformat(state.get("updated_at", ""))
        if value.utcoffset() is not None:
            return value
    except (TypeError, ValueError):
        pass
    return None


def age(state):
    value = observation(state)
    return (datetime.now(UTC) - value).total_seconds() if value else None


def fresh(state):
    seconds = age(state)
    return state.get("connection") == "online" and seconds is not None and -5 <= seconds < 90


def validate_state(state):
    zones = state.get("zones")
    if (type(state.get("demo")) is not bool or not isinstance(zones, list) or len(zones) > 32
            or any(not isinstance(zone, dict) or type(zone.get("id")) is not int
                   or not 1 <= zone["id"] <= 32 for zone in zones)
            or len({zone["id"] for zone in zones}) != len(zones)):
        raise AirzoneError("Réponse Aizone invalide.")
    return state


class Client:
    def __init__(self, origin):
        try:
            url = urlsplit(origin)
            if (url.scheme != "https" or not url.hostname or url.username is not None
                    or origin != f"https://{url.netloc}" or url.port == 0
                    or any(char.isspace() or ord(char) < 32 for char in origin)
                    or "\\" in origin or origin.endswith(":")):
                raise ValueError
        except ValueError as error:
            raise AirzoneError("AIRZONE_URL : origine HTTPS attendue.") from error
        self.origin = origin

    def _request(self, method, path, change=None, *, budget=REQUEST_TIMEOUT):
        headers = {"Accept": "application/json"}
        if change is not None:
            headers.update({"Origin": self.origin, "X-Command-ID": str(uuid4())})
        deadline = time.monotonic() + budget
        with requests.request(method, self.origin + path, json=change, headers=headers,
                              timeout=(min(3, budget), min(8, budget)),
                              allow_redirects=False, stream=True) as response:
            if 300 <= response.status_code < 400:
                raise AirzoneError("Redirection Aizone refusée.")
            body = bytearray()
            for chunk in response.iter_content(4096):
                body.extend(chunk)
                if len(body) > MAX_BODY or time.monotonic() >= deadline:
                    raise AirzoneError("Réponse Aizone trop longue.")
            data = json.loads(body)
            if not isinstance(data, dict):
                raise AirzoneError("Réponse Aizone invalide.")
            return response.status_code, data

    def state(self, *, budget=REQUEST_TIMEOUT):
        try:
            status, state = self._request("GET", "/api/state", budget=budget)
            if status not in (200, 409, 503):
                raise AirzoneError("Lecture Aizone refusée.")
            return validate_state(state)
        except (requests.RequestException, UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise AirzoneError("Lecture Aizone indisponible.") from error

    def write(self, zone_id, field, value):
        if field == "mode" and zone_id is None:
            path = "/api/mode"
        elif field in {"setpoint", "on"} and type(zone_id) is int and 1 <= zone_id <= 32:
            path = f"/api/zones/{zone_id}"
        else:
            raise AirzoneError("Commande Airzone invalide.")
        try:
            status, receipt = self._request("POST", path, {field: value})
            outcome = receipt.get("outcome")
            if (outcome in {"rejected", "not_sent", "unconfirmed", "unknown"}
                    or status == 200 and outcome == "confirmed"
                    or status == 202 and outcome == "sent"):
                return receipt
        except (requests.RequestException, ValueError, TypeError, RecursionError):
            pass
        return {"outcome": "unknown"}

    def apply(self, zone_id, field, value):
        receipt = self.write(zone_id, field, value)
        if field != "setpoint" or receipt["outcome"] != "sent":
            return receipt
        baseline = observation(receipt)
        if baseline is None:
            return receipt
        deadline = time.monotonic() + 6
        for _ in range(2):
            time.sleep(1)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                state = self.state(budget=min(2, remaining))
            except AirzoneError:
                continue
            observed = observation(state)
            if state["demo"] or not fresh(state) or observed is None or observed <= baseline:
                continue
            zone = next((zone for zone in state["zones"] if zone["id"] == zone_id), {})
            if (zone.get("available") is True and "pending_setpoint" in zone
                    and zone["pending_setpoint"] is None and zone.get("setpoint") == value):
                return {"outcome": "confirmed"}
        return receipt
