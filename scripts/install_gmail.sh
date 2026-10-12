#!/bin/bash
# Optional Gmail setup. No credential or key is ever written into the checkout.
set -euo pipefail
GMAIL_PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$EUID" -ne 0 ]]; then
  echo 'Run with sudo on the Pi.' >&2
  exit 1
fi
if [[ "${1:-}" != '' && "${1:-}" != '--activate' ]] || [[ "$#" -gt 1 ]]; then
  echo 'Usage: sudo bash scripts/install_gmail.sh [--activate]' >&2
  exit 1
fi
python3 - "$GMAIL_PROJECT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from services.gmail_auth import GmailError, systemd_preflight
if sys.version_info < (3, 10):
    sys.exit('Gmail requires Python >=3.10. Upgrade the Pi OS first; no plaintext fallback.')
try:
    print(systemd_preflight())
except GmailError:
    sys.exit('Running systemd/systemd-creds >=250 required. Upgrade OS first; no plaintext fallback.')
PY
apt-get update
apt-get install -y python3-venv python3-cryptography
# Debian's ARM package avoids a Rust build on the Pi Zero; Google requires >=38.
python3 - <<'PY'
import cryptography
if int(cryptography.__version__.split('.')[0]) < 38:
    raise SystemExit('Upgrade OS: python3-cryptography >=38 required.')
PY
if [[ ! -x "$GMAIL_PROJECT/.venv/bin/python" ]]; then
  python3 -m venv --system-site-packages "$GMAIL_PROJECT/.venv"
fi
"$GMAIL_PROJECT/.venv/bin/python" -m pip install -r "$GMAIL_PROJECT/requirements-gmail.txt"
chown -R minitel:minitel "$GMAIL_PROJECT/.venv"
install -d -m 0755 /usr/local/lib/minitel-gmail /usr/local/libexec
install -o root -g root -m 0644 "$GMAIL_PROJECT/services/gmail_auth.py" /usr/local/lib/minitel-gmail/gmail_auth.py
install -o root -g root -m 0755 "$GMAIL_PROJECT/scripts/gmail_credential_helper.py" /usr/local/libexec/minitel-gmail-credential
install -d -o root -g root -m 0700 /etc/minitel-gmail /etc/credstore.encrypted
# Restrict sudo to this root-owned, zero-argument helper; token values travel via pipes.
printf '%s\n' 'minitel ALL=(root) NOPASSWD: /usr/local/libexec/minitel-gmail-credential ""' > /etc/sudoers.d/minitel-gmail
chmod 0440 /etc/sudoers.d/minitel-gmail
visudo -cf /etc/sudoers.d/minitel-gmail
printf '%s\n' 'd /run/minitel-gmail 0700 minitel minitel -' > /etc/tmpfiles.d/minitel-gmail.conf
systemd-tmpfiles --create /etc/tmpfiles.d/minitel-gmail.conf
if [[ "${1:-}" != '--activate' ]]; then
  echo 'Dependencies installed. Enroll using scripts/gmail_enroll.py, then rerun with --activate.'
  exit 0
fi
if [[ ! -f /etc/credstore.encrypted/minitel-gmail ]]; then
  echo 'Encrypted enrollment missing. Services unchanged; enroll first.' >&2
  exit 1
fi
# Verify ciphertext silently before enabling; no token printing.
python3 - "$GMAIL_PROJECT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from services.gmail_auth import decrypt_info
decrypt_info()
PY
for GMAIL_SERVICE in minitel-chatgpt admin-ui; do
  mkdir -p "/etc/systemd/system/$GMAIL_SERVICE.service.d"
  cat > "/etc/systemd/system/$GMAIL_SERVICE.service.d/gmail.conf" <<EOF
[Unit]
After=systemd-tmpfiles-setup.service
[Service]
Environment=GMAIL_ENABLED=1
LoadCredentialEncrypted=gmail:/etc/credstore.encrypted/minitel-gmail
PrivateMounts=yes
ExecStart=
ExecStart=$GMAIL_PROJECT/.venv/bin/python $GMAIL_PROJECT/services/${GMAIL_SERVICE//-/_}.py
EOF
done
systemctl daemon-reload
systemctl restart minitel-chatgpt admin-ui
echo 'Gmail activated. Open /gmail and run the read-only probe. No email was sent.'
