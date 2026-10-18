#!/usr/bin/env bash
# Bootstrap only: OS Python constructs the first shared environment.
set -euo pipefail
RUNTIME_PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$EUID" -ne 0 ]]; then
  echo 'Run with sudo on the Raspberry Pi.' >&2
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python >=3.11 required"'
# Native wheels are scarce on 32-bit ARM; small C extensions may need compiling.
apt-get install -y python3-venv python3-dev build-essential libffi-dev curl ca-certificates
RUNTIME_UV_VERSION="$(cat "$RUNTIME_PROJECT/config/uv-version")"
if [[ ! -x /usr/local/bin/uv ]] || [[ "$(/usr/local/bin/uv --version | cut -d ' ' -f 2)" != "$RUNTIME_UV_VERSION" ]]; then
  RUNTIME_INSTALLER="$(mktemp)"
  trap 'rm -f "$RUNTIME_INSTALLER"' EXIT
  curl --fail --silent --show-error --location \
    "https://astral.sh/uv/$RUNTIME_UV_VERSION/install.sh" -o "$RUNTIME_INSTALLER"
  UV_UNMANAGED_INSTALL=/usr/local/bin \
    sh "$RUNTIME_INSTALLER"
  rm -f "$RUNTIME_INSTALLER"
  trap - EXIT
fi
# The worker survives an SSH disconnect when the Wi-Fi service is restarted.
echo 'Preparing shared runtime; follow journalctl -u minitel-runtime-install.'
exec systemd-run --wait --collect --unit=minitel-runtime-install \
  /usr/bin/python3 "$RUNTIME_PROJECT/scripts/runtime.py" install "$@"
