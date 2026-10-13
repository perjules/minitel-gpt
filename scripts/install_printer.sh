#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  echo "Install uv first; no system Python packages were changed." >&2
  exit 1
fi
uv pip install --python .venv/bin/python bleak pillow
echo "Optional printer dependencies installed in .venv."
echo "Bluetooth BLE-only mode may be needed for MXW01 on this Pi."
echo "No Bluetooth controller settings were changed."
