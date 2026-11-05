# Matra M1 UX follow-up implementation plan

Prepared 5 November 2026 against `develop` commit `8154ded`.
Implementation status: **WP1–WP2 implemented; offline checks passed (1,370 tests); physical acceptance pending**.

Final owner decision after the hardware review: use verified **native lowercase**
for the physical Minitel, including passwords, controlled through the admin
console. Console/web input stays unchanged. This supersedes the earlier Python
swap proposal. Accented-character parsing is explicitly deferred and unchanged.
The owner authorized completing WP1–WP4A offline before physical acceptance; no
intermediate hardware run is required to continue implementation.

This plan follows the owner's ordering: clear/redraw efficiency and prompt recall
first; **all text parsing, accent handling and uppercase/lowercase changes later
in a separate work package**. Hardware tests may cover all of them together.

## Constraints and existing behavior

- Plain Matra Minitel 1, 40 x 24 usable cells, 1200 baud 7E1; no assumed 1B/ANSI,
  80-column or mosaic capability. Keep the current 39-column safe writing width.
- Keep the conversational interface and command vocabulary. No menu framework,
  new dependency, API request for navigation or wholesale renderer refactor.
- `services/terminal_display.py` owns `Geometry`, the loaded `View`, row cache,
  fixed editor, shared content transition and visible-character clear threshold.
- Heading row 1, content 2–19, navigation 20, status 21, input 22–24. Input is
  inverse foreground 2, nominally 70% gray. Do not change these visuals here.
- Above 100 visible answer characters, current transition sends FF and rebuilds
  the frame. At/below 100 it erases right-to-left, bottom-to-top. Loaded pages,
  partial input and feature context survive paging.
- `Term` owns the UART and serial queue/reply budgets; workers never draw.
  Echo suppression/restore must remain acknowledged and bounded.
- Commands already compare case-insensitively where intended; drafts/messages
  preserve their existing case. Accent input currently lacks a complete decoder.
- Gmail reviewed-draft checks, PRONOTE grade/exit keys, private expiry, hidden
  entry, SOMMAIRE/LOGOUT and terminal recovery are existing compatibility contracts.

## H0 — Hardware evidence (reviewed 5 November 2026)

Run [INTERACTION_PROBE.md](INTERACTION_PROBE.md); return its final report and
labelled photos. The single-script `--interaction` mode never imports production
services. No later WP starts before we review the evidence. Unknown, missing,
contradictory or interrupted tests do not count as passes.

| Gate | GO evidence | NO-GO / unknown path |
| --- | --- | --- |
| G0 recovery | E2 matches K1; original case/echo restored; no recovery errors | Stop rollout; investigate restoration or power-cycle; retain current application |
| G1 normal CAN | C2/C3: normal whole/partial/bottom/empty rows correct, fences intact; corresponding cursor checks pass | Retain existing reverse erase and FF threshold; isolate failure before any CAN use |
| G2 gray CAN | C2/C3: inverse shade uniform, prefix preserved, no tails/holes; gray cursor checks pass; clearing C40 causes no wrap/artifact | CAN may be used only in normal content if G1 passed; retain existing input erasure |
| G3 basic REP | R1: exact 1/2/39-cell pairs and correct cursor positions; no rollover | Keep literal runs for ordinary text; no REP optimization |
| G4 gray REP | R2: literal/compressed gray and black runs identical and no stale text; cursor at C40 | Keep literal input fills; G3 can still enable separately validated normal runs |
| G5 RÉPÉTITION | K1 records `13 43`, other function keys retain their codes; E1/Q1/Q2 have no routing/caret anomalies | Defer recall key binding if key/routing unreliable; do not add a command substitute automatically |
| G6 native lowercase (informational) | K2/K3 status/raw results and K4 restoration document firmware capability | Retain original behavior and report failure if native mode cannot be confirmed |
| G7 repertoire | T1 photos identify supported glyphs; K1/K2/K3 raw accented/special input captured without ambiguity | Later parsing WP supports only verified sequences; retain readable ASCII output fallback |
| G8 REP accents | R1 eight accented cells identical and cursor advances eight cells | No multibyte-character repeat compression; unrelated REP runs may still qualify |

Visual proof and cursor proof are complementary. A raw status ACK is not proof
of a display change. If cursor replies are absent but photos look correct, mark
the gate unknown and review explicitly; do not silently loosen its criteria.
G6/G7 results do not authorize parsing changes before WP4. G0 is common to every
rollout; other gates are independent, so unsupported features need not block all
work. Every choice is resolved by this matrix once evidence is supplied.

Record results here after review:

