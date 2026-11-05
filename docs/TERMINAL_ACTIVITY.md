# Terminal activity feedback and future autonomous tasks

Design decisions recorded 5 November 2026. **Planning only; no runtime changes.**
Nearer priority: short, truthful activity messages during existing requests.
Later exploration: a full-screen activity view for a concrete autonomous task.
Neither postpones the pending hardware experiment or clearing/compression/recall
work in [TERMINAL_UX_PLAN.md](TERMINAL_UX_PLAN.md). Lowercase remains a separate
work package; accented input remains last priority.

## Product direction

Short prompts and concise answers remain the normal conversation design. They
do not impose a permanent limit on the complexity of work initiated by a short
request. A future task may use remote inference and tools for substantial work,
with the Pi coordinating execution, showing progress and returning a concise
report. Do not build a general agent framework without a concrete use case.

Output transport and terminal presentation are independent choices. The service
can consume provider progress events while keeping final answer text buffered
until complete. Full token-by-token answer display is not a current objective.
An occasional longer final report uses the existing loaded-result pagination.

## Existing implementation to preserve

- `services/mistral_answers.py` requests `stream=False`, `store=False`; it parses
  completed Conversations JSON. Hosted web search runs inside the provider call,
  so Python cannot observe its live stages through the current response path.
- The final parser ignores assistant activity before the last tool execution and
  validates the final answer, tool metadata and references. Preserve these checks.
- `services/pronote_answers.py` owns local function execution and can emit actual
  start/end events without changing HTTP transport. Its current limits are two
  tool rounds and four calls; this proposal does not increase them.
- `start_answer()` returns one final result/error through a size-one queue.
  Workers have no UART access. `wait_answer()` owns polling and exit keys.
- `View.busy()` clears the draft, displays status row 21, and clears old content.
  Activity updates must use the existing status area without clearing it all again.
- `terminal_link.MAX_WORKER_AGE` is 360 seconds. Current timeouts, discarded-result
  guards and worker retirement are for bounded requests, not durable long jobs.
- SOMMAIRE/RETOUR can stop local waiting without stopping provider work/billing.
  Do not relabel that behavior as guaranteed task cancellation.

## WP-A — Useful activity messages (nearer candidate)

### Visible behavior

1. Immediately acknowledge submission with the existing waiting message while
   the old answer is cleared. Fast plain completions may finish without another
   status change; do not delay them to stage an animation.
2. When an actual operation starts, replace status row 21 with one brief,
   in-character description. Example wording below illustrates intent only; it
   is not a new approved personality phrase bank:

   - `Consultation des archives du reseau`
   - `Interrogation du dossier scolaire`
   - `Redaction du compte rendu`

   Where a safe topic is actually available, prefer a contextual description such
   as `Recherche des horaires ferroviaires` over a bare tool name. The desired
   model-authored label explains what this particular call is about in character;
   it is not just a rotating generic waiting slogan.

3. The limit is **39 visible cells**, after existing display sanitization, to keep
   column 40 as the rollover margin. The owner's requested approximately 40-char
   description fits one line; Unicode/encoded byte length is not the cell limit.
   A malformed/long label uses a safe bounded fallback, never spills into input.
4. Update on meaningful observed stage changes. A small set of varied labels may
   keep the voice fresh, but never simulate searches, discoveries or verification.
   An elapsed-time notice may truthfully say the request is still pending; it
   must not invent a new operation merely because a timer expired.
5. Final success replaces the pending status and installs the complete normal
   response; error/timeout/exit replaces or clears it appropriately. Late events
   must not overwrite the next screen, restored input cursor or a different user.

### Where the wording comes from

Start with deterministic, personality-appropriate labels for actual operation
types. The runtime knows when its own tools execute; provider events can reveal
hosted tool activity. A model can optionally generate a short label **within the
same inference/tool-decision step**, for example as documented preceding assistant
activity or supported application-owned tool metadata. Describe intention/current
activity, not hidden reasoning, new factual findings or a promised successful result.

