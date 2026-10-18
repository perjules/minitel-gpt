#!/usr/bin/env bash
set -euo pipefail
PRINTER_PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$EUID" -ne 0 ]]; then
  echo 'Run with sudo on the Raspberry Pi.' >&2
  exit 1
fi
bash "$PRINTER_PROJECT/scripts/install_runtime.sh" --extra printer
printf '%s\n' 'Printer dependencies installed in the shared runtime; Bluetooth settings unchanged.'
