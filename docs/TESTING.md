# Offline tests

Run the automated suite with **pytest 9** from the repository root. Python 3.11+
is required by the combined application/Rose suite. Linux is the full-suite
development target: tests exercise `fcntl`, subprocesses and simulated terminal
behavior. CI runs on Python 3.11 and 3.12.

## Development environment

Install the uv version recorded in `config/uv-version`, then from a development
checkout run:

```bash
uv sync --locked --all-extras
uv run --locked --all-extras pytest
uv run --locked --all-extras ruff check .
uv run --locked --all-extras python scripts/export_requirements.py --check
```

This creates a local `.venv` and includes the development group plus every optional
SDK needed by the full offline suite. `pyproject.toml` defines dependencies and
`uv.lock` fixes the resolution. The requirements files are generated compatibility
exports; edit the manifest, run `uv lock`, then regenerate the exports with
`uv run --locked --all-extras python scripts/export_requirements.py`.

Node.js is needed for the calculator's browser-code regression test. Without it,
that test explicitly skips; CI installs Node 22 so it runs there. No npm packages
are required. CI also checks installer shell syntax and export freshness.

On the deployed Pi, `.venv` is a compatibility link to the root-owned production
environment, which excludes development tools. Use a separate development checkout
for pytest; do not run `uv sync` against the Pi's linked `.venv`. See
[RUNTIME.md](RUNTIME.md) for installation, synchronization and rollback.

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

## Isolated physical display probe (WP1)

See [DISPLAY_PROBE.md](DISPLAY_PROBE.md) for the STUM references, safe Pi
procedure, recovery and observations needed before any geometry change. Focused
offline checks: `uv run --locked --all-extras pytest tests/test_display_probe.py`.
The diagnostic is never run against hardware by the automated suite.

The separate `--interaction` mode tests CAN line clearing, REP compression, raw
keys/accents, candidate lowercase mode, echo and buffered input during redraws.
See [INTERACTION_PROBE.md](INTERACTION_PROBE.md) for the single-script Pi procedure
and recovery, and [TERMINAL_UX_PLAN.md](TERMINAL_UX_PLAN.md) for hardware gates.
Run its offline checks with:

```bash
uv run --locked --all-extras pytest tests/test_display_probe.py tests/test_interaction_probe.py
```

The 5 November hardware review passed the applicable gates. Production CAN, REP,
recall and native lowercase have separate tests and await physical application
acceptance; accent parsing remains unchanged.

## Fixed 40-column frame

[TERMINAL_UX.md](TERMINAL_UX.md) records the WP1 hardware findings, frame layout,
shared editor/view behavior and WP6 physical checklist. The console and offline
fixtures interpret absolute cursor writes; tests of visible output must inspect
rendered rows rather than count whole-page UART substrings. Focused tests live in
`tests/test_terminal_display.py` and `tests/test_terminal_documents.py`.

The fixed-frame suite also includes `tests/test_terminal_serial_timing.py`, whose
1200-baud virtual transport delays screen delivery and protocol replies. Its
bounded USB-batch variant also exercises write backpressure during PRECEDENT,
finite write budgets, partial-output failure and CAN paging. Console
checks and immediate-ACK UART fakes alone cannot detect output-backlog deadline
failures on the physical Matra.

## Validated Matra UX follow-up

Focused offline checks (no live terminal or provider calls):

```bash
uv run --locked --all-extras pytest tests/test_terminal_display.py tests/test_terminal_documents.py tests/test_terminal_serial_timing.py tests/test_terminal_rep.py tests/test_terminal_recall.py tests/test_terminal_keyboard.py tests/test_console_terminal.py
```

These cover normal/inverse CAN, blank-before-redraw transitions, REP cell/style
parity and wire savings, actual USB backpressure, ordinary-chat recall and session
isolation, native case acknowledgement/fallback/cleanup, mixed-case hidden
password entry, console bypass and authenticated admin persistence/restarts.
The physical acceptance procedure is in
[TERMINAL_UX_PLAN.md](TERMINAL_UX_PLAN.md#resume-checklist). No accent codec or
activity/scrolling behavior was added by those original packages. Later French
G2 and scrolling checks are documented below.

Rose's fixed-frame adapter has focused checks in `tests/test_rose_display.py`:
visible gray input, CAN transitions, long replies, pinned stop word, local paging,
private exit keys, echo restoration/failure, bounded input and retiring private
frames before ordinary chat. `tests/test_terminal_ascii.py` checks readable symbol
fallbacks and unchanged source text through email/school document views.

`tests/test_terminal_diagnostics.py` verifies the local `TEST UNICODE` tool,
JSON text with escaped/literal Unicode, the real one-page answer viewer, ordinary
French accents versus isolated combining marks, and exclusion from API requests,
conversation history, memory and prompt recall. The SSH module entry point only
prints its source fixture; it does not open the serial port.

`tests/test_terminal_scrolling.py` covers the explicit result flag and local
TEST SCROLL command, native rouleau, EOF without a key, static/partial-stream
stop semantics, indefinite quiet streams, split chunks/accents, source and serial
failures, Ctrl-C cleanup, echo gating, direct last-page restoration, normal
paging/editing afterward, nonblocking console keys, and bounded 1200/7E1 backlog
with presence probes. It uses synthetic text/queues and fake UARTs only. Physical
acceptance remains separate in [TERMINAL_SCROLLING.md](TERMINAL_SCROLLING.md).

`tests/test_videotex_text.py` covers native French G2: split/dead accents across
polling timeouts, protocol replies, immediate function exits, unknown sequences,
shared/private readers, no secret echo, uppercase fallback, encoded byte budgets,
console key round trips and white-on-gray C2 zone preservation through editing,
CAN and paging. Historical byte-stripping test helpers now decode SS2 rather
than treating its selector B as visible text. No live UART/API is used.


## Owned streaming sources

Tests test_stream_sources.py and test_stream_lifecycle.py cover process pipes/UTF-8,
TERM/KILL and descendants, quiet/blocked sources, fragmented SSE, Mistral final
validation/citations, hidden reasoning exclusion, partial/failure paths and terminal
recovery. HTTP integration uses only a synthetic loopback server, never a real
provider. Source/stream commands bypass ordinary history, memory and local tools.
See [STREAM_SOURCES.md](STREAM_SOURCES.md) for physical checks.

## Source QR codes

`tests/test_terminal_qr.py` covers QR/G1 raster and quiet-zone fidelity, exact
structured source URLs/numbering, local LINK/LIEN commands, no model
re-entry, page/input/review preservation, full-screen capacity/recovery, native
echo and 1200/7E1 backpressure, and streamed transcript source numbering. Segno is
a locked core dependency; tests do not fetch URLs or call an external QR service.
The console supports all 64 joined G1 shapes. Physical scans of a local fixture
and a real web-search source were accepted on 7 November 2026; photos are linked
from [TERMINAL_QR.md](TERMINAL_QR.md). The temporary TEST QR runtime command was
removed after acceptance; synthetic automated regressions remain.

Deployment compatibility regressions in `tests/test_runtime.py` use real temporary
Git repositories to stage a plain target from a helper with Airzone-only files.
They verify target-manifest selection without execution, obsolete policy removal,
rejection of unknown privileged paths before staging/stopping, rollback gating,
and safe failure step/path reporting without leaking argv or captured data.
`tests/test_admin_maintenance.py` checks that Maintenance displays that diagnostic.
No Pi installer, real root command or systemd transaction runs in these tests.
