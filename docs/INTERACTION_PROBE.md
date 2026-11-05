# Matra M1 interaction hardware probe

Status: prepared, offline tested; **physical results pending**. No production
renderer, parser, configuration or dependencies change in this work package.
The follow-up implementation and acceptance gates are in
[TERMINAL_UX_PLAN.md](TERMINAL_UX_PLAN.md).

## Get the single script onto the Pi

Start from the working `develop` deployment. The script is published on
`feature/terminal-capability-probe`. Use the current branch version, which fixes
SSH Enter handling; the original `7458795` version can time out at K1. Fetch the
explicit branch reference and extract only the
script; these commands do **not** switch the checkout, pull code into its files,
change runtime metadata or restart services:

```bash
cd ~/minitel-gpt
git branch --show-current
git fetch origin refs/heads/feature/terminal-capability-probe:refs/remotes/origin/feature/terminal-capability-probe &&
git show refs/remotes/origin/feature/terminal-capability-probe:scripts/display_probe.py > /tmp/display_probe.py &&
.venv/bin/python /tmp/display_probe.py --help
```

The branch should remain `develop`, and help must list `--interaction`, `--report`
and the `/dev/tty` CR/LF note. If fetching/extraction fails, stop and inspect that error. You may
instead copy the same single file with SSH/vim; it needs only the already-installed
`pyserial` dependency. The older WP1 script does not have `--interaction`.

