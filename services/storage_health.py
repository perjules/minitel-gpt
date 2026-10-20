"""Read-only storage measurements; archives and legacy logs are never deleted."""
import os
import shutil
import stat
from pathlib import Path


def tree_usage(root, limit=10000):
    root = Path(root)
    total = count = 0
    partial = root.is_symlink()

    def failed(_):
        nonlocal partial
        partial = True

    if not partial:
        for folder, _, files in os.walk(root, followlinks=False, onerror=failed):
            for name in files:
                count += 1
                if count > limit:
                    return {"bytes": total, "partial": True}
                try:
                    details = (Path(folder) / name).lstat()
                    if stat.S_ISREG(details.st_mode):
                        total += details.st_size
                except OSError:
                    partial = True
    return {"bytes": total, "partial": partial}


def status(project, memory=None):
    project = Path(project)
    usage = shutil.disk_usage(project)
    memory = Path(memory or os.getenv("MINITEL_MEMORY_DIR") or
                  Path.home() / ".local/share/3615-ia").expanduser()
    return {
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "warning": usage.free < 1024 ** 3 or usage.free / usage.total < 0.1,
        "legacy_logs_and_rose": tree_usage(project / "logs"),
        "rose_transcripts": tree_usage(project / "logs/rose"),
        "memory_and_conversations": tree_usage(memory),
    }
