"""Explicit local dashboard commissioning; never an LLM tool or HVAC control."""
import ipaddress
import json
import os
import re
import stat
import subprocess
import threading
import time
from pathlib import Path
from queue import Queue
from urllib.parse import urlsplit

from . import airzone_demo, airzone_hack, config_store, rose_terminal, terminal_display

DASHBOARD_DEPLOYMENT = Path('/opt/aizone/deployment.json')
ADDRESSES_HEADING = '=== ADRESSES DU DASHBOARD ==='


def origin_url(value):
    if (not isinstance(value, str) or len(value) > 280
            or not re.fullmatch(r'https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?', value)):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.hostname and (parsed.port is None or 1 <= parsed.port <= 65535):
            return value
    except ValueError:
        pass
    return None


def dashboard_addresses(origin=None):
    """Display the installed URLs, including in mock rehearsal; no network changes.

    The Aizone service binds loopback. A guessed Wi-Fi IP would not be a working
    dashboard URL; the local address is explicitly for a browser on the Pi.
    """
    metadata = {}
    try:
        descriptor = os.open(DASHBOARD_DEPLOYMENT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('regular_manifest_required')
            raw = stream.read(4097)
        if len(raw) <= 4096:
            value = json.loads(raw)
            if isinstance(value, dict):
                metadata = value
    except (OSError, ValueError):
        pass
    origin = origin_url(origin) or origin_url(metadata.get('origin'))
    port = metadata.get('port')
    local = f'http://127.0.0.1:{port}' if type(port) is int and 1 <= port <= 65535 else None
    tailnet = False
    if origin:
        hostname = urlsplit(origin).hostname
        tailnet = hostname.endswith('.ts.net')
        try:
            tailnet |= ipaddress.ip_address(hostname) in ipaddress.ip_network('100.64.0.0/10')
        except ValueError:
            pass
    lines = [ADDRESSES_HEADING]
    if tailnet:
        lines.extend(('Tailnet (URL configurée) :', origin))
    else:
        lines.append('Tailnet : adresse non déterminée.')
        if origin:
            lines.extend(('URL configurée :', origin))
    lines.extend(('Local - sur le Pi uniquement :', local or 'Adresse indisponible.',
                  'Wi-Fi/LAN : accès non exposé.'))
    return '\n'.join(lines)


def show_summary(term, text, language='fr'):
    """End on the addresses; retain the diagnostic/photo pages with ordinary paging."""
    previous = getattr(term, 'view', None)
    if previous is not None:
        text = previous.text + '\n\n' + text
    prefix, heading, addresses = text.rpartition(ADDRESSES_HEADING)
    if heading:
        # Keep both addresses together on the final page, including long DNS names.
        geometry = getattr(term, 'geometry', terminal_display.STANDARD)
        chat = rose_terminal.api()
        prefix = prefix.rstrip('\n')
        rows = len(chat.wrap(chat.safe_display(prefix), geometry.columns))
        padding = -rows % geometry.content_rows
        text = prefix + '\n' * (padding + 1) + heading + addresses
    view = terminal_display.View(term, text, language)
    view.page = len(view.pages) - 1
    view.install()
    return 'done'


def command(question):
    return bool(re.fullmatch(r'\s*connect\s+airzone\s*[!.]*\s*', question, re.IGNORECASE))


def activate(target):
    """The installed sudo rule accepts this one fixed helper, without arguments."""
    try:
        result = subprocess.run(['/usr/bin/sudo', '-n', '/usr/local/libexec/minitel-airzone-connect'],
                                input=json.dumps(target).encode(), shell=False,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=210,
                                check=False)
        if len(result.stdout) > 4096:
            raise ValueError('oversized_receipt')
        data = json.loads(result.stdout)
        if result.returncode == 0 and data.get('ok') is True:
            # The fixed helper returns only local operator-configured URL and counts.
            if (data.get('host') != target['host'] or type(data.get('zones')) is not int
                    or not 1 <= data['zones'] <= 32 or not origin_url(data.get('origin'))):
                raise ValueError('invalid_receipt')
            try:
                config_store.update_env(airzone_hack.ROOT / '.env', {'AIRZONE_GATEWAY': target['host']})
                os.environ['AIRZONE_GATEWAY'] = target['host']
                paired = 'Passerelle Minitel enregistrée.'
            except (OSError, ValueError, config_store.StorageFailure):
                paired = 'Dashboard actif; sauvegarde .env échouée.'
            text = (f"=== AIRZONE CONNECTÉ ===\nLecture réelle : {data['zones']} zones.\n"
                    f"Passerelle : {target['host']}\n{paired}\n"
                    'Commandes HVAC désactivées.\nCompresseur physique non vérifié.\n\n'
                    + dashboard_addresses(data['origin']))
        elif data.get('rollback') is True:
            text = 'Connexion échouée. Configuration et mode précédents restaurés. Vérifiez par SSH.'
        elif data.get('rollback') is False:
            text = 'Connexion ET restauration échouées. Intervention SSH nécessaire. État du dashboard incertain.'
        else:
            text = 'Connexion indisponible. Vérifiez par SSH le service Aizone installé et le helper Minitel. Aucun succès confirmé.'
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        text = 'Connexion non confirmée. Vérifiez le dashboard et son service par SSH avant de recommencer.'
    return {'display': text}


def confirm(term, current):
    """Return a worker only after fresh diagnostics and an explicit local OUI."""
    result = airzone_hack.verified_state(current)
    if result is None:
        return None, 'done'
    chat = rose_terminal.api()
    while True:
        term.view.status('Activer le dashboard réel ? OUI/NON' if not result['demo'] else 'SIMULATION : connecter ? OUI/NON')
        answer, action = chat.read_question(term, allow_empty=True)
        if action in {'sommaire', 'guide'}:
            return None, action
        answer = (answer or '').strip().upper()
        if action == 'timeout' or answer in {'', 'NON', 'NO', 'ANNULER', 'CANCEL'}:
            term.view.status('Connexion annulée. Bilan conservé.')
            return None, 'done'
        if answer not in {'OUI', 'YES'}:
            continue
        if result['demo']:
            show_summary(term, 'SIMULATION : aucun service modifié.\nAdresses du dashboard existant.\n\n'
                         + dashboard_addresses(), getattr(term.view, 'language', 'fr'))
            term.view.status('SIMULATION : aucun service modifié.')
            return None, 'done'
        # Consent may take minutes: revalidate freshness before starting anything.
        if airzone_hack.verified_state(current) is None or not airzone_demo.enabled():
            term.view.status('Diagnostic expiré. Relancez CONNECT AIRZONE.')
            return None, 'done'
        target = result['target']
        output = Queue(maxsize=1)
        def request(output=output, target=target):
            output.put((activate(target), None))
        worker = threading.Thread(target=request, daemon=True)
        started = time.monotonic()
        worker.start()
        term.view.status('Raccordement réel. Restauration armée.')
        return (worker, output, started), 'done'
