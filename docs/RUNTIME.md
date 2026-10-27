# Shared uv runtime

The source layout stays unchanged and the application is not packaged. All
application Python processes use one active, isolated environment. Python itself
comes from the OS (3.11+); Python libraries come from `uv.lock`, without inheriting
Debian site-packages. OS tools/libraries still use apt.

## Files and ownership

| Path | Purpose |
| --- | --- |
| `pyproject.toml` | Core requirements, Gmail/PRONOTE/printer extras, development tools |
| `uv.lock` | Committed dependency resolution and artifact hashes |
| `config/uv-version` | Tested bootstrap and CI uv version |
| `/opt/minitel-gpt/releases/<id>/venv` | Root-owned environment, created at its final path |
| `/opt/minitel-gpt/releases/<id>/root` | Root-owned Wi-Fi, credential-helper and deployment code; service definitions |
| `/opt/minitel-gpt/current` | Atomic pointer to the active release |
| `/opt/minitel-gpt/venv` | Stable interpreter path through `current/venv` |
| `/var/lib/minitel-gpt/runtime.json` | Installed branch, active/previous code+environment pairs, selected extras |
| `/var/lib/minitel-gpt/deployment.json` | Readable deployment phase/error type; no credentials |
| `/var/lib/minitel-gpt/uv-cache` | Root-managed download/build cache |
| `<checkout>/.venv` | Production compatibility link; a normal local environment in development |

The production tree and all its parents must be root-owned and not writable by
other accounts. Services execute `/opt/minitel-gpt/venv/bin/python` directly.
Terminal/admin remain `User=minitel`; Wi-Fi remains root. Wi-Fi and the Gmail
helper run copied root-owned code with Python `-I`, never imports from the writable
checkout. The helper retains its fixed command and encrypted credential flow.

uv is maintenance tooling, not part of boot. The initial bootstrap uses OS Python
with stdlib only to create the first environment; the installed deployment and
credential helpers subsequently use the same environment as the services.
The bootstrap/CI version is pinned to uv 0.13.0. Project commands also accept the
previously tested 0.12.23 series; 0.13 is supported rather than rejected by a
0.12-only upper bound. Use the installer on the Pi, not bare `uv sync`, so the
shared runtime path and installed extras are selected correctly.

## Fresh install or migration on the Pi

Commit or stash tracked changes first. Install from a branch containing this
migration, in `/home/minitel/minitel-gpt`, using the existing `minitel` account:

```bash
sudo bash install.sh
# To explicitly set the branch followed by the admin updater:
sudo bash install.sh --branch develop
```

The default is the previously installed branch, or the checkout's current branch
for the first install. The checkout must be on that branch; explicitly pass its
new branch when deliberately changing deployment branches. It is not silently
forced to `main`. Dependencies are
staged before running services are stopped. First installation enables services;
start them or reboot as described by the installer. Root owns the production
environment; no `chown` to `minitel` is applied to its contents.
Installation runs in a separate systemd worker so the runtime transaction survives
an SSH disconnect. Follow `journalctl -u minitel-runtime-install` for progress.

On first migration the installer detects legacy optional packages by probing the
old interpreter as the **minitel user**. You may also specify `--extra gmail`,
`--extra pronote`, or `--extra printer` explicitly. Extras are unioned with those
already installed; a later installer cannot silently remove another feature.
The original `.venv` directory becomes `.venv.before-uv`, ignored by Git. This is
an archival backup, not a relocated runnable environment: scripts inside it can
still contain the original absolute path. A failed first activation restores it
to `.venv`. After a successful migration, retained uv releases provide rollback.

The main installer retains OS/network provisioning and prepares the shared
runtime through `scripts/install_runtime.sh`. Each feature installer uses that
same helper:

```bash
sudo bash scripts/install_gmail.sh
sudo bash scripts/install_pronote.sh
sudo bash scripts/install_printer.sh
```

Gmail enrollment and `install_gmail.sh --activate` still require the existing
encrypted credentials/systemd checks. Installing dependencies never activates
Gmail, enables PRONOTE, or prints. Already running services may restart after an
environment change. Repeating an unchanged installation retains the rollback
pair and does not recreate the environment. A changed system Python version or
failed import check forces a fresh environment even when the commit is unchanged.

Do not run bare `sudo uv sync` from the production checkout: the deployment helper
sets the environment path, selected extras, system interpreter, and transaction
boundaries. To synchronize after a manual clean source update, rerun `install.sh`.

## ARM and offline setup

