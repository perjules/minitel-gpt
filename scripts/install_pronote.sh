#!/usr/bin/env bash
# Install the pinned PRONOTE extra in the same runtime as every other service.
set -euo pipefail
PRONOTE_PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
bash "$PRONOTE_PROJECT/scripts/install_runtime.sh" --extra pronote
printf '%s\n' 'PRONOTE dependencies installed. Enable the integration in /pronote.'
