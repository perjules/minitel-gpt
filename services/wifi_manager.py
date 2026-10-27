#!/usr/bin/env python3
"""
WiFi Manager - provisioning WiFi style IoT (Sonos/Chromecast).

Comportement :
  1. Au boot : tente de rejoindre un réseau connu (30s timeout)
  2. Si échec : crée le hotspot MinitelGPT-Setup sur 192.168.4.1
  3. Expose une page Web pour scanner et configurer un nouveau réseau
  4. En cas de succès WiFi : coupe le hotspot, relance les services
"""

import hmac
import ipaddress
import logging
import secrets
import subprocess
import sys
import threading
import time
import uuid

from flask import Flask, jsonify, render_template_string, request, session

AP_SSID = "MinitelGPT-Setup"
AP_IP = "192.168.4.1"
AP_CON_NAME = "MinitelGPT-AP"
CONNECT_TIMEOUT = 30  # legacy manual connection timeout
FLASK_PORT = 80

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [wifi-manager] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = secrets.token_bytes(32)
app.config.update(MAX_CONTENT_LENGTH=4096, SESSION_COOKIE_SAMESITE="Strict")
NETWORK_LOCK = threading.Lock()
COMMAND_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LC_ALL": "C.UTF-8"}
EXECUTABLES = {"nmcli": "/usr/bin/nmcli", "ip": "/usr/sbin/ip", "systemctl": "/usr/bin/systemctl"}


# ── Portail captif ──────────────────────────────────────────────────────────
# Quand le hotspot est actif, on redirige toute requête (et les sondes de
# détection des OS) vers le portail, pour qu'il s'ouvre automatiquement.
CAPTIVE_PROBES = (
    "/generate_204", "/gen_204", "/ncsi.txt", "/connecttest.txt",
    "/hotspot-detect.html", "/library/test/success.html",
    "/canonical.html", "/success.txt", "/redirect",
)

@app.before_request
def captive_redirect():
    from flask import redirect as _redir
    from flask import request
    if request.path == "/healthz":
        return None
    host = request.host.split(":")[0]
    path = request.path
    # Si on est en hotspot et que la requête ne vise pas déjà notre IP,
    # ou que c'est une sonde de détection captive → renvoyer vers le portail.
    if host != AP_IP and path != "/connect":
        try:
            if hotspot_active():
                return _redir(f"http://{AP_IP}/", code=302)
        except (OSError, subprocess.SubprocessError) as error:
            log.debug("Hotspot status unavailable (%s)", type(error).__name__)
    if path in CAPTIVE_PROBES:
        return _redir(f"http://{AP_IP}/", code=302)


# ── Utilitaires nmcli ──────────────────────────────────────────────────────

def run(cmd: list[str], check: bool = True, timeout=20) -> subprocess.CompletedProcess:
    """Fixed programs, trusted environment and bounded waits; never log passwords."""
    args = [EXECUTABLES[cmd[0]], *cmd[1:]]
    if cmd[0] == "nmcli":
        args[1:1] = ["--wait", str(max(1, min(15, int(timeout) - 1)))]
    return subprocess.run(args, capture_output=True, text=True, check=check,
                          timeout=timeout, env=COMMAND_ENV)


def fields(line):
    """Decode nmcli's escaped colons and backslashes, including SSID/profile names."""
    result, escaped = [""], False
    for char in line:
        if escaped:
            result[-1] += char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            result.append("")
        else:
            result[-1] += char
    if escaped:
        result[-1] += "\\"
    return result


def is_connected() -> str | None:
    out = run(["ip", "-4", "addr", "show", "wlan0"]).stdout
    for line in out.splitlines():
        if "inet " in line:
            return line.strip().split()[1].split("/")[0]
    return None


def active_connections():
    out = run(["nmcli", "-t", "-f", "UUID,NAME,DEVICE", "con", "show", "--active"]).stdout
    return [parts for line in out.splitlines() if len(parts := fields(line)) == 3]