The installer supplies C compiler/Python/libffi headers because some 32-bit ARM
packages have no PyPI wheels. Whenever the selected extras include the printer,
the common runtime installer adds BlueZ, JPEG/zlib headers and DejaVu fonts. This
also covers automatically detected printer installations during migration.
Build concurrency is limited to one. Cryptography is wheel-only to avoid an
unexpected Rust build on a small Pi; an unavailable wheel fails preparation while
the active environment remains untouched. There is no fallback to apt Python
libraries. Confirm the actual Pi architecture, OS Python and glibc before rollout.

The pinned uv installer and uncached packages require networking. Offline setup
requires uv plus a complete cache of the selected artifacts and any build
requirements. An apt cache alone is insufficient. Boot needs no package network
access. The supplied lockfile covers platform-specific packages; a host test does
not prove ARM compilation or physical hardware behavior.

## Updates, status and rollback

Admin buttons call fixed, root-owned commands allowed by sudoers. They queue a
separate systemd job with a short delay so the HTTP response can finish; queued
does not mean successfully applied. The job is independent of the admin process.

```bash
sudo /usr/local/sbin/minitel-runtime update
sudo /usr/local/sbin/minitel-runtime rollback
sudo journalctl -u minitel-deploy
cat /var/lib/minitel-gpt/deployment.json
```

Authenticated admin users can also inspect `/deployment-status`. The status moves
through queued/preparing/activating/ready or failed. Errors expose their class;
detailed command failures are in the systemd journal.

The updater checks the installed branch, refuses tracked edits and divergent
history, and fetches only `origin/<installed-branch>`. It snapshots required files
from the selected commit, creates a new environment without development packages,
and checks imports/font rendering. No arbitrary Git archive is extracted as root.
Then it stops only services that were running, switches code and release pointer,
installs matching units, and starts those services again. Wi-Fi can briefly
interrupt SSH/admin access during that switch.

Preparation failures leave the running code/environment untouched. Activation or
service-start failures attempt to restore the previous code, pointer, units,
wrappers and running services. Runtime state is committed after service-state
checks. An explicit rollback restores the retained previous code+environment pair,
including that environment's extras; it requires no dependency download. It does
not restore user data or revoke credentials. The first migration cannot roll back
to pre-uv code through the new admin button; retain a backup/SD image for that.
Retained environments also depend on the OS Python: rollback cannot undo an OS or
system-Python upgrade. Reapply installation after changing the system interpreter.

These checks test imports and systemd running state, not application health or
physical connectivity. Power failure during activation is not an automatically
recovered transaction: inspect runtime state/pointer and reapply the intended
installation before restarting services. Housekeeping retains the active and previous release pairs and removes older
unreferenced generated releases. User archives and legacy log files are untouched.

## Unattended terminal operation and bounded logs

A missing adapter or failed serial read/write is an expected availability state.
The GPT process stays alive, closes the old transport, redetects the port, and
retries after 5, 10, 30, then 60 seconds (remaining at 60 seconds).
Serial writes have a two-second timeout. The first failure is logged once;
continued failures are summarized at most every five minutes. Recovery is logged
only after 30 seconds of stable serial I/O, avoiding repeated open/fail messages.
Unexpected application errors still retain their traceback and systemd restart.

An idle read is not an error. The powered USB adapter may remain usable while the
Minitel is off: the program cannot infer screen power from a successful port open.
No speculative presence probes are sent. Switch the Minitel on and use SOMMAIRE
to redraw if its power cycle was invisible to the adapter.

Detected transport loss discards the interactive session, invalidates Gmail
review tickets, and closes the memory session. Late worker responses cannot
write to the UART or enter the new session. A new session waits until the old
worker has finished; workers are not accumulated across repeated disconnections.
An already-authorized external request that has reached its server cannot be
recalled; recovery never retries sending mail or printing.

The three services send stdout/stderr once to the `minitel-gpt` journal namespace.
There are no application file handlers and no systemd append-to-file redirects.
A namespace-specific configuration sets a 64 MiB persistent budget, 8 MiB files,
seven-day maximum retention, 16 MiB runtime budget and 512 MiB free-space reserve.
Size-based rotation can shorten history during heavy logging. Active files mean
these are journal management limits, not an exact filesystem quota. Rate limits
add a second line of defense; suppressed messages are counted by journald.
Other programs can still fill the filesystem.

```bash
sudo journalctl --namespace=minitel-gpt -u minitel-chatgpt -f
sudo journalctl --namespace=minitel-gpt -u wifi-manager -n 100 --no-pager
sudo journalctl --namespace=minitel-gpt --disk-usage
```

