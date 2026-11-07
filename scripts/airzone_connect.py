"""Fixed commissioning operation for an already installed Aizone dashboard.

Installed root-owned by the Minitel runtime. No checkout code, installer, Git,
network configuration, or HVAC writes run with elevated privileges.
"""
import fcntl
import importlib.util
import ipaddress
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

BASE = Path('/opt/aizone')
CONFIG = Path('/etc/aizone/config.json')
UNIT = Path('/etc/systemd/system/aizone.service')
STATE = Path('/var/lib/minitel-gpt')
LOCK = Path('/run/lock/aizone-install.lock')
BRANCH = 'feature/airzone-demo'


def trusted(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if item.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('untrusted_installation')


def read(path):
    trusted(path)
    with path.open('rb') as stream:
        value = stream.read(1024 * 1024 + 1)
    if len(value) > 1024 * 1024:
        raise ValueError('oversized_file')
    return value


def replace(path, content, mode, gid=0):
    # Use a fresh staging name even after an interrupted commissioning.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.minitel-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), 0, gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def command(*args):
    return subprocess.run(list(args), shell=False, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=True, timeout=25)


def validator():
    # Reuse the very same schema/size/deadline validator as the ordinary tool.
    spec = importlib.util.spec_from_file_location('airzone_diagnostic', Path(__file__).with_name('airzone_diagnostic.py'))
    diagnostic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostic)
    return diagnostic


def receipt(path, host, port, system):
    diagnostic = validator()
    try:
        status, out, _ = diagnostic.capture([
            '/usr/sbin/runuser', '-u', 'aizone', '--', '/usr/bin/python3', str(path / 'diagnostics.py'),
            'state', '--host', host, '--port', str(port), '--system-id', str(system),
            '--timeout', '2', '--deadline', '5'], 8)
        result = diagnostic.validate(out, status, 'state', host, False, port, system)
    except diagnostic.DiagnosticError as error:
        raise ValueError('real_state_failed') from error
    if not result['ok']:
        raise ValueError('real_state_failed')
    return result


def dashboard(port, origin, revision, *, state=False):
    url = urlsplit(origin)
    host = url.netloc
    endpoint = '/api/state' if state else '/api/health'
    request = Request(f'http://127.0.0.1:{port}{endpoint}', headers={'Host': host})
    with build_opener(ProxyHandler({})).open(request, timeout=6) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('oversized_dashboard')
    result = json.loads(raw)
    if result.get('demo') is not False:
        raise ValueError('dashboard_still_mock')
    if state:
        if (result.get('connection') != 'online' or result.get('commands_enabled') is not True
                or not result.get('zones') or any(z.get('available') is not True for z in result['zones'])):
            raise ValueError('dashboard_state_failed')
    elif result.get('status') != 'ok' or result.get('revision') != revision:
        raise ValueError('dashboard_health_failed')
    return result


def snapshot():
    paths = (CONFIG, UNIT, BASE / 'deployment.json')
    return [{'path': str(p), 'content': read(p).decode(), 'mode': p.stat().st_mode & 0o777,
             'gid': p.stat().st_gid} for p in paths]


def restore(saved):
    if [item['path'] for item in saved] != [str(CONFIG), str(UNIT), str(BASE / 'deployment.json')]:
        raise ValueError('invalid_backup')
    revision = json.loads(saved[2]['content'])['revision']
    if (BASE / 'current').resolve(strict=True) != BASE / 'releases' / revision:
        raise ValueError('backup_release_changed')
    for item in saved:
        read(Path(item['path']))
        replace(Path(item['path']), item['content'].encode(), item['mode'], item['gid'])
    command('/usr/bin/systemctl', 'daemon-reload')
    command('/usr/bin/systemctl', 'restart', 'aizone.service')
    command('/usr/bin/systemctl', 'is-active', '--quiet', 'aizone.service')


class PreflightError(ValueError):
    pass


@contextmanager
def preflight(stage):
    try:
        yield
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # Fixed stage names, never exception text or captured gateway output.
        raise PreflightError(stage) from error


