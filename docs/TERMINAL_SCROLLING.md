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

## Implemented interface

The main loop recognizes the application-owned result flag
`{"display": long_text, "turn": ..., "scrolling": True}`. Providers do not select
it themselves. Existing chat, Gmail, PRONOTE, Rose, boot and CALC paths keep their
current behavior. Ordinary result/history/privacy handling remains outside this
display primitive. The synthetic `TEST SCROLL` command is restricted to ordinary
chat, just like `TEST UNICODE`, and bypasses AI, memory, recall and printing.

`services.terminal_scrolling.show(term, text, language)` owns the transition and
returns `done`, `sommaire` or `guide`. ANNULATION returns `done` with the last
page of the **complete** static document. SOMMAIRE/GUIDE restore the frame and
then retain their normal session exit/help semantics. SUITE/RETOUR, ENVOI and
typed text are discarded during the scroll; the keyboard is for stopping it,
not an invisible input editor. After restoration, all ordinary keys work again.

The future-stream entry point accepts a caller-owned queue instead of a string:

```python
# Called by the main/UART thread; producer code never receives the terminal.
action = terminal_scrolling.show(term, chunks, "fr", cancel=request_stop)
```

Chunks are strings, `None` ends the source, an exception item reports a source
failure without exposing its payload, and an empty queue means wait indefinitely.
The caller should use a bounded queue and a nonblocking `request_stop`; it must
also own/clean up its producer. True confirms termination; False/None or a failed
callback means only that a stop was requested/unconfirmed. No callback means no
claim of producer termination. Received text is retained even when a chunk has
not all reached the CRT; unread chunks after cancellation are not consumed.
The stopped source is never resumed/refetched by paging. This seam does not open
an HTTP stream, spawn a process/thread, retire an API worker, or introduce a job
store. Do not place an indefinite monitor in the existing bounded `_pending`
request slot. Existing request/protocol deadlines remain intact.

The viewer retains the complete static document/received stream in process RAM,
without an artificial time limit or silent truncation. It is not a disk-backed
log store; durable or unlimited-volume monitoring needs a later retention design.

The UART backlog is constrained to approximately one row ahead plus the row
being sent (at most about two seconds for dense French G2 text at 1200/7E1).
Polling while those bytes physically drain is flow control, not an animation
pause. The usual short `Term.clear()` settling waits apply only at entry/exit.
No minimum reading pause, SUITE wait or EOF timeout is added. The announcement
scrolls off with everything else. Queued serial bytes are allowed to drain in
order rather than flushed in the middle of an escape/accent sequence.

Every entered transition uses finally cleanup: stop the producer if interrupted,
select page mode, discard the stale View, clear/rebuild the frame directly on the
last page, restore its caret and original keyboard routes. An unconfirmed echo
OFF prevents scrolling and leaves static text in the ordinary viewer instead.
Transport failures still propagate to existing link recovery; software cannot
redraw an unplugged/powered-off screen. A thrown source-adapter bug propagates
after cleanup rather than being disguised as a successful monitoring result.

## Hardware acceptance

1. Deploy `feature/terminal-scrolling` through the normal administration update
   controls. There is no separate process or second serial-port opener; leave the
   production service running. Return to ordinary chat after login.
2. Enter **TEST SCROLL**, then ENVOI. The screen clears and announces the continuous
   journal. Its 54 numbered lines must scroll upward naturally, with accents,
   without SUITE. At the end it clears and opens **Page 3/3**, lines 37–54.
3. Press RETOUR twice (Page 1/3), SUITE once (Page 2/3), then type a normal prompt
   and ENVOI. The scroll test must not be rerun or sent to the model by paging.
4. Run TEST SCROLL again and press ANNULATION midway. Scrolling must stop promptly
   and still open Page 3/3 with line 54: it is a complete preloaded document.
   Check white text on gray, caret R22 C3, accents, correction and prompt entry.
5. Repeat with SOMMAIRE, and separately GUIDE. Confirm their usual reset/help
   behavior and that the next ordinary reply remains in page mode. Try typing
   during scrolling: no letters may appear in the log or become the next prompt.

Report approximate stop latency, missing/duplicated lines, unexpected wrapping,
caret/gray-field changes, or transport warnings. Photographs are most useful at
the restored last page and after backward navigation. The queue seam has offline
coverage only; no provider stream or live monitor is enabled for this acceptance.
Stopping further log output and drawing the restored last page are distinct:
rebuilding a dense page still takes the normal serial transmission time.

Offline acceptance completed: 1,495 tests and 400 subtests pass, Ruff passes,
and locked dependency exports match. Physical Matra scrolling acceptance is
pending; the software test result does not establish hardware acceptance.