The admin log viewer uses a root-owned isolated Python helper with four fixed,
read-only selections (terminal, admin, Wi-Fi and deployment). Deployment uses the
default journal namespace; application services use minitel-gpt. Its sudoers rule permits no arbitrary units or arguments.
The dashboard shows free disk space. Maintenance exposes generic log/archive and
memory usage totals; scans are bounded and never follow links. Private transcript
files are available only behind the separate private-panel password gate.
Low-space warnings appear below 1 GiB free or 10% free. Partial measurements are
labelled. Diagnostic journals rotate automatically; saved memory, conversations,
Rose transcripts and old `logs/*.log` files are never automatically deleted.

### First rollout and legacy logs

This upgrade needs a **manual installer run**, because the old deployed runtime
does not know about the new protected helper, journal policy and timer assets.
After recovering the Pi and freeing sufficient space yourself, pull the intended
installed branch and run `sudo bash install.sh` on that same branch. Feature
installers use the common runtime helper and install the same policies.

Existing log files are neither truncated, renamed nor removed. Their cleanup is
the owner's responsibility. Free space is required for apt, Git and deployment;
the installer is not a recovery mechanism for a completely full filesystem.
Capture incident evidence before manually removing old logs.

### Runtime/cache housekeeping

After a successful deployment, and weekly via `minitel-housekeeping.timer`,
maintenance retains the active and previous code/environment pairs, removes only
unreferenced generated release directories, and runs uv's cache-prune command.
It holds the deployment lock, validates state against the current pointer, never
follows release-directory symlinks, and ignores unrelated/manual directories.
Neither the legacy venv backup nor any user logs, memory or credentials are
eligible. A failed maintenance pass does not roll back a successful deployment.
The timer records failures in the application journal; a busy deployment causes
that maintenance pass to defer until a subsequent deployment or scheduled run.

```bash
sudo /usr/local/sbin/minitel-runtime housekeeping
systemctl list-timers minitel-housekeeping.timer
sudo journalctl --namespace=minitel-gpt -u minitel-housekeeping --no-pager
```

Before building an environment the helper requires at least 1 GiB free on both
runtime and cache filesystems. This is a staging guard, not a reservation or a
guarantee about concurrent disk users. Journal settings, the reader and timer
are installed with the protected release assets and restored on activation
failure. Explicit rollback to a pre-journal release removes the added integration
and disables the maintenance timer; that old code resumes its old logging policy.

## Developer workflow

Use a separate checkout with a local `.venv` (see TESTING.md):

```bash
uv sync --locked --all-extras
uv run --locked --all-extras pytest
uv run --locked --all-extras ruff check .
```

Requirements files are compatibility exports including core and transitive
requirements; feature exports select only that extra and omit development tools.
They are not separately maintained inputs:

```bash
# After a deliberate manifest/dependency change:
uv lock
uv run --locked --all-extras python scripts/export_requirements.py
uv run --locked --all-extras python scripts/export_requirements.py --check
```

Changing Python/toolchain/OS provisioning requirements may need a manual
`install.sh` run rather than the admin updater, which only deploys code and Python
dependencies. Follow UPSTREAM_SYNC.md when integrating upstream changes.

## Physical acceptance checklist

On the Pi, verify effective units with `systemctl cat` for all three services;
each interpreter must be `/opt/minitel-gpt/venv/bin/python`. Check `.venv`, `current`
and root ownership; run the existing Gmail read-only and PRONOTE diagnostics with
that interpreter. Verify title fonts, serial display, Wi-Fi reconnection/hotspot,
printer preview/BLE, and reboot without package networking. Exercise a deliberate
failed preparation, a normal update and retained-pair rollback. Keep these results
separate from pytest's simulated deployment evidence.

For terminal/logging acceptance, leave the Minitel powered off overnight, then
test adapter removal/reinsertion (including port renumbering), welcome-screen
recovery, quiet retry summaries and bounded journal growth. Check admin log
permissions, storage warnings, timer execution and a retained-pair rollback.
Verify that existing log files and user archives remain unchanged. Hosted CI
simulates failures and validates configuration; it cannot establish physical
Minitel/USB behavior or the Pi's effective journal permissions and storage use.

## Wi-Fi recovery and provisioning

The root Wi-Fi service has a fixed working directory and command environment and
never imports the user-writable application `.env`. NetworkManager calls have
explicit process deadlines and operate on UUIDs; escaped names are decoded.

