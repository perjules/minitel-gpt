# Matra terminal UX: 40 columns, fixed input, loaded-result navigation

Implementation branch: `feature/terminal-ux`. Changes remain local until the
owner requests a push. Physical validation is required before stable `main`.

## Hardware decision and evidence (1 November 2026)

The owner's Matra Minitel 1, 722 079 V / XB0006B-2, stayed in Videotex during WP1:

- `ESC 3A 32 7D` received no mixed-mode acknowledgement or visible transition;
  ROM identification also had no reply. Photos show the original M1 keyboard
  with LOUPE. Treat this terminal as **40 columns × 24 usable rows**.
- Absolute cursor queries exactly matched `(1,1)`, `(12,20)` and `(24,40)`.
- Accents `é à è ù ç` displayed, and gray levels were distinguishable. The
  application retains its ASCII transliteration for this work package; a new
  accent codec is not part of this change.
- Screen receiver `58` reported `4D`; modem receiver `5A` reported `46` → `44`
  when its keyboard route was disabled, then `46` on restoration. `ABC123` was
  received invisibly and `HELLO` appeared after restoration. Local echo passes
  through the modem loop on this hardware.

The test strongly supports using the original display, without 80-column
switching or a smaller-font setting. Eight protocol colors are **gray levels on
this CRT**. The working frame uses white body text and an inverse heading, so
readability does not depend on distinguishing colors or dim text. The probe and
STUM references remain in [DISPLAY_PROBE.md](DISPLAY_PROBE.md).

## Screen layout

Service row 00 is left alone. Physical column 40 is reserved to avoid automatic
wrap and bottom-right rollover; text lines have up to 39 characters.

| Physical rows | Purpose |
| --- | --- |
| 01 | Inverse service/context heading |
| 02–19 | 18 rows of loaded content |
| 20 | Page number and contextual function-key navigation |
| 21 | Ready/waiting/status/length notice |
| 22–24 | Fixed input area, up to three visible lines |

`services/terminal_display.py` defines a small immutable geometry and one loaded
`View`: complete text/pages, current page, draft input, displayed-page set and
row cache. `Term` uses the geometry for line/cursor bounds and retains its
existing byte transport and key decoder. `COLS`, `SCREEN_ROWS` and `CONTENT_ROWS`
remain compatibility aliases for the standard geometry.

View rendering uses the Matra-validated `US row column` addresses and standard
Videotex attributes. It sends only changed spans, with spaces to erase old text.
Paging does not clear the whole screen, and consecutive results reuse the frame.
A full clear is retained when entering/leaving the frame, on session resets and
for the existing full-screen theatre/password workflows. No dependencies,
database schema, model prompts or service configuration change.

## Interaction

- Type a command or follow-up and press **ENVOI** from any page, including the
  first and last. Responses no longer block in a forward-only pagination loop.
- **SUITE** advances; **RETOUR** goes back in ordinary loaded results. Boundaries
  stay on the first/last page. `SUIVANT`, `SUITE` and `PRECEDENT` plus ENVOI are
  textual page shortcuts. They never leave the reader to invoke a model/service.
- Partially typed input survives paging. **CORRECTION** and Backspace remove one
  character; **ANNULATION** clears the complete draft, including hidden portions.
- Input still accepts up to **2,000 ASCII characters**. After roughly 114 typed
  characters the three-row viewport moves to a fresh segment, with the total
  length shown in the status row. Backspace can return to the previous segment.
  No part of the stored command is truncated or auto-submitted. An oversized
  command is rejected through ENVOI; its suffix cannot become another command.
- **SOMMAIRE**, **GUIDE**, typed `SOMMAIRE`, `LOGOUT`, language defaults, inactivity
  behavior and the command vocabulary retain their existing application meaning.
  Session reset discards the loaded view and unsent input.
- In **PRONOTE grades**, SUITE/RETOUR retain next/previous-grade semantics and
  preserve partially typed commands. Grade navigation uses existing loaded grade
  records. Other PRONOTE/CALC mode screens retain RETOUR to leave the mode;
  `PRECEDENT` can still page a long loaded result. The footer reflects the mode.
- No backend result/selected mail/PRONOTE/journal state is replaced by page
  changes. Printing retains the complete original result. Review of an outgoing
  Gmail or journal draft is acknowledged only after **every page** was displayed,
  never just because the last page was reached or a command was entered early.
  Maintenance confirmation also waits for the full preview to be displayed.

The shared reader handles chat results, Gmail, memory inventories, Christmas
lists, PRONOTE results and nonsecret journal interaction. Existing boot, CALC
animation, update theatre, guide and password screens keep their dedicated loops.
A completed CALC animation returns to the frame with its complete result.

## Echo and private input

The fixed editor explicitly renders input once, after disabling the keyboard
routes to both screen and modem using the existing verified `hidden_echo` guard.
Original routes are restored on return, timeout and errors. Protocol replies are
consumed by the existing key decoder rather than becoming typed text.

