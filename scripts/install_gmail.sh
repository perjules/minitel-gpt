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
bash "$GMAIL_PROJECT/scripts/install_runtime.sh" --extra gmail
/opt/minitel-gpt/venv/bin/python -I - <<'PY'
import sys
sys.path.insert(0, "/opt/minitel-gpt/current/root")
from gmail_auth import systemd_preflight
print(systemd_preflight())
PY
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
if [[ ! -f /etc/credstore.encrypted/minitel-gmail && ! -f /etc/credstore.encrypted/minitel-gmail-accounts ]]; then
  echo 'Encrypted enrollment missing. Services unchanged; enroll first.' >&2
  exit 1
fi
# Verify ciphertext silently before enabling; no token printing.
/opt/minitel-gpt/venv/bin/python -I - "$GMAIL_PROJECT" <<'PY'
import sys
sys.path.insert(0, "/opt/minitel-gpt/current/root")
from gmail_auth import ACCOUNTS_STORE, account_bundle, decrypt_info
account_bundle() if ACCOUNTS_STORE.exists() else decrypt_info()
PY
GMAIL_CREDENTIAL=/etc/credstore.encrypted/minitel-gmail
GMAIL_CREDENTIAL_NAME=gmail
if [[ -f /etc/credstore.encrypted/minitel-gmail-accounts ]]; then
  GMAIL_CREDENTIAL=/etc/credstore.encrypted/minitel-gmail-accounts
  GMAIL_CREDENTIAL_NAME=gmail-accounts
fi
for GMAIL_SERVICE in minitel-chatgpt admin-ui; do
  mkdir -p "/etc/systemd/system/$GMAIL_SERVICE.service.d"
  cat > "/etc/systemd/system/$GMAIL_SERVICE.service.d/gmail.conf" <<EOF
[Unit]
After=systemd-tmpfiles-setup.service
[Service]
Environment=GMAIL_ENABLED=1
LoadCredentialEncrypted=$GMAIL_CREDENTIAL_NAME:$GMAIL_CREDENTIAL
PrivateMounts=yes
EOF
done
systemctl daemon-reload
systemctl restart minitel-chatgpt admin-ui
echo 'Gmail activated. Open /gmail and run the read-only probe. No email was sent.'
