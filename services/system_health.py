"""Small, read-only OS probes used when the administration page is opened."""
import json
import shutil
import subprocess
from pathlib import Path

UNAVAILABLE = "indisponible"


def command(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=3)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return ""


def uptime():
    try:
        seconds = float(Path("/proc/uptime").read_text().split()[0])
        minutes = int(seconds) // 60
        days, hours = divmod(minutes // 60, 24)
        return f"{days} j {hours} h {minutes % 60} min"
    except (OSError, ValueError, IndexError):
        return UNAVAILABLE


def disk(project):
    try:
        usage = shutil.disk_usage(project)
        return {"free_gib": round(usage.free / 1024**3, 1),
                "total_gib": round(usage.total / 1024**3, 1),
                "used_percent": round(usage.used / usage.total * 100),
                "warning": usage.free < 1024**3 or usage.free / usage.total < .1}
    except (OSError, ZeroDivisionError):
        return None


def wifi():
    output = command("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status")
    connections = []
    for line in output.splitlines():
        fields = line.split(":", 3)
        if len(fields) == 4 and fields[1] == "wifi":
            device, _, state, connection = fields
            connection = connection.replace(r"\:", ":").replace("\\\\", "\\")
            connections.append(f"{connection or '—'} ({device}, {state})")
    return " ; ".join(connections) or UNAVAILABLE


def tailscale():
    # A missing command is reported the same way as any other unavailable probe.
    try:
        status = json.loads(command("tailscale", "status", "--json"))
        state = status.get("BackendState")
        if not isinstance(state, str):
            return UNAVAILABLE
        addresses = status.get("TailscaleIPs", [])
        ips = ", ".join(ip for ip in addresses if isinstance(ip, str))
        return state + (f" · {ips}" if ips else "")
    except (ValueError, TypeError, AttributeError):
        return UNAVAILABLE


def deployment(path):
    try:
        status = json.loads(Path(path).read_text())
        if isinstance(status, dict) and isinstance(status.get("phase"), str):
            return status
    except (OSError, ValueError):
        pass
    return {"phase": "indisponible"}


def temperature():
    try:
        value = float(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
        return round(value, 1) if -20 <= value <= 150 else None
    except (OSError, ValueError):
        return None


def local_ip():
    for line in command('ip', '-o', '-4', 'address', 'show', 'scope', 'global').splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1].startswith(('wl', 'eth', 'en')):
            return fields[3].split('/')[0]
    return UNAVAILABLE