Model-generated wording is optional polish, not a dependency for progress. A
prompt alone cannot make the current non-streaming hosted response deliver an
early update. Do not assume there is a universal `progress` JSON field, add fields
to provider-owned tool schemas, or show arbitrary assistant text as status.
Application-owned metadata must be declared/validated separately from backend
arguments and must never cause a second execution.

Do not add a separate paid inference call per status message. It adds latency,
cost and another failure path to short operations. No extra model call is needed
for fixed stage labels or small variants. Evaluate optional same-call narration
against measured latency; discard it if the benefit does not justify its cost.

### Minimal delivery path

1. Add a small optional activity callback to existing request/tool paths. Carry a
   request-scoped operation ID, stage/tool classification and bounded safe label.
   Do not send raw tool results, arguments, secrets or hidden reasoning to UART.
2. Keep the existing final-result queue separate. Use at most one pending/latest
   activity update rather than an unbounded event backlog or a new event-bus
   abstraction. An activity failure must not lose a completed answer or block a
   worker. The UART owner alone consumes updates and calls `View.status()`.
3. Coalesce identical/stale updates and start with at most one status repaint per
   two seconds. Completion/error wins immediately. A 39-cell line plus positioning
   and attributes takes a noticeable fraction of a second at 1200/7E1; output
   backlog and keyboard handling must remain bounded. No sleep in the UART loop.
4. For local Python tools, emit actual start/end boundaries while retaining all
   existing deadlines, permissions, tool limits and review requirements.
5. For hosted web search, assess the **same** Conversations endpoint's documented
   event stream: `tool.execution.started`, `.done`, `message.output.delta`, response
   completion/error, and associated IDs/indexes. Consume events on the worker,
   buffer all answer text, expose only recognized activity to the terminal.
6. Reconstruct the complete response by entry/content IDs and indexes. Do not
   concatenate every `message.output.delta` into the final answer: intermediate
   messages and final text can use the same event type. Preserve references,
   tool success/failure, usage and private-output filtering before installation.
   Provider completion is not the same as successful tool execution.
7. Keep `store=False` and current conversation/privacy semantics. Do not silently
   enable stored conversations or add a follow-up fetch of stored messages just
   because an SDK cookbook uses that approach. On transport/protocol failure do
   not retry a tool-bearing request automatically; it may already have executed.
8. Unsupported endpoints/providers retain completed-response handling and generic
   waiting text; locally observable stages can still improve. No migration to a
   new provider API/SDK or dependency change is authorized by this design note.

### Go/no-go and validation

| Gate | GO | NO-GO / fallback |
| --- | --- | --- |
| A0 framing | Existing hardware/UX gates reviewed; status updates preserve clearing order, frame and input | Retain current working message; fix display ownership first |
| A1 local activity | Callback boundaries match actual local execution; no behavior change or extra call | Use generic waiting text for that path |
| A2 hosted events | Official contract and sanitized real fixture verify event timing and final reconstruction on the configured endpoint/model with `store=False` | Keep hosted web search non-streaming with honest generic status; local progress remains independent |
| A3 model label | Same-call metadata/prose is unambiguously classifiable, bounded and truthful, with acceptable measured overhead | Use deterministic stage labels; no secondary narrator call |
| A4 serial acceptance | No backlog growth, stuck input, wrong caret, expired guard, flicker or stale status on Matra | Reduce/coalesce updates or disable affected activity path |

Inspect current official provider support before implementation; APIs and SDKs
change. No paid live call is part of offline testing. A2 requires a separately
authorized, bounded live capture with secrets/private content removed, after
synthetic contracts pass. Do not claim synthetic fixtures establish real behavior.

