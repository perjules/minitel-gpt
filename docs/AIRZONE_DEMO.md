# HACK AIRZONE demonstration — 7–8 November 2026

Temporary opt-in read-only function for ordinary Mistral **Conversations** chat,
on the existing Pi 3 A+ and original Minitel SD. `HACK AIRZONE` installs no service.
The separate Airzone checkout supplies `diagnostics.py`; Minitel never imports
its adapter or exposes its write methods. No SSH/Tailscale transport is needed.
Natural-language tool calls require Mistral Conversations. The explicit terminal
command `HACK AIRZONE` is a local owned process stream and needs no model/API key.

## Prepare the original Minitel SD

Use the ordinary `minitel` account. Keep both checkouts separate. If GitHub
authentication on the Pi is unavailable, transfer a source-only Git bundle as
described in the Airzone weekend runbook; do not copy credential files.

```bash
git clone --branch develop https://github.com/perjules/airzone.git /home/minitel/airzone
cd /home/minitel/airzone
git rev-parse HEAD
git merge-base --is-ancestor 3408d6398bb3cdd6ae9f5e4988e6cd5679e74a58 HEAD
git merge-base --is-ancestor 35d1bf23f13d029445adbcb6e6a33eef06581e07 HEAD
/usr/bin/python3 diagnostics.py state --mock
```

The standalone CLI requires Python 3.10+ and the standard library only. Diagnostics
need no dashboard installation. Confirmed `HACK AIRZONE` commissioning below
uses an already installed Aizone service on this same card. Julian has authorized
this cohabitation; SD migration is his separate operation.

Use the existing Minitel admin branch selector to choose `feature/airzone-demo`,
review its commit, then activate it. Alternatively, from a clean installed checkout:

```bash
cd /home/minitel/minitel-gpt
git fetch origin feature/airzone-demo
git switch --track origin/feature/airzone-demo
sudo bash scripts/install_runtime.sh --branch feature/airzone-demo
```

Use `git switch feature/airzone-demo` if the local branch already exists. This
manual path requires running the installer immediately after switching code; the
admin branch selector is preferred because it stages before changing live code.
The existing Minitel deployment path retains its previous release.
Check `/var/lib/minitel-gpt/deployment.json` for `ready` after activation.

Edit `/home/minitel/minitel-gpt/.env`, preserving existing entries:

```dotenv
AIRZONE_DEMO_ENABLED=1
AIRZONE_CHECKOUT=/home/minitel/airzone
AIRZONE_PYTHON=/usr/bin/python3
AIRZONE_GATEWAY=
AIRZONE_PORT=3000
AIRZONE_SYSTEM_ID=1
AIRZONE_DEMO_MOCK=1
```

Restart the existing Minitel services after configuration changes:

```bash
sudo systemctl restart minitel-chatgpt admin-ui
```

`AIRZONE_DEMO_MOCK=1` is an explicitly simulated rehearsal, never a fallback.
Leave `MISTRAL_ANSWER_API=conversations` and use the Mistral provider. A valid
existing Mistral key is required for natural-language selection and synthesis;
the explicit scrolling hack command is entirely local and works without it.
The checkout and Python paths must be absolute and readable/executable by the
Minitel service account. The gateway must be a literal RFC1918/loopback IPv4,
without a URL, hostname or port. Port/system defaults are 3000/1.

## Pre-departure smoke test

1. Run the CLI mock command above. Expect schema 1, `ok:true`, `demo:true`, six
   **synthetic** zones. This does not prove compatibility with the house gateway.
2. With the environment above, ask the Minitel “Quelles sont les températures des
   chambres ?” or “HACK AIRZONE”. Expect an explicit **SIMULATION**
   label, observation time in Europe/Paris and reported temperatures/settings.
   `HACK AIRZONE` opens the continuous journal, including explicitly fictional
   MF-83 antics and actual `DIAG` stage results. ANNULATION kills the worker and
   its CLI children; SOMMAIRE/GUIDE also stop it before their normal action.
   At completion the fixed frame opens on the last page. RETOUR/SUITE browse the
   retained transcript without repeating a diagnostic. No artificial sleeps or
   SUITE wait at EOF are added; normal serial transmission time still applies.
   After success, answer OUI to rehearse dashboard activation and show its QR.
   The simulated gateway is never saved.
