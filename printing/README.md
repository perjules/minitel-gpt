# 3615 IA optional printing — hardware smoke-test checkpoint

The generic renderer and authorization primitives are separate from the MXW01 backend.
Nothing in the Minitel interface or LLM can currently initiate printing.

## Installation on Raspberry Pi

From the project root:

```bash
git pull --ff-only origin develop
bash scripts/install_printer.sh
```

The installer adds only `bleak` and `pillow` to the project virtual environment.
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
.venv/bin/python -m unittest discover -s tests -p 'test_printing.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_mxw01.py' -v
```

## Limitations and safety

- **Not yet physically validated:** the MXW01 driver is based on a community protocol specification, which describes itself as incomplete.
- Each job is bounded to 6000 image lines; printing may take time.
- Connection and completion errors are surfaced to the operator.
- The driver pads short image buffers and paces BLE writes; these details need validation on the actual hardware.
- The `PrintApproval` class is a primitive, **not an integrated security boundary**. Its `confirm` method must only receive actual trusted terminal input. The model may propose or render, but must never call physical printing or synthesize approval.
- Printer is entirely optional. No printing on application startup.
- Source protocol: https://github.com/jeremy46231/MXW01-catprinter/blob/main/PROTOCOL.md

## Terminal integration (develop)

The optional extension is detected when both `bleak` and `Pillow` are installed
in the Python environment running the chat service. No Bluetooth scan is made
until a print job is actually requested. Set `PRINTING_ENABLED=0` to disable
the extension explicitly; `MXW01_ADDRESS` overrides the default BLE address.

- `IMPRIMER` prints the last assistant answer immediately, with no LLM call and
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

Run `.venv/bin/python -m unittest discover -s tests -p 'test_printing_session.py' -v`
and validate `IMPRIMER` in the terminal console before testing the physical
Minitel. Only the standalone text print protocol has received hardware feedback
so far; Minitel integration and image printing remain to be validated.
