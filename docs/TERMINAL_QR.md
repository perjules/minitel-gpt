# Source links as QR codes

`LINK 1` (or `LIEN 1`) followed by ENVOI opens source `[1]` from the currently
loaded answer. It generates the QR locally; there is no model call, network
request, URL-shortening service or QR-generation service. The source list uses
structured response metadata, never URLs reconstructed from the lossy screen
text. New answers replace that list. A printed `[1]` without source metadata
does not create a link.

## Interaction

- Small and medium codes replace only the answer field. The frame and input
  remain available. Type another `LINK n` to change source or a normal command
  to continue the conversation.
- RETOUR, SUITE or ANNULATION closes the QR and restores the **same answer page**.
  Subsequent SUITE/RETOUR presses resume normal pagination or feature navigation.
- Larger codes temporarily use rows 1–23 and put a return instruction on row 24.
  RETOUR, SUITE, ANNULATION or ENVOI restores the same answer page. Starting to
  type restores the input area and retains that first character.
- SOMMAIRE, GUIDE and the usual session timeout retain their existing meaning.
  There is no additional QR-specific timeout. Opening/closing a QR does not
  acknowledge unseen document pages or put LINK in prompt recall/history/memory.
- A missing source or oversized URL gives a status message and keeps the answer.
  No truncated URL is encoded. Plain unstructured links from legacy providers
  and arbitrary email body text are not inferred into numbered sources.

Completed Mistral Conversations streams also retain source links. Their numbering
follows the displayed transcript's arrival order, separately from the canonical
final answer ordering. Failed/cancelled streams do not expose provisional links.
Generic process output and Chat Completions text do not supply structured sources.

## Hardware trial (required before merging)

Use the feature branch through the normal updater/runtime workflow. Segno is a
new core dependency, so copying Python files alone is insufficient. Do not run a
second process against `/dev/ttyUSB0`; these checks run **inside ordinary chat**.

1. Enter `TEST QR`, then ENVOI. This installs a synthetic answer and pre-fills
   `LINK 1`; press ENVOI again. There is no API call or data persistence.
2. Scan the QR using the phone camera. It must offer exactly
   `https://example.org`. Opening the website is optional: inspect the URL first.
   Allow the drawing to finish before judging the pattern.
3. RETOUR must restore the diagnostic text and gray input field without mosaic
   fragments. Type a few characters, CORRECTION, ANNULATION; then `LINK 1` + ENVOI
   to repeat. SOMMAIRE must leave cleanly.
4. Ask an ordinary web-search question (this step uses the configured API), find
   a numbered source, then `LINK n` + ENVOI. Scan it and compare the destination
   with that source. RETOUR must restore exactly the page you were reading;
   SUITE/RETOUR must then continue paging. Test a second source and a normal
   follow-up prompt. Invalid `LINK 999` must not call the model.
5. If a real URL requires full-screen mode, check its bottom return instruction,
   scan, return and type again. Very long URLs should report the capacity limit.

Record phone model/camera app, whether each scan succeeds, decoded URL accuracy,
return/edit/navigation behavior and approximate draw time. If scanning fails,
photograph the entire screen straight-on with `TEST QR` identified; include the
white border. Try a little farther away and moderate CRT brightness to distinguish
focus/bloom from geometry. A code that looks right in the console is not a
hardware pass. Native G1 rendering and phone scanning on this Matra remain
**unvalidated** until these observations arrive.

Go/no-go: exact destination + reliable scanning + clean return/input are required
for acceptance. If the camera cannot resolve the coarse raster, keep the feature
branch for a larger-module/layout experiment; do not silently shorten URLs or
change the main display geometry.

## Encoding and limits

The terminal uses joined G1 mosaics: each character contains 2 × 3 binary blocks.
The renderer sends one pattern byte per cell, with explicit white foreground,
black background, joined graphics and SI restoration. Lit blocks form the white
paper; unlit blocks are the QR's black modules. It only writes the QR rectangle,
never physical column 40, and preserves a four-module white quiet zone. It does
not approximate QR modules with letters or separate mosaic tiles.

Segno 1.6.6 is a pure Python QR encoder with no transitive dependencies. It provides
standard full QR, error correction M, explicit UTF-8 (ECI for non-ASCII URLs), and
no Micro QR. The exact URL, including query string and fragment, reaches the
encoder before display sanitization. At one module per mosaic block:

| Region | Available coarse pixels | Largest QR including quiet border |
| --- | --- | --- |
| 39 × 18 answer cells | 78 × 54 | Version 7: 53 × 53 |
| 39 × 23 temporary cells | 78 × 69 | Version 11: 69 × 69 |

Capacity depends on QR encoding mode and the actual URL; it is not a character
count cutoff. The demo QR rectangle is 343 UART bytes (about 2.9 seconds at
1200/7E1), excluding clearing and UI updates. Full-screen Version 11 uses 1065
bytes for the graphics rectangle. No artificial drawing delay is added.

The console maps all 64 G1 shapes to Unicode sextants/half/full blocks. A font
with Symbols for Legacy Computing support is needed for a useful preview; CRT
raster geometry and optical scan quality remain physical checks.

References: [original STUM G1 chart](https://jbellue.github.io/stum1b/img/p101/1.svg),
[STUM presentation controls](https://jbellue.github.io/stum1b/),
[Segno API](https://segno.readthedocs.io/en/latest/api.html).

## Offline evidence

`tests/test_terminal_qr.py` exercises all mosaic shapes against independent Unicode
block names, every emitted QR pixel and quiet zone, polarity, source scope and
numbering, exact URL preservation, invalid/oversized links, local command routing,
page/draft preservation, no premature review acknowledgement, native echo,
partial-draw failure, private expiry, full-screen restoration, stream source order,
and 1200/7E1 backpressure with presence replies. Tests use synthetic URLs and UARTs.

During development, ZXing-C++ independently decoded the actual emulated G1 raster
for five URLs (19, 36, 120, 150 and 250 UTF-8 bytes), including an accented URL,
at square and 9:7 rectangular pixel ratios. Every decoded URL matched exactly.
ZXing/Pillow were temporary development tools, not added runtime dependencies.
This is software evidence only; no physical scan result is claimed.
