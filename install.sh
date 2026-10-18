#!/bin/bash
# install.sh - Installation complète de MINITEL GPT sur Raspberry Pi
# Prérequis : avoir cloné le dépôt dans /home/minitel/minitel-gpt
#   cd /home/minitel && git clone https://github.com/jherard-fr/minitel-gpt.git
# Usage : sudo bash install.sh
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "Run with sudo on the Raspberry Pi." >&2
  exit 1
fi

PROJ_DIR="/home/minitel/minitel-gpt"
SERVICE_DIR="/etc/systemd/system"

echo "=== MINITEL GPT - Installation ==="

# ── Dépendances système ─────────────────────────────────────────────────────
echo "[1/7] Paquets système..."
# Hors ligne (installation au 1er boot avant saisie du WiFi via le portail
# captif), les paquets sont pré-installés depuis des .deb : l'update peut échouer.
apt-get update -q || echo "      (pas de réseau : index apt non mis à jour)"
apt-get install -y \
  git \
  python3 python3-venv curl ca-certificates \
  dnsmasq-base iw minicom \
  figlet toilet-fonts
#   git         : mise à jour de l'app depuis l'admin web (git fetch/reset)
#   python3     : installed interpreter; uv manages all application libraries
#   dnsmasq-base + iw : hotspot WiFi de provisioning
#   minicom     : utilitaire de debug série (optionnel)
#   figlet + toilet-fonts : polices du titre ASCII. Sur Trixie, python3-pyfiglet
#               est livré SANS police (simples Recommends) -> aperçu en erreur.

# Python dependencies are installed together by install_runtime.sh below.

# ── Désactiver le dnsmasq système (conflit port 53 avec le hotspot) ─────────
echo "[3/7] Nettoyage dnsmasq système..."
systemctl disable --now dnsmasq 2>/dev/null || true
# NetworkManager utilise son propre dnsmasq-base interne pour le hotspot.
# Portail captif : en hotspot, toute requête DNS renvoie 192.168.4.1, sinon le
# téléphone ne détecte pas le portail et ne l'ouvre pas tout seul.
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cp "$PROJ_DIR/config/dnsmasq-shared-captive.conf" /etc/NetworkManager/dnsmasq-shared.d/minitel-captive.conf

# ── Permissions port série + répertoires ────────────────────────────────────
echo "[4/7] Permissions et répertoires..."
usermod -a -G dialout minitel                 # accès à /dev/ttyUSB0 (FTDI)
mkdir -p "$PROJ_DIR/logs" "$PROJ_DIR/config/knowledge"
chown -R --no-dereference minitel:minitel "$PROJ_DIR"

# ── Personnalités : prompts.json est local (gitignoré) ──────────────────────
echo "[5/7] Initialisation des personnalités..."
# prompts.json (édité via l'admin) n'est pas versionné, pour qu'une mise à jour
# ne l'écrase jamais. On le crée depuis le défaut fourni par le dépôt s'il manque.
if [ ! -f "$PROJ_DIR/config/prompts.json" ] && [ -f "$PROJ_DIR/config/prompts.default.json" ]; then
  cp "$PROJ_DIR/config/prompts.default.json" "$PROJ_DIR/config/prompts.json"
  chown minitel:minitel "$PROJ_DIR/config/prompts.json"
  echo "      prompts.json créé depuis prompts.default.json"
fi

# .env : créé depuis env.example s'il manque, pour que les services démarrent
# même sans clé (la clé se renseigne ensuite via l'admin web). Sans ce fichier,
# l'ancienne unité systemd échouait au démarrage (EnvironmentFile introuvable).
if [ ! -f "$PROJ_DIR/.env" ] && [ -f "$PROJ_DIR/config/env.example" ]; then
  cp "$PROJ_DIR/config/env.example" "$PROJ_DIR/.env"
  chown minitel:minitel "$PROJ_DIR/.env"
  echo "      .env créé depuis env.example (renseigner la clé via l'admin)"
fi

# ── Règle sudo (l'admin web redémarre les services sans mot de passe) ───────
echo "[6/7] Règle sudo..."
cp "$PROJ_DIR/config/minitel-gpt-sudoers" /etc/sudoers.d/minitel-gpt
chmod 440 /etc/sudoers.d/minitel-gpt
visudo -c -f /etc/sudoers.d/minitel-gpt        # autorise systemctl restart des 3 services

# ── Services systemd ────────────────────────────────────────────────────────
echo "[7/7] Services systemd..."
bash "$PROJ_DIR/scripts/install_runtime.sh" "$@"
systemctl enable minitel-chatgpt.service wifi-manager.service admin-ui.service

echo ""
echo "=== Installation terminée ==="
echo ""
echo "1. Demarrer les services (un redemarrage suffit, recommande car"
echo "   wifi-manager touche au reseau et peut couper la session SSH) :"
echo "     sudo reboot"
echo "   (ou, sans reboot : sudo systemctl start admin-ui minitel-chatgpt wifi-manager)"
echo ""
echo "2. Renseigner la cle API depuis l'admin web (aucun .env a editer a la main) :"
echo "   un .env par defaut a ete cree. Choisir le fournisseur (Mistral ou Claude)"
echo "   et coller la cle dans l'onglet Parametres."
echo ""
echo "3. Admin web : http://<ip-du-pi>:8080   (mot de passe : mistral)"
echo "   - onglet Parametres > Mise a jour : met a jour le code depuis GitHub"
echo "   - l'IP s'affiche aussi sur le Minitel via la touche GUIDE"
echo ""
echo "Cablage DIN : seules 3 broches (1=Rx, 2=GND, 3=Tx) ; 4 et 5 LIBRES."
echo "A l'allumage du Minitel :"
echo "  - Minitel 1 : rien a faire, l'accueil s'affiche."
echo "  - Minitel 2 : Fnct+Sommaire (sort du Repertoire) puis Sommaire."
echo "    NE PAS faire Fnct+T A (force le mode teleinformatique, defilant)."
echo "Si rien ne s'affiche : verifier contacts + jumper FTDI sur 5V."
