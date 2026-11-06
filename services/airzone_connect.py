"""Explicit local dashboard commissioning; never an LLM tool or HVAC control."""
import json
import os
import re
import subprocess
import threading
import time
from queue import Queue

from . import airzone_demo, airzone_hack, config_store, rose_terminal


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
                    or not 1 <= data['zones'] <= 32 or not isinstance(data.get('origin'), str)
                    or not re.fullmatch(r'https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?', data['origin'])):
                raise ValueError('invalid_receipt')
            try:
                config_store.update_env(airzone_hack.ROOT / '.env', {'AIRZONE_GATEWAY': target['host']})
                os.environ['AIRZONE_GATEWAY'] = target['host']
                paired = 'Passerelle Minitel enregistrée.'
            except (OSError, ValueError, config_store.StorageFailure):
                paired = 'Dashboard actif; sauvegarde .env échouée.'
            text = (f"=== AIRZONE CONNECTÉ ===\nLecture réelle : {data['zones']} zones.\n"
                    f"Passerelle : {target['host']}\nDashboard : {data['origin']}\n{paired}\n"
                    'Commandes HVAC désactivées.\nCompresseur physique non vérifié.')
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