def hotspot_active() -> bool:
    return any(name == AP_CON_NAME and device == "wlan0" for _, name, device in active_connections())


def client_connected() -> str | None:
    return next((identifier for identifier, name, device in active_connections()
                 if device == "wlan0" and name != AP_CON_NAME), None)


def scan_networks() -> list[dict]:
    run(["nmcli", "dev", "wifi", "rescan"], check=False)
    out = run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "dev", "wifi", "list"]).stdout
    networks, seen = [], set()
    for line in out.splitlines()[:256]:
        parts = fields(line)
        if len(parts) != 4:
            continue
        ssid, signal, security, used = parts
        if not ssid or ssid == AP_SSID or ssid in seen:
            continue
        seen.add(ssid)
        networks.append({"ssid": ssid, "signal": int(signal) if signal.isdigit() else 0,
                         "security": security or "Open", "connected": used == "*"})
    return sorted(networks, key=lambda n: -n["signal"])


def create_hotspot():
    # Explicitly activating the AP switches wlan0; saved client profiles and their
    # autoconnect preferences are never modified or removed.
    run(["nmcli", "con", "delete", AP_CON_NAME], check=False)
    run(["nmcli", "con", "add", "type", "wifi", "ifname", "wlan0", "mode", "ap",
         "con-name", AP_CON_NAME, "ssid", AP_SSID, "802-11-wireless.band", "bg",
         "802-11-wireless.channel", "6", "ipv4.method", "shared",
         "ipv4.addresses", f"{AP_IP}/24", "ipv6.method", "disabled", "autoconnect", "no"])
    run(["nmcli", "con", "up", "id", AP_CON_NAME])
    log.info("Provisioning hotspot active")


def stop_hotspot():
    run(["nmcli", "con", "down", "id", AP_CON_NAME], check=False)


def connect_wifi(ssid: str, password: str) -> tuple[bool, str]:
    """Test a unique candidate without deleting the last working credentials."""
    identifier = str(uuid.uuid4())
    add = ["nmcli", "con", "add", "type", "wifi", "ifname", "wlan0",
           "con-name", "minitel-setup-" + identifier, "connection.uuid", identifier,
           "ssid", ssid, "ipv4.method", "auto", "autoconnect", "no"]
    if password:
        add += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
    try:
        run(add)
        run(["nmcli", "con", "up", "uuid", identifier])
        if client_connected() != identifier or not is_connected():
            raise RuntimeError("No client address")
        run(["nmcli", "con", "modify", "uuid", identifier, "connection.autoconnect", "yes"])
        log.info("WiFi candidate connected and saved")
        return True, is_connected() or "connected"
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        log.warning("WiFi candidate failed (%s); saved profiles preserved", type(error).__name__)
        run(["nmcli", "con", "delete", "uuid", identifier], check=False)
        create_hotspot()
        return False, "Connexion impossible ; anciens réseaux conservés."


