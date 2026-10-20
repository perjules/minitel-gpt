"""Fixed read-only journal selections for the authenticated admin log viewer."""
import argparse
import os
import subprocess
import sys

UNITS = {"chatgpt": "minitel-chatgpt", "wifi": "wifi-manager", "admin": "admin-ui"}


def read(name):
    return subprocess.run(
        ["/usr/bin/journalctl", "--namespace=minitel-gpt", "--unit=" + UNITS[name],
         "--lines=200", "--no-pager", "--output=short-iso"],
        capture_output=True, text=True, check=True, timeout=5,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8", "SYSTEMD_PAGER": "/bin/cat"},
    ).stdout[-65536:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=sorted(UNITS))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(1, "Run through the installed sudo rule.\n")
    try:
        sys.stdout.write(read(args.service))
    except (OSError, subprocess.SubprocessError, UnicodeError):
        parser.exit(1, "Application journal unavailable.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
