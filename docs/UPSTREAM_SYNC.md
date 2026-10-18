# Upstream synchronization runbook (human or AI)

Use this procedure to incorporate fixes from `jherard-fr/minitel-gpt` into the
`perjules/minitel-gpt` fork. Read AGENTS.md and RUNTIME.md first. Work in a development
checkout, not the deployed Pi with its production `.venv` link. Keep code/layout
close to upstream; retain the fork's features, owner text assets and local data.

## 1. Establish the actual state

```bash
git status --short
git remote -v
git branch -avv
git fetch origin
```

Do not overwrite uncommitted work. Verify `origin` identifies the owner's fork.
Verify `upstream` points to `https://github.com/jherard-fr/minitel-gpt.git`; if it
does not exist, add it with `git remote add upstream <that-url>`. Never change an
existing unexpected remote without resolving its identity. Do not assume a branch
or commit from an earlier conversation is current. Inspect local commits ahead
of `origin/develop` before selecting the integration base.

Start from the latest intended `develop`, including any explicitly accepted local
work. Fast-forward it from `origin/develop` only when the graph permits. Create a
fresh integration branch, using an unused name if needed:

```bash
git switch develop
git merge --ff-only origin/develop
git switch -c sync/upstream
git fetch upstream
git log --oneline HEAD..upstream/main
git diff --stat HEAD...upstream/main
git merge upstream/main
```

If upstream changes its default branch, verify and use its actual branch. If the
fast-forward is impossible, inspect the divergence and preserve accepted local
commits. Never `reset --hard upstream/main`, force-push, or replace the fork with
upstream. Do not auto-resolve source conflicts with a blanket ours/theirs choice.

## 2. Review dependencies even after a clean merge

Inspect the incoming diff from the merge base, especially:

- New imports and changed SDK/API assumptions in Python files.
- `install.sh`, feature installers, apt package lists and Python version needs.
- Service users, privileges, working directories, restart behavior and networking.
- Python requirements, native libraries, fonts, build prerequisites and uv settings.
- Upstream security fixes and dependency minimum versions.

Our Python libraries are isolated from Debian. Adding/upgrading an apt Python
package upstream does not update our environment. Translate that requirement into
`pyproject.toml`; retain genuine OS executables/native libraries in the installers.
Map the dependency to core or the appropriate extra. All installers must still use
the common runtime helper. No source relocation or packaging is needed.

Preserve explicit Gmail/PRONOTE SDK pins unless an upgrade is in scope and verified
against their adapter tests. A fix requiring an upgrade must not be ignored merely
to preserve a pin: make the upgrade and validation explicit. Update the protected
Wi-Fi/helper copies via ROOT_FILES if their structure changes. Reconcile systemd
templates and credential settings; do not reintroduce system-Python service paths.

If upstream adopts dependency tooling, reconcile its manifest/configuration with
ours rather than maintaining competing dependency sources.

## 3. Reconcile and regenerate

`pyproject.toml` is authoritative; `uv.lock` and requirements files are generated.
Resolve manifest conflicts first. Reuse a suitable existing lock as a starting
point, then run `uv lock` and inspect the result. Never hand-edit artifact hashes.
Preserve unrelated versions where possible; do not pass blanket `--upgrade`.
For a reviewed security fix use a targeted upgrade, for example
`uv lock --upgrade-package requests`, with appropriate manifest bounds.

```bash
uv lock
uv sync --locked --all-extras
uv run --locked --all-extras python scripts/export_requirements.py
git diff -- pyproject.toml uv.lock requirements-dev.txt requirements-gmail.txt requirements-pronote.txt
```

A lockfile resolves all extras/groups, even those not installed on a particular
Pi. Investigate incompatible extras instead of dropping another feature. Review
32-bit/64-bit ARM wheels and native builds; a successful x86 installation does not
establish ARM compatibility. If uv needs changing, align config/uv-version,
pyproject's required-version, bootstrap and CI together.

## 4. Validate proportionately

```bash
uv lock --check
uv sync --locked --all-extras
uv run --locked --all-extras pytest
uv run --locked --all-extras ruff check .
uv run --locked --all-extras python scripts/export_requirements.py --check
bash -n install.sh scripts/install_*.sh
git diff --check
```

Run focused deployment tests after changes to installers, units or admin updates.
Keep the full offline suite offline: no live credentials, probes, model requests,
email sending or physical printing. Record passes, skips and limitations. Do not
invent hosted CI results or claim Pi/Minitel success from mocked tests.

OS packages, Python changes and system privileges may require manual provisioning
on the Pi. State exactly when `sudo bash install.sh` must be rerun. Preserve the
installed feature selection and branch. Source pulls alone do not synchronize
the production environment.

## 5. Review and promote

Inspect the complete integration diff for lost fork behavior and accidental text,
configuration, credential or data changes. Commit the integration and describe
upstream fixes, dependency/deployment changes, validation and physical checks still
needed. Follow the current user's requested publishing/PR scope.

Integrate into `develop` after review. Promote to `main` only after the intended
physical acceptance. Deployment follows the installed fork branch, never an
upstream hard reset. The admin updater must retain matching code/environment
rollback pairs; it does not provision newly required OS packages automatically.
