# MinitelGPT: purpose of this fork

This fork builds on [jherard-fr/minitel-gpt](https://github.com/jherard-fr/minitel-gpt),
which connects an original Minitel terminal to modern AI services. See the original
[README](README.md) for the upstream application and installation instructions.

The aim is a useful conversational assistant with a coherent retro-futuristic
identity: a Minitel terminal linked to an imagined atomic-age mainframe. It combines
earnest technological optimism, deadpan machine diagnostics, cinematic hacker
jargon and occasional unsettling composure. Vintage presentation does not impose
a fictional 1989 cutoff on factual knowledge.

## Interaction design: words, context and clear feedback

**The primary interface is: type what you want into the computer.** Users express
intentions, read results and refine them in place. Each new capability extends the
usable vocabulary while reusing the terminal's input, result display and navigation.
Its design starts with: what can the user say, and what useful result should appear?

The Minitel's small screen, keyboard and slow serial link are design constraints
to preserve. Build ordinary feature workflows around words and context; do not
expand them into feature-specific screens, menu trees, forms or WYSIWYG editors.
The web admin remains available for configuration, diagnostics and faithful testing
of terminal behavior; its extra space must not become a requirement for everyday use.

- **Keep a small, memorable command vocabulary.** Exact commands such as `MAIL`,
  `LIRE 2`, `TEXTE` and `ANNULER` are predictable shortcuts with explicit Python
  behavior. Do not call a model just to interpret an already recognized command,
  or accumulate cryptic syntax and dozens of aliases.
- **Accept flexible language around that vocabulary.** AI can interpret a request,
  compose or summarize content and apply conversational revisions. It translates
  intentions into the small set of operations the application actually supports.
  Python validates targets, parameters and permissions; the model cannot invent
  capabilities, recipients, successful actions or authorization.
- **Make context explicit internally.** A selected result and pending draft make
  references such as "it" or "make it shorter" meaningful. Use bounded session
  state, clarify ambiguous targets with a short question or numbered choices, and
  preserve deterministic reset behavior. Do not silently guess what a consequential
  request refers to.
- **Show useful next actions with the result.** A short line such as
  `TEXTE / RESUMER / REPONDRE instructions` teaches the available vocabulary without
  another navigation level. Keep hints relevant and brief. A contextual `AIDE`
  command is a possible future extension, not an implemented feature claim.
- **Use the same interaction rhythm across capabilities:** request, result,
  refinement and explicit confirmation when an action needs approval. Reuse
  pagination and function keys; paging loaded output should not refetch data or
  invoke AI. The ENVOI key submits input; approval of email sending requires the
  distinct literal `ENVOYER` command after review of the current draft.
- **Separate composition from output formatting.** Users supply meaning, facts,
  recipient and tone; AI can draft and revise; a renderer adapts the result to the
  terminal, email or a future printed document. Provide an appropriate preview
  where layout matters. Review must cover the actual outgoing content, including
  application-owned additions such as the Minitel signature.
- **Keep the fiction in presentation.** Retro character and world-building may
  accompany interaction, but real messages, factual results, errors, permissions
  and action confirmations must remain truthful. Do not inject fictional facts
  into outgoing content unless the user explicitly requests that style.

Keep interpretation and orchestration proportionate to the Pi: small functions,
bounded requests/state and justified model calls. These principles do not require
a new routing framework or a refactor of working features. Future capabilities
remain subject to their own scope and validation; this section defines design
guidance and adds no runtime functionality.

## Initial scope

The default birthday/1980s personality is replaced with this character through
the existing preset configuration. Other presets remain available.

The character's self-name is **Minitel 722079V**, identifying the owner's Matra
Communication Quimper terminal (model 722 079 V, hardware reference XB0006B-2).
**3615 IA** remains the service identifier. Its imagined remote infrastructure is
**ALCATEL MF-83**, with MF-83 as the short code: mothballed, long forgotten, at an
undisclosed location, yet mostly operational through persistent fallbacks. Its
resources are immense by 1970s-1980s standards and hopelessly outdated today.
This does not identify the Matra terminal as Alcatel equipment or a historical
Alcatel mainframe product.

The model internally understands that this machinery and its telemetry are fiction.
Theatrical reports remain completely earnest, without winks, joke explanations or
visible fiction labels. Factual answers, actual-status and out-of-character questions,
real errors and tool results remain truthful; fictional readings are never evidence.
This prompt policy does not provide structural separation of reasoning and presentation.

Reference: the personality workshop's updated v0.4 harness, archive version 38,
10 October 2026, specifically `data/world.json` and `prompts/personality.txt`.
The policy is adapted to the existing single chat prompt and independent boot request.

The terminal starts with its logo and a language prompt. Submit `en`/`English`
or `fr`/`French` with **ENVOI** (case-insensitive). Blank, unrecognised or timed-out
input silently selects French. A fictional mainframe startup aims for roughly one
screen and scrolls automatically when longer. **SUITE** opens the question prompt;
**SOMMAIRE** returns to language selection, and **GUIDE** retains access to administration. Boot-screen
inactivity returns to the start screen after five minutes.

The language applies to terminal messages and the model's default answer language;
function keys always retain their French names. French question/loading messages
retain the active preset's custom wording; English uses the terminal's translated
wording. Language selection and startup diagnostics are never conversation turns.
**SOMMAIRE** resets both language selection and conversation history.

After language submission, a short waiting message appears while one independent
request generates boot diagnostics, aiming for 12-18 lines without a hard line-count
limit. It uses the configured Mistral or Claude model and credentials, with an
eight-second HTTP request timeout and no
retry. The dedicated prompt is `config/boot_prompt.txt`; its nine bilingual examples
in `config/boot_examples.json` are reviewed selections from the personality lab
(i05, i09, l11, l13, i16, l25, l04, l12 and l24). These are alternate examples,
not consecutive stages to copy into one startup.

Python rejects incomplete/empty responses, control characters, Markdown and explicit
function-key instructions. It transliterates accents using the existing ASCII helper
and wraps long lines to 39 columns without truncating their text. It then adds
a blank separator and the final **SUITE** instruction itself. Boot display uses
Videotex rouleau mode (`ESC 3A 69 43`), so the ending stays visible even when the
sequence exceeds 24 rows. Page mode (`ESC 3A 6A 43`) is restored on every exit;
protocol acknowledgements are consumed rather than becoming question input.
These commands follow the [STUM protocol](https://jbellue.github.io/stum1b/);
physical-terminal scrolling still requires validation. On failure, missing
credentials/prompt files, or a search-only Linkup configuration, the existing static
21-line startup is used. A language-screen timeout also uses static text: leaving
the terminal unattended does not repeatedly spend boot-generation API calls.

The complete displayed boot sequence remains in a separate local variable for the
session. It is never added to the chat prompt or factual history. Restarting the
chat service or returning to **SOMMAIRE** discards it. A future paired shutdown may
use that same text; no shutdown generator or hardware-state parser is implemented.

Static UI/fallback text live in `config/terminal_ui.json`. Edit the prompt/examples
or JSON and restart the service to refine the performance. Generated wording is
not newly approved phrase-bank content. Checks ensure display integrity, not humour,
semantic consistency or translation quality; live review is still needed. These
diagnostics describe imaginary machinery, not actual measurements. The standalone
personality harness and idle scenarios are not integrated; no new runtime
dependencies are required.

The default lives in `config/prompts.default.json`. Upstream copies it to
`config/prompts.json` only when the latter does not exist. Updating the repository
therefore preserves an existing installation's active personality. To use the
fork's default on an existing installation, back up `config/prompts.json`, then
copy `config/prompts.default.json` to it and restart the chat service. This replaces
local preset configuration; alternatively add/select the new preset in the admin UI.

To apply only this workshop's chat-personality update while preserving the other
local presets, active selection, logo and UI wording, run from the repository:

```bash
cp -n config/prompts.json config/prompts.before-workshop.json
python3 - <<'PY'
import json
from pathlib import Path
path = Path("config/prompts.json")
local = json.loads(path.read_text(encoding="utf-8"))
defaults = json.loads(Path("config/prompts.default.json").read_text(encoding="utf-8"))
local["presets"]["mainframe"]["system"] = defaults["presets"]["mainframe"]["system"]
path.write_text(json.dumps(local, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
sudo systemctl restart minitel-chatgpt
```

The mainframe preset must exist locally; select it in administration if another
preset is active. Generated boot prompts and static headings update with the pull
and service restart independently of the locally stored chat personality.

## Planned direction

- Develop and play-test the character without requiring a physical terminal.
- Extend the new neutral Mistral factual path with separately reviewed performance, if desired.
- Add coherent startup/recovery/shutdown diagnostics, reviewed situation-dependent
  messages and optional fictional idle scenes beyond the initial generated startup.
- Preserve useful answers, uncertainty, technical details and honest real status.
- Explore narrowly scoped useful tools, such as search or printing, independently
  of the character's imaginary capabilities.
- Keep Python dependencies and resource use modest, adopt an isolated `uv`
  environment, and use Ruff plus proportionate integration/regression checks.

These further steps are plans, not implemented features. Fictional hacking and telemetry never
grant real permissions or establish real access. The Mistral Conversations answer path now separates neutral factual reasoning
from preset presentation; styled factual prose and the independent idle/actor lab
remain future integration work. Explicit Chat Completions rollback retains the
previous single-prompt behavior.

Development takes place on `develop`. Stable changes reach `main` after physical
Minitel validation. Valuable upstream fixes and improvements will be considered
as the fork evolves.

## Startup regression checks

Using an environment with the existing runtime dependencies (`requests`,
`python-dotenv`, `pyserial`; `pyfiglet` is optional):

```bash
python3 -m unittest discover -s tests -v
```

The checks run the conversation loop with a simulated UART and mocked provider
responses, including Videotex/VT100 function keys, language defaults, the SUITE gate,
screen dimensions, variable-length boot scrolling, mode restoration and protocol
acknowledgements, session resets, factual-history isolation, completion reasons,
timeouts and fallback behavior. They make no live API calls and do not replace
live-model review or validation on the physical Minitel.

## Hosted Mistral web search

Normal Mistral answers now use `POST /v1/conversations` with neutral factual
instructions (`config/factual_prompt.txt`), the actual date, locally owned factual
history, `store: false` and basic `web_search`. The configured model is retained.
Mistral decides when useful searches run and handles them remotely; enabling the
tool does not guarantee a search. There is no local search engine, scraper,
routing model or tool framework. `store: false` controls saved conversation
history, not every kind of provider retention.

The terminal keeps at most ten complete factual turns and 60,000 serialized
characters, including ordered real source metadata. Oldest pairs are dropped;
a single oversized pair is displayed but not retained. Each request starts a new
unstored provider conversation with this context. No conversation IDs, raw search
results, boot text, personality prompt, waiting theater or source footers enter
factual memory. Preset knowledge files remain user-supplied factual reference
material; put fictional world-building in the personality prompt, not those files.
Source follow-ups retain the preceding answer's ordered real references; Mistral
may search again because previous tool execution state is not replayed.

Factual prose is passed through unchanged before the terminal's existing ASCII
transliteration and wrapping. Python adds numbered markers at source chunks and a
complete numbered domain footer; exact-URL duplicates share one number. Unicode
domains use their ASCII IDNA spelling. Common technical symbols get readable ASCII
equivalents; unsupported glyphs get visible code points instead of disappearing. Long words,
URLs and code now wrap without tail truncation. The browser's existing preset test
shows titles and exact full URLs, including Unicode paths, as plain text. No new
export surface is added. The preset still supplies the logo, question/waiting
messages and Minitel 722079V / ALCATEL MF-83 boot theater. It no longer styles or
rewrites the neutral Mistral factual answer. Other provider adapters and generated
startup calls retain their existing APIs; startup never receives a search tool.

Installation settings in `.env` (restart the chat service after changes):

```dotenv
MISTRAL_ANSWER_API=conversations
MISTRAL_WEB_SEARCH=1
MISTRAL_REQUEST_TIMEOUT=60
```

`MISTRAL_WEB_SEARCH=0` keeps Conversations but removes the tool. Explicit search
requests then receive an honest unverified notice. `MISTRAL_ANSWER_API=chat_completions`
is an explicit rollback to the existing personality-prompt answer path without
search. There is no automatic endpoint/model fallback or retry. The 60-second local
deadline is provisional: live search latency must be measured before tuning it.

A single background answer worker leaves SOMMAIRE and GUIDE responsive. Reset or
deadline discards that request's local result; it cannot update a new session. If
an abandoned HTTP call is still running, another question gets a short recoverable
busy notice instead of queuing another paid request. This does not cancel provider
work or billing. Completed requests, failed requests and verification notices are
handled separately; only successful user/assistant pairs enter factual history.
Explicit search without observed execution, unfinished/failed execution or missing
traceable references produces an unverified notice, not a fabricated current answer.
The explicit-search check covers common English/French requests; it is not a full
semantic classifier. The model is also instructed to report missing evidence.

Logs record endpoint/model, elapsed time, observed search, reference count, status
and allowlisted token counters. They omit questions, credentials and arbitrary
provider diagnostics. No premium tool, new account or runtime dependency is needed.

### Validation and remaining checks

Offline fixtures are **synthetic contract fixtures**, not captured live responses.
The request/input/output fields were checked against Mistral's official Python SDK
and [web-search documentation](https://docs.mistral.ai/studio/agents/agent-tools/websearch).
The parser ignores assistant activity before the last tool execution and requires
one final assistant entry afterward (or one ordinary assistant entry). Ambiguous
multiple final entries fail recoverably; verify this rule with a real response
before relaxing it. Failed search `info` metadata varies by provider: recognized
error/status fields and missing completion timestamps are treated conservatively.

Mocked API and simulated UART tests cover neutral payloads, local source follow-ups,
fiction isolation, ordinary/off paths, mixed chunks, duplicate sources, malformed
responses, preserved code/URLs, recoverable errors, deadlines, reset while blocked,
late replies and prevention of overlapping requests. Existing startup tests remain
passing. All 43 tests pass. Ruff passes on new Python code and tests; error checks (`--select F`)
pass on the modified existing services, whose legacy style is left intact. These
checks do not establish live tool/model compatibility or hardware usability.

There is no API key in the implementation workspace, so no live calls or cost
measurement were made. Before the paid Pi probe, check current basic-tool pricing,
account/model availability and the existing provider spending cap in Mistral's
console. One HTTP request may involve multiple billed model/search steps. Run from
the repository with its normal Python dependencies:

```bash
python3 scripts/smoke_mistral_search.py --capture-dir /tmp/minitel-search-check
```

This makes at most three requests: an explicit current question with official
sources, a source follow-up, and an ordinary explanation. It stops on an error or
unverified result, with no retry. It prints observed latency/search/reference/usage
counts and full sources. The optional capture strips credentials, IDs, arguments
and private reasoning; review it before sharing. Its HTTP timeout is an inactivity
limit; the terminal independently enforces its total local deadline. Real fixture
review, live search/model/cost validation and Julian's physical citation/pagination,
waiting/reset/recovery check remain outstanding. Development takes place on `develop`; keep `main` behind physical validation.
