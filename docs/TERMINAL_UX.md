# Matra terminal UX: 40 columns, fixed input, loaded-result navigation

Integrated into `develop` on 5 November 2026 from `feature/terminal-ux`, after the
owner confirmed technical operation on the Matra. The inverse input field remains
a UX trial to assess in use. Stable `main` is unchanged.

Follow-up implementation gates are in [TERMINAL_UX_PLAN.md](TERMINAL_UX_PLAN.md).
Proposed activity messages and a future full-screen autonomous-task view are
documented in [TERMINAL_ACTIVITY.md](TERMINAL_ACTIVITY.md); neither is implemented.

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
this CRT**. The working frame uses white body text, a centered starred heading
and an experimental inverse input area. Readability does not depend on
distinguishing colors or dim text. The probe and
STUM references remain in [DISPLAY_PROBE.md](DISPLAY_PROBE.md).

Follow-up physical testing on 5 November confirmed normal/inverse CAN row clears,
REP runs (including gray spaces and accented cells), echo suppression/restoration,
buffered typeahead and the R22 C3 input caret. Native lowercase also works on this
particular original M1: AZaz becomes azAZ and restores correctly. The agreed
later implementation remains a global Python case swap with an admin setting.
The [interaction evidence](INTERACTION_PROBE.md#completed-run-protocol-keyboard-and-echo-review-5-november-2026)
closes the corresponding hardware gates; CAN clearing is implemented; subsequent package status is recorded in the plan. Full accent parsing remains separate and last priority.

## Screen layout

Service row 00 is left alone. Physical column 40 is reserved to avoid automatic
wrap and bottom-right rollover; text lines have up to 39 characters.

| Physical rows | Purpose |
| --- | --- |
| 01 | Centered `*** service/context ***` heading, normal video |
| 02–19 | 18 rows of loaded content |
| 20 | Page number and contextual function-key navigation |
| 21 | Ready/waiting/status/length notice |
| 22–24 | Fixed inverse input area: light-gray rectangle, black prompt/text |

`services/terminal_display.py` defines a small immutable geometry and one loaded
`View`: complete text/pages, current page, draft input, displayed-page set and
row cache. `Term` uses the geometry for line/cursor bounds and retains its
existing byte transport and key decoder. `COLS`, `SCREEN_ROWS` and `CONTENT_ROWS`
remain compatibility aliases for the standard geometry.

View rendering uses the Matra-validated `US row column` addresses and standard
Videotex attributes. Editing sends changed spans. Every new result and real page
change clears the previous visible content before drawing the next page, using
validated bottom-to-top CAN row clears while retaining the frame.
The same loaded view, draft input and review/context state survive that clear.
Existing session resets and full-screen theatre/password workflows are retained.
No dependencies, database schema, model prompts or service configuration change.

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

### Presence polling during page changes and waiting placement

A further hardware report exposed a second deadline path: production enables
terminal-presence polling, while the original paced UART fixture did not. With
two genuinely different full pages, the redraw queued several seconds of output;
presence polling still trusted only `out_waiting`, counted three premature
misses and retired a live terminal (`terminal_status_lost`). The earlier paging
fixture also reused identical page text, so its delta redraw barely transmitted
anything. Both omissions are corrected in the regression coverage.

Routing ACKs and presence probes now share `Term.reply_deadline`, taking the
maximum of the tracked wire completion time, driver-reported backlog and current
time before adding the finite reply allowance. Disconnect detection remains
active; long redraws no longer count transmission delay as power loss. All paced
input tests enable the production presence monitor and use atomic function-key
frames; added cases page through distinct full screens in both directions while
preserving a partial command.

Chat/CALC/PRONOTE waiting output now uses an explicit frame operation: the message
is centered on status row 21, long wording is visibly abbreviated, submitted
input is cleared and the cursor is hidden while processing. Input setup restores
the cursor within rows 22–24. Raw CR/LF operations are no longer used to position
these waiting messages. The console mirrors cursor visibility as well as text.

### Shared content transitions (4 November 2026)

The owner confirmed that normal chat and the initial upward wipe worked, then
clarified that the wipe should run **right to left, bottom to top**. Email/local
results also need the same transition; the earlier wipe was attached only to API
waiting, leaving document replacement and paging to overwrite old text in place.

Following the 5 November hardware validation, occupied rows clear with cursor
positioning, explicit attributes and CAN (`18`), visiting lower rows first.
CAN erases the entire row tail at once and leaves the cursor in place. This
supersedes the earlier right-to-left `BS SPACE BS` animation and 100-character
full-screen threshold. One row clear uses 10 bytes; all 18 content rows take
180 bytes, approximately 1.5 seconds of UART time, excluding other frame updates.
The heading remains visible regardless of answer length. FF is reserved for
initial setup or an uncertain screen cache.

On submission, clear submitted input first, retain `> `, update the centered
waiting indicator, then clear old content. Input clearing retains the gray
attributes through physical C40 without ever printing there. The API response
is queued after the wipe; a new view inherits the blank cache. There is no added
animation sleep or serial flush.

Email list/read/reply, journal, PRONOTE, replacement results and both page
directions use the same transition. Drafts, loaded results, feature context and
review state remain intact. First/last-page boundary keys do not clear content.
New text is drawn top-to-bottom onto blank cells. Private checks bracket the
transition; the caret is hidden until the editor is ready at the input region.

Tests inspect blank-before-replacement ordering, bottom-to-top clears, partial
row and bottom-row behavior, normal/inverse attributes, uncertain-cache recovery,
shorter answers, long drafts, review checks and serial backpressure. The console
models CAN as well as BS and FF; physical acceptance remains necessary.

### Write-side backpressure after PRECEDENT (4 November 2026)

The owner reported an interrupted answer with the old `Page 1/1` footer and
waiting label still visible, followed by local keyboard echo inside the answer.
The journal confirmed `Terminal link unavailable (SerialTimeoutException)` and
a subsequent transport reopen, specifically after PRECEDENT. This is an aborted
redraw: the new page counter, ready status and input cursor had not reached the
screen when the write failed. Recovery then waited for SOMMAIRE, leaving the
partial frame visible with the original echo route restored.

The earlier deadline corrections covered incoming protocol replies, but physical
writes still had a fixed two-second timeout. The paced UART fixture also accepted
an unlimited output queue. It now has a bounded USB-batch variant that starts
applying backpressure on RETOUR; with the old write budget, it reproduces the
same exception during the backward redraw. Its batch sizes are test scenarios,
not measurements of the owner's particular adapter.

Each physical write now gets a finite budget from the same tracked/driver queue
estimate as protocol replies, plus its own 7E1 wire time and the original
two-second slack. The slack is saved separately so enlarged budgets cannot
compound from one write to the next. The operation is attempted once: a real
timeout can follow a partial write and must still use the existing recovery path.
There is no blind retry, serial flush or page/model refetch.

Tests cover backward navigation with two buffer sizes in chat and the document
reader, partial command preservation, the upward wipe followed by three distinct
pages and repeated forward/backward navigation, actual footer/input positions,
finite stalled-write failure without replay, and return to the short budget once
the queue drains. Repeat these paths on the Matra before treating the fix as
physically validated.

### Heading and inverse-input trial (5 November 2026)

The owner found inverse video effective as an accent but too heavy for the
top-level heading. Row 1 now centers `*** title ***` in normal video. Long titles
are shortened to leave room for all three stars on each side.

The three input rows are filled with inverse spaces across the existing
39 usable columns, including blank rows. Typed characters, backspace, viewport
updates and ANNULATION keep that inverse style. Reverse submission erasure also
uses the same shade of inverse spaces so it cannot punch holes in the field. Full
clears rebuild the entire input region; content, heading, status and
navigation stay in normal video. Column 40 remains the unused rollover margin.
Private password screens retain their separate hidden-input behavior.

The owner confirmed the inverse input works well, but found its initial white
background too bright. Check the gray field, black typed text and cursor visibility on the Matra,
then compare short/long input, submission and both paging directions.

### Softer input background (5 November 2026)

`INPUT_FOREGROUND = 2` selects the Videotex green code. On a monochrome CRT this
is a gray level, documented by [STUM 1B](https://jbellue.github.io/stum1b/) as
nominally 70% luminance, versus white's 100%; actual luminance depends on the tube.
Inverse video swaps the foreground and background pixels, so the input becomes
black text on gray while ordinary content remains white on black. This uses the
existing character inversion rather than changing zone-background delimiters.

The row cache now includes foreground color as well as text/inversion; a color
change therefore repaints even identical text. Reverse erasure preserves the
cached color, and full clears restore all three input rows at the selected level.
The constant is the single adjustment point if the Matra needs further dimming;
Videotex color numbers are not ordered by brightness (magenta code 5 is nominally
60%, cyan code 6 is 80%). Console gray-level rendering is approximate; assess
comfort on the physical screen.

## WP6 physical acceptance — owner to run after the branch is made available

The offline suite cannot validate CRT readability, serial input latency or the
Matra's exact attribute behavior. Use synthetic/non-sensitive content and inspect
an outgoing draft before any deliberate send. No deployment/push is automatic.

| Check | Expected observation |
| --- | --- |
| Power-on/boot/30-second continuation | Original startup; no repeated boot after SOMMAIRE |
| Login and long chat result | Frame restored after dense-page clear; correct page count and input cursor |
| Short answer followed by another short answer | Right-to-left, bottom-to-top erasure; no old tail |
| Dense answer followed by a short answer; MAIL list/read/reply | Fast full clear and rebuilt frame; new content starts on blank cells |
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
