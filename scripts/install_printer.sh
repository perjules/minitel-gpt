#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv/bin/python. Activate or create the existing project virtual environment first." >&2
  exit 1
fi

.venv/bin/python -m pip install bleak pillow
echo "Optional printer dependencies installed in the existing .venv."
echo "Bluetooth settings were not changed."
