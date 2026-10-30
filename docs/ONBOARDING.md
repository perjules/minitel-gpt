# First conversation / Faire connaissance

Implementation sequence:

1. Add durable per-profile welcome progress and a bounded Mistral dialogue engine.
   Only newly created named identities are enrolled automatically; existing/default
   profiles keep their data. Existing named profiles may opt in explicitly.
2. Connect the first greeting and replies to the terminal's existing interruptible
   request/display loop, and add an opt-in preview in the admin conversation test.
   Tools and explicit memory commands retain their existing routes.
3. Verify child/adult prompt contracts, topic refusals, pause/resume, grounded short
   answers, memory isolation, stale/reset requests, API failure and terminal/admin
   integration. Document operation; run locked dependency sync, pytest, Ruff,
   requirements-export and whitespace checks before publishing develop.

Contract: one optional question at a time. Use the selected personality unchanged;
introduce its vintage mainframe resources enthusiastically while describing real
capabilities truthfully. Follow the user's age/life stage and interests rather
than a fixed questionnaire. Suggested topics: age or optional birthday, family,
school/work/daily life, friends, interests, preferences and desired help. No demand
for full names, contact details, precise location, school identity or exact birth
 date. Declined topics remain skipped across sessions. User-led digressions are
welcome; the model keeps a topic checklist and gently wraps up once useful context
is known. The conversation can finish without every topic being answered.

The general Mistral model produces a structured reply, progress and up to three
short evidenced facts/preferences in one call. Python validates IDs, topic names,
quotes and ownership before an atomic state/memory write. The assistant's own
fiction is never personal evidence. Short replies require the actual preceding
question, preserved with source metadata. These turns do not enter generic memory
extraction. Explicit edits/forget invalidate late writes and remove old welcome
history from subsequent prompts. No permissions or integration accounts are granted.

State is a small additive `user_onboarding` table in users.sqlite3; the existing
identity and memory schema versions remain unchanged. It is created automatically,
without importing/deleting profiles or resetting memories. Language, names and
memory namespaces stay under the existing profile system. Optional user passwords
are still deferred. No new dependency, service or polling loop is introduced.
