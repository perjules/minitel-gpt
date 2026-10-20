#!/usr/bin/env python3
"""Root-managed shared uv runtime. See docs/RUNTIME.md for deployment semantics."""
import argparse
import fcntl
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

PROJECT = Path("/home/minitel/minitel-gpt")
RUNTIME = Path("/opt/minitel-gpt")
STATE = Path("/var/lib/minitel-gpt")
UV = Path("/usr/local/bin/uv")
EXTRAS = {"gmail", "pronote", "printer"}
SERVICES = ("minitel-chatgpt", "admin-ui", "wifi-manager")
ROOT_FILES = {
    "runtime.py": "scripts/runtime.py",
    "wifi_manager.py": "services/wifi_manager.py",
    "gmail_credential_helper.py": "scripts/gmail_credential_helper.py",
    "gmail_auth.py": "services/gmail_auth.py",
    "journal_reader.py": "scripts/journal_reader.py",
}
SYSTEM_FILES = {
    "journald-minitel-gpt.conf": "/etc/systemd/journald@minitel-gpt.conf.d/60-minitel-gpt.conf",
    "minitel-gpt-journal-sudoers": "/etc/sudoers.d/minitel-gpt-journal",
    "minitel-housekeeping.service": "/etc/systemd/system/minitel-housekeeping.service",
    "minitel-housekeeping.timer": "/etc/systemd/system/minitel-housekeeping.timer",
}
MIN_STAGE_FREE = 1024 ** 3



def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o644)
    temporary.replace(path)


def point(link, target):
    temporary = link.with_name(link.name + ".tmp")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(link)


