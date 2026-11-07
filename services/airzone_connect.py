"""Explicit local dashboard commissioning; never an LLM tool or HVAC control."""
import errno
import ipaddress
import json
import os
import re
import stat
import subprocess
import threading
import time
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import urlsplit

from . import (
    airzone_demo,
    airzone_hack,
    config_store,
    rose_terminal,
    terminal_display,
    terminal_scrolling,
)

DASHBOARD_DEPLOYMENT = Path('/opt/aizone/deployment.json')
ADDRESSES_HEADING = '=== ADRESSES DU DASHBOARD ==='
LAN_LINK_TITLE = 'Airzone Wi-Fi/LAN'
FAILURE_HEADING = '=== CONNEXION AIRZONE ÉCHOUÉE ==='
PREFLIGHT_LABELS = {
    'runtime': 'Runtime Minitel ou branche installée.',
    'lock': 'Verrou Aizone dans /run/lock.',
    'request': 'Paramètres de la demande.',
    'installation': 'Installation ou métadonnées Aizone.',
    'service': 'Service aizone inactif.',
    'gateway_read': 'Lecture depuis le compte aizone.',
    'config': 'Lecture JSON /etc/aizone/config.json.',
    'template': 'Modèle /opt/aizone/current/aizone.service.',
    'backup': 'Écriture airzone-before-connect.json.',
}
PREFLIGHT_DETAILS = {
    'unsupported_service_template': 'Marqueurs ARGS / REVISION absents.',
    'untrusted_installation': 'Propriétaire, droits ou lien refusés.',
    'control_target_mismatch': 'Droits liés à une autre passerelle.',
    'oversized_file': 'Fichier supérieur à 1 Mio.',
}


ACTIVATION_TIMEOUT = 220
BUILD_LOG = """*** 3615 IA / SOUMISSION DE TÂCHE ***

CONFIRMATION OUI ENREGISTRÉE
TÂCHE AZ-01 ADMISE EN FILE PRIORITAIRE

DEMANDE MÉMOIRE .............. 64 Kio
ALLOCATION ACCORDÉE
QUOTA EXCEPTIONNEL POUR UNE TÂCHE WEB

COMPILATEUR COBOL ............. CHARGÉ
MODULE DE CONVERSION JSON ..... INCLUS
ÉDITION DE LIENS ................. OK

*** 3615 IA / GÉNÉRATION WEB ***

SCHÉMA DES ZONES ............. IMPORTÉ
VUES PAR PIÈCE .............. GÉNÉRÉES

PROFIL DE POINTE .............. ACTIVÉ
CIBLE SYSTÈME ............. WINDOWS 95
CIBLE WEB ...... NETSCAPE NAVIGATOR 3.0
NORME DE DERNIER CRI ........ HTML 3.2

MODE HAUTE RÉSOLUTION ...... 800 × 600
PALETTE ÉTENDUE ......... 256 COULEURS
AFFICHAGE SIMULTANÉ DE TOUTES LES ZONES
DEMANDE MÉMOIRE SUPPLÉMENTAIRE . REFUS
GÉNÉRATION SOUS 64 Kio ............ OK

*** 3615 IA / MISE EN SERVICE ***

DEMANDE D'ACTIVATION ........... ÉMISE
CONTRÔLE DU SERVICE HTTP LOCAL
LECTURE DE L'ÉTAT DES ZONES

SORTIE DEMANDÉE ............... QR CODE
FORMAT DE SORTIE .............. VIDÉOTEX
ADRESSE LOCALE ATTENDUE

APPLI > SERVEUR MINITEL FRANCE TÉLÉCOM"""


class BuildStream:
    """Pace fictional log blocks independently of the local activation worker."""

    blocks = True
    stop_message = '[Journal arrêté. Activation non annulée.]'

    def __init__(self, term, pending):
        self.term = term
        self.worker, _, started = pending
        self.deadline = started + ACTIVATION_TIMEOUT
        self.chunks = iter(BUILD_LOG.split('\n\n'))
        self.ready_at = 0
        self.hold_until = None
        self.drained = False

    def get_nowait(self):
        now = time.monotonic()
        if now < self.ready_at:
            raise Empty
        chunk = next(self.chunks, None)
        if chunk is not None:
            self.ready_at = now + 0.65
            return chunk + '\n\n'

        if not self.drained:
            # Measure the final hold from delivery of the last block to the CRT.
            if now < self.term._tx_until:
                raise Empty
            self.drained = True
            self.hold_until = now + (5 if not self.worker.is_alive() else 0)
        if now < self.hold_until or (self.worker.is_alive() and now < self.deadline):
            raise Empty
        return None


