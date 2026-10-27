#!/usr/bin/env bash
# Optional Tailscale setup for remote SSH access to the Pi.
# Run as the regular user: bash scripts/setup_tailscale.sh
set -euo pipefail

if [[ ${EUID} -eq 0 ]]; then
  echo "Run this script as your regular user, not with sudo." >&2
  exit 1
fi

echo "=== MinitelGPT: Tailscale setup ==="
echo "This joins the Pi to an existing tailnet. It does NOT enable public Funnel,"
echo "subnet routing, an exit node, or Tailscale SSH."
echo

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale is not installed."
  read -r -p "Install it using the official Tailscale installer? [y/N] " reply
  case "$reply" in
    y|Y|yes|YES)
      command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }
      curl -fsSL https://tailscale.com/install.sh | sudo sh
      ;;
    *) echo "Installation cancelled."; exit 0 ;;
  esac
else
  echo "Tailscale is already installed."
fi

echo
echo "Starting Tailscale..."
sudo systemctl enable --now tailscaled

if tailscale status >/dev/null 2>&1; then
  echo "Tailscale is already connected."
else
  echo "A login URL may appear below. Open it on your phone or laptop,"
  echo "sign in to your existing tailnet, and approve this Pi."
  sudo tailscale up
fi

echo
echo "=== Tailnet status ==="
tailscale status
echo
echo "Pi Tailscale IPv4 address:"
tailscale ip -4
echo
echo "From Termux (with Tailscale connected on your phone), run:"
echo "  ssh $(id -un)@<PI_TAILSCALE_IP>"
echo "Replace <PI_TAILSCALE_IP> with the address printed above."
echo "Your existing SSH server must be running; this script does not change SSH settings."
echo "Test with phone Wi-Fi disabled to confirm remote access."
