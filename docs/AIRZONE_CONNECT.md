# CONNECT AIRZONE — dashboard commissioning

The exact local terminal command `CONNECT AIRZONE` runs the existing scrolling
discover → probe → state sequence, then asks **Activer le dashboard réel ? OUI/NON**.
It is never an LLM tool. `HACK AIRZONE` keeps its detection/photo/.env pairing.
Mock OUI reports simulation; no sudo, config, service or .env changes occur.
ANNULATION kills diagnostics as usual. NON/empty/exit makes no service change.

Both applications can run on the current Minitel SD, as Julian now explicitly
authorizes. SD migration, credential transfer and service removal are his separate
operations; no cloning, migration or card scrub is automated here.

## One-time SSH preparation

An active Aizone service is required, installed by Airzone's own installer with
`/opt/aizone/deployment.json`, `/api/health` and a root-owned release. A foreground
`app.py --mock` is not this installation. Reuse your working installation/Serve
URL. For first installation on this card, follow Airzone's README/runbook, using
the actual private HTTPS origin and an unused backend port, for example:

```bash
cd /home/minitel/airzone
git pull --ff-only origin develop
sudo bash install.sh --mode mock --origin https://YOUR-PI.YOUR-TAILNET.ts.net:8443 --port 8081
```

Replace the origin. Port 8081 is only an example matching Serve to 127.0.0.1:8081;
never overwrite a working deployment with guessed URL/port settings. Minitel
does not configure Tailscale or first-install dashboard packages.

Install this revision's protected helper once; old MAJ code cannot install new
privileged assets it does not know about:

```bash
cd /home/minitel/minitel-gpt
git switch feature/airzone-demo
git pull --ff-only origin feature/airzone-demo
sudo bash scripts/install_runtime.sh --branch feature/airzone-demo
```

Wait for `ready` in `/var/lib/minitel-gpt/deployment.json`. No new dependencies.
Use `AIRZONE_DEMO_ENABLED=1`, `AIRZONE_DEMO_MOCK=1` for rehearsal. At the house,
set mock to `0`, restart `minitel-chatgpt`, enter CONNECT AIRZONE, then OUI.

## Confirmed activation and recovery

The root-owned isolated helper accepts only bounded `{host,port,system_id}` JSON
stdin, on installed `feature/airzone-demo` only, with the shared Airzone install
lock. It requires an active installed dashboard and does another **real state read
as unprivileged aizone**, using its installed CLI and the same v1 consumer validator.
Public/loopback/invalid targets, simulation, stale/malformed/oversized responses or
failure stop before config changes. Photos/old reports cannot authorize activation.

It privately backs up config/unit/deployment manifest, then atomically updates
`/etc/aizone/config.json` with the verified host/port/system. Other settings remain,
with deliberate exceptions: preserve the deployment origin, force
`allow_commands:false`, `zone_controls:{}`, `master_zone_id:null`,
`supported_modes:[]` and `expected_zone_ids:[]`. Discovery does not verify complete
physical room/master mapping or safe write capabilities.

It renders the **installed Airzone service template** for real mode, reusing the
installed release, venv and backend port; reloads/restarts only Aizone; verifies
health revision/non-demo mode; and does one dashboard GET state requiring online,
available zones with commands disabled. Only then does its deployment manifest
record real mode for Airzone's updater. Minitel saves/applies AIRZONE_GATEWAY after
success; a failed .env save is reported separately from dashboard activation.

No Git, installer, checkout code, package manager, router or Tailscale command runs
as root here. Sudo permits only this fixed helper without arguments, plus fixed
`rollback`. Activation has a 120-second budget; restoration commands are separately
bounded. Failure restores old config/unit/manifest and restarts the previous mode;
failed restoration is explicitly uncertain and requires SSH. Power loss/SIGKILL
cannot guarantee automatic restoration; the private backup remains available.

After OUI, SOMMAIRE/GUIDE may leave the waiting screen; the already authorized
transaction finishes/restores in its bounded worker. This intentionally differs
from killing diagnostics halfway through a configuration transaction. The final
summary verifies the local dashboard, not external HTTPS or physical compressor
operation. Keep diagnostic photos/JSON; verify the phone URL and reboot separately.

Restore the last dashboard configuration/mode with:

```bash
sudo /usr/local/libexec/minitel-airzone-connect rollback
```

Backup: `/var/lib/minitel-gpt/airzone-before-connect.json` (0600, overwritten only
after preflight passes). Restore refuses a different installed Airzone release;
after a source upgrade, use Airzone's own release rollback first if necessary.
This does not restore the separate Minitel .env gateway; correct/remove that line
and restart Minitel if required. Disable terminal entry points with
`AIRZONE_DEMO_ENABLED=0` and restart Minitel; stop the independent dashboard with
`sudo systemctl disable --now aizone.service` if desired.

## Validation and Pi smoke test

Automated tests use actual v1 mock output with refreshed replay timestamps and
explicitly synthetic non-demo variants. They exercise config/mode transactions,
failure restoration, the fixed subprocess invocation, GET-only dashboard checks,
mock/confirmation/flag gating, protected runtime assets and ordinary chat recovery.
They never install a service or probe real HVAC. The Airzone repository is unchanged.

Before departure, install the helpers/service and run CONNECT AIRZONE with mock=1:
all phases simulated, OUI causes no config/service change, photo summary remains
available, ordinary chat still works. Check the phone's existing demo URL. On site,
with mock=0, confirm a real state read, OUI, then check the phone page has no demo
banner and matches thermostats. Verify remote HTTPS/SSH after reboot. This proves
the read-only integration; physical compressor operation and writes remain untested.
