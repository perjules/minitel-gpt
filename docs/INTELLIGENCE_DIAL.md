# Intelligence dial (software phase)

The Philips wheel will eventually select six machine-wide positions. For now,
use `:INTELLIGENCE 1`, `:INTELLIGENCE 3`, `:INTELLIGENCE 5`, `:INTELLIGENCE 7`,
`:INTELLIGENCE 9` or `:INTELLIGENCE 11` in ordinary conversation.
`:INTELLIGENCE` reports the current position. `:I` is a shorthand: use `:I 11`
to select a level or `:I` to show the current level. Submitting `:` first also arms
the next entry as a command, so `INTELLIGENCE 11` then works without another colon. Commands are local, case-insensitive
and tolerate surrounding/repeated whitespace. They never enter the model,
conversation transcript, memory, printer history or prompt recall. Invalid input
lists the six accepted positions. No conversation reset occurs.

The progression deliberately jumps from 9 to 11: 11 adds randomness, not ability.
Each level appends its own `config/intelligence_<level>_prompt.txt` to ordinary
conversation instructions: 1 gives a direct short answer; 3 adds a simple
explanation; 5 balances synthesis and context; 7 examines assumptions and trade-offs;
9 applies expert judgment and second-order consequences. Higher levels improve
selection and depth rather than automatically increasing length. None claims new
knowledge, tool access or permission. Level 11 uses a stronger deadpan, opinionated
and absurd style without a fixed example to copy. It encourages deadpan bureaucratic
absurdity, unexpected opinions and 80–140-word replies, while explicitly retaining
factual accuracy, privacy and all operational safeguards. All levels retain the shared factual and personality rules. Both position and model profile are captured before the worker
starts, so switching the dial cannot change an in-flight request's personality.
The browser conversation preview follows the same rule. School and other
operational stages never receive this suffix. Existing saved temperatures and
reasoning settings are preserved; the owner's confirmed test temperature for 11
is 0.9. Changing personality does not require restoring profiles.
The machine starts at 5 when no saved selection exists. Selection survives
SOMMAIRE, logout, user changes and restart. Do not intercept the reserved command
inside private/literal editors, mail, calculator, school or confirmation workflows;
return to ordinary conversation first.

## Defaults and provider capabilities

| Level | Character | Model | Reasoning effort | Temperature |
|---|---|---|---|---|
| 1 | Économique | `mistral-small-2603` | `none` | 0.2 |
| 3 | Simple | `mistral-small-2603` | `high` | 0.2 |
| 5 | Normal | `mistral-medium-3-5` | `none` | 0.2 |
| 7 | Avancé | `mistral-medium-3-5` | `high` | 0.2 |
| 9 | Maximum | `mistral-large-4-0` | `high` | 0.2 |
| 11 | Débridé | `mistral-large-4-0` | `high` | 1.1 |

Reviewed against Mistral's official documentation on 12 November 2026:

- [Reasoning](https://docs.mistral.ai/inference/conversations/reasoning): Small 4
  and Medium 3.5 accept `none/high`; Large 4 accepts `low/medium/high`, not `none`.
  Conversations accepts reasoning in `completion_args`. Explicit model versions
  avoid silently changing reasoning capabilities behind a `latest` alias.
- [Chat API](https://docs.mistral.ai/api/endpoint/chat) recommends 0–0.7, which is
  guidance rather than a claim that 0.7 is the API maximum.
- [Sampling](https://docs.mistral.ai/inference/sampling) and
  [temperature cookbook](https://docs.mistral.ai/resources/cookbooks/concept-deep-dive-sampling-temperature)
  describe increased diversity and degraded coherence at high temperatures.
  The console deliberately bounds this experiment to finite values in 0–1.5.

Temperature 1.1 is an experimental default, not a live-account certification.
No paid inference was performed during implementation. Account availability,
latency, pricing and the visible effect of 1.1 require Pi testing. The reasoning
capability allowlist is shared with Gmail settings, not inferred from API-wide
enums. Unknown/custom model IDs are accepted syntactically with blank effort
(provider default); explicit reasoning is rejected until supported by the allowlist.
The existing authenticated model catalogue can check account availability on demand.
No automatic catalogue network requests occur on boot or save. `top_p` is untouched.

## Administration and persistence

**Configuration IA → Intelligence** provides six responsive profile cards, with
model ID, reasoning effort and numeric temperature. Save validates all six before
writing; a failure preserves the previous configuration and displays the error.
Restore defaults resets profiles only, leaving the selected position intact.
Level 11 is independently editable. No API keys belong in this schema.

The existing `config_store` provides bounded reads, interprocess/thread locks,
0600 files and atomic fsync/replace writes:

- `config/intelligence-profiles.json`: `{"version":1,"profiles":{"1":{...},...}}`.
  Each profile contains exactly `model`, `reasoning_effort`, `temperature`.
- `config/intelligence-state.json`: `{"version":1,"level":5}`.

Both are local Git-ignored files. Absence selects defaults without overwriting
existing `.env` or service settings. No database migration or new dependency is
needed. Admin changes are read on subsequent requests without restarting services.
Malformed files are rejected; the console shows an error. Runtime profile/storage
failure logs a fixed warning and uses default profile 5. Corrupt/unreadable state
shows `I:??`; a valid command can replace corrupt state. Restore defaults repairs
profile JSON; symlinks/storage errors require operator repair.

## Routing and request lifetime

`start_answer` resolves a frozen `Profile` before starting its worker.
`call_answer` supplies that snapshot only to ordinary Mistral Conversations
answers, after existing Gmail, school, memory, onboarding, Christmas list,
calculator and home-automation routing. Direct synchronous calls snapshot at entry.
The browser's ordinary conversation preview also uses the shared selection.

Operational parsing, authorization, validation, confirmation and final tool
results keep their existing models/settings. PRONOTE defensively ignores a dial
profile even if one is accidentally supplied. Home automation and Gmail never
receive one. There is no additional creative rewrite of a tool result; the
existing architecture does not separate a safe final prose stage for those tools.
Hosted read-only web search remains available for ordinary answers. No local
function tools are supplied to that public request.

Legacy `chat_completions`, other providers, explicit `STREAM` experiments,
Rose, admin prompt generation and background memory consolidation retain their
existing adapters. The indicator reports the physical selection even in those
views; it does not imply those adapters use the dial. The default Conversations
adapter is required for the intended level 9/11 comparison.

Reasoning profiles receive an 8192-token total completion budget rather than
700, so hidden thinking does not routinely consume the entire answer allowance.
Thinking chunks are excluded from displayed prose. Existing concise-answer rules
and request timeout remain; difficult requests can still exhaust the budget or
time out. No artificial delays are added.

A structured HTTP 400/404/422 rejection specifically identifying `model`,
`reasoning_effort` or `temperature` permits **one** retry using the configured
base `MISTRAL_MODEL`, provider-default effort, temperature 0.2 and 700 tokens.
The retry uses the remaining request deadline. The terminal displays
`INTELLIGENCE : mode de secours` with a successful fallback answer; the selected
position/profiles remain unchanged. Ambiguous errors, timeouts, authentication,
rate limits and server failures are not retried. Operational calls are never
replayed by this mechanism. Logs contain fixed rejection warnings and the actual
effective model ID, never provider prose or secrets.

## Videotex behavior

Standard geometry remains 40 × 24; C40 is still untouched. Row 21, immediately
above the three input rows, reserves C36–C39 for `I:01` … `I:11`, with C35 as a
separator. The message area is 34 cells; long notices use an ellipsis. The update
confirmation was shortened to keep its complete OUI/NON choice visible.

A command changes only status chrome, with acknowledgements such as
`INTELLIGENCE : 07 - AVANCÉ` or `INTELLIGENCE : 11 - ÇA CHAUFFE !`. Feedback remains
until replaced by the next ordinary status. Content, loaded pages and input width
are unchanged. Cached row-span writes avoid full clears. Every ordinary frame
restores the indicator after paging, menus, QR return or reconnection. Private
pinned notices (notably Rose's stop word) retain their full status row. No UART
write occurs on a worker/adapter thread. The command does not cancel an in-flight
request; changes submitted after a locally abandoned request affect only later
requests. The existing waiting screen does not parse typed commands while waiting.

## Original hardware integration plan (historical)

The hardware/calibration implementation belongs in
[mainframe-maintenance](https://github.com/perjules/mainframe-maintenance).
No ADC, GPIO, sampling, calibration or transport code is implemented here.

The integration seam is an in-process instance:

```python
from services.intelligence import controller

controller.set_level(7)   # int, strictly one of 1, 3, 5, 7, 9, 11
profile = controller.resolve()  # frozen model/effort/temperature snapshot
```

Both the software command and a future hardware adapter use **this same**
`set_level`. The adapter validates/debounces/quantizes its readings in its own
repository, holds a reference to this controller, and submits stable positions.
Invalid positions raise `config_store.InvalidConfig`; failed persistence raises
`StorageFailure`/`OSError` and must be surfaced, not reported as accepted.
A startup hardware reading calls `set_level` before the first request to override
saved software selection. Hardware authority/arbitration becomes a policy of that
future adapter; this phase does not pretend hardware is attached.

Never render from the adapter thread. After draining a future hardware event on
the existing UART owner's safe input loop, refresh with `view.status(view.notice)`
and `view.input()` to retain the draft and caret. During an active request, defer
that UI feedback to a safe boundary; the immutable request retains its snapshot.
The hardware integration will need that small lifecycle/polling hook; it will not
need changes to profile resolution or model routing. No daemon, endpoint or IPC
contract is preselected.

## Offline and physical acceptance

```bash
uv sync --locked --all-extras
uv run --locked --all-extras pytest
uv run --locked --all-extras ruff check .
uv run --locked --all-extras python scripts/export_requirements.py --check
```

`tests/test_intelligence.py` covers profiles, malformed values, persistence,
commands, actual API payloads, conservative fallback, in-flight snapshots, routing,
authenticated forms and a simulated UART's visible rows. Existing display,
request-lifetime, services and deployment regressions run with the full suite.
No hardware, API key or live account is required.

On the Pi, use **Maintenance → branch preview/switch** to deploy
`feature/intelligence-dial`. For a deliberate manual deployment from a clean
tracked checkout (see [RUNTIME.md](RUNTIME.md) for mismatch recovery):

```bash
cd /home/minitel/minitel-gpt
git status --short --branch
git fetch origin
git switch feature/intelligence-dial
git pull --ff-only origin feature/intelligence-dial
sudo bash scripts/install_runtime.sh --branch feature/intelligence-dial
sudo systemctl is-active minitel-chatgpt admin-ui
```

Stop and preserve tracked modifications if the first status shows any; never
force-reset. A first `git switch` normally tracks the fetched remote branch.

1. In Configuration IA, select Mistral and the Conversations adapter; check the
   configured API key and six default profiles. Save. Return to ordinary chat.
2. Type `:INTELLIGENCE`: expect 05 initially, or the previously saved position.
3. Ask a question producing several pages. Type `:INTELLIGENCE 7`: the answer
   stays visible, status acknowledges AVANCÉ and the header shows `I:07`.
   SUITE/RETOUR should still page the same answer. Type `:INTELLIGENCE 10` and
   confirm all six accepted values appear with no API request.
4. Select 9, ask `Invente trois slogans absurdes pour une administration qui
   réglemente les nuages.` Repeat several times. Select 11, confirm `I:11` and
   `ÇA CHAUFFE !`, then repeat the same question. Expect more variety over several
   samples, not guaranteed differences from a single random response.
5. At 11, read a Gmail message, consult an authorized child's school data and
   inspect Airzone state. Any write still follows its existing validation and
   confirmations. Confirm actual thermostat/email behavior only deliberately;
   offline tests do not perform these actions.
6. Enter a partial prompt, page back/forward, then complete it. Enter/exit other
   views and SOMMAIRE; verify clean input borders and restored selection.
7. Restart the terminal service. Confirm the selected level survived and editing
   profile 11 did not change profile 9 or any Gmail overrides. Test fresh prompts
   after changes; an already-started completion must keep its original profile.
8. Check the journal for the actual chosen model and any fixed fallback warning.
   A fallback notice means the experimental configuration was not used: verify
   model access/effort in the console rather than judging randomness from it.

Physical CRT appearance, model availability, output quality and latency remain
manual acceptance items. Do not merge to develop/main on offline evidence alone.

## Rotating waiting tasks

Ordinary terminal submissions select a curated task from
`services/waiting_tasks.py`, in shuffled bags without immediate repeats.
Examples include rebobinating the reasoning, aligning core memory and thawing a
standby Cray. While a request remains pending, the UART owner replaces the task
every two seconds using only the cached 34-cell status region; I:xx stays visible.
No busy redraw, input clearing, full-screen refresh, additional API call or sleep
is used for rotation. A result already queued takes priority over the next task.
Cancellation, errors and deadlines end the sequence with the existing request
lifecycle. These are explicitly display theatre, never actual provider telemetry.
French task names are used on both terminal language settings, like machine
operation labels. Fixed service commands and calculator-specific waiting text
keep their own messages. The original per-personality loading setting remains
available for those paths. Tests use a virtual clock and simulated UART, not real
waiting or live provider calls. Physical acceptance: submit a slow ordinary prompt,
verify the status changes about every two seconds, cancel with SOMMAIRE, then try
a fast prompt and confirm there is no minimum display delay or leftover task.

## Editing individual prompts

Each profile card has **Voir / modifier le prompt**, opening its own text editor.
Save applies only that level's prompt to subsequent ordinary requests; restore
returns only that level to the shipped default. Login and CSRF protection apply.
Blank, over-16000-character and control-character input is rejected; a revision
check prevents stale pages overwriting newer prompt edits. Text is HTML-escaped.
The editor changes the level-specific suffix, leaving the shared personality and
operational rules in place. Existing profile/model settings and active position
are independent. Overrides live in Git-ignored `config/intelligence-prompts.json`,
using the same atomic locked storage as other configuration. Tracked prompt files
remain the defaults, so deployments preserve edits without dirtying the checkout.

## Physical wheel adapter (experimental, 22 November 2026)

The optional in-process `services/intelligence_hardware.py` reader polls ADS1115
A0 on I²C bus 1 at address `0x48`. The wheel has measured endpoints of
approximately 0–3.298 V. Equal-voltage six-way quantization is the physically tested v1 mapping.
A 30 mV hysteresis margin prevents switching near band boundaries, and a candidate must remain stable for 0.4 s before
changing the controller; active requests retain their immutable profile.

The terminal process starts and stops the polling thread. It requires
`i2c-tools` and access to `/dev/i2c-1` (normally membership of the `i2c`
group for the service user). No extra Python library, daemon, API or UART
writer is introduced. Missing I²C tools/device leave keyboard commands usable;
three consecutive polling failures mark hardware unavailable. Transient errors
do not immediately transfer authority.

In **Configuration IA → Intelligence**, the independent **Activer la molette
physique** checkbox defaults to on and is stored in the Git-ignored
`config/intelligence-wheel.json`. When enabled and the ADC is responding,
the wheel exclusively selects levels: `:I #` and `:INTELLIGENCE #` return
`INTELLIGENCE : utilisez la molette`. Bare `:I` still reports the current
level. When disabled or unavailable, keyboard selection is active. Disabling
the wheel preserves the last selected level; re-enabling selects the physical
position after stabilization. The adapter does not edit model profiles.

**Important limitation:** detecting a responding ADS1115 does not prove the
potentiometer's wiper is connected. A floating A0 can yield plausible values.
There is no reliable software-only standard disconnect test for this circuit;
wiper validity remains TODO until a bias/resistor or equivalent hardware
strategy is selected. The admin checkbox is the manual override.

Acceptance on the Pi: use the standalone
`mainframe-maintenance/hardware/intelligence-dial/probe.py` to characterize
the wheel; then verify six stable positions, the admin opt-out, both command
paths, startup selection, missing-device fallback, user changes and in-flight
profile immutability. Hardware behavior has not yet been physically accepted.