| Gate | Decision | Evidence / limitation |
| --- | --- | --- |
| G0 | GO | Completed run; original case and keyboard routes acknowledged, page mode restored; owner explicitly confirmed final E2 echo passed |
| G1/G2 | GO | C1/C2/C3 photos show correct normal/gray clearing and redraw; all six CAN cursor checks pass, including bottom and empty rows |
| G3/G4/G8 | GO | R1/R2 photos show identical literal/compressed runs, uniform gray and no stale text; all five REP cursor checks pass, including eight accented cells |
| G5 | GO for implementation | RÉPÉTITION is `13 43`; all eight function codes captured; E1 echo absent, Q1 receives complete ordered ABC123 + ENVOI without visible output contamination, Q2 cursor exactly R22 C3 |
| G6 | SUPPORTED; informational | Native mode changes AZaz to azAZ, status `40` → `48` → `40`; original case restored. Native mode selected; no Python case swap |
| G7 | VERIFIED SUBSET | T1 output repertoire confirmed; input captures case, digits, `*`/`#`, punctuation and é/à/è/ù. Cedilla input remains untested; full parsing stays deferred |

Partial evidence, 5 November 2026: first K1 captured correct unshifted/shifted
letters, digits, punctuation, four SS2 accented letters and all eight function
keys (`13 43` for RÉPÉTITION). Owner confirmed visual echo. Cedilla and keypad
`*`/`#` were unobserved in that first run. SSH Enter did not advance the probe;
timeout attempted recovery before CAN/REP/echo tests. That interrupted run did
not pass full gates; retain it as historical evidence alongside the completed run.

