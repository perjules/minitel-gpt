# WP1: isolated compact-display experiment

For the later 40-column CAN/REP/keyboard experiment use `--interaction` and follow
[INTERACTION_PROBE.md](INTERACTION_PROBE.md). That mode does not retry 80 columns;
the original WP1 procedure below is retained for reference.

This is a physical test, not an application setting. Only
`scripts/display_probe.py` opens the UART. It imports neither the application nor
its configuration, makes no network/API calls and does not change the deployed
branch, environment, systemd units or normal display. WP2–WP6 initially waited for hardware
results and a geometry decision; that gate is now complete (see below). The console emulator is **not** evidence of
80-column support and remains unchanged.

## Owner's hardware result (1 November 2026)

The physical Matra stayed in 40-column Videotex and did not acknowledge mixed
mode. Cursor positioning and echo suppression/restoration were confirmed. The
owner agreed to retain 40 × 24; see [TERMINAL_UX.md](TERMINAL_UX.md) for the
recorded observations and the subsequent fixed-frame implementation.

## What the documentation actually establishes

Primary reference: France Télécom/CNET, **STUM 1B**, available as a
[scan](https://www.goto10.fr/minitel/specifications/stum1b.pdf) and
[searchable transcription](https://jbellue.github.io/stum1b/).
Sections below refer to the printed page numbers. Transcription has occasional
OCR errors; the byte values here follow the documented protocol tables.

- Part 1, chapter 1, pp. 21–22: ROM identification distinguishes models/firmware;
  Matra also manufactured bistandard terminals. A Matra brand or enclosure label
  alone does not establish that this particular **722 079 V / XB0006B-2** is 1B
  capable. A plain Minitel 1 may have no mixed mode. Photograph its keyboard and
  rear label and retain the raw ROM reply; do not infer capability from the
  reference's letter `B` alone.
- Part 1, chapter 3, pp. 29–30, and part 2, chapter 2, pp. 105–107: mixed mode
  has **80 columns, rows 01–24**, plus special service row 00. It doubles normal
  text capacity; there are no documented 48 content rows. Glyphs become narrower,
  not half-height. This probe leaves row 00 alone.
- Part 2, chapter 6, pp. 134, 139, 144–145: protocol requests and replies:

| Operation | Bytes sent (hex) | Expected response |
| --- | --- | --- |
| ROM identification | `1B 39 7B` | `01 manufacturer type firmware 04` on 1B; older models may differ |
| Videotex → mixed/US | `1B 3A 32 7D` | `13 70` |
| Mixed → Videotex | `1B 3A 32 7E` | `13 71` |
| Cursor location | `1B 61` | `1F row column` (retain raw bytes) |
| Page mode | `1B 3A 6A 43` | protocol operating-status reply |
| Routing status | `1B 3A 62 receiver` | `1B 3B 63 receiver status` |
| Keyboard route OFF/ON | `1B 3B 60/61 receiver 51` | same routing-status frame |

  Receiver `58` is screen, `5A` modem; status bit 1 represents the keyboard
  route. In local mode echo can pass through the modem loop. The probe snapshots
  both routes, temporarily disables them, and restores the original pair. It
  never disables keyboard-to-UART routing. Missing status means the controlled
  echo test is skipped, with no guessed routing restoration. An interrupted
  mutation is recorded before writing, so cleanup also covers partial commands.
- Part 2, chapter 2, p. 106; part 3, chapter 2, pp. 162–169: mixed mode uses
  ISO 6429 `ESC [ row ; col H` positioning, `ESC [ 2 J` clearing and individual
  SGR attributes. Matra accepts only one SGR parameter per command. The available
  attributes are intensity, underline, inverse and blink; **Videotex's eight
  colors/gray levels do not carry over** as a color palette. The first screen
  tests all eight Videotex colors (gray levels on a monochrome CRT); the second
  tests the mixed-mode attribute set.
- Mixed mode starts in US G0; `SO` (`0E`) selects French G1 and `SI` (`0F`)
  restores US. French code positions `40 5B 5C 5D 7B 7C 7D 7E` should display
  `à ° ç § é ù è ¨` (older VGP2 can approximate §). Full Videotex SS2 accent
  composition is **not** assumed in mixed mode. The probe compares SS2 accents
  in 40 columns with French G1 substitutions in 80. It does not implement a new
  general-purpose text codec.
- Mixed mode preserves Télétel function keys, while enabling extended ASCII
  keyboard/cursor sequences. Local echo, editing and actual glyph readability
  must be observed. No keystroke is echoed back by the Python probe.

The probe never enters the separate **Téléinformatique standard** (`1B 3A 31
7D`), changes baud rate, dials or resets the whole terminal. No mixed-mode ACK
means **unsupported or unconfirmed**, not proof that a particular model cannot
support it (power, routing and firmware also matter). It does not force an
80-column page after a missing ACK.

## Safe Raspberry Pi procedure

Get this experiment's committed files onto the Pi first. Until explicitly pushed,
`experiment/compact-display-probe` exists only in the development checkout. Do
not change the production deployment branch or run the installer for this test.
The script can run from a separate checkout with the existing production Python.

Use a persistent interactive SSH session (or Pi Connect terminal), with the Matra
powered on in its normal local state. Avoid the admin updater/restart controls
throughout the experiment. Do not run any other UART tools. Budget about 5–10
minutes: a full grid takes several seconds at 1200 baud.

From the checkout containing the probe:

```bash
cd /home/minitel/minitel-gpt  # or the separate checkout containing these files
# Check the actual FT232RL path; prefer its stable by-id symlink if available.
ls -l /dev/serial/by-id/
sudo systemctl stop minitel-chatgpt.service
systemctl is-active minitel-chatgpt.service  # must report inactive
sudo fuser -v /dev/ttyUSB0                  # must show no owner
sudo /opt/minitel-gpt/venv/bin/python scripts/display_probe.py --port /dev/ttyUSB0
```

Replace `/dev/ttyUSB0` everywhere if your FT232RL has another path. The serial
settings are deliberately fixed at **1200 baud, 7E1**, matching production;
there is no `.env` auto-detection. On older installations use the checkout's
`.venv/bin/python` if `/opt/minitel-gpt/venv/bin/python` does not exist. The
existing `pyserial` is the only Python dependency. Linux `fuser` comes from
`psmisc`; if missing, install it with `sudo apt-get install psmisc` before testing.
Run with sudo so the busy-port checks can see every process. The script also
requests cooperative and kernel-exclusive UART access and checks for competing
owners again before sending any bytes. An active/starting/stopping service,
unknown service status, unavailable busy-port check or busy UART aborts the run.
These checks complement stopping the service; do not deliberately restart it
while the probe owns the port.

Optional: append `2>&1 | tee /tmp/display-probe.log` to capture stdout/stderr.
Only stdout is piped; stdin must remain the interactive SSH terminal. Use dummy
input: raw key bytes appear in this log. No report is written automatically.

**Advance using Enter in SSH, not ENVOI on the Minitel.** `q` then Enter in SSH,
Ctrl-C, SIGTERM, SIGHUP, or a 120-second observation timeout exits through cleanup.
Use the Minitel keys solely for observing behavior. The stages are:

1. ROM query; request standard 40-column mode; numbered grid and cursor queries.
2. Standard-mode keyboard/local-echo observation, confirmed echo-OFF trial with
   dummy `abc123`, then restored-echo trial.
3. Request mixed mode; only on `13 70`, show the 80-column grid and repeat those
   trials. Grid labels cover rows 01–24 (row 02 is the ruler); right markers cover
   each other row (row 24's
   marker is at column 39/79 to avoid wrapping the bottom-right cell). The ruler
   spans exactly 40/80 columns. Cursor queries explicitly address the last cell.
4. Request 40-column Videotex, page mode, normal text attributes and visible
   cursor; restore saved echo routes. Verify the final `DISPLAY PROBE TERMINE`
   screen is readable and normally sized.

After recovery, and only once the script has exited:

```bash
sudo systemctl start minitel-chatgpt.service
systemctl is-active minitel-chatgpt.service
```

No automatic restart is performed, so a failed restoration cannot silently put
the running application into a different terminal mode.

## Recovery if interrupted

Cleanup is best effort, not a guarantee: SIGKILL, a lost USB connection or power
cannot be handled in Python. If the screen is still narrow/garbled, keep the
service stopped and try:

```bash
sudo /opt/minitel-gpt/venv/bin/python scripts/display_probe.py --port /dev/ttyUSB0 --restore-only
```

This only requests mixed → Videotex and the baseline page/text state; it cannot
reconstruct echo routes from a previous killed process. If restoration is
uncertain, **switch the Minitel off and on**, wait for its normal `F` indicator,
then restart the service. You do not need to reboot the Pi or disconnect wiring.
On a genuine 1B keyboard the documented manual reset is **Fnct+T, then V**;
your plain Matra 1 may lack that key, so power cycling is the reliable fallback.
Do not press a second Ctrl-C during cleanup. If a process was killed, confirm it
has exited and `fuser` shows no owner before another attempt.

## Results to return

- ROM reply and mixed-mode/restoration ACKs from SSH, or the captured log.
- Front keyboard and rear label photos if exact model support is still unclear.
- Photos of **both grids** before typing (including top/bottom and right edges).
  Confirm the 24-row grid (row 02 is the ruler), ending at column 40/80; report clipping,
  unexpected wrapping or missing right markers. Describe cursor-query anomalies.
- Readability from your usual position: body text, punctuation, `O/0`, `I/l/1`,
  eye strain; whether the 80-column font is comfortable for whole answers.
- Accents actually displayed on each screen; any substitutions, block glyphs or
  missing characters. Compare US and French rows in mixed mode.
- Which gray levels and mixed attributes are distinguishable and comfortable.
- For each mode: does typing appear **once**, twice, or never? What do CORRECTION,
  ANNULATION, ENVOI, SUITE, RETOUR, SOMMAIRE and arrows do visually? Their raw RX
  bytes are recorded without interpreting them as probe navigation.
- In echo-OFF, is dummy `abc123` invisible but still received? After restoration,
  is the first trial's behavior recovered? Do not type a real password.
- Confirm normal 40-column chat/boot operation after restarting production.

Only these physical results determine whether mixed mode is usable and whether
it should become the default. No default or geometry is changed in WP1.

## Offline verification

```bash
uv run --locked --all-extras pytest tests/test_display_probe.py
```

Tests use fake UART/systemd/subprocess responses: service/busy-port rejection,
exclusive-port race refusal, exact STUM switch bytes, absent ACK, mode cleanup on
interrupt/errors/timeouts, partial echo changes and restoration, restore-only,
geometry/encoding separation and short writes. They do not prove this Matra can
switch modes or validate CRT/keyboard behavior.
