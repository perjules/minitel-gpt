# Offline tests

Run the automated suite with **pytest 9** from the repository root. Python 3.11+
is required by the combined application/Rose suite. Linux is the full-suite
development target: tests exercise `fcntl`, subprocesses and simulated terminal
behavior. CI runs on Python 3.11 and 3.12.

## Development environment

On a development machine, create an environment and install the complete test
dependencies, including the pinned Gmail and PRONOTE SDKs:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

If a prepared environment already contains the runtime and optional test
dependencies, only pytest needs adding:

```bash
.venv/bin/python -m pip install 'pytest>=9,<10'
.venv/bin/python -m pytest
```

Node.js is needed for the calculator's browser-code regression test. Without it,
that test explicitly skips; CI installs Node 22 so it runs there. No npm packages
are required. The development manifest is not a Pi provisioning script; the
existing application and optional-feature installers still manage production.

## Selecting tests

```bash
# Collect without executing tests.
.venv/bin/python -m pytest --collect-only -q
# A feature, individual case, or matching names.
.venv/bin/python -m pytest tests/test_rose_script.py
.venv/bin/python -m pytest tests/test_gmail.py::AuthTests::test_memory_renewal_rotation_and_revocation
.venv/bin/python -m pytest -k 'pagination or startup' -v
```

The `pytest` executable also works from the repository root. Configuration in
`pyproject.toml` limits default discovery to `tests/test_*.py`; hardware utilities
in `services/`, `printing/` and `tools/` are not part of this default run. Invalid
configuration and unknown markers are errors. Failed and skipped cases are
summarized with `-ra`.

## Compatibility and test style

Pytest is the canonical runner. Existing `unittest.TestCase` tests, assertions,
setup/cleanup methods and `unittest.mock` patches remain supported. Pytest 9 is
required to preserve `subTest` execution and reporting without another plugin.
The suite does not use the unsupported unittest `load_tests` protocol.

Keep existing tests readable and make new tests native pytest functions where
practical, using plain assertions, `tmp_path`, `monkeypatch` and parametrization.
Existing TestCase methods cannot receive pytest fixture arguments directly;
convert the relevant class before introducing those arguments. `unittest.mock`
remains useful in either style.

Preserve the current directory layout and cross-test helper imports to minimize
merge churn. No custom runner, broad fixture rewrite or parallel execution is
required. Changes to individual test styles can happen with feature work.

## What a successful run means

The full suite mocks provider, Gmail and school network operations and simulates
UART/BLE behavior. It uses synthetic credentials and temporary data. It requires
no API key, OAuth enrollment, live PRONOTE account, UART, printer or systemd
service. The separate scripts for live probes and Rose playtests are not run by
pytest. Offline success does not validate model wording or physical hardware.

The migration baseline contains 364 test methods. Pytest additionally reports
successful subtests, so its summary includes more outcomes than unittest's test
count; collection should still contain those same 364 method IDs. Future added
or parametrized tests can legitimately change that count. Tests must work in the
machine's local timezone as well as UTC; token-expiry fixtures use UTC explicitly.
