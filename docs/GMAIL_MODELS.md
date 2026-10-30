# Gmail model selection

Configuration IA → **Gmail — modèles par tâche (Mistral)** uses the existing
Mistral key, independently of the ordinary chat provider. Model precedence is
drafting task → Gmail service → general Mistral model. Blank overrides inherit;
no existing installation is automatically switched to a different model.

Recommended first trial: keep the general model on `mistral-small-latest`, leave
Gmail operations inherited, choose `mistral-large-4-0` for drafting and `medium`
effort. Save with **Enregistrer et appliquer**. This restarts the terminal using
the existing protected mechanism; restart failures are reported separately from
successful persistence. The admin test console reads saved settings on the next
model call. The model list is fetched on demand and shows labels plus IDs.

New messages, replies and AI revisions use the drafting profile. Routing,
recipient matching and summaries use the Gmail operations profile. Memory
processing remains on the general profile even though it shares an HTTP helper.
Literal paragraph edits do not invoke a model. Natural-language composition
retains the user's original wording instead of giving the writer only the
router's paraphrase; recipient selection and validation remain in Python.

## Settings and limits

```dotenv
MISTRAL_GMAIL_MODEL=
MISTRAL_GMAIL_DRAFT_MODEL=mistral-large-4-0
MISTRAL_GMAIL_DRAFT_REASONING_EFFORT=medium
```

Clear both drafting fields to return to the inherited model and provider effort
default. An empty effort omits the API parameter; it does not explicitly disable
reasoning. Current recognized effort options:

- Small 4 (`mistral-small-2603` / current `mistral-small-latest`): `none`, `high`.
- Medium 3.5 (`mistral-medium-3-5`, `mistral-medium-2604`, current
  `mistral-medium-latest`): `none`, `high`.
- Large 4 (`mistral-large-4`, `mistral-large-4-0`): `low`, `medium`, `high`.
- Other IDs: provider default only until their options are explicitly supported.

Aliases may change upstream. Prefer versioned IDs for repeatable comparisons.
Unsupported combinations are rejected before saving or making a request. A
rejected/unavailable model never silently falls back to another model.

Temperature remains 0.2. Existing requests retain the 3,000-token / 25-second
limits. Explicit reasoning drafts (and Large 4 drafts with provider-default
effort) allow up to 8,192 generated tokens / 45 seconds, still capped by the
remaining overall operation deadline (normally 60 seconds). These are ceilings,
not required usage. Reasoning consumes additional time/tokens; truncated answers
are rejected rather than used as partial drafts. The final body remains capped
at 12,000 characters.

Only final text chunks enter the draft/JSON parser. Thinking chunks are neither
displayed nor logged as email or conversation text. Call metadata logs contain
service/task, model ID, effort, elapsed time, status and numeric token totals;
they contain no API key, prompt, body, recipient or thinking trace. Replies and
revisions retain their existing bounded context. Draft approval, per-user access,
fixed recipients, literal ENVOYER and review-ticket invalidation are unchanged.

## Offline verification and manual quality comparison

Automated checks use mocked APIs. They test settings inheritance, actual call
routing, memory isolation, instruction preservation, reasoning response parsing,
bounded requests, private logging, atomic form validation and existing send guards.
These checks cannot establish whether one model writes better French emails.

For a separate live comparison, the optional script below generates synthetic
drafts only. It never reads Gmail, resolves real contacts or sends messages.
Without `--live` it prints help and makes no requests. An explicit `--live` run
uses the Pi's existing Mistral key and incurs normal API charges. Start with one
case (three requests):

```bash
cd ~/minitel-gpt
.venv/bin/python scripts/compare_gmail_models.py --live --case conditional > /tmp/gmail-model-comparison.json
```

Omit `--case conditional` to compare all ten cases (30 sequential requests).
The three profiles are Small 4 without reasoning, Small 4 with high reasoning,
and Large 4 with medium reasoning. Each result includes the prompt, expected
intent, model/effort, elapsed seconds and draft or a public error code.

Judge intent preservation, invented facts/commitments, tone, editing required
and waiting time. Then compare Medium 3.5 manually through Configuration IA and
the existing Gmail test console if needed. During actual mailbox testing, leave
the draft unapproved unless you deliberately intend to send it. No live model
quality result is claimed by the offline suite.

Provider references, checked 30 October 2026:

- https://docs.mistral.ai/studio/conversations/reasoning
- https://docs.mistral.ai/inference/pricing
- https://docs.mistral.ai/inference/model-lifecycle

Offline validation on 30 October 2026: all 690 tests passed, including execution
of the rendered model-control JavaScript in Node with DOM primitives. Ruff,
requirements-export freshness and whitespace checks passed. Browser binaries
were unavailable, so phone/desktop visual inspection remains a Pi acceptance
check. No live Mistral/Google calls, email sends or Pi deployment were performed.
