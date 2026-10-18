#!/usr/bin/env python3
"""
Simulation 'lieu inconnu' avec filet de sécurité.
1. Déconnecte les réseaux connus (simule absence de réseau).
2. Monte le hotspot ouvert MinitelGPT-Setup.
3. Attend jusqu'à 5 min qu'une config réseau réussisse via le portail.
4. Fallback : si rien, restaure Verrieres (config en dur) pour ne pas perdre le Pi.
"""
import subprocess
import sys
import time

sys.path.insert(0, "/home/minitel/minitel-gpt/services")
from wifi_manager import (
    AP_CON_NAME,
    create_hotspot,
    run,
    set_clients_autoconnect,
    stop_hotspot,
)

LOG = "/home/minitel/minitel-gpt/logs/sim_wifi.log"
FALLBACK_NET = "Verrieres"
TIMEOUT = 300  # 5 min

def log(m):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {m}\n")

def client_connected():
    """Retourne le nom du réseau client actif sur wlan0 (hors hotspot), ou None."""
    r = run(["nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active"], check=False)
    for line in r.stdout.splitlines():
        p = line.split(":")
        if len(p) >= 2 and p[1] == "wlan0" and p[0] != AP_CON_NAME:
            return p[0]
    return None

try:
    log("=== SIMULATION LIEU INCONNU ===")
    log("Hotspot ouvert MinitelGPT-Setup en cours de creation...")
    create_hotspot()
    log("Hotspot ACTIF. Connectez-vous (sans mot de passe) puis http://192.168.4.1")
    log(f"Fallback automatique sur {FALLBACK_NET} dans {TIMEOUT//60} min si pas de config.")

    deadline = time.time() + TIMEOUT
    success = None
    while time.time() < deadline:
        c = client_connected()
        if c:
            success = c
            break
        time.sleep(8)

    if success:
        log(f"=== SUCCES : connecte a '{success}' via le portail ===")
    else:
        log("=== TIMEOUT : aucune config recue ===")
except (OSError, subprocess.SubprocessError, RuntimeError) as e:
    log(f"ERREUR: {e}")
finally:
    if not client_connected():
        log(f"FALLBACK : restauration {FALLBACK_NET}")
        stop_hotspot()
        set_clients_autoconnect(True)
        time.sleep(2)
        for i in range(5):
            r = run(["nmcli", "con", "up", FALLBACK_NET], check=False)
            log(f"  up {FALLBACK_NET} essai {i+1}: rc={r.returncode}")
            if r.returncode == 0:
                break
            time.sleep(5)
    else:
        log("Config reussie, pas de fallback necessaire.")
    log("=== FIN SIMULATION ===")