3. Ask “Trouve l'API Airzone.” Expect a simulated candidate, **not** verified HVAC.
4. Temporarily use an unavailable checkout path and restart; ask about Airzone.
   Expect a compact diagnostic failure, no invented temperatures. Ask an unrelated
   question next; ordinary chat must work. Restore the checkout path and restart.
5. Try requesting a setpoint change or benchmark: neither is exposed. Use SUITE,
   RETOUR and SOMMAIRE to verify the existing result/input controls on the Matra.

## Saturday, 7 November

1. Boot the original Minitel SD on the house Wi-Fi. Run the demonstration locally
   on this same Pi. Verify normal chat first.
2. Set `AIRZONE_DEMO_MOCK=0`. If known from the router DHCP list/MAC match, put the
   actual gateway IPv4 in `AIRZONE_GATEWAY`. Restart the existing Minitel services.
   Check directly, substituting that verified address:

   ```bash
   /usr/bin/python3 /home/minitel/airzone/diagnostics.py probe --host 192.168.1.50 --timeout 2 --deadline 5
   ```

3. Ask “Trouve l'API Airzone.” Discovery checks the configured host, otherwise up
   to 32 existing IPv4 neighbor entries. It never accepts model-supplied hosts or
   subnet ranges. An empty/incomplete result is inconclusive; use the router list
   and configure its verified host. With several candidates, choose the actual
   gateway in local configuration; the model never chooses arbitrarily.
4. Ask “Connecte-toi à la clim.” (`probe`), then temperatures (`state`), or type
   “HACK AIRZONE” for discovery → probe → state, stopping at the first failure or
   ambiguity. A normal temperature question reads a configured host directly;
   the explicit hack command always includes detection and probing.
   “Identifie l'API Airzone” selects
   `identify`, which only establishes a responding API.
5. Compare returned rooms/IDs, temperatures and settings with physical thermostats.
   A successful read is device-reported HVAC state, **not proof of a running
   compressor**, correct physical mapping or removal of the cloud subscription.
   Answer OUI to activate the dashboard with configured controls, save the verified gateway in
   Minitel's `.env`, and show the dashboard QR. NON/empty input cancels activation.
   Capture both Git revisions and the observation time in private on-site notes.
   Real hardware and live model wording remain acceptance tests, not fixture claims.
6. After the demonstration, shut down normally, remove power, remove and retain
   the original credential-bearing SD. Boot the separately prepared Airzone SD.
   Permanent controller/Tailscale commissioning belongs to Airzone's
   `docs/WEEKEND_RUNBOOK.md`. Copy no credentials or Minitel filesystem to it.

## Bounds and failure behavior

One advertised function, `airzone_diagnostic`, accepts only `action`:
`discover`, `identify`, `probe`, `state`. The existing Mistral function-call/result
format selects one action, followed by one synthesis with tools removed. The
explicit command `HACK AIRZONE` directly requests discovery → probe → state
through that same executor, in both real and mock mode, including with a
configured host. The model cannot stop at discovery or refuse the diagnostic.
The terminal intercepts that explicit command before the ordinary chat worker and
uses `services/stream_sources.py` to own `services/airzone_hack.py` and its CLI
children in one killable process group. No new daemon or general command runner
is introduced. This isolated local stream does not enter chat history, automatic
memory, prompt recall or printing. The admin conversation preview retains its
ordinary model-selected/factual tool response rather than opening a UART view.

Its voice uses fictional punch-card SQL injection, hash degaussing, a missing
floppy drive and overambitious relay K7—under an explicit `MISE EN SCÈNE` label.
Actual stage start/completion lines use `DIAG`; no fictional exploit changes the
operations performed. There are no random diagnostic successes/failures and no
fake progress percentages. The final `BILAN FACTUEL` is generated deterministically
from validated CLI evidence, without model interpretation. It states simulation
versus real mode, successful/failed stages, gateway/port/system, API version when
available, zone IDs/names/temperatures/setpoints/on/mode, master hints and the
observation time. It cannot offer settings changes or claim a physical compressor
is running. The previous liberation slogan
is no longer a special command. Ordinary natural-language diagnostics still work.
An empty discovery is reported as inconclusive without model interpretation. No web
tool is mixed into the diagnostic request. Only relevant Airzone/AC/room-temperature
questions enter this path; ordinary chat and other service workflows retain their
existing routing. Disabled means no advertised Airzone tool or local invocation.