def connect(request):
    if not isinstance(request, dict) or set(request) != {'host', 'port', 'system_id'}:
        raise ValueError('invalid_target')
    host, port, system = request['host'], request['port'], request['system_id']
    if not isinstance(host, str):
        raise TypeError('invalid_target')
    address = ipaddress.IPv4Address(host)
    if (not any(address in ipaddress.IPv4Network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
            or int(address) & 255 == 255 or type(port) is not int or not 1 <= port <= 65535
            or type(system) is not int or not 1 <= system <= 32):
        raise ValueError('invalid_target')
    with preflight('installation'):
        saved = snapshot()  # Also requires an existing, root-owned installation.
        metadata = json.loads(saved[2]['content'])
        revision, origin, backend_port = metadata['revision'], metadata['origin'], metadata['port']
        if (not re.fullmatch('[0-9a-f]{40}', revision) or metadata['mode'] not in {'mock', 'real'}
                or type(backend_port) is not int or not 1 <= backend_port <= 65535
                or not re.fullmatch(r'https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?', origin)):
            raise ValueError('invalid_deployment')
        release = (BASE / 'current').resolve(strict=True)
        if release != BASE / 'releases' / revision:
            raise ValueError('deployment_mismatch')
        trusted(release)
    # Require the installed service to be running before attempting a mode change.
    with preflight('service'):
        command('/usr/bin/systemctl', 'is-active', '--quiet', 'aizone.service')
    with preflight('gateway_read'):
        receipt(release, host, port, system)
    with preflight('config'):
        settings = json.loads(saved[0]['content'])
        if (settings.get('zone_controls') or settings.get('master_zone_id') is not None):
            configured_target = (settings.get('airzone_host'), settings.get('airzone_port', 3000),
                                 settings.get('system_id', 1))
            if configured_target != (host, port, system):
                raise ValueError('control_target_mismatch')
        settings.update(airzone_host=host, airzone_port=port, system_id=system,
                        public_origin=origin, allow_commands=True)
    with preflight('template'):
        template = read(release / 'aizone.service').decode()
        if '@@ARGS@@' not in template or '@@REVISION@@' not in template:
            raise ValueError('unsupported_service_template')
        unit = template.replace('@@ARGS@@', f'--config /etc/aizone/config.json --port {backend_port}').replace('@@REVISION@@', revision)
    with preflight('backup'):
        backup = STATE / 'airzone-before-connect.json'
        replace(backup, json.dumps(saved).encode(), 0o600)
    try:
        replace(CONFIG, (json.dumps(settings, indent=2) + '\n').encode(), 0o640, CONFIG.stat().st_gid)
        replace(UNIT, unit.encode(), 0o644)
        command('/usr/bin/systemctl', 'daemon-reload')
        command('/usr/bin/systemctl', 'restart', 'aizone.service')
        for attempt in range(10):
            try:
                dashboard(backend_port, origin, revision)
                break
            except (OSError, ValueError):
                if attempt == 9:
                    raise
                time.sleep(.5)
        result = dashboard(backend_port, origin, revision, state=True)
        command('/usr/bin/systemctl', 'is-active', '--quiet', 'aizone.service')
        metadata['mode'] = 'real'
        replace(BASE / 'deployment.json', (json.dumps(metadata, indent=2) + '\n').encode(), 0o644)
        controlled = sum(
            any(zone.get('controls', {}).get(key) is True for key in ('on', 'setpoint'))
            for zone in result['zones']
        )
        return {'ok': True, 'origin': origin, 'host': host, 'zones': len(result['zones']),
                'controlled_zones': controlled, 'mode_enabled': result.get('mode_enabled') is True}
    except BaseException:  # noqa: BLE001 - Restore the transaction on interruption too.
        # Finish restoration even if the activation budget or SSH session expires.
        signal.alarm(0)
        try:
            restore(saved)
        except (OSError, ValueError, subprocess.SubprocessError):
            return {'ok': False, 'error': 'activation_failed', 'rollback': False}
        return {'ok': False, 'error': 'activation_failed', 'rollback': True}


def open_install_lock():
    trusted(LOCK.parent.parent)
    parent = LOCK.parent.lstat()
    # A root-owned sticky directory protects root-owned entries from replacement.
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0
            or (parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX)):
        raise ValueError('untrusted_lock_directory')
    lock = os.fdopen(os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600), 'r+')
    try:
        info = os.fstat(lock.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 or info.st_nlink != 1):
            raise ValueError('untrusted_lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ValueError):
        lock.close()
        raise
    return lock


def main():
    stage = 'runtime'
    try:
        if os.geteuid() != 0 or sys.argv[1:] not in ([], ['rollback']):
            raise ValueError('unsupported_operation')
        # Clear caller-controlled Python/proxy/HTTP settings before any child runs.
        os.environ.clear()
        os.environ.update(PATH='/usr/sbin:/usr/bin:/sbin:/bin', HOME='/root', LANG='C.UTF-8')
        if json.loads(read(STATE / 'runtime.json'))['branch'] != BRANCH:
            raise ValueError('wrong_branch')
        stage = 'lock'
        with open_install_lock():
            stage = 'request'
            signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
            signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError()))
            signal.signal(signal.SIGHUP, lambda *_: (_ for _ in ()).throw(InterruptedError()))
            signal.alarm(120)
            if sys.argv[1:] == ['rollback']:
                restore(json.loads(read(STATE / 'airzone-before-connect.json')))
                result = {'ok': True, 'restored': True}
            else:
                result = connect(json.loads(sys.stdin.buffer.read(1025)))
        signal.alarm(0)
    except PreflightError as error:
        result = {'ok': False, 'error': 'preflight_failed', 'stage': str(error)}
        cause = error.__cause__
        if isinstance(cause, OSError) and cause.errno is not None:
            result['errno'] = cause.errno
        elif isinstance(cause, ValueError) and cause.args and isinstance(cause.args[0], str) and cause.args[0] in {
                'unsupported_service_template', 'untrusted_installation', 'oversized_file', 'control_target_mismatch'}:
            result['reason'] = cause.args[0]
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        result = {'ok': False, 'error': 'preflight_failed', 'stage': stage}
    print(json.dumps(result), flush=True)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
