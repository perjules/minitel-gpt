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
installation before restarting services. Old releases are retained for recovery;
remove obsolete releases manually only after checking active/previous references.

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