def show_build(term, pending, language):
    return terminal_scrolling.show(term, BuildStream(term, pending), language, restore=False)


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


def current_dashboard_addresses(port, origin):
    """The installed dashboard owns live interface discovery; GET loopback only."""
    connection = None
    try:
        connection = HTTPConnection('127.0.0.1', port, timeout=2)
        connection.request('GET', '/api/addresses')
        response = connection.getresponse()
        if response.status != 200:
            return None  # Older services may not expose this endpoint yet.
        raw = response.read(16385)
        if len(raw) > 16384:
            return None
        data = json.loads(raw)
        local = f'http://127.0.0.1:{port}'
        if (not isinstance(data, dict) or data.get('origin') != origin or data.get('loopback') != local
                or type(data.get('lan_enabled')) is not bool or not isinstance(data.get('lan_urls'), list)
                or len(data['lan_urls']) > 16 or (not data['lan_enabled'] and data['lan_urls'])):
            return None
        for value in data['lan_urls']:
            if not origin_url(value):
                return None
            url = urlsplit(value)
            address = ipaddress.IPv4Address(url.hostname)
            if (url.scheme != 'http' or url.port != port
                    or not any(address in ipaddress.IPv4Network(n) for n in
                               ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))):
                return None
        return data
    except (OSError, ValueError, HTTPException):
        return None
    finally:
        if connection is not None:
            try:
                connection.close()
            except (OSError, HTTPException):
                pass  # Optional URL reporting must not conceal a completed activation.


