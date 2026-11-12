"""Small local inventory of homes and their configured automation controllers."""
import json
import os
import re
import unicodedata
from pathlib import Path

from dotenv import dotenv_values

from . import airzone_client, config_store

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "homes.json"


def normalized(value):
    return " ".join(unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode().split())


def mentions(text, name):
    return bool(re.search(r"(?<!\w)" + re.escape(normalized(name)) + r"(?!\w)", normalized(text)))


def text(value, title):
    if (not isinstance(value, str) or not value.strip() or len(value) > 80
            or any(not char.isprintable() for char in value)):
        raise config_store.InvalidConfig(f"{title} : texte de 1 à 80 caractères attendu.")
    return value.strip()


def validate(data):
    if (not isinstance(data, dict) or data.get("version") != 1
            or type(data.get("revision")) is not int or data["revision"] < 0
            or type(data.get("enabled")) is not bool or not isinstance(data.get("homes"), dict)
            or len(data["homes"]) > 20):
        raise config_store.InvalidConfig("Configuration des maisons invalide.")
    names = set()
    for home_id, home in data["homes"].items():
        if not isinstance(home_id, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}", home_id):
            raise config_store.InvalidConfig("Identifiant de maison invalide.")
        if (not isinstance(home, dict) or type(home.get("enabled")) is not bool
                or not isinstance(home.get("aliases"), list) or len(home["aliases"]) > 10
                or not isinstance(home.get("targets"), dict) or len(home["targets"]) > 10):
            raise config_store.InvalidConfig("Maison invalide.")
        home["name"] = text(home.get("name"), "Nom de maison")
        home["aliases"] = [text(alias, "Alias") for alias in home["aliases"]]
        for name in [home["name"], *home["aliases"]]:
            key = normalized(name)
            if not key or key in names:
                raise config_store.InvalidConfig("Chaque nom et alias de maison doit être unique.")
            names.add(key)
        target_names = set()
        for target_id, target in home["targets"].items():
            if not isinstance(target_id, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}", target_id):
                raise config_store.InvalidConfig("Identifiant de contrôleur invalide.")
            if (not isinstance(target, dict) or target.get("kind") != "airzone"
                    or type(target.get("enabled")) is not bool):
                raise config_store.InvalidConfig("Type de contrôleur attendu : Airzone.")
            target["name"] = text(target.get("name"), "Nom de contrôleur")
            key = normalized(target["name"])
            if key in target_names:
                raise config_store.InvalidConfig("Les contrôleurs d'une maison doivent avoir des noms distincts.")
            target_names.add(key)
            if not isinstance(target.get("url"), str):
                raise config_store.InvalidConfig("URL du contrôleur invalide.")
            target["url"] = target["url"].strip()
            try:
                airzone_client.Client(target["url"])
            except airzone_client.AirzoneError as error:
                raise config_store.InvalidConfig(str(error)) from error
    return data


def load(env=None):
    if CONFIG.exists() or CONFIG.is_symlink():
        try:
            return validate(json.loads(config_store.read_bytes(CONFIG)))
        except (ValueError, UnicodeError, OSError) as error:
            raise config_store.InvalidConfig("Configuration des maisons illisible ou invalide.") from error
    values = {**os.environ, **env} if env is not None else {**dotenv_values(ROOT / ".env"), **os.environ}
    url = (values.get("AIRZONE_URL") or "").strip()
    homes = {}
    if url:
        homes["legacy-home"] = {"name": "Maison", "aliases": [], "enabled": True, "targets": {
            "legacy-airzone": {"kind": "airzone", "name": "Climatisation", "url": url, "enabled": True}}}
    return validate({"version": 1, "revision": 0, "enabled": bool(url), "homes": homes})


def update(change, *, revision=None, env=None):
    with config_store.locked(CONFIG):
        data = load(env)
        if revision is not None and data["revision"] != revision:
            raise config_store.InvalidConfig("Configuration modifiée ailleurs. Rechargez la page.")
        change(data)
        validate(data)
        data["revision"] += 1
        config_store.atomic_write(CONFIG, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode(), mode=0o600)
        return data


def targets(data=None):
    data = load() if data is None else data
    if not data["enabled"]:
        return []
    return [{"home_id": home_id, "target_id": target_id, "home": home["name"], "name": target["name"], "url": target["url"]}
            for home_id, home in data["homes"].items() if home["enabled"]
            for target_id, target in home["targets"].items() if target["enabled"] and target["kind"] == "airzone"]


def enabled():
    try:
        return bool(targets())
    except (config_store.InvalidConfig, config_store.StorageFailure, OSError):
        return False


def resolve(question):
    data = load()
    available = targets(data)
    if not available:
        raise airzone_client.AirzoneError("Climatisation désactivée ou aucun contrôleur actif.")
    homes = [(key, home) for key, home in data["homes"].items()
             if any(mentions(question, name) for name in [home["name"], *home["aliases"]])]
    if len(homes) > 1:
        raise airzone_client.AirzoneError("Choisissez une seule maison.")
    if homes:
        available = [target for target in available if target["home_id"] == homes[0][0]]
        if not available:
            raise airzone_client.AirzoneError("Climatisation désactivée pour cette maison.")
    elif len({target["home_id"] for target in available}) > 1:
        raise airzone_client.AirzoneError("Précisez la maison : " + ", ".join(dict.fromkeys(t["home"] for t in available)))
    matched = [target for target in available if mentions(question, target["name"])]
    if len(matched) == 1:
        return matched[0]
    if len(available) != 1:
        raise airzone_client.AirzoneError("Précisez le contrôleur : " + ", ".join(t["name"] for t in available))
    return available[0]


def authorize(home_id, target_id, url):
    target = next((item for item in targets() if item["home_id"] == home_id and item["target_id"] == target_id), None)
    if target is None or target["url"] != url:
        raise airzone_client.AirzoneError("Contrôleur désactivé ou modifié. Relancez votre demande.")
    return target