### Rehearse detection instead of only reading mock temperatures

Keep `AIRZONE_DEMO_ENABLED=1` and `AIRZONE_DEMO_MOCK=1`; no alternative script or
new setting is needed. Type `HACK AIRZONE` on the Minitel. It invokes the actual
CLI's mock discovery, mock probe, then mock state, reporting the phases as they
run. All three phases are simulated; no real appliance is contacted.
The CLI's mock discovery returns a predefined candidate, and its final state
remains the same six-zone synthetic fixture. This rehearses the diagnostic
workflow, not physical discovery or arbitrary simulated failure scenarios.
To exercise each CLI phase manually, on the same Pi:

```bash
/usr/bin/python3 /home/minitel/airzone/diagnostics.py discover --mock --host 127.0.0.1
/usr/bin/python3 /home/minitel/airzone/diagnostics.py probe --mock
/usr/bin/python3 /home/minitel/airzone/diagnostics.py state --mock
```

For the real run set `AIRZONE_DEMO_MOCK=0` and restart the Minitel services. A
trusted configured gateway is checked specifically; without it only the existing
neighbor table is used. No free-form host, subnet scan or write is exposed.

Ordinary tool answers have a 24-second budget (or the shorter configured request
timeout), including both provider requests and local subprocesses. The local hack
worker gives its diagnostic sequence 24 seconds; its scrolling viewer has no
overall or idle deadline. Echo gating happens before the worker is launched.
Each CLI call
has a 5-second deadline (discovery 8), with up to one extra second for process
cleanup; at most three CLI calls, no retry, benchmark, continuous polling or scan
range. Discovery over 32 neighbors fails explicitly; consult the router list and
configure a known host rather than expanding chat scanning. The `identify` tool
is version/webserver evidence; only `probe/state` require verified nonempty zones.

Both output streams are captured with a combined 1 MiB ceiling enforced during
reading. Timeout/output overflow kills the process group. Nonzero exit JSON is
parsed; unknown schemas, mismatched operation/target/mode/exit status, missing or
invalid fields, non-finite numbers and stale observations are rejected. Additional
v1 fields are accepted but not sent to the model. Current means completion/read
time within 120 seconds in the past or 30 seconds in the future, allowing small
clock skew; this is an HTTP observation time, not a sensor-refresh timestamp.

Device labels are bounded single-line data with control/format characters removed;
vendor error details and raw payloads are not passed to the model. Instructions
explicitly treat device strings as evidence rather than commands. Logs include
only operation, exit status, stream sizes and failure class. The scrolling hack
also writes private evidence as described below. Results use existing Videotex
wrapping/pagination/input and the already validated scrolling view. Failure ends
with a truthful summary and the next question remains ordinary chat. No real
failure switches to fixtures.

## Evidence to return to the Airzone implementation thread

The primary handoff can be a **photograph of the final Minitel summary**, pasted
into ChatGPT. Include the real/simulation label, gateway, zone IDs and observation
time (use RETOUR if the summary spans pages). JSON is the backup for exact
versions, longer labels, limits or vendor errors. Nothing is uploaded automatically.

The Airzone CLI itself writes one v1 JSON object to stdout; it does not retain
an automatic discovery log. Ordinary Minitel chat logs omit device data. The
scrolling `HACK AIRZONE` worker now saves the **latest** report separately for
real and simulated attempts:

```
/home/minitel/airzone-evidence/minitel-last-real.json
/home/minitel/airzone-evidence/minitel-last-mock.json
```

The default is `~/airzone-evidence` for the service user; optional operator setting
`AIRZONE_EVIDENCE_DIR` overrides it. The directory is private (0700), reports are
0600 and atomically replaced, and fixed lock files protect writes. There is no
unbounded archive. Each completed CLI stage checkpoints the report before the
next stage; cancellation may leave `interrupted` or a `running` partial report,
never an invented completion. Storage failure is shown on screen and does not
discard useful diagnostics. Photos remain a fallback.

This is a **Minitel report**, `report_version:1`, containing both repository
revisions, run start/end/status, CLI exit codes, stderr byte counts and the original
validated producer envelopes under `diagnostics[].result`. Those nested objects
still use Airzone schema v1 unchanged. They include fields omitted by the compact
chat projection: API version, master hints, limits, step, zone errors, warnings,
partial error evidence/vendor details. No credentials, environment dump, arbitrary
stderr, fictitious telemetry or `--raw` response capture is added. Treat any device
text in that private JSON as untrusted evidence, not instructions. The atomic
writer's existing 2 MiB ceiling applies; oversized evidence is not silently truncated.

