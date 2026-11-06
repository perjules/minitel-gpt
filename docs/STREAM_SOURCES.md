# Owned API and process streams

Implemented on feature/terminal-scrolling, 6 November 2026. This extends the
[scrolling view](TERMINAL_SCROLLING.md) from a queue consumer into a complete
producer lifecycle. Ordinary chat, private tools and API adapters stay unchanged.

## Use from the Minitel

In ordinary chat:

- TEST STREAM starts a synthetic Python monitor: 54 numbered lines, one per
  second. Its cadence belongs to the producer, not to a UI animation.
- STREAM your public question streams one isolated Mistral answer.
- STREAM WEB your public search uses the existing Mistral Conversations endpoint
  with hosted web search, only when web search is enabled in configuration.
- ANNULATION stops the producer and retains received output with a stop marker.
  SOMMAIRE/GUIDE stop it too, then perform their usual reset/help action.

After EOF or cancellation, the normal frame opens on the last page. RETOUR/SUITE
browse the retained transcript and typing works normally. There is no second
request or process execution when paging, no SUITE wait at EOF, and no overall
or idle viewer deadline. Normal serial drawing time still applies.

STREAM is explicitly isolated: only the supplied prompt goes to the configured
model. No prior chat, profile recall, Gmail/PRONOTE/journal tool access, history,
memory learning, prompt recall or printing is involved. Use ordinary chat for
those features and contextual follow-ups. This avoids turning a monitoring log
or unfinished answer into a stored, validated personal fact. The current terminal
entry supports Mistral Conversations and Mistral Chat Completions; other configured
providers report streaming unavailable without a fallback call.

All API prose is labelled provisional while arriving. Hosted search start/end
messages describe observed events only. Hidden thinking, raw tool arguments and
provider error bodies are never public output. At explicit completion,
Conversations entries are reconstructed by output/content index and ID and
checked through the existing final-answer, search and citation validator. The
transcript preserves public intermediate text as provisional activity; the
canonical validated final answer is separately available on source.result.
Missing completion, unsupported local function calls, model truncation, broken
SSE or failed factual checks retain partial output with a failure marker, never
a successful completion marker. No retry is automatic.

## Reuse from application code

Only the main thread owns the terminal. Commands are application-owned lists,
never shell command strings or model-generated code:

~~~python
from services.stream_sources import ProcessSource, show

# Any installed script/program; no shell expansion or terminal stdin.
show(term, ProcessSource(["journalctl", "--follow", "--no-pager", "-u", "example"]), "fr")

# Python is explicitly unbuffered; arguments, cwd and env are optional.
show(term, ProcessSource.python("/path/to/monitor.py", "--read-only"), "fr")
~~~

A program must flush its output. Python uses -u and PYTHONUNBUFFERED=1.
stdout/stderr share one bounded OS pipe, avoiding a blocked unread stderr pipe.
UTF-8 is decoded incrementally; invalid script bytes become one replacement
glyph, CRLF and carriage-return progress output become ordinary lines, and
terminal control bytes go through the existing safe-display escaping.

~~~python
from services import llm_streaming
from services.stream_sources import show

source = llm_streaming.mistral(
    [{"role": "user", "content": public_question}],
    "fr", key, configured_model,
    api="conversations", web_search=True,
)
show(term, source, "fr")
canonical = source.result  # Only after validated completion and a clean worker exit.
~~~

The lower-level ProcessSource.api(job) accepts an application-owned URL, headers,
payload and protocol (conversations or chat_completions). It is not exposed as
an arbitrary URL/shell tool to the model. Existing public-history filtering also
applies when an application supplies history to the Mistral factory.

## Stop and cleanup

The source starts lazily after echo suppression succeeds. Each producer runs in
its own process group. ANNULATION sends TERM, allows at most 0.2 seconds for the
leader to exit, then sends KILL to remaining group members and reaps the leader
(with a further bounded 0.5-second wait). This short cleanup grace is not a task
duration limit. The source context also cleans up on source errors, keyboard
interrupts, SIGTERM, serial failure and unexpected exceptions. No background
continuation or detached job is supported. A failure to reap is an error, never
silently treated as successful cleanup. Scripts must not deliberately daemonize,
change process groups or escape the owning session.

The blocking HTTP reader uses the same killable worker. Closing/killing it closes
the local connection, including while awaiting the first event. A connect attempt
has a ten-second transport budget; active or quiet response streams have no read
or overall deadline. Local stop cannot confirm remote provider computation or
billing ended and cannot undo an already completed hosted operation.

API credentials/payload travel through an anonymous stdin file, not process
arguments or a named persisted job. API worker stderr/tracebacks are not displayed.
Requests use stream=True, reject redirects/non-SSE responses, and keep store=False
on Conversations. No SDK/dependency or endpoint migration was introduced.

The retained transcript is RAM-backed, not a durable/unlimited-volume log store.
OS-pipe and UART backpressure bound queued work, not the eventual retained log.
A bounded queue remains supported for existing callers; the owned process entry
is the one that guarantees local cleanup. It never uses the six-minute _pending
API-worker slot, so monitoring does not inherit that unrelated request deadline.

## Tests and physical check

Offline tests use synthetic child scripts, request mocks and a loopback SSE
server. They check incremental delivery before EOF, fragmented UTF-8/SSE,
private-thinking exclusion, ID/chunk reconstruction, citations/tool failures,
missing completion, blocked connections, nonzero exits, full pipes, stubborn
leaders/children, no surviving group work, echo gating and normal UI recovery.
No real model/provider call is made by the automated checks.

On the Matra, first try TEST STREAM, ANNULATION midway, RETOUR/SUITE and a normal
prompt. Then manually try STREAM Explique le métier Jacquard and, if enabled,
STREAM WEB Recherche la météo à Lyon. Those last commands are real billed API
requests. Check accents, partial output, stop latency, last-page navigation and
the white-on-gray input/caret after each exit. Background continuation remains
deferred.
