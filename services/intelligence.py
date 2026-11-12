"""Machine-wide dial; locked local persistence, no UART/GPIO."""
import json
import logging
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config_store, mistral_settings, model_catalogue

LEVELS = (1, 3, 5, 7, 9, 11)
LABELS = {1: "ÉCONOMIQUE", 3: "SIMPLE", 5: "NORMAL", 7: "AVANCÉ", 9: "MAXIMUM", 11: "DÉBRIDÉ"}
ROOT = Path(__file__).resolve().parents[1] / "config"
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Profile:
    model: str
    reasoning_effort: str
    temperature: float

    def completion_args(self):
        return {"temperature": self.temperature,
                **({"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {})}


def defaults():
    return {level: Profile(model, effort, temperature) for level, model, effort, temperature in (
        (1, "mistral-small-2603", "none", .2),
        (3, "mistral-small-2603", "high", .2),
        (5, "mistral-medium-3-5", "none", .2),
        (7, "mistral-medium-3-5", "high", .2),
        (9, "mistral-large-4-0", "high", .2),
        (11, "mistral-large-4-0", "high", 1.1),
    )}


def valid_level(level):
    if type(level) is not int or level not in LEVELS:
        raise config_store.InvalidConfig("INTELLIGENCE : 1, 3, 5, 7, 9, 11")
    return level


def validate_profiles(data):
    if not isinstance(data, dict) or set(data) != {str(n) for n in LEVELS}:
        raise config_store.InvalidConfig("Les six profils Intelligence sont requis.")
    result = {}
    for level in LEVELS:
        row = data[str(level)]
        if not isinstance(row, dict) or set(row) != {"model", "reasoning_effort", "temperature"}:
            raise config_store.InvalidConfig(f"Profil {level} invalide.")
        model, effort, temperature = row["model"], row["reasoning_effort"], row["temperature"]
        if not model_catalogue.valid_id(model):
            raise config_store.InvalidConfig(f"Profil {level} : identifiant de modèle invalide.")
        if not isinstance(effort, str) or (effort and effort not in mistral_settings.reasoning_options(model)):
            raise config_store.InvalidConfig(f"Profil {level} : raisonnement non pris en charge pour ce modèle.")
        if (type(temperature) not in (int, float) or not 0 <= temperature <= 1.5
                or not math.isfinite(temperature)):
            raise config_store.InvalidConfig(f"Profil {level} : température entre 0 et 1.5 attendue.")
        result[level] = Profile(model, effort, float(temperature))
    return result


class Controller:
    """Adapters call set_level(int); resolve() returns an immutable request snapshot.

    No callbacks run on adapter threads. Only the terminal owner renders UART.
    Profiles and selection have distinct files; admin saves cannot reset the dial.
    """
    def __init__(self, directory=ROOT):
        self.profiles_path = Path(directory) / "intelligence-profiles.json"
        self.state_path = Path(directory) / "intelligence-state.json"

    def profiles(self):
        with config_store.locked(self.profiles_path):
            if not self.profiles_path.exists():
                return defaults()
            try:
                data = json.loads(config_store.read_bytes(self.profiles_path))
                if (not isinstance(data, dict) or type(data.get("version")) is not int
                        or data["version"] != 1):
                    raise ValueError
                return validate_profiles(data.get("profiles"))
            except (ValueError, UnicodeError):
                raise config_store.InvalidConfig("Configuration Intelligence invalide.") from None

    def save_profiles(self, data):
        profiles = validate_profiles(data)
        config_store.atomic_write(self.profiles_path, (json.dumps({"version": 1, "profiles": {
            str(n): asdict(p) for n, p in profiles.items()}}, indent=2) + "\n").encode())

    @property
    def level(self):
        with config_store.locked(self.state_path):
            if not self.state_path.exists():
                return 5
            try:
                data = json.loads(config_store.read_bytes(self.state_path))
                if (not isinstance(data, dict) or type(data.get("version")) is not int
                        or data["version"] != 1):
                    raise ValueError
                return valid_level(data.get("level"))
            except (ValueError, UnicodeError):
                raise config_store.InvalidConfig("Position Intelligence invalide.") from None

    def set_level(self, level):
        valid_level(level)
        config_store.atomic_write(self.state_path, (json.dumps({"version": 1, "level": level}) + "\n").encode())
        return level

    def resolve(self, *, with_level=False):
        level = self.level
        profile = self.profiles()[level]
        return (level, profile) if with_level else profile


controller = Controller()


def snapshot(*, with_level=False):
    """Optionally include the position from the same immutable selection."""
    try:
        return controller.resolve(with_level=with_level)
    except (config_store.InvalidConfig, config_store.StorageFailure, OSError):
        log.warning("intelligence configuration unavailable; using default profile 5")
        return (5, defaults()[5]) if with_level else defaults()[5]


def indicator():
    try:
        return f"I:{controller.level:02d}"
    except (config_store.InvalidConfig, config_store.StorageFailure, OSError):
        return "I:??"


def command(text):
    """None means not a command; otherwise a short local acknowledgement/error."""
    match = re.fullmatch(r"\s*INTELLIGENCE(?:\s+(.*?))?\s*", text or "", re.IGNORECASE)
    if match is None:
        return None
    argument = (match[1] or "").strip()
    try:
        if argument:
            if argument not in {str(n) for n in LEVELS}:
                raise config_store.InvalidConfig("INTELLIGENCE : 1, 3, 5, 7, 9, 11")
            controller.set_level(int(argument))
        level = controller.level
        return f"INTELLIGENCE : {level:02d} - " + ("ÇA CHAUFFE !" if level == 11 else LABELS[level])
    except config_store.InvalidConfig as error:
        return str(error)
    except (config_store.StorageFailure, OSError):
        return "INTELLIGENCE : sauvegarde impossible"
