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

## Physical validation: accepted 7 November 2026

The owner confirmed successful phone scans on the Matra Minitel 1:

- The local validation URL decoded exactly to `https://example.org`.
- A real web-search source decoded exactly to
  `https://www.marabout.com/livre/ce-que-cecile-sait-9782501189651/`.
- The owner approved the feature for integration into `develop`.

The [validation record and three original screenshots](hardware/qr-2026-11-07/README.md)
are retained in the repository. These demonstrate output-field QR codes;
maximum-capacity full-screen codes and Unicode URL scanning remain software-tested
rather than separately confirmed on the CRT.

The temporary `TEST QR` chat command and its runtime fixture were removed after
acceptance. Automated synthetic fixtures remain under `tests/`. Normal operation
uses `LINK n` or `LIEN n` against the current answer's structured sources.

For future physical regressions, request an ordinary web-search answer, display
`LINK n`, compare the camera's decoded destination to that source, then RETOUR
and check paging/editing. Do not run a second process against `/dev/ttyUSB0` while
the production service is active. A console preview does not prove optical scan
quality on the CRT.

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
count cutoff. The 19-byte validation URL uses 343 UART bytes (about 2.9 seconds at
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
These raster checks are software evidence; the separate physical results are recorded above.