class Runtime:
    def __init__(self, project=PROJECT, root=RUNTIME, state=STATE, uv=UV, system=Path("/")):
        self.project, self.root, self.state, self.uv = map(Path, (project, root, state, uv))
        self.system = Path(system)

    def system_path(self, path):
        return self.system / path.lstrip("/")

    def initialize(self):
        for folder in (self.root, self.state):
            folder.mkdir(parents=True, exist_ok=True)
            # Check parents too: a writable parent permits replacing a protected folder.
            for path in (folder, *folder.parents):
                details = path.lstat()
                if path.is_symlink() or details.st_uid != 0 or details.st_mode & 0o022:
                    raise RuntimeError(f"Root-owned, non-writable runtime path required: {path}")
        executable = self.uv.resolve(strict=True)
        for path in (executable, *executable.parents):
            details = path.stat()
            if details.st_uid != 0 or details.st_mode & 0o022:
                raise RuntimeError("The installed uv executable and its parents must be root-owned")

    def command(self, args, **kwargs):
        return subprocess.run([str(arg) for arg in args], check=True, **kwargs)

    def git(self, *args):
        # Keep Git credentials/config and checkout ownership with the original user.
        return self.command(
            ["runuser", "-u", "minitel", "--", "git", "-C", self.project, *args],
            capture_output=True, text=True,
        ).stdout.strip()

    def read(self):
        path = self.state / "runtime.json"
        return json.loads(path.read_text()) if path.exists() else {}

    @contextmanager
    def locked(self):
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "runtime.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def status(self, phase, **values):
        atomic_json(self.state / "deployment.json", {"phase": phase, "time": int(time.time()), **values})

    def python_version(self):
        return self.command(["/usr/bin/python3", "-I", "-c", "import sys; print(sys.version)"],
                            capture_output=True, text=True).stdout.strip()

    def probe(self, environment, extras):
        modules = ["flask", "requests", "serial", "dotenv", "pyfiglet"]
        for extra, names in {"gmail": ["google.auth", "google_auth_oauthlib", "cryptography"],
                             "pronote": ["pronotepy"], "printer": ["bleak", "PIL"]}.items():
            if extra in extras:
                modules.extend(names)
        code = "; ".join("import " + module for module in modules)
        code += "; assert pyfiglet.figlet_format('IA', font='digital').strip()"
        self.command([environment / "bin/python", "-I", "-c", code])

    def selected_extras(self, requested, old):
        selected = set(old.get("active", {}).get("extras", [])) | set(requested)
        if selected - EXTRAS:
            raise ValueError("Unknown optional feature")
        # First migration: preserve features installed by the old independent scripts.
        legacy = self.project / ".venv/bin/python"
        if not old and legacy.exists():
            probe = (
                "import importlib.util,json; "
                "print(json.dumps([extra for extra,module in "
                "[('gmail','google_auth_oauthlib'),('pronote','pronotepy'),('printer','bleak')] "
                "if importlib.util.find_spec(module)]))"
            )
            result = self.command(["runuser", "-u", "minitel", "--", legacy, "-c", probe],
                                  capture_output=True, text=True)
            selected.update(json.loads(result.stdout))
        return sorted(selected)

    def prepare(self, source, extras, commit):
        """Create at the final path: virtual environments cannot safely be relocated."""
        self.check_space()
        release = self.root / "releases" / uuid.uuid4().hex
        release.mkdir(parents=True, mode=0o755)
        try:
            python_version = self.python_version()
            metadata = release / "project"
            metadata.mkdir()
            for name in ("pyproject.toml", "uv.lock"):
                shutil.copyfile(source / name, metadata / name)
            protected = release / "root"
            protected.mkdir()
            for name, relative in ROOT_FILES.items():
                shutil.copyfile(source / relative, protected / name)
                compile((protected / name).read_bytes(), str(protected / name), "exec")
            for service in SERVICES:
                shutil.copyfile(source / f"config/{service}.service", protected / f"{service}.service")
            for name in SYSTEM_FILES:
                shutil.copyfile(source / "config" / name, protected / name)
            environment = release / "venv"
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/root",
                "UV_PROJECT_ENVIRONMENT": str(environment),
                "UV_CACHE_DIR": str(self.state / "uv-cache"),
                "UV_PYTHON_DOWNLOADS": "never", "UV_NO_CONFIG": "1",
                "UV_CONCURRENT_BUILDS": "1", "CARGO_BUILD_JOBS": "1",
            }
            # Support standard enterprise/network proxies, not arbitrary uv/Python settings.
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                         "http_proxy", "https_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE"):
                if name in os.environ:
                    env[name] = os.environ[name]
            command = [self.uv, "sync", "--project", metadata, "--locked", "--no-dev",
                       "--no-sources", "--no-build-package", "cryptography", "--python", "/usr/bin/python3"]
            for extra in extras:
                command.extend(["--extra", extra])
            self.command(command, env=env)
            self.probe(environment, extras)
            return {"release": str(release), "commit": commit, "extras": extras, "python": python_version}
        except Exception:
            shutil.rmtree(release)
            raise

    def activate(self, release):
        point(self.root / "current", Path(release["release"]))
        point(self.root / "venv", self.root / "current/venv")

    def wrappers(self):
        paths = {
            self.system_path("/usr/local/sbin/minitel-runtime"): "runtime.py",
            self.system_path("/usr/local/libexec/minitel-gmail-credential"): "gmail_credential_helper.py",
            self.system_path("/usr/local/libexec/minitel-journal"): "journal_reader.py",
        }
        for path, script in paths.items():
            if script == "journal_reader.py" and not (self.root / "current/root" / script).exists():
                path.unlink(missing_ok=True)  # Compatibility with retained pre-journal releases.
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "#!/bin/sh\nexec /opt/minitel-gpt/venv/bin/python -I "
                f"/opt/minitel-gpt/current/root/{script} \"$@\"\n"
            )
            path.chmod(0o755)

    def units(self):
        for name, relative in SYSTEM_FILES.items():
            source = self.root / "current/root" / name
            destination = self.system_path(relative)
            if not source.exists():
                destination.unlink(missing_ok=True)
                continue
            if name == "minitel-gpt-journal-sudoers":
                self.command(["visudo", "-c", "-f", source])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            destination.chmod(0o440 if name.endswith("sudoers") else 0o644)
        for service in SERVICES:
            destination = self.system_path(f"/etc/systemd/system/{service}.service")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / f"current/root/{service}.service",
                            destination)
        # Gmail retains its credential settings; stale interpreter overrides go away.
        gmail = """[Unit]
After=systemd-tmpfiles-setup.service
[Service]
Environment=GMAIL_ENABLED=1
LoadCredentialEncrypted=gmail:/etc/credstore.encrypted/minitel-gmail
PrivateMounts=yes
"""
        for service in SERVICES[:2]:
            folder = self.system_path(f"/etc/systemd/system/{service}.service.d")
            (folder / "pronote.conf").unlink(missing_ok=True)
            if (folder / "gmail.conf").exists():
                (folder / "gmail.conf").write_text(gmail)
        self.command(["systemctl", "daemon-reload"])
        self.command(["systemctl", "try-restart", "systemd-journald@minitel-gpt.service"])

    def compatibility_link(self):
        legacy = self.project / ".venv"
        if legacy.exists() and not legacy.is_symlink():
            # Keep the original environment in place as an explicit migration backup.
            backup = self.project / ".venv.before-uv"
            if backup.exists():
                raise RuntimeError("Existing .venv.before-uv; resolve the previous migration backup first")
            legacy.rename(backup)
        point(legacy, self.root / "venv")
        owner = pwd.getpwnam("minitel")
        os.lchown(legacy, owner.pw_uid, owner.pw_gid)

    def running(self):
        return [service for service in SERVICES if subprocess.run(
            ["systemctl", "is-active", "--quiet", service], check=False,
        ).returncode == 0]

    def switch(self, candidate, old, *, branch, target=None):
        running = self.running()
        old_commit = old.get("active", {}).get("commit") or self.git("rev-parse", "HEAD")
        paths = [self.system_path(f"/etc/systemd/system/{service}.service") for service in SERVICES]
        paths += [self.system_path(f"/etc/systemd/system/{service}.service.d/{name}.conf")
                  for service in SERVICES[:2] for name in ("gmail", "pronote")]
        paths += [self.system_path("/usr/local/sbin/minitel-runtime"),
                  self.system_path("/usr/local/libexec/minitel-gmail-credential"),
                  self.system_path("/usr/local/libexec/minitel-journal")]
        paths += [self.system_path(relative) for relative in SYSTEM_FILES.values()]
        saved = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None for path in paths}
        previous_pointer = (self.root / "current").readlink() if (self.root / "current").is_symlink() else None
        previous_link = (self.project / ".venv").readlink() if (self.project / ".venv").is_symlink() else None
        try:
            if running:
                self.command(["systemctl", "stop", *running])
            if target:
                self.git("reset", "--hard", target)
            self.activate(candidate)
            self.wrappers()
            self.units()
            self.compatibility_link()
            if running:
                self.command(["systemctl", "start", *running])
                # With multiple units is-active succeeds if *any* is active.
                for service in running:
                    self.command(["systemctl", "is-active", "--quiet", service])
            atomic_json(self.state / "runtime.json", {
                "version": 1, "branch": branch, "active": candidate, "previous": old.get("active"),
            })
        except Exception:
            if target or old.get("active"):
                self.git("reset", "--hard", old_commit)
            if previous_pointer:
                point(self.root / "current", previous_pointer)
            else:
                (self.root / "current").unlink(missing_ok=True)
                (self.root / "venv").unlink(missing_ok=True)
            for path, original in saved.items():
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original[0])
                    path.chmod(original[1])
            legacy = self.project / ".venv"
            if legacy.is_symlink():
                legacy.unlink()
                if previous_link:
                    legacy.symlink_to(previous_link)
                elif (self.project / ".venv.before-uv").exists():
                    (self.project / ".venv.before-uv").rename(legacy)
            self.command(["systemctl", "daemon-reload"])
            self.command(["systemctl", "try-restart", "systemd-journald@minitel-gpt.service"])
            if running:
                self.command(["systemctl", "restart", *running])
            raise

    def snapshot(self, commit, source):
        """Read only required tracked files; never extract an untrusted archive as root."""
        files = ["pyproject.toml", "uv.lock", *ROOT_FILES.values()]
        files += [f"config/{service}.service" for service in SERVICES]
        files += [f"config/{name}" for name in SYSTEM_FILES]
        for relative in files:
            result = self.command(
                ["runuser", "-u", "minitel", "--", "git", "-C", self.project,
                 "show", f"{commit}:{relative}"], capture_output=True,
            )
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(result.stdout)

    def install(self, requested, branch):
        old = self.read()
        if self.git("status", "--porcelain", "--untracked-files=no"):
            raise RuntimeError("Commit or stash tracked changes before installing")
        branch = branch or old.get("branch") or self.git("branch", "--show-current")
        self.git("check-ref-format", "--branch", branch)
        if self.git("branch", "--show-current") != branch:
            raise RuntimeError("Switch the checkout to the selected branch, or pass its current branch with --branch")
        commit = self.git("rev-parse", "HEAD")
        extras = self.selected_extras(requested, old)
        # Legacy detection and explicit --extra must provision the same native prerequisites.
        if "printer" in extras:
            self.command(["apt-get", "install", "-y", "bluez", "fonts-dejavu-core", "libjpeg-dev", "zlib1g-dev"])
        active = old.get("active", {})
        reusable = active.get("commit") == commit and active["extras"] == extras and active.get("python") == self.python_version()
        if reusable:
            try:
                self.probe(Path(active["release"]) / "venv", extras)
            except (OSError, subprocess.SubprocessError):
                reusable = False
        if reusable:
            self.wrappers()
            self.units()
            self.compatibility_link()
            old["branch"] = branch
            atomic_json(self.state / "runtime.json", old)
            self.status("ready", commit=commit)
            self.maintain()
            return
        with tempfile.TemporaryDirectory(prefix="minitel-install-") as directory:
            source = Path(directory)
            self.snapshot(commit, source)
            candidate = self.prepare(source, extras, commit)
        self.switch(candidate, old, branch=branch)
        self.status("ready", commit=candidate["commit"])
        self.maintain()

    def update(self):
        old = self.read()
        if not old.get("active"):
            raise RuntimeError("Run install.sh first")
        branch = old["branch"]
        if self.git("branch", "--show-current") != branch:
            raise RuntimeError("Checkout branch differs from the installed update branch")
        if self.git("status", "--porcelain", "--untracked-files=no"):
            raise RuntimeError("Commit or stash tracked changes before updating")
        self.git("fetch", "origin", branch)
        target = self.git("rev-parse", f"origin/{branch}")
        # Refuse to discard local commits or cross a divergent branch.
        self.git("merge-base", "--is-ancestor", "HEAD", target)
        if target == old["active"]["commit"]:
            self.status("ready", commit=target)
            self.maintain()
            return
        with tempfile.TemporaryDirectory(prefix="minitel-update-") as directory:
            source = Path(directory)
            self.snapshot(target, source)
            candidate = self.prepare(source, old["active"]["extras"], target)
        self.status("activating", commit=target)
        self.switch(candidate, old, branch=branch, target=target)
        self.status("ready", commit=target)
        self.maintain()

    def rollback(self):
        old = self.read()
        previous = old.get("previous")
        if not previous or not Path(previous["release"], "venv/bin/python").exists():
            raise RuntimeError("No retained code/environment pair to restore")
        if self.git("status", "--porcelain", "--untracked-files=no"):
            raise RuntimeError("Commit or stash tracked changes before rolling back")
        self.switch(previous, old, branch=old["branch"], target=previous["commit"])
        self.status("ready", commit=previous["commit"])
        self.maintain()

    def check_space(self):
        for folder in (self.root, self.state):
            if shutil.disk_usage(folder).free < MIN_STAGE_FREE:
                raise RuntimeError("At least 1 GiB free is required before staging; free space manually")

    def housekeeping(self):
        """Only disposable runtime/cache storage; never user files or legacy logs."""
        state = self.read()
        active = state.get("active")
        if not active:
            return
        releases = self.root / "releases"
        keep = set()
        for entry in (active, state.get("previous")):
            if entry:
                path = Path(entry["release"])
                if path.parent != releases or path.is_symlink() or not path.is_dir():
                    raise RuntimeError("Invalid retained release; refusing housekeeping")
                keep.add(path)
        current = self.root / "current"
        if not current.is_symlink() or current.resolve() != Path(active["release"]):
            raise RuntimeError("Runtime pointer and state differ; refusing housekeeping")
        for path in releases.iterdir():
            if (path not in keep and re.fullmatch(r"[0-9a-f]{32}", path.name)
                    and not path.is_symlink() and path.is_dir()):
                shutil.rmtree(path)
        cache = self.state / "uv-cache"
        if cache.is_symlink():
            raise RuntimeError("Unexpected cache symlink; refusing cache cleanup")
        if cache.exists():
            self.command([self.uv, "cache", "prune", "--cache-dir", cache],
                         timeout=120, env={"PATH": "/usr/local/bin:/usr/bin:/bin",
                                           "HOME": "/root", "UV_NO_CONFIG": "1",
                                           "UV_PYTHON_DOWNLOADS": "never"})
        print("Runtime housekeeping complete; active/previous releases and user data retained.")

    def maintain(self):
        """Post-commit maintenance failures must never undo a successful deployment."""
        try:
            timer = self.system_path("/etc/systemd/system/minitel-housekeeping.timer")
            if timer.exists():
                self.command(["systemctl", "enable", "--now", "minitel-housekeeping.timer"])
            else:
                self.command(["systemctl", "disable", "--now", "minitel-housekeeping.timer"])
            self.housekeeping()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyError, TypeError) as error:
            print(f"Runtime maintenance deferred ({type(error).__name__}); inspect the application journal.",
                  file=sys.stderr)

    def queue(self, operation):
        if not self.read().get("active"):
            raise RuntimeError("Run install.sh first")
        self.command([
            "systemd-run", "--quiet", "--collect", "--unit=minitel-deploy", "--on-active=3s",
            "/usr/local/sbin/minitel-runtime", "apply-" + operation,
        ])
        self.status("queued", operation=operation)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "update", "rollback", "apply-update", "apply-rollback", "housekeeping"))
    parser.add_argument("--extra", action="append", choices=sorted(EXTRAS), default=[])
    parser.add_argument("--branch")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(1, "Run with sudo.\n")
    if args.operation != "install" and (args.extra or args.branch):
        parser.error("Feature/branch options are for installation only")
    os.umask(0o022)
    manager = Runtime()
    initialized = False
    try:
        manager.initialize()
        initialized = True
        with manager.locked():
            if args.operation in {"update", "rollback"}:
                manager.queue(args.operation)
            elif args.operation == "install":
                manager.install(args.extra, args.branch)
            elif args.operation == "housekeeping":
                manager.housekeeping()
            else:
                manager.status("preparing", operation=args.operation)
                getattr(manager, args.operation.removeprefix("apply-"))()
        return 0
    except BlockingIOError:
        print("Another deployment is running.", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, SyntaxError, KeyError, TypeError) as error:
        if initialized and args.operation not in {"update", "rollback", "housekeeping"}:
            manager.status("failed", error_type=type(error).__name__)
        message = str(error) if isinstance(error, RuntimeError) else "Inspect journalctl -u minitel-deploy -u minitel-runtime-install."
        print(f"Deployment failed ({type(error).__name__}): {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
