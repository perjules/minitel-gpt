"""Bounded, locked and durable configuration writes; no plaintext backup files."""
import fcntl
import io
import json
import os
import re
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from dotenv.parser import parse_stream

MAX_CONFIG_BYTES = 2 * 1024 * 1024
_LOCK = threading.RLock()
_LOCAL = threading.local()


class InvalidConfig(ValueError):
    pass


class StorageFailure(RuntimeError):
    pass


@contextmanager
def locked(path, timeout=5):
    """Reentrant within a thread; coordinates other threads and processes."""
    key = str(Path(path).absolute())
    if not _LOCK.acquire(timeout=timeout):
        raise StorageFailure("Configuration busy")
    active = getattr(_LOCAL, "active", set())
    _LOCAL.active = active
    descriptor = None
    try:
        if key not in active:
            lock = Path(key + ".lock")
            lock.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise StorageFailure("Configuration busy") from None
                    time.sleep(.01)
            active.add(key)
        yield
    except OSError:
        raise StorageFailure("Configuration storage unavailable") from None
    finally:
        if descriptor is not None:
            active.discard(key)
            os.close(descriptor)
        _LOCK.release()


def atomic_write(path, content, mode=None):
    path = Path(path)
    if len(content) > MAX_CONFIG_BYTES:
        raise InvalidConfig("Configuration exceeds 2 MiB")
    with locked(path):
        if path.is_symlink():
            raise InvalidConfig("Configuration symlinks are not writable")
        permissions = mode if mode is not None else stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
        descriptor, name = tempfile.mkstemp(prefix=".minitel-config-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), permissions)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(folder)
            finally:
                os.close(folder)
        finally:
            temporary.unlink(missing_ok=True)


def read_bytes(path):
    path = Path(path)
    with locked(path):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InvalidConfig("Regular configuration file required")
            data = stream.read(MAX_CONFIG_BYTES + 1)
        if len(data) > MAX_CONFIG_BYTES:
            raise InvalidConfig("Configuration exceeds 2 MiB")
        return data


def validate_prompts(data):
    if (not isinstance(data, dict) or not isinstance(data.get("presets"), dict)
            or not data["presets"] or not isinstance(data.get("active"), str)
            or data["active"] not in data["presets"]):
        raise InvalidConfig("Invalid personality configuration")
    for key, preset in data["presets"].items():
        if (not isinstance(key, str) or not key or key in {".", ".."}
                or len(key) > 128 or any(c in key for c in "/\\\x00")
                or not isinstance(preset, dict)):
            raise InvalidConfig("Invalid personality identifier")
        if "title_art" in preset and (not isinstance(preset["title_art"], list)
                or any(not isinstance(segment, dict)
                       or not isinstance(segment.get("text", ""), str)
                       or not isinstance(segment.get("font", "standard"), str)
                       for segment in preset["title_art"])):
            raise InvalidConfig("Invalid title artwork")
        for name in ("system", "label", "title_msg", "question_msg", "loading_msg"):
            if name in preset and not isinstance(preset[name], str):
                raise InvalidConfig("Invalid personality text")
    return data


def read_prompts(path):
    try:
        return validate_prompts(json.loads(read_bytes(path)))
    except (ValueError, UnicodeError):
        raise InvalidConfig("Personality configuration is invalid; restore a valid copy") from None


def save_prompts(path, data):
    validate_prompts(data)
    atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def update_env(path, updates):
    for key, value in updates.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or not isinstance(value, str)
                or any(ord(c) < 32 or ord(c) == 127 or c in "\x85\u2028\u2029" for c in value)):
            raise InvalidConfig("Environment values must be single-line text")
    path = Path(path)
    def assignment(key):
        value = updates[key]
        if not re.fullmatch(r"[A-Za-z0-9_./:+,=@%-]*", value):
            value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return f"{key}={value}\n"

    with locked(path):
        try:
            original = read_bytes(path).decode() if path.exists() else ""
        except UnicodeError:
            raise InvalidConfig("Invalid environment encoding") from None
        bindings = list(parse_stream(io.StringIO(original)))
        if any(binding.error for binding in bindings):
            raise InvalidConfig("Environment configuration is invalid; restore a valid copy")
        result, seen = [], set()
        for binding in bindings:
            key = binding.key
            if key in updates:
                if key not in seen:
                    result.append(assignment(key))
                    seen.add(key)
            else:
                result.append(binding.original.string)
        if result and not result[-1].endswith("\n"):
            result.append("\n")
        result.extend(assignment(key) for key in updates if key not in seen)
        atomic_write(path, "".join(result).encode(), mode=0o600)
