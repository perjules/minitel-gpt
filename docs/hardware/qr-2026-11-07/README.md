# Matra source QR validation — 7 November 2026

Status: **owner accepted for integration into develop**.

Hardware: Matra Minitel 1, 722 079 V / XB0006B-2, 40 × 24 Videotex,
1200 baud 7E1 through the Raspberry Pi/FT232RL connection. Native joined G1
mosaics render a black QR on a white quiet zone in the ordinary answer area.

The owner reported that the mock and a real web-search source both scanned
successfully. The three original uploads are retained unchanged:

| Photo | Evidence |
| --- | --- |
| [1 — camera view](01-example-camera.jpg) | Local test QR with the ordinary header, navigation and input region visible. |
| [2 — decoded result](02-example-decoded.jpg) | Phone reports exactly `https://example.org`. |
| [3 — real web source](03-live-source-camera.jpg) | QR generated from a numbered source in a live web-search answer. |

For photo 3 the owner independently reported this exact decoded destination:

`https://www.marabout.com/livre/ce-que-cecile-sait-9782501189651/`

The source QR feature is accepted on this evidence and the owner's approval.
The phone model, optical draw/scan timings, maximum-capacity full-screen code and
Unicode URL scanning were not separately recorded. Software regressions cover
those layout/data paths; this record does not claim every capacity limit was
physically exercised.

The temporary TEST QR command was removed after acceptance. The production
commands remain LINK n / LIEN n, with RETOUR restoring the same loaded page.
