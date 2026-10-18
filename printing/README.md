# 3615 IA optional printing — hardware smoke-test checkpoint

The generic renderer and authorization primitives are separate from the MXW01 backend.
The Minitel interface accepts explicit trusted-keyboard printing commands; model output cannot authorize printing.

## Installation on Raspberry Pi

From the project root:

```bash
git pull --ff-only origin develop
sudo bash scripts/install_printer.sh
```

The installer adds the printer extra (`bleak` and `Pillow`) to the shared uv
runtime, preserving Gmail and PRONOTE. It also installs BlueZ, DejaVu fonts and
native image build prerequisites through apt. Accented French text and common
punctuation are rendered as glyphs, not stripped. See [runtime setup](../docs/RUNTIME.md).
It does not modify Bluetooth controller settings or enable printing at startup.

## Preview (no Bluetooth needed)

```bash
.venv/bin/python -m printing.print_test --text "3615 IA - BONJOUR" --preview /tmp/print-preview.png
.venv/bin/python -m printing.print_test --image /path/to/photo.jpg --preview /tmp/photo-preview.png
```

## Physical printing (explicit operator command only)

Your Pi previously attempted Bluetooth Classic instead of BLE when dual mode was enabled.
If that still happens, temporarily select BLE-only mode:

```bash
sudo btmgmt power off
sudo btmgmt bredr off
sudo btmgmt power on
```

Power on the MXW01, then run:

```bash
.venv/bin/python -m printing.print_test --text "3615 IA - ESSAI"
.venv/bin/python -m printing.print_test --image /path/to/photo.jpg
```

After testing, restore normal Bluetooth dual mode:

```bash
sudo btmgmt power off
sudo btmgmt bredr on
sudo btmgmt power on
```

## Offline tests

```bash
uv run --locked --all-extras pytest tests/test_printing.py -v
uv run --locked --all-extras pytest tests/test_mxw01.py -v
```

## Limitations and safety

- **Not yet physically validated:** the MXW01 driver is based on a community protocol specification, which describes itself as incomplete.
- Long text is split into short consecutive sections of at most 600 raster lines (384 dots wide), sent over one BLE connection. The paper is a continuous roll, not paginated sheets. Each section must receive the printer completion notification before the next is sent; an ambiguous completion stops the job rather than risking duplicate output. Individual oversized image blocks still require resizing.
- Connection and completion errors are surfaced to the operator.
- The driver pads short image buffers to the firmware minimum of 90 rows and paces BLE writes; these details need validation on the actual hardware.
- The `PrintApproval` class is a primitive, **not an integrated security boundary**. Its `confirm` method must only receive actual trusted terminal input. The model may propose or render, but must never call physical printing or synthesize approval.
- Printer is entirely optional. No printing on application startup.
- Source protocol: https://github.com/jeremy46231/MXW01-catprinter/blob/main/PROTOCOL.md

## Terminal integration (develop)

The optional extension is detected when both `bleak` and `Pillow` are installed
in the Python environment running the chat service. No Bluetooth scan is made
until a print job is actually requested. Set `PRINTING_ENABLED=0` to disable
the extension explicitly; `MXW01_ADDRESS` overrides the default BLE address.

- `IMPRIMER` prints the complete last assistant answer (not just the final Minitel display page), with no LLM call and
  no additional confirmation.
- `Imprime cette réponse` prepares the last answer and asks `OUI / NON`.
  Confirmation is processed only from actual keyboard input, never from LLM output.
- `NON` or `ANNULER` cancels the pending job. Any unrelated command cancels
  pending approval before processing the new request.
- Natural-language image requests are currently **not supported** by the chat
  runtime: there is no installed image-generation provider or secure image
  attachment workflow. The standalone CLI still prints existing image files.

Printing runs on a worker while the terminal displays deterministic fictional
reprographics stages. No extra LLM requests are made for stage wording. The
Bluetooth status and actual completion remain authoritative. The terminal may
be busy until the print attempt completes. The extension does not modify
Bluetooth controller settings; BLE-only setup may still be required on the Pi.

Run `uv run --locked --all-extras pytest tests/test_printing_session.py -v`
and validate `IMPRIMER` in the terminal console before testing the physical
Minitel. Hardware testing has shown partial printing and prolonged BLE writes on longer documents. The single-connection multi-section protocol remains to be validated on the physical printer.

When an email is selected in Gmail, `IMPRIMER` prints its full selected message text
(headers and body), rather than the last assistant response. The Gmail message
menu advertises `TEXTE / RESUMER / REPONDRE / IMPRIMER`. Printing is explicit
and does not send, forward or otherwise modify email.

## 2026-10-14 continuous-roll printing checkpoint
\nThe MXW01 uses a continuous thermal roll: short raster chunks should be printed sequentially without page breaks. Previous implementation reconnected for each 600-line chunk; now one BLE scan/connection and notification subscription serve the entire document. Each chunk uses its own A9 print request, raster payload and AD end command, and waits for AA completion before the next chunk. If AA is missing, stop; do not blindly retry a chunk that may already have printed. Printer firmware may not reliably emit AA; this is the next physical validation point. No Pi Bluetooth settings are changed.