The encrypted journal supplies `echo=True` only because its outer guard already
disabled hardware echo; an inner reader must not re-enable it. Password readers
remain separate, produce no secret text, drain oversized submissions and clear
on exit. No input is newly logged or sent to an API by the display code.

If a terminal cannot confirm echo suppression, ordinary public input falls back
to the existing local-echo editor. The shared document reader exits the feature
cleanly instead of guessing about echo or crashing; private guards retain their
existing fail-closed behavior. The Matra's relevant routing commands were
physically confirmed in WP1, but the new editor still needs real typing tests.

## Console and offline verification

The curses console retains the real session loop/key decoder and now interprets
split absolute cursor sequences, foreground gray-level metadata and inverse
text. Curses dim/bold/inverse attributes approximate CRT contrast; double-size
art and exact phosphor appearance remain outside this text emulator. It remains
40 × 24, with no UART or 1200-baud timing simulation.

```bash
uv sync --locked --all-extras
uv run --locked --all-extras pytest
uv run --locked --all-extras ruff check .
uv run --locked --all-extras python scripts/export_requirements.py --check
# Focused physical-style routing/editor/region tests:
uv run --locked --all-extras pytest tests/test_terminal_display.py tests/test_terminal_documents.py
# Interactive console (real APIs and ordinary side effects):
.venv/bin/python scripts/terminal_console.py
```

Tests cover actual byte-stream rendering, split cursor sequences, full input
budgets/viewport boundaries, editing, deltas/full-clear counts, first-page
follow-ups, no model/API paging, exact echo restoration, private expiry, draft
review, grade keys, reset, maintenance, printing and the existing full suite.
The fixtures now inspect rendered frames where cursor-delta writes replace the
old assumption of complete page strings separated by FF.

## Buffered serial regression and correction (3 November 2026)

The first real-hardware rollout exposed a gap in the tests: the console and
original UART fakes handled outgoing bytes immediately. The Matra receives only
120 bytes/second at 1200 baud, 7E1. A complete response can remain queued for many
seconds after Python's `write()` returns. The routing guard's one-second timeout
therefore expired before its query reached the terminal, producing the observed
`Saisie indisponible` message and unwanted reset/fallback instead of input.
WP1 did not catch this because the diagnostic explicitly paced its own writes.

`Term` now tracks a conservative transmission completion time for transports
that provide a baud rate. Routing timeouts allow for all preceding display bytes
and the query, then grant the normal finite reply budget. This does not rely
solely on the driver's `out_waiting`, which can omit bytes already handed to a
USB adapter. It does not call an unbounded serial `flush()`.

The echo guard also reads and verifies both restoration acknowledgements before
returning. Previously these were only queued and could be mistaken for a later
OFF command's reply. Unconfirmed restoration is treated as a link failure,
rather than falling back to an editor that assumes local echo works.

`tests/test_terminal_serial_timing.py` reproduces the original failure with a
virtual 1200-baud queue, split/delayed acknowledgements and a driver reporting
`out_waiting == 0`. It covers long results, successive mail-like commands,
PRONOTE mode keys, paging with partial input, hidden password entry and bounded
failure when replies stop. Actual Matra confirmation of this correction remains
required.

## WP6 physical acceptance — owner to run after the branch is made available

The offline suite cannot validate CRT readability, serial input latency or the
Matra's exact attribute behavior. Use synthetic/non-sensitive content and inspect
an outgoing draft before any deliberate send. No deployment/push is automatic.

| Check | Expected observation |
| --- | --- |
| Power-on/boot/30-second continuation | Original startup; no repeated boot after SOMMAIRE |
| Login and long chat result | Fixed frame; heading/status readable; 18 content rows |
| Follow-up on page 1, then page forward/back | Immediate submission from page 1; paging loaded text makes no new request |
| Type `REPONDRE ...`, page, correct, submit | Draft preserved; one echo; no text in content/status rows |
| Input crossing rows and 114-character viewport; backspace; ANNULATION | Viewport stays below status; full clear/correction; no top-of-screen rollover |
| Gmail list/read/reply and a long draft | Selected message retained; early `ENVOYER` cannot bypass complete review |
| Journal unlock/read/modify/cancel/timeout | Password invisible; expiry clears/locks; no private text after exit |
| PRONOTE normal results and grades | Existing child/subject context; grades go next/back without refetch; RETOUR exits other modes |
| CALC expression and animation | Result returns to frame; RETOUR leaves CALC; ordinary chat resumes |
| MAJ preview/cancel | Can read both ways; explicit confirmation still required after full preview |
| GUIDE / SOMMAIRE / LOGOUT / idle timeout | Correct exits/reset; no old content or unsent input in the next session |
| Unplug/reconnect or power-cycle terminal during input | Existing quiet recovery; usable keyboard/echo on the next session |

Return a photo of a multi-page answer with partially typed input, plus any
observed duplication, typing lag, flicker, wrong key meaning or stale content.
Keep stable `main` unchanged until these checks pass.