At boot, the service gives NetworkManager a bounded grace period to connect to a
saved client network. If connectivity is present it leaves reconnection entirely
to NetworkManager. Otherwise it enables the provisioning hotspot once. There is
no periodic monitor that takes over wlan0 after a later disconnect. Saved profiles
and autoconnect preferences are preserved. An explicit setup request tests a unique
candidate, removes only that candidate on failure and restores the hotspot.

The port-80 server permits configuration only when the connection actually arrives
on 192.168.4.1 from the setup subnet while hotspot mode is active. Host headers do
not grant access. Requests need a session-bound token and valid, bounded JSON;
the normal home LAN exposes no network-changing operation. The hotspot is still
open for deliberate local provisioning. Passwords retain spaces and never enter
command logs. Validate boot grace, wrong-password recovery and live provisioning on
the Pi before promoting to main; offline tests mock every NetworkManager command.

## Operation deadlines and resource budgets

Background requests have a six-minute maximum lifetime. Terminal input polling
and reconnection supervision detect expired workers. Since Python cannot safely
cancel arbitrary SDK threads, the production terminal then exits immediately
with failure so systemd starts a fresh process; this bypasses potentially blocked
SDK shutdown hooks. No submitted email, print job or user request is replayed.
An already delivered external operation remains irreversible. Printing also has
a three-minute job budget and tracks its worker across a serial disconnect.

Terminal questions are capped at 2000 characters. Overflow discards the entire
line on ENVOI and requests fresh input; it never submits a truncated command.
Admin HTTP bodies are limited to 2 MiB and knowledge uploads to 256 KiB per file.
All files in an upload are checked before any existing file is replaced. Legacy
knowledge reads consume at most 64 KiB per file before the existing prompt budget.

Runtime subprocesses have a 60-second default deadline; Git fetch gets two
minutes, uv environment preparation twenty minutes, and optional apt provisioning
ten minutes. Timeouts kill the command's process group, including Git transport
children. NetworkManager and admin service/status calls have shorter deadlines.

A deployment now requires five seconds with stable service process identities,
within a twenty-second readiness window. Admin and Wi-Fi must also answer their
local `/healthz` endpoints. The terminal may be waiting for an unplugged adapter.
Legacy rollback releases without health routes receive process stability checks
only. Readiness failure triggers the existing restoration transaction.

## Admin configuration and request integrity

Every admin mutation, including login and logout, requires a session-bound CSRF
token. Forms and JSON requests carry it automatically; scripts using the admin
HTTP API must obtain a session and send the form token or `X-CSRF-Token` header.
Logout is POST-only. Session cookies are HTTP-only with SameSite=Lax. Personality
JSON embedded in the page is escaped before entering JavaScript.

Personality edits and `.env` settings use bounded thread/process locks and atomic
replacement: write a temporary file beside the destination, flush and fsync it,
replace the destination, then fsync the directory. New files are private, existing
personality file modes are preserved, and `.env` is restricted to mode 0600.
All settings submitted together are validated and written in one transaction;
comments and unrelated environment entries are retained. Values cannot inject
new environment assignments. No plaintext backup copies are created.

Invalid personality JSON/schema or malformed environment syntax is rejected
without replacing the original. Restore a valid owner-managed copy before trying
again. If storage fails after replacement, the response reports an unconfirmed
save: reload and inspect the current state before retrying. Atomic replacement
protects individual files, not a multi-file upload as a whole; a later disk error
can leave earlier files saved. Existing legacy logs are never removed.

Knowledge paths must belong to a configured personality and a direct `.txt` file
inside its directory. Symlinked directories/files are rejected for admin edits;
knowledge readers refuse links and non-regular files such as FIFOs. Configuration
reads are capped at 2 MiB. Lock contention fails after a bounded wait instead of
blocking indefinitely. The terminal's direct systemd entry point shares package
exception identities with its adapters so these failures reach the supervisor.

## Admin branch and power controls

See [ADMIN_CONSOLE.md](ADMIN_CONSOLE.md) for the manual first rollout, explicit
ADMIN_PASSWORD requirement and console behavior. New fixed helper operations are
`switch-branch` (bounded JSON stdin containing branch and confirmed commit),
`reboot` and `poweroff`. Only root can run the apply operation; the admin queues
it through the narrowly scoped sudo rule. Branch preparation precedes activation,
and retained-pair rollback also restores the previous branch. Both local commits
and private installation data are protected. A target branch must support the
shared runtime assets. Power requests are delayed transient systemd jobs and do
not run concurrently with deployment. This adds no permanent monitoring service.