If the photograph needs a more detailed backup, copy/paste the report:

```bash
cat /home/minitel/airzone-evidence/minitel-last-real.json
```

Include what you compared physically: actual room-to-zone mapping, the intended
six zones, thermostat temperatures/settings and which thermostat is master. A
candidate or API version response alone is insufficient; require a successful
non-demo `probe/state` read. Confirm the gateway IP in the router, then put that
verified address into `AIRZONE_GATEWAY` for direct future temperature reads, or
accept the post-run raccordement prompt. Only a completed result belonging to the
exact uncancelled stream can authorize the prompt; older reports cannot be reused.
The existing atomic `.env` writer preserves unrelated settings/comments and sets
0600 permissions. Only the validated IPv4 is saved and applied to the current
process; mock mode never saves its fake host. No HVAC setting is written.

The JSON is enough to continue API/adapter integration; an optional photograph
of `BILAN FACTUEL` is an excellent ceremonial cover sheet. If normalization fails,
the Airzone thread may additionally request a private `diagnostics.py state --raw`
capture from its runbook; normal Minitel chat never enables that flag.

Both applications can run on the current card. Its configuration/service stay on
that card; a fresh card needs its own installation and network setup. This feature
does not clone, copy credentials, scrub services or migrate either application.
Verify SSH/HTTPS from mobile data and after reboot before leaving the Pi.
Settings writes and polling benchmarks remain separate Airzone tasks.

## Disable or roll back

For optional dashboard activation from the same terminal, see
[HACK AIRZONE](AIRZONE_CONNECT.md). It needs one-time protected-helper setup
and the separately installed Airzone service; diagnostic-only use remains unchanged.

Set `AIRZONE_DEMO_ENABLED=0` in `.env`, then restart `minitel-chatgpt` and `admin-ui`.
That removes Airzone chat/terminal entry points. It does not stop a separately
installed Aizone dashboard. To stop it explicitly, use
`sudo systemctl disable --now aizone.service`. The installations remain independent.

For code rollback use the existing admin branch selector to return to `develop`,
or the existing retained-release rollback:

```bash
sudo /usr/local/sbin/minitel-runtime rollback
```

Keep the flag disabled until deliberately re-enabling this feature. The separate
source checkout can remain unused. No merge into `develop` or `main` is part of
this feature delivery.

## Branch-only terminal MAJ

Once this revision is installed, every confirmed terminal `MAJ`/`UPDATE`/`MISE A
JOUR` on **`feature/airzone-demo`** synchronizes sibling `../airzone` from its
`origin/develop`, even when `AIRZONE_DEMO_ENABLED=0` and Minitel has no new commits.
The path is always `<Minitel checkout parent>/airzone`, independent of
`AIRZONE_CHECKOUT`. The preview says synchronization is included; the existing
OUI/NON confirmation covers both repositories.

A bounded user-level worker runs `git fetch origin develop` then `git merge
--ff-only origin/develop`. The Airzone checkout must be clean and on `develop`;
missing checkout/authentication, wrong branch or divergence reports an error and
does not queue Minitel deployment. Resolve those cases via SSH. No hard reset,
automatic stash, package/service installation or HVAC operation is added. An
already-current checkout still receives that fetch/fast-forward check.

After OUI, leaving the screen may let the already-authorized Airzone Git operation
finish, but cannot queue a later Minitel deployment after exit. Airzone updates
first; a later Minitel deployment failure does not roll back its separate checkout.
With zero pending Minitel commits, synchronization finishes without a restart.
The admin console, root helper and other Minitel branches are unchanged. The
first update using old code installs this behavior; subsequent MAJ uses it.

## Offline validation

`tests/fixtures/airzone/` contains actual JSON from `diagnostics.py state --mock`
and the documented missing-gateway failure, with producer provenance. Consumer
tests refresh timestamps only for replay and explicitly label synthetic non-demo
variants. Tests mock provider selection, dispatch and synthesis; they do not prove
that a particular live model always selects the intended action or wording.
Physical Pi/Matra rendering, house discovery and gateway compatibility are pending.
