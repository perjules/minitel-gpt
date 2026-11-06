# HACK AIRZONE demonstration — 7–8 November 2026

Temporary opt-in read-only function for ordinary Mistral **Conversations** chat,
on the existing Pi 3 A+ and original Minitel SD. No Airzone service is installed.
The separate Airzone checkout supplies `diagnostics.py`; Minitel never imports
its adapter or exposes its write methods. No SSH/Tailscale transport is needed.
Other providers and the legacy chat-completions path do not have this tool.

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

The standalone CLI requires Python 3.10+ and the standard library only. Do **not**
run Airzone `install.sh`, install its dashboard dependencies, or start `aizone`
on this card. No credentials need to be transferred between cards.

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
existing Mistral key is required for natural-language selection and synthesis.
The checkout and Python paths must be absolute and readable/executable by the
Minitel service account. The gateway must be a literal RFC1918/loopback IPv4,
without a URL, hostname or port. Port/system defaults are 3000/1.

## Pre-departure smoke test

1. Run the CLI mock command above. Expect schema 1, `ok:true`, `demo:true`, six
   **synthetic** zones. This does not prove compatibility with the house gateway.
2. With the environment above, ask the Minitel “Quelles sont les températures des
   chambres ?” or “HACK AIRZONE”. Expect an explicit **SIMULATION**
   heading on the first page, observation time in Europe/Paris and reported temperatures/settings.
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
Its voice is a scrappy retro hacker/mainframe report, without invented exploits
or a claim that the subscription was bypassed. The previous liberation slogan
is no longer a special command. Ordinary natural-language diagnostics still work.
An empty discovery is reported as inconclusive without model interpretation. No web
tool is mixed into the diagnostic request. Only relevant Airzone/AC/room-temperature
questions enter this path; ordinary chat and other service workflows retain their
existing routing. Disabled means no advertised Airzone tool or local invocation.

### Rehearse detection instead of only reading mock temperatures

Keep `AIRZONE_DEMO_ENABLED=1` and `AIRZONE_DEMO_MOCK=1`; no alternative script or
new setting is needed. Type `HACK AIRZONE` on the Minitel. It invokes the actual
CLI's mock discovery, mock probe, then mock state, before the model reports the
observed phases. All three phases are simulated; no real appliance is contacted.
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

The whole answer has a 24-second budget (or the shorter configured request
timeout), including both provider requests and local subprocesses. Each CLI call
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
only operation, exit status, stream sizes and failure class. Results use existing
Videotex wrapping/pagination/input; no terminal state or custom dialogue mode is
created. Failure returns a normal answer and the next question remains ordinary
chat. No real failure switches to fixtures.

## Disable or roll back

Set `AIRZONE_DEMO_ENABLED=0` in `.env`, then restart `minitel-chatgpt` and `admin-ui`.
That is sufficient to remove all Airzone chat capability. No daemon to stop,
credentials to revoke, or Airzone service/configuration to remove on this card.

For code rollback use the existing admin branch selector to return to `develop`,
or the existing retained-release rollback:

```bash
sudo /usr/local/sbin/minitel-runtime rollback
```

Keep the flag disabled until deliberately re-enabling this feature. The separate
source checkout can remain unused. No merge into `develop` or `main` is part of
this feature delivery.

## Offline validation

`tests/fixtures/airzone/` contains actual JSON from `diagnostics.py state --mock`
and the documented missing-gateway failure, with producer provenance. Consumer
tests refresh timestamps only for replay and explicitly label synthetic non-demo
variants. Tests mock provider selection, dispatch and synthesis; they do not prove
that a particular live model always selects the intended action or wording.
Physical Pi/Matra rendering, house discovery and gateway compatibility are pending.