def try_known_networks(timeout: int = CONNECT_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    if client_connected() and is_connected():
        return True
    out = run(["nmcli", "-t", "-f", "UUID,TYPE,AUTOCONNECT", "con", "show"]).stdout
    profiles = [p[0] for line in out.splitlines()
                if len(p := fields(line)) == 3 and "wireless" in p[1] and p[2] == "yes"]
    if not profiles:
        return False
    stop_hotspot()
    try:
        for identifier in profiles[:10]:
            remaining = deadline - time.monotonic()
            if remaining < 1:
                break
            try:
                run(["nmcli", "con", "up", "uuid", identifier], timeout=min(20, remaining))
                if client_connected() and is_connected():
                    log.info("Known WiFi connection recovered")
                    return True
            except (OSError, subprocess.SubprocessError):
                continue
        return False
    finally:
        if not (client_connected() and is_connected()):
            create_hotspot()


# ── Interface Web ──────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>MinitelGPT - Configuration WiFi</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: monospace; background: #1a1a2e; color: #e0e0e0; margin: 0; padding: 20px;
    max-width: 600px; margin: 0 auto; }
  h1 { color: #00ff88; font-size: 1.3em; }
  .card { background: #16213e; border: 1px solid #0f3460; border-radius: 8px; padding: 16px; margin: 12px 0; }
  .network { cursor: pointer; padding: 10px; border-radius: 4px; margin: 4px 0; border: 1px solid #0f3460; }
  .network:hover { background: #0f3460; }
  .network.selected { border-color: #00ff88; }
  .signal { float: right; color: #888; }
  .security { font-size: 0.8em; color: #aaa; }
  input[type=password] { width: 100%; padding: 10px; background: #0f3460; border: 1px solid #00ff88;
    color: #e0e0e0; border-radius: 4px; box-sizing: border-box; margin-top: 8px; font-family: monospace;
    font-size: 16px; }
  button { background: #00ff88; color: #1a1a2e; border: none; padding: 10px 24px;
    border-radius: 4px; cursor: pointer; font-weight: bold; margin-top: 10px; font-size: 1em; }
  button:hover { background: #00cc66; }
  .status { padding: 10px; border-radius: 4px; margin: 8px 0; }
  .ok { background: #0d3320; color: #00ff88; border: 1px solid #00ff88; }
  .err { background: #330d0d; color: #ff4444; border: 1px solid #ff4444; }
  #spinner { display: none; color: #aaa; margin-top: 8px; }
</style>
</head>
<body>
<h1>🖥 MinitelGPT - Configuration WiFi</h1>

{% if status_msg %}
<div class="status {{ 'ok' if status_ok else 'err' }}">{{ status_msg }}</div>
{% endif %}

<div class="card">
<b>Réseaux disponibles</b> - <a href="/" style="color:#888;font-size:0.85em">Rafraîchir</a>
<div id="networks">
{% for net in networks %}
<div class="network" data-ssid="{{ net.ssid }}" onclick="selectNet(this.dataset.ssid, this)" id="net-{{ loop.index }}">
  {{ net.ssid }}
  <span class="signal">📶 {{ net.signal }}%</span><br>
  <span class="security">{{ net.security }}</span>
  {% if net.connected %}<span style="color:#00ff88"> ✓ connecté</span>{% endif %}
</div>
{% endfor %}
{% if not networks %}<p style="color:#888">Aucun réseau détecté</p>{% endif %}
</div>
</div>

<div class="card">
<form id="wifiForm" onsubmit="connectWifi(event)">
  <b>Réseau sélectionné :</b> <span id="selectedSSID" style="color:#00ff88">-</span>
  <input type="hidden" id="ssid" name="ssid">
  <input type="password" id="password" name="password" placeholder="Mot de passe WiFi" autocomplete="off">
  <br>
  <button type="submit">Se connecter</button>
  <div id="spinner">⏳ Connexion en cours (30s max)...</div>
</form>
</div>

<script>
function selectNet(ssid, element) {
  document.getElementById('selectedSSID').textContent = ssid;
  document.getElementById('ssid').value = ssid;
  document.querySelectorAll('.network').forEach(el => el.classList.remove('selected'));
  element.classList.add('selected');
  document.getElementById('password').focus();
}
async function connectWifi(e) {
  e.preventDefault();
  const ssid = document.getElementById('ssid').value;
  if (!ssid) { alert('Sélectionnez un réseau'); return; }
  document.getElementById('spinner').style.display = 'block';
  // On envoie la demande SANS attendre la fin (le hotspot va se couper).
  try {
    const response = await fetch('/connect', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': {{ csrf|tojson }}},
      body: JSON.stringify({ssid, password: document.getElementById('password').value})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Connexion indisponible');
    document.body.innerHTML = '<div style="padding:40px;font-family:monospace;text-align:center">'
      + '<h2>Connexion demandee</h2><p>Le Minitel tente de rejoindre <b id=connectingSSID></b>.</p>'
      + '<p>Reconnectez votre telephone au WiFi habituel. En cas d echec, revenez au hotspot.</p></div>';
    document.getElementById('connectingSSID').textContent = ssid;
  } catch (error) {
    document.getElementById('spinner').style.display = 'none';
    alert(error.message);
  }
}
</script>
</body>
</html>"""


def provisioning_allowed():
    # Host/Origin headers cannot establish which interface received the request.
    connection = request.environ.get("werkzeug.socket")
    try:
        local = connection.getsockname()[0] if connection is not None else None
        peer = ipaddress.ip_address(request.remote_addr or "")
        return (local == AP_IP and peer in ipaddress.ip_network("192.168.4.0/24")
                and hotspot_active())
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


@app.route("/")
def index():
    if not provisioning_allowed():
        return "WiFi configuration is available through the MinitelGPT-Setup hotspot only.", 200
    session.setdefault("csrf", secrets.token_urlsafe(32))
    return render_template_string(HTML, networks=scan_networks(), csrf=session["csrf"],
                                  status_msg=None, status_ok=False)


@app.route("/connect", methods=["POST"])
def connect():
    if not provisioning_allowed():
        return jsonify(success=False, error="Provisioning hotspot required"), 403
    token = request.headers.get("X-CSRF-Token", "")
    if not session.get("csrf") or not hmac.compare_digest(token.encode(), session["csrf"].encode()):
        return jsonify(success=False, error="Invalid request token"), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(success=False, error="JSON object required"), 400
    ssid, password = data.get("ssid"), data.get("password", "")
    if (not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32
            or any(ord(c) < 32 for c in ssid) or not isinstance(password, str)
            or any(ord(c) < 32 for c in password)
            or not (not password or 8 <= len(password.encode()) <= 63
                    or len(password) == 64 and all(c in "0123456789abcdefABCDEF" for c in password))):
        return jsonify(success=False, error="Invalid WiFi parameters"), 400
    if not NETWORK_LOCK.acquire(blocking=False):
        return jsonify(success=False, error="Network change already running"), 409

    def do_connect():
        try:
            time.sleep(3)  # Allow the HTTP response to leave before switching wlan0.
            if hotspot_active():
                success, _ = connect_wifi(ssid, password)
                if success:
                    run(["systemctl", "start", "minitel-chatgpt"])
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            log.warning("WiFi setup failed (%s)", type(error).__name__)
        finally:
            NETWORK_LOCK.release()

    try:
        threading.Thread(target=do_connect, daemon=True).start()
    except RuntimeError:
        NETWORK_LOCK.release()
        return jsonify(success=False, error="Setup unavailable"), 503
    return jsonify(success=True, queued=True, ssid=ssid), 202


@app.route("/healthz")
def health():
    return jsonify(ok=True)


@app.route("/status")
def status():
    ip = is_connected()
    return jsonify(connected=bool(ip), ip=ip, hotspot=hotspot_active())


# ── Surveillance continue ──────────────────────────────────────────────────
CHECK_INTERVAL = 30        # secondes entre deux vérifications
GRACE_CYCLES = 4           # cycles déconnectés avant hotspot (~2 min)


def startup_provisioning(timeout=90, poll=3):
    """One boot-time decision. Never seize wlan0 after a successful client boot."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client_connected() and is_connected():
            log.info("WiFi connected at startup; NetworkManager owns recovery")
            return
        time.sleep(poll)
    if client_connected() and is_connected():
        return
    log.info("No WiFi at startup; enabling provisioning hotspot")
    create_hotspot()



def main():
    # Portail web en permanence (thread daemon) : configuration WiFi accessible
    # depuis le hotspot (192.168.4.1) ou depuis le réseau local.
    server = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=FLASK_PORT,
                               debug=False, use_reloader=False),
        daemon=True,
    )
    server.start()
    startup_provisioning()
    server.join()


if __name__ == "__main__":
    main()
