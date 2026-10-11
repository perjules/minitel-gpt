#!/bin/bash
# Optional dependency installation and isolated interpreters for both services.
# Run on the Pi: sudo bash scripts/install_pronote.sh
set -euo pipefail
PRONOTE_PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$EUID" -ne 0 ]]; then
  echo 'Run with sudo on the Raspberry Pi.' >&2
  exit 1
fi
apt-get update
apt-get install -y python3-venv python3-dev build-essential
python3 -m venv --system-site-packages "$PRONOTE_PROJECT/.venv"
"$PRONOTE_PROJECT/.venv/bin/python" -m pip install -r "$PRONOTE_PROJECT/requirements-pronote.txt"
# Existing service units use the same project path, user and environment.
for PRONOTE_SERVICE in minitel-chatgpt admin-ui; do
  mkdir -p "/etc/systemd/system/$PRONOTE_SERVICE.service.d"
  cat > "/etc/systemd/system/$PRONOTE_SERVICE.service.d/pronote.conf" <<EOF
[Service]
ExecStart=
ExecStart=$PRONOTE_PROJECT/.venv/bin/python $PRONOTE_PROJECT/services/${PRONOTE_SERVICE//-/_}.py
EOF
done
chown -R minitel:minitel "$PRONOTE_PROJECT/.venv"
systemctl daemon-reload
systemctl restart minitel-chatgpt admin-ui
printf '%s\n' 'PRONOTE installed. Open /pronote in the existing admin interface.'