def dashboard_report(origin=None):
    """Keep exact dashboard URLs as metadata for the existing local QR viewer.

    Never infer dashboard IPs from the gateway or cache the Pi's DHCP address.
    Airzone's address endpoint also works in mock mode without gateway I/O.
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
    current = current_dashboard_addresses(port, origin) if local and origin else None
    tailnet = False
    if origin:
        hostname = urlsplit(origin).hostname
        tailnet = hostname.endswith('.ts.net')
        try:
            tailnet |= ipaddress.ip_address(hostname) in ipaddress.ip_network('100.64.0.0/10')
        except ValueError:
            pass
    references = []

    def add_link(value, title):
        hostname = urlsplit(value).hostname
        if hostname == 'localhost' or hostname.endswith('.localhost'):
            return  # A phone must never be sent to its own loopback interface.
        try:
            if ipaddress.ip_address(hostname).is_loopback:
                return
        except ValueError:
            pass
        if not any(ref['url'] == value for ref in references):
            references.append({'url': value, 'title': title})

    # The family standing beside the Minitel can open LAN without Tailscale.
    if current:
        for value in current['lan_urls']:
            add_link(value, LAN_LINK_TITLE)
    if origin:
        add_link(origin, 'Airzone Tailnet' if tailnet else 'Airzone dashboard')

    def numbered(value):
        return next((f'[{index}] {value}' for index, ref in enumerate(references, 1)
                     if ref['url'] == value), value)

    lines = [ADDRESSES_HEADING]
    if tailnet:
        lines.extend(('Tailnet (URL configurée) :', numbered(origin)))
    else:
        lines.append('Tailnet : adresse non déterminée.')
        if origin:
            lines.extend(('URL configurée :', numbered(origin)))
    if current is not None:
        if not current['lan_enabled']:
            lines.append('Wi-Fi/LAN : accès non activé.')
        elif current['lan_urls']:
            lines.append('Wi-Fi/LAN (réseau local) :')
            lines.extend(numbered(value) for value in dict.fromkeys(current['lan_urls']))
        else:
            lines.append('Wi-Fi/LAN : aucune adresse actuelle.')
    elif metadata.get('lan_access') is True:
        lines.append('Wi-Fi/LAN : adresses indisponibles.')
    else:
        lines.append('Wi-Fi/LAN : accès non confirmé.')
    lines.extend(('Sur le Pi uniquement :', local or 'Adresse indisponible.'))
    if references:
        lines.append('LINK n : QR du lien [n].')
    return {'display': '\n'.join(lines), 'references': references}


def dashboard_addresses(origin=None):
    return dashboard_report(origin)['display']


def show_summary(term, text, language='fr', *, references=(), simulated=False, previous_text=None):
    """End on the addresses; retain the diagnostic/photo pages with ordinary paging."""
    page_heading = FAILURE_HEADING if text.startswith(FAILURE_HEADING) else ADDRESSES_HEADING
    previous = getattr(term, 'view', None)
    if previous_text is None and previous is not None:
        previous_text = previous.text
    if previous_text:
        text = previous_text + '\n\n' + text
    prefix, heading, addresses = text.rpartition(page_heading)
    if heading:
        # Start the address or error block on its own page.
        geometry = getattr(term, 'geometry', terminal_display.STANDARD)
        chat = rose_terminal.api()
        prefix = prefix.rstrip('\n')
        rows = len(chat.wrap(chat.safe_display(prefix), geometry.columns))
        padding = -rows % geometry.content_rows
        text = prefix + '\n' * (padding + 1) + heading + addresses
    view = terminal_display.View(term, text, language, references=references)
    view.page = len(view.pages) - 1
    if simulated:
        view.notice = 'SIMULATION : aucun service modifié.'
    auto_qr = bool(references and references[0].get('title') == LAN_LINK_TITLE)
    view.install(draw=not auto_qr)
    if auto_qr:
        view.open_link(1)
        if view.qr is not None:
            view.status('SIMULATION / QR [1] : scannez' if simulated else 'Airzone [1] : scannez le QR')
        else:
            view.draw()
    return 'done'


def activate(target):
    """The installed sudo rule accepts this one fixed helper, without arguments."""
    references = []
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
            addresses = dashboard_report(data['origin'])
            references = addresses['references']
            controlled = data.get('controlled_zones')
            controls = 'Commandes HVAC : état non vérifié.'
            if type(controlled) is int and 0 <= controlled <= data['zones']:
                controls = f'Zones pilotables : {controlled}/{data["zones"]}.'
                if data.get('mode_enabled') is True:
                    controls += '\nMode central disponible.'
                if controlled == 0:
                    controls += '\nVérifiez les droits et les alertes.'
            text = (f"=== AIRZONE CONNECTÉ ===\nLecture réelle : {data['zones']} zones.\n"
                    f"Passerelle : {target['host']}\n{paired}\n"
                    f'{controls}\nCompresseur physique non vérifié.\n\n'
                    + addresses['display'])
        elif data.get('rollback') is True:
            text = 'Connexion échouée. Configuration et mode précédents restaurés. Vérifiez par SSH.'
        elif data.get('rollback') is False:
            text = 'Connexion ET restauration échouées. Intervention SSH nécessaire. État du dashboard incertain.'
        elif data.get('error') == 'preflight_failed' and isinstance(data.get('stage'), str):
            label = PREFLIGHT_LABELS.get(data['stage'], 'Étape de préparation inconnue.')
            number = data.get('errno')
            detail = errno.errorcode.get(number) if type(number) is int else None
            reason = data.get('reason')
            if detail is None and isinstance(reason, str):
                detail = PREFLIGHT_DETAILS.get(reason)
            text = f'Vérification refusée.\n{label}'
            if detail:
                text += f'\n{detail}'
            text += '\nAucun succès confirmé.'
        else:
            text = 'Connexion indisponible. Vérifiez par SSH le service Aizone installé et le helper Minitel. Aucun succès confirmé.'
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        text = 'Connexion non confirmée. Vérifiez le dashboard et son service par SSH avant de recommencer.'
    if not text.startswith('=== AIRZONE CONNECTÉ ==='):
        text = FAILURE_HEADING + '\n' + text
    return {'display': text, 'references': references}


def confirm(term, current):
    """Return a worker only after fresh diagnostics and an explicit local OUI."""
    result = airzone_hack.verified_state(current)
    if result is None:
        return None, 'done'
    chat = rose_terminal.api()
    while True:
        term.view.status('Activer les commandes ? OUI/NON' if not result['demo'] else 'SIMULATION : connecter ? OUI/NON')
        answer, action = chat.read_question(term, allow_empty=True)
        if action in {'sommaire', 'guide'}:
            return None, action
        answer = (answer or '').strip().upper()
        if action == 'timeout' or answer in {'', 'NON', 'NO', 'ANNULER', 'CANCEL'}:
            term.view.status('Connexion annulée. Bilan conservé.')
            return None, 'done'
        if answer not in {'OUI', 'YES'}:
            continue
        # Consent may take minutes: revalidate freshness before real activation.
        if not result['demo'] and (airzone_hack.verified_state(current) is None or not airzone_demo.enabled()):
            term.view.status('Diagnostic expiré. Relancez HACK AIRZONE.')
            return None, 'done'
        output = Queue(maxsize=1)

        def request(output=output):
            if result['demo']:
                addresses = dashboard_report()
                connected = {'display': 'SIMULATION : aucun service modifié.\n'
                                        'Adresses du dashboard existant.\n\n' + addresses['display'],
                             'references': addresses['references'], 'simulated': True}
            else:
                connected = activate(result['target'])
            output.put((connected, None))

        worker = threading.Thread(target=request, daemon=True)
        started = time.monotonic()
        worker.start()
        return (worker, output, started), 'done'
