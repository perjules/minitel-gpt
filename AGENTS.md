# Working on this fork

This repository forks `jherard-fr/minitel-gpt`; preserve straightforward upstream
integration. Keep the existing source layout and unpackaged application model.

- Before upstream synchronization, follow **docs/UPSTREAM_SYNC.md** in full.
- Before changing dependencies, installation, systemd or deployment, read
  **docs/RUNTIME.md**. `pyproject.toml` is authoritative; `uv.lock` and requirements
  exports must agree. Keep explicit Gmail/PRONOTE SDK pins unless the task includes
  reviewing an SDK upgrade.
- Run `uv sync --locked --all-extras`, then `uv run --locked --all-extras pytest`
  and `uv run --locked --all-extras ruff check .`. Check requirements exports with
  `uv run --locked --all-extras python scripts/export_requirements.py --check`.
- Do not run Pi installation/deployment helpers on a development host. Deployment
  tests use temporary directories and mocks. Describe physical Pi/Minitel checks
  separately from offline evidence.
- Do not modify owner-edited personality/Rose text or data as part of a tooling,
  deployment or upstream synchronization task unless explicitly in scope.
- Preserve `.env`, local prompts, memory, logs, enrollment and encrypted credentials.
  Test with synthetic accounts and mocked APIs. Never invoke live probes or send
  messages as part of the automated suite.

Development happens on feature branches based on `develop`; stable `main` follows
physical validation. See docs/TESTING.md for test collection and compatibility.
