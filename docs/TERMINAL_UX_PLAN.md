# Matra M1 UX follow-up implementation plan

Prepared 5 November 2026 against `develop` commit `8154ded`.
Implementation status: **hardware gate pending; application unchanged**.

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

## H0 — Hardware evidence (ready for the owner)

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
| G6 native lowercase | K2/K3 status and actual raw letter/Shift case agree; keypad/symbol codes unchanged; K4 restores baseline | Later parsing WP retains hardware mode; consider explicit ASCII-letter swap only after separate review |
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
| G0–G8 | PENDING | No physical run of the interaction probe yet |

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

## WP4 — Text parsing and lowercase (separate, AFTER WP1–WP3)

Explicit hold: no production changes to encoding, decoding, case, command parsing
or password values in the probe/clearing/compression/history work. Unsupported
earlier display optimizations can be closed as no-go without blocking this later
WP; supported preceding work must first pass regression and physical acceptance.

1. Use G6/G7 evidence to specify the exact Matra input sequences/repertoire.
   Maintain an incremental SS2 decoder across serial reads, with a bounded wait
   for incomplete sequences; never swallow a following function key or expose
   accent selector bytes as ordinary letters. Unsupported/malformed input has an
   explicit behavior. Keep protocol replies out of user text.
2. Select lowercase behavior: verified native mode with snapshot/restore if G6
   passed; otherwise explicitly reviewed ASCII-letter swap at the input boundary.
   Do not implement both simultaneously. Digits, symbols, keypad and function
   keys must retain their meaning. Keep terminal power-cycle/reconnect recovery.
3. Preserve message/draft case. Normalize only command matching, with existing
   command semantics. Check usernames, addresses, passwords, consent prompts,
   calculator expressions and private editors before sharing a transformation.
   Existing passwords/credentials must remain enterable; no silent migration.
4. Support verified accented output through a dedicated Videotex encoder, with
   ASCII fallback for unsupported symbols. Decode incoming text to Python strings;
   encode output at the wire boundary. No UTF-8 bytes sent over a 7-bit UART.
5. Count displayed cells rather than encoded bytes for wrapping, cursor positions,
   input viewports, backspace and limits. One accented letter is one editable cell.
   Keep raw controls escaped so user/model content cannot issue terminal commands.
6. Update the emulator and test split/malformed sequences, letter/Shift case,
   special characters and all existing feature editors. Prompt history preserves
   decoded strings only after this WP changes the representation.

Separate acceptance: hardware/native or software lowercase decision documented,
letter case intentional, verified accents round-trip, one-key correction, and all
existing passwords/features still work. Roll back this WP independently if needed.

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

When report/photos arrive, map them to G0–G8, record limitations, then implement
only the eligible WPs in order. If evidence is missing, name the exact stage
needed; do not ask the owner to repeat the entire experiment unnecessarily.
No production change is awaiting an additional design decision before reviewing
hardware evidence; the only current blocker is that evidence.
