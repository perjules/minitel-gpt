#!/usr/bin/env python3
"""Regenerate compatibility exports; --check compares without changing files."""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORTS = {
    "requirements-dev.txt": ["--all-extras", "--dev"],
    "requirements-gmail.txt": ["--extra", "gmail", "--no-dev"],
    "requirements-pronote.txt": ["--extra", "pronote", "--no-dev"],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for filename, options in EXPORTS.items():
        result = subprocess.run(
            ["uv", "export", "--locked", "--no-emit-project", "--no-header",
             "--no-annotate", *options],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        content = "# Generated from uv.lock; edit pyproject.toml, then run scripts/export_requirements.py.\n" + result.stdout
        path = ROOT / filename
        if args.check:
            if not path.exists() or path.read_text() != content:
                stale.append(filename)
        else:
            path.write_text(content)
    if stale:
        parser.exit(1, "Stale exports: " + ", ".join(stale) + "\n")


if __name__ == "__main__":
    main()