Do not deploy the probe branch, change the production `.venv`, run an installer or
switch the application's branch for this diagnostic. If deployment already reports
a checkout/installed-branch mismatch, first use the confirmed recovery procedure in
[RUNTIME.md](RUNTIME.md#recovering-a-checkoutinstalled-branch-mismatch).

## Safe SSH procedure

Use an interactive SSH terminal. The Minitel is the test subject; **advance stages
from SSH**, never with ENVOI. Allow roughly 10 minutes, longer for photos. Each
observation has a 180-second timeout; expiry quits and attempts recovery.
The interaction probe reads SSH controls directly from `/dev/tty`, accepts either
CR or LF as Enter (CRLF counts once), and restores the SSH terminal's original
settings on exit. Notes support Backspace. This bypasses line-buffered stdin and
does not depend on stdout being a terminal when using `sudo` and `tee`.

From the normal repository directory, where `.venv/bin/python` already works:

```bash
cd ~/minitel-gpt
sudo systemctl stop minitel-chatgpt.service
sudo systemctl is-active minitel-chatgpt.service
sudo fuser /dev/ttyUSB0
.venv/bin/python /tmp/display_probe.py --help
```

After confirming the service is inactive and no process owns the port, create a
new results directory under the user's home so evidence survives reboot. Output
may be piped to `tee`; **stdin must remain the interactive SSH terminal**:

```bash
probe_results=$(mktemp -d "$HOME/minitel-interaction-XXXXXX")
probe_report="$probe_results/report.json"
sudo .venv/bin/python /tmp/display_probe.py \
  --interaction --port /dev/ttyUSB0 --report "$probe_report" | tee "$probe_results/ssh.log"
```

`is-active` should print `inactive` (its nonzero exit status is normal).
`fuser` should report no PID (also normally exits nonzero). Stop any console,
serial monitor or old probe using this port. Do not use MAJ/deploy or manually
restart the application during the experiment. The probe independently checks
the service and port, acquires kernel serial exclusivity, and rechecks owners
before sending any bytes. Run it with sudo so these checks see all processes.
Missing checks or another owner cause refusal, not a best-effort attempt.

The script uses 1200 baud, 7E1, no flow control, paced small writes, and bounded
reads/writes. It never loads `.env`, calls an API, modifies user data, sends modem
commands, requests 80 columns or exercises mosaic graphics. Use dummy text only:
all received keyboard bytes are deliberately logged in the report.

If using the file directly from a separate development checkout, substitute its
absolute script path for `/tmp/display_probe.py`. Do not run `uv sync` against the
Pi's production environment.

## How to answer and what to photograph

Every screen/SSH instruction has a stage ID. At an observation prompt, type in SSH:

- `p` + Enter: observed pass.
- `f` + Enter: observed failure.
- Enter alone: unknown; a photo can resolve it later.
- A short description + Enter: save a note; result stays unknown pending review.
- `q` + Enter, or Ctrl-C: stop and attempt recovery.

Keyboard test stages ask for SSH Enter after typing their list **on the Minitel**.
There is no software echo. Baseline/case stages retain the original local echo;
echo-off stages deliberately show no typed characters. Function keys are captured
as bytes, not acted upon. Do not press CONNEXION/FIN or LOUPE in this experiment.
Do not type during printed cursor/status exchanges; type when explicitly invited.

| Stage | Test and expected observation | Evidence to return |
| --- | --- | --- |
| K1 | Original letters, Shift letters, keypad, punctuation, accents and all eight application function keys | Raw `KEYS K1...` line; describe original local echo |
| C1 | Populated normal/gray whole and partial rows before clearing | Inspect, then SSH Enter |
| C2 | CAN clears normal rows 8/24, gray row 10 and right-hand tails on 12/14; left text, fences and heading survive; empty row 16 stays blank | Photo plus CAN cursor checks |
| C3 | Short replacement text has no old tails; gray field has no black holes | Pass/note or photo |
| R1 | Literal/compressed pairs: one X, two X, 39 dashes, eight accented e characters | Photo plus REP cursor checks |
| R2 | Literal/compressed spaces erase old text identically; rows 6/7 gray, 10/11 black | Photo plus cursor check |
| T1 | Case/digits/punctuation, acute/grave/cedilla, circumflex, diaeresis, ligatures and pound sign | Photo; list any missing/malformed symbols |
| K2/K3 | Same physical keys under requested uppercase and lowercase modes; only alphabetic case should reverse | Both raw key lines, protocol checks, notes about symbols/keypad |
| K4 | Original keyboard mode restored | Compare raw keys/echo to K1 |
| E1 | Dummy `abc123` received in SSH but invisible on CRT | Pass/note |
| Q1 | Type `abc123`, ENVOI **while the rows draw**, at the SSH NOW cue | Ordered raw bytes; no letters in output region |
| Q2 | Visible caret at row 22, column 3; typed text remains invisible with echo off | Cursor check; photo if placement is wrong |
| E2 | Original echo and case work again | Pass/note and final recovery result |

The letter test starts with physical `a`, `z`, then Shift+a, Shift+z. Accents use
the keyboard's normal combinations; do not substitute literal `e` plus apostrophe
for an accented character. If a combination is unavailable, record that rather
than guessing. RÉPÉTITION should produce `13 43`; this experiment captures it but
does not implement prompt history.

The script snapshots operating mode before requesting case changes. If no valid
status arrives, K2–K4 are skipped without changing case. This is **inconclusive**,
not proof of no hardware support. The baseline and other tests remain useful.
A status bit changing is not sufficient for acceptance: the raw letter, Shift,
symbol and keypad results must also match. The owner has since selected a global
Python case swap for the later lowercase WP; native-mode results are informational,
not a prerequisite. They remain part of this already-prepared diagnostic rather
than a proposal to change production keyboard configuration.

The Q1 test demonstrates paced output plus buffered keyboard reception. It does
not validate production editor responsiveness or reproduce every USB-driver
backpressure condition. Those remain explicit application acceptance tests.

## Results and recovery

At completion, paste the block headed `MINITEL INTERACTION REPORT v1` into the
development conversation, and attach the C2, R1, R2 and T1 photos. The final block
contains manual observations, raw keyboard bytes and independent cursor checks.
If it is lost, the full SSH transcript and JSON remain available:

```bash
cat "$probe_results/ssh.log"
sudo cat "$probe_report"
```

After an SSH reconnect, use `ls -dt ~/minitel-interaction-*` to locate the directory;
shell variables do not survive a new session. The script itself is in `/tmp` and
must be extracted again after reboot if another run is needed. Use a new results
directory for each run: the probe refuses to overwrite an existing JSON report.

The JSON contains attempted TX, raw RX, stage IDs, observations and restoration
errors. A completed run or exit code 0 means the procedure completed, **not that
the hardware features passed**. Empty/skipped observations stay unknown. Photos
and raw results can be reviewed later without editing or rerunning the script.
Interrupted/timed-out observations also retain captured keyboard bytes in the
summary as unknown; they are not silently lost just because SSH confirmation failed.

### First K1 run: SSH Enter timeout (5 November 2026)

The owner confirmed the typed characters displayed correctly. Captured bytes
established unshifted `AZ` / shifted `az`, keypad digits 0–9, the requested
punctuation, `é à è ù`, and all eight application function keys. RÉPÉTITION
produced `13 43`. Cedilla was not located; keypad `*`/`#` were not present in the
capture. These are retained observations, not a complete gate pass.

SSH Enter failed to advance K1 and its 180-second timeout restored page mode.
The exact Termux/SSH cause was not established remotely. Real-PTY tests reproduce
the old poller's inability to see CR-only input when canonical newline translation
is disabled. The updated controlling-tty reader covers CR/LF/CRLF, redirected
stdio, editing, quit, terminal-setting restoration and retaining aborted evidence.
This does not imply a Minitel UART fault.

For a repeat with the fixed script, the owner need not retype the captured K1
sequence: press SSH Enter immediately, note that K1 evidence is in the previous
run, and continue with C1/C2. If Enter still fails, try `p` plus Enter or `q` plus
Enter, and report that behavior. Do not repeat a whole key sequence while the
control path is unconfirmed. Keep the original report/transcript alongside the
new results directory. Cedilla remains optional.

On ordinary exit, q, Ctrl-C, SIGHUP, SIGTERM, timeout or I/O failure, the probe
attempts each independent cleanup: original case if changed, original keyboard
routes if changed, page mode, normal text, clear display and visible cursor.
Case/routing restoration checks acknowledgements; missing or wrong replies are
reported, not silently accepted. Visually check the ending screen and keyboard.

Only after recovery is satisfactory:

```bash
sudo systemctl start minitel-chatgpt.service
sudo systemctl status minitel-chatgpt.service --no-pager
```

If restoration is unconfirmed, the screen/keyboard behaves oddly, SSH died, or
the process was killed forcibly: **leave the service stopped**, ensure the probe
has exited, switch the Minitel off and back on, then restart the service. A power
cycle loses the current screen but restores the terminal's power-on configuration.
`kill -9`, power loss or a disconnected UART cannot run cleanup. A new
`--restore-only` invocation has no previous process's saved case/routes and cannot
promise to restore them; power-cycle instead. Do not run two recovery tools at once.

## Protocol references and interpretation

- [Original STUM M1, December 1984, chapter 4](https://www.minitel-alcatel.fr/documents/M1_1983-1984/STUM%20M1.pdf):
  CAN, REP, US, character sets and attributes. CAN is `18`; REP is `12` followed
  by `40 + additional repeats`; US parameters are `40 + row/column`.
- [STUM 1B, Videotex display and module operating modes](https://jbellue.github.io/stum1b/):
  mode-status query `1B 39 72`, reply `1B 3A 73 status`; lowercase START/STOP
  `1B 3A 69/6A 45`, with lowercase flag bit `08`. This is a **candidate extension**
  on the original Matra M1, not an assumed firmware capability.
- SS2 accent examples: `19 42 65` = e-acute; `19 41 61` = a-grave;
  `19 4B 63` = c-cedilla. G2 repertoire varies; record unsupported glyphs.

Cursor replies show position, not screen contents. CAN does not have a dedicated
success ACK. Therefore no ACK-only result can prove that a row actually cleared
or retained the correct shade. Nominal gray percentages are approximate on a CRT.