Subsequently the owner reported finishing and supplied C1/C2/C3, R1/R2 and T1
photos, followed by the complete log from `minitel-interaction-FCO0uZ`, five
keyboard/echo/typeahead photos, and explicit confirmation that final E2 echo
passed. These close the applicable hardware gates; no rerun is required. Manual
OBS fields remain `unknown` in the original report because Enter was used;
the reviewed decisions above use the separate photos, raw checks and owner
confirmation without rewriting that report. See the detailed evidence in
[INTERACTION_PROBE.md](INTERACTION_PROBE.md#completed-run-photo-review-5-november-2026).

## WP1 — Shared clearing with validated CAN

Dependency: G0 + G1; G2 additionally for input use. Limit changes to existing
screen primitives/transitions; no text normalization or keyboard-mode changes.

1. Introduce a small explicit cursor-position-plus-CAN helper, reapplying the
   intended attributes after US. Confirm its resulting row-cache state includes
   blank cells and their existing inverse/color style.
2. Clear occupied answer rows bottom-to-top, then render new answer top-to-bottom.
   Use the same transition for chat, Gmail list/read/reply, journal documents,
   PRONOTE, loaded-page moves and shorter replacement content. Skip blank rows.
3. If G2 passed, clear submitted input rows bottom-to-top with the corresponding
   gray style; preserve the `> ` prefix and erase only its tail on the first row.
   Otherwise preserve current right-to-left input erasure.
4. Treat C40 intentionally: CAN clears to physical C40 but does not advance the
   cursor. Never print there or leave a different shade that causes flicker or
   wrap on the Matra. If the hardware evidence is unsuitable, restrict CAN to
   normal content and retain the input fallback.
5. With CAN accepted, prefer row clears over FF for ordinary view transitions:
   retain heading/frame, avoid rebuilding input unnecessarily. Keep FF for initial
   setup, explicit recovery and uncertain cache state. Evaluate measured wire-byte
   cost rather than blindly retaining the 100-character threshold designed for
   three-byte-per-cell erasure. If CAN fails, leave that threshold unchanged.
6. Hide the caret during transitions, return it to the fixed input field on ready.
   Preserve the established submit rhythm: clear draft, show waiting status, clear
   old output, draw response onto blanks. Keep all UART writes on their owner.

Validation: cache/output coherence, gray/prefix preservation, shorter shared
prefix answers, blank rows, bottom row, cursor after every path, full/partial
drafts during paging, no rerequest or context loss. Extend existing
`test_terminal_display.py`, `test_terminal_documents.py` and paced/backpressure
fixtures rather than building a second renderer. Console emulator must implement
CAN semantics before tests rely on its output. Repeat distinct-page SUITE/RETOUR
on real hardware, especially PRECEDENT's former timeout path.

Acceptance: blank-before-new-text transition, no tails, no header repaint in the
CAN path, correct page count/caret, bounded serial operations and usable input.

## WP2 — Compress validated repeated runs

Dependency: G0 + G3; G4 additionally for inverse spaces. G8 only if repeating
encoded accents is ever added in the later parsing WP.

1. Add a small wire encoder for sufficiently long repeated ASCII runs. A REP
   parameter counts **additional** cells, up to 63; short runs stay literal and
   longer runs are split without exceeding the row's safe writing extent.
2. Begin with spaces/input backgrounds and existing separators, only in styles
   which passed the probe. Do not add decorations or change timing with sleeps.
3. Preserve logical cell caches independently of wire compression. Byte savings
   must not alter page text, pixel shade, review state or cursor coordinates.
4. Keep fallback literal encoding explicit. Update the console to decode REP and
   preserve current attributes. Serial budgets already count encoded bytes;
   verify them against compressed writes and a bounded USB queue.

Validation: literal versus compressed rendered equality, threshold/count edges,
row boundaries, attribute resets, gray fills and repeated full-clear/paging paths.
Compare transmitted bytes for representative normal/gray screens. Physical pass:
identical appearance with less wire traffic; no protocol bytes in input.

CAN and REP remain independently adoptable. CAN may make REP less useful for
erasure, but REP can still reduce gray input fills and frame rebuilding.

## WP3 — RÉPÉTITION recalls recent prompts

Dependency: G0 + G5. No native lowercase, Unicode decoder, persistence, schema
change or new model call. This WP can proceed if CAN/REP are unsupported.

1. Keep a bounded in-memory history (initially 20 submitted ordinary chat prompts)
   scoped to the active user/conversation. Preserve the existing representation
   exactly. Collapse consecutive identical entries, not distinct prompts.
2. Capture eligibility at the ordinary chat-submission path, not a global serial
   reader: exclude feature commands, email/journal composition, passwords,
   onboarding, consent/admin confirmations and private Rose/journal interaction.
   Start with a deliberately narrow scope rather than guessing which feature
   inputs are safe to replay. No cross-user recall.
3. First RÉPÉTITION saves the current draft/viewport and recalls the newest entry.
   Further presses walk older entries; after the oldest, restore the saved draft,
   then cycle again. Recalled text is editable; ENVOI is always required. Empty
   history leaves the draft unchanged with a brief status indication.
4. Any actual edit exits recall traversal while keeping the edited draft.
   ANNULATION clears the draft and recall traversal. Paging preserves draft and
   traversal. A successful submission resets traversal and adds eligible text.
5. SOMMAIRE, LOGOUT, user switch, idle session expiry and terminal-session recovery
   discard recall history. Persisting recall across sessions is explicitly later.
6. Include K_REPET in genuine keyboard-activity accounting; do not let recall
   accidentally look like terminal inactivity. Keep all other key meanings intact.

Validation: empty/one/many entries; bound and adjacent duplicates; saved long draft
and viewport; editing/correction/clear; page forward/back; submission exactly once;
no AI/API calls merely for recall; identity/session isolation; private/confirmation
inputs excluded. Console needs a documented RÉPÉTITION binding, preserving current
shortcuts. Physical pass: repeated presses recover exact prompts and the original
draft, keep gray fill/caret intact, and never auto-submit.

## WP4A — Native lowercase input (separate, AFTER WP1–WP3)

Final owner decision: use the firmware mode confirmed by G6, with no software
case swap and no accent/parser changes. Implement after preceding packages pass
offline regression; physical acceptance of the combined branch follows.

1. Configure only the physical terminal. Snapshot its case flag, set the selected
   mode and verify its acknowledgement at initialization/reconnection. Consume
   complete protocol replies internally; preserve any early typed input.
2. Cover all input, including usernames, composition, confirmations and passwords,
   through the keyboard mode itself. Shift gives the opposite case. Leave stored
   credentials/hashes, output, command normalization and accent parsing unchanged.
3. Add an authenticated, CSRF-protected Terminal/keyboard selector to the admin
   console. Persist allowlisted `MINITEL_INPUT_CASE=lowercase|legacy`; default to
   lowercase. Apply by an explicitly announced service restart, never mid-entry.
   Console, browser and API input are unaffected.
4. If support/configuration cannot be confirmed, log a bounded warning and attempt
   confirmed original behavior; never pretend lowercase was enabled or add a
   Python swap fallback. Restore the previous case flag on clean close where
   possible; retry the configured mode on each new physical connection. Report
   unconfirmed restoration without logging input or secrets.

Validation: correct exact commands/status flag, unsupported/malformed/delayed
replies, early key buffering, serial budgets, reconnection and cleanup; both modes
across ordinary/secret readers, mixed-case existing passwords, Shift/nonletters,
console bypass, admin persistence/validation/auth/CSRF/restart success and failure.
G6 supports implementation; production physical acceptance remains separate.

## WP4B — Accented and supplementary characters (last, separate)

Priority: after WP4A and other accepted UX work; optional until justified. Full
accent support must not expand the small lowercase change into a Unicode overhaul.

1. Use G7 evidence for the Matra repertoire. Extend existing sequence framing
   with bounded incremental SS2 decoding; do not swallow a following function key.
   Define unsupported/malformed-input behavior. Preserve native shifted function
   key/character assignments. Keep protocol replies out of user text.
2. Support verified accented output through a dedicated Videotex encoder, with
   ASCII fallback for unsupported symbols. Decode incoming text to Python strings;
   encode output at the wire boundary. No UTF-8 bytes sent over a 7-bit UART.
3. Count displayed cells rather than encoded bytes for wrapping, cursor positions,
   input viewports, backspace and limits. One accented letter is one editable cell.
   Keep raw controls escaped so user/model content cannot issue terminal commands.
4. Update the emulator and test split/malformed sequences, letter/Shift case,
   special characters and all existing feature editors. Prompt history preserves
   decoded strings only after this WP changes the representation.

Separate acceptance: verified accents round-trip, one-key correction and cell
counting work, all existing passwords/features remain usable. Keep native keyboard case independent of SS2 selectors and output encoding. Roll back this
WP independently; unsupported symbols keep readable ASCII fallbacks.

## Additional direction — activity feedback and autonomous tasks

[TERMINAL_ACTIVITY.md](TERMINAL_ACTIVITY.md) records a nearer candidate WP-A for
brief in-character messages grounded in actual activity, plus a later WP-B for a
concrete autonomous task's full-screen scrolling view. Provider event streaming
can carry progress while final answer text remains buffered. This does not change
the hardware gate criteria or the order of WP1–WP3. Native lowercase stays in
WP4A; full accents stay last in WP4B. Scheduling activity implementation is separate
from recording the design; no runtime progress/agent work begins in this change.

## Common regression and rollout gate

Before implementing, fetch/reinspect remote `develop` and concurrent changes.
Use coherent commits; no push without explicit owner request; leave `main` stable.

For each applicable WP, run `uv sync --locked --all-extras`, focused tests, then
the complete pytest suite, Ruff and requirement-export check from AGENTS.md.
No automated live serial, Gmail, PRONOTE, printer or model calls. Add production
CAN/REP emulator support only with the corresponding implementation WP.

Physical acceptance before promotion: long/short chat, dense-to-short replacement,
MAIL list/read/reply/draft review, PRONOTE results/grades, journal unlock/read/edit,
CALC, boot/maintenance, hidden input, long input/backspace/ANNULATION, both paging
directions with an unsent draft, GUIDE/SOMMAIRE/LOGOUT, timeout and reconnect.
Check no stale output, accidental sends, lost context, private-history recall,
wrong page counts, double echo or cursor in answer rows. Record which paths the
owner physically exercised; offline success is not physical validation.

Rollback each WP as a coherent commit rather than altering user data or terminal
geometry. Keep the pre-probe application behavior as the no-go fallback. Further
heading/attribute polish and mosaic experiments are out of scope.

## Resume checklist

Hardware review is complete: G0–G5 and G8 allow WP1–WP3 implementation in order.
Fetch/reinspect remote `develop` before production edits, then validate each WP
offline and physically as described above. Native lowercase support is used by the agreed native/admin approach in WP4A. Cedilla input remains untested
for WP4B and does not block clearing, compression or prompt recall. Hardware
capability passes do not replace application regression and rollout acceptance.

## Implementation record

- WP1: shared normal/inverse CAN tail clears, bottom-to-top occupied rows; normal
  transitions retain the frame and draft. Full clear is reserved for initialization
  and uncertain-cache recovery. CAN establishes input C40 shade without printing
  into it; the console mirrors CAN cursor and attribute semantics. The old
  100-character FF threshold is removed because one occupied row now clears in
  10 wire bytes (3 position + 4 attributes + 1 CAN + 2 positive),
  rather than three bytes per erased character. See package tests for exact
  transmission checks; physical acceptance follows implementation of all packages.

- WP2: printable ASCII spans use REP only when shorter than literal text; split
  runs at 64 cells and retain literal fallback for runs shorter than four. Logical
  rows/cache remain uncompressed. The console accepts split REP sequences, with
  identical cells/styles/cursor. A dense repeated-text page saves at least 648
  bytes; the USB backpressure fixture deliberately uses incompressible rows so
  it still exercises the original failure condition after optimization.