Offline tests: fragmented/multiline SSE frames, interleaved IDs, intermediate
prose, tool failures, citations, missing final completion and broken connections;
bounded labels/control escaping; update coalescing; error/final precedence;
SOMMAIRE/GUIDE/RETOUR/timeout and late events; private expiry; no worker UART writes,
new API calls or loss of final results. Use the paced/bounded-UART fixture to check
status writes mixed with clearing, routing queries and prompt restoration.
Console and physical checks cover normal fast chat, slow actual search, local
tools and cancellation/exit. Confirm users see useful information without waiting
for a manufactured minimum display duration. Run the full required repository
checks when runtime code is eventually changed.

## WP-B — Full-screen activity for a long autonomous task (future, no use case yet)

Feasible direction: a short normal-chat request starts a substantial remote task;
the terminal presents sparse milestones/intermediate artifacts in full-screen
scrolling mode, then restores the fixed input layout with a concise final report.
Long content may exist internally without turning every chat answer into an essay.

Enter this view because the task runner explicitly selected a long-task policy,
or a defined observed-work threshold is met. Do not infer hidden reasoning or
switch screens simply because a normal API request is briefly slow. Keep ordinary
web search in the fixed frame. Task kind, runner limits and entry criteria require
a concrete use case before implementation.

The M1 scrolls application rows 1–24 together; there is no fixed input region in
this mode. Reserve no pretend frozen header/footer. Display meaningful completed
lines/milestones, at a rate compatible with 120 bytes/second, instead of flooding
the wire with token deltas or every tool result. Reasoning summaries, where a
provider exposes suitable public summaries, are optional activity content, never
raw hidden reasoning or an execution audit trail. No periodic narration calls.

Use an explicit enter/try/finally/exit display transition owned by the UART
thread. Disable screen echo as appropriate while monitoring exit keys; pause
normal text entry rather than allowing typing to scroll the log. On every finish,
error or local exit, restore page mode, attributes, routing/caret and the normal
frame, discard its stale row cache and install the complete final/error view.
Preserve identity, selected feature context and review rules. Ensure queued activity
does not delay restoration. Retain a bounded structured activity record separately
if needed: the terminal's scrolling screen has no scrollback. Final report paging
must never re-execute the task. Do not store raw private tool output as public chat
or memory merely because it appeared in activity.

Before building, define the real task, permitted tools/effects, maximum duration,
rounds/calls/cost, retry/idempotency policy, and whether any work survives reset or
process restart. Existing provider threads are not durable jobs; merely raising
the 360-second worker limit is not sufficient. Distinguish leaving the view from
stopping unscheduled work, cancelling an in-flight remote call and undoing effects.
Do not promise that SOMMAIRE reverses a completed operation or makes billing stop.
Retain existing explicit approvals such as final email review/ENVOYER. These are
task-specific requirements, not a reason to build speculative job infrastructure.

Gate B0: a concrete useful task justifies this view and its lifecycle. Gate B1:
owner agrees task limits and cancellation semantics. Gate B2: offline restoration,
slow-UART, stale-worker and no-reexecution checks pass. Gate B3: physical test
confirms readable scrolling, responsive exit keys and reliable normal-frame return.
Until then, preserve this direction in the roadmap only; do not implement a generic
autonomous runner, durable task store, new command vocabulary or menu system.

## References and status

- [Mistral Conversations API](https://docs.mistral.ai/api/endpoint/beta/conversations)
  and [official streaming example](https://platform-docs-public.pages.dev/agents/agents_basics/):
  event-driven progress is available independently of terminal rendering policy.
- [Official database-advisor cookbook](https://docs.mistral.ai/resources/cookbooks/mistral-connectors-01-build-a-database-advisor-agent):
  cautions that intermediate and final outputs share `message.output.delta`;
  its stored-message retrieval is not automatically compatible with this fork.
- [Agents & Conversations documentation](https://docs.mistral.ai/studio/agents/agents-api)
  currently warns about an Agents API v1 transition. Revalidate the specific
  Conversations route before work; this note authorizes no opportunistic migration.
- [Existing terminal UX and recovery evidence](TERMINAL_UX.md).

No progress implementation, new tool, provider request, prompt/personality edit
or long-task runtime is included in this documentation change.
