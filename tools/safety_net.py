#!/usr/bin/env python3
"""
Filet de sécurité pour le test du hotspot.
Après 6 min, si aucun réseau client n'est connecté, restaure Verrieres
proprement (stoppe le wifi-manager le temps de la manœuvre).
"""
import sys
import time

sys.path.insert(0, "/home/minitel/minitel-gpt/services")
from wifi_manager import AP_CON_NAME, client_connected, run

LOG = "/home/minitel/minitel-gpt/logs/safety.log"
def log(m):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {m}\n")

FALLBACK = "Verrieres"
time.sleep(360)  # 6 min

if client_connected():
    log("Config reussie, filet non declenche.")
else:
    log("FILET DE SECURITE : restauration Verrieres")
    run(["systemctl", "stop", "wifi-manager"], check=False)
    time.sleep(1)
    run(["nmcli", "con", "down", AP_CON_NAME], check=False)
    run(["nmcli", "con", "modify", FALLBACK, "connection.autoconnect", "yes"], check=False)
    time.sleep(1)
    for i in range(5):
        r = run(["nmcli", "con", "up", FALLBACK], check=False)
        log(f"  up {FALLBACK} essai {i+1}: rc={r.returncode}")
        if r.returncode == 0:
            break
        time.sleep(5)
    run(["systemctl", "start", "wifi-manager"], check=False)
    log("Filet termine.")
