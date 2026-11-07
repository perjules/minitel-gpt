# CONNECT AIRZONE — dashboard commissioning

The exact local terminal command `CONNECT AIRZONE` runs the existing scrolling
discover → probe → state sequence, then asks **Activer le dashboard réel ? OUI/NON**.
It is never an LLM tool. `HACK AIRZONE` keeps its detection/photo/.env pairing.
Mock OUI reports simulation; no sudo, config, service or .env changes occur.
ANNULATION kills diagnostics as usual. NON/empty/exit makes no service change.

## Optical handoff — scan the Minitel

After confirmed success, **CONNECT AIRZONE automatically displays the current LAN
dashboard as a QR code**. Scan the CRT with a phone on the same Wi-Fi to open the
dashboard. If no LAN URL is available, it opens the configured Tailnet URL instead;
the phone then needs Tailscale access. URLs are entry points, not a claim that
physical HVAC compatibility or phone connectivity has been independently verified.

This reuses the [physically validated QR viewer](TERMINAL_QR.md); no encoder,
shortener, remote QR service, listener or dependency is added. Numbered link
metadata retains exact validated URLs, separately from wrapped/sanitized screen
text. LAN links come first, followed by Tailnet/configured origin; duplicate URLs
share one number. Pi-only loopback addresses are text-only, never phone QR links.

RETOUR restores the same address/report page. `LINK n` / `LIEN n` opens another
numbered dashboard link; normally LINK 1 is LAN and LINK 2 is Tailnet. Normal
pagination/input/exit behavior is unchanged. Missing or oversized QR destinations
leave the factual text and full address available; there is no truncated encoding.
In mock rehearsal, the QR leads to the actual installed dashboard while the
simulated diagnostic remains explicitly labelled **SIMULATION**. It does not
activate the real service or write HVAC settings. Failed real commissioning does
not present a successful connection QR or inherit old web-search sources.

Smoke test: MAJ, CONNECT AIRZONE, OUI; scan the automatically shown LAN QR, RETOUR,
LINK 2 for Tailnet (if listed), RETOUR, then an ordinary chat question. Compare
decoded destinations with the numbered address page and confirm simulation/real
labelling and thermostat readings separately. Existing generic QR scan acceptance
does not substitute for on-site Airzone/phone acceptance.

Under the QR, mock and real runs retain a dedicated **ADRESSES DU DASHBOARD** page:
the configured Tailscale URL (including its actual HTTPS port), current **Wi-Fi/LAN**
URLs, and the loopback URL labelled **Sur le Pi uniquement**. The installed
deployment manifest supplies the configured origin/port; Minitel makes one GET to
`http://127.0.0.1:PORT/api/addresses` for current `origin`, `loopback`, `lan_enabled`
and `lan_urls`, following Airzone's LAN handoff of 6 November. That endpoint does
no gateway I/O and works in mock mode. LAN IPs are never cached or inferred from
the HVAC gateway IP: moving the Pi or renewing DHCP is reflected on the next run.

The optional request has a two-second socket timeout and 16 KiB/16-address limits;
it never uses environment proxies or follows redirects. Origin/loopback must match
the installed configuration, LAN URLs must be private IPv4 HTTP origins on that
same dashboard port, and malformed/inconsistent responses are rejected. No raw
response or vendor text becomes screen instructions.

Missing configuration/addresses are explicitly unavailable, never replaced with
fixture URLs or guessed IPs/ports. Endpoint failure preserves configured Tailnet
and Pi URLs and a successful HVAC activation. An installed `lan_access:true` flag
with failed address reporting is **adresses indisponibles**, not a claim that LAN
is off. A disabled LAN and no current private IPv4 have distinct messages. Older
Airzone services lacking the endpoint show LAN as unconfirmed. A non-Tailscale
configured origin is labelled accordingly. These URLs are entry points, not proof
that a phone has successfully reached them.

The Airzone dashboard project owns LAN listening/access rules; this integration
makes no listener, firewall, Serve or router changes. Its commissioning helper
preserves the `lan_access` deployment key and still forces real HVAC writes off.
Diagnostic/photo pages remain available with RETOUR/SUITE, with normal chat input
immediately available. Normal Pi installations show Tailnet, LAN and Pi URLs together
on the final page; additional interface addresses paginate normally.

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
