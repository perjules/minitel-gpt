# 3615 IA printing — initial isolated checkpoint

This is a development checkpoint, **not yet connected to the Minitel interface or MXW01 printer**.

Implemented:
- printer-independent immutable document blocks (text and local images)
- 384-pixel monochrome renderer using optional Pillow with Floyd–Steinberg dithering
- bounded document height and single-use explicit `OUI` approval state

Pending:
- MXW01 BLE driver and standalone print CLI
- optional installation script and dependencies
- integration with trusted terminal input/session state
- physical printer validation

**Safety:** `PrintApproval.confirm` must only be called by trusted deterministic input handling, never with model-generated content. Keep approval tokens out of LLM prompts.

Run isolated tests with Pillow installed: `python -m unittest discover -s tests -p 'test_printing.py' -v`.
