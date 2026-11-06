# Full-screen scrolling view

Owner request and implementation plan, 6 November 2026. Branch:
`feature/terminal-scrolling`. This is a display primitive and local diagnostic,
not an autonomous task runner or a change to ordinary chat/API behavior.

## Work packages

1. **Explicit entry and rendering.** The main loop accepts a completed result
   with `scrolling=True` and its existing `display` text. Clear the screen,
   disable local echo and the caret, select native 40-column rouleau mode, and
   announce `MF-83 / OUVERTURE DU JOURNAL CONTINU` plus ANNULATION instructions.
   Wrap/sanitize through the normal French G2 display path. Send rows without
   animation delays or SUITE waits. Limit queued wire output to roughly one row
   so a cancellation is not buried behind a whole document.
2. **Return and navigation.** At EOF, restore page mode and attributes, clear the
   scrolling screen and rebuild the standard frame directly on the last page.
   Keep the entire original text in the ordinary loaded View. RETOUR/SUITE and
   prompt entry work normally, with no refetch or second model/tool call.
3. **Cancellation and future stream seam.** ANNULATION stops display and opens
   the last loaded page. Static text remains complete. An optional caller-owned
   queue supplies string chunks, `None` for EOF, or an exception for failure;
   Empty means still pending. No idle/duration deadline applies to this view.
   The producer never owns the UART and is separate from bounded API workers.
   An optional nonblocking cancellation callback returns True only if the source
   confirms it stopped. Otherwise say stop requested/unconfirmed, not killed.
   Received streamed text ends with an appropriate stop/error marker. No provider
   stream, subprocess, background job or durable task machinery is added.
4. **Acceptance.** Test protocol ordering, real scrolling, EOF without a key,
   early/late cancellation, exact static retention, partial stream/error markers,
   indefinite quiet streams, cleanup on exceptions, echo/caret/gray frame, French
   characters, bounded 1200/7E1 backlog, backward/forward paging and main-loop
   isolation from models/memory. Run the complete repository checks. Publish the
   feature branch for physical acceptance, leaving develop/main unchanged.

## Future direction

Leaving the view while its producer continues in the background is deferred.
It needs explicit ownership, reattachment, retention/spooling, resource budgets,
privacy/session lifetime and restart semantics. ANNULATION currently requests a
stop; it does not detach, undo completed effects or guarantee remote billing ends.

Protocol reference: [STUM rouleau/page mode](https://jbellue.github.io/stum1b/),
sections “Mode rouleau ou mode page” and “Format des commandes de mode de
fonctionnement des modules”. Rouleau moves rows 1–24 together; row 00 stays put.
Use `ESC : i C` to start and `ESC : j C` to stop, never assume a frozen header.
