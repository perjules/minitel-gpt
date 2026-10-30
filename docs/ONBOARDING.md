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
capabilities truthfully. Explicitly bridge into “faisons connaissance”, then lead
with a broad personal question about who the user is, rather than asking them to
choose a subject or repeat their name. Each continuing reply responds to their
answer and ends with one concrete follow-up question; inviting them to elaborate
on the same subject is welcome. A genuine finish, pause or handoff need not ask
another question. Follow the user's age/life stage and interests rather
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

## Operation

After boot and name/language selection, a new named profile receives a generated
introduction before the first answer. On SOMMAIRE/LOGOUT or a later connection,
an unfinished welcome resumes from its checklist/history without replaying the
mainframe boot or grand first introduction. Completed/declined welcomes do not
restart automatically. MinitelGPT (blank name) is excluded.

- `PASSER` / `SKIP` skips the current topic; natural refusals are also understood.
- `PLUS TARD` / `LATER` pauses, then returns to ordinary conversation.
- `FIN ACCUEIL` / `STOP INTRO` ends the optional phase.
- `FAISONS CONNAISSANCE` / `GET TO KNOW ME` opts an existing named profile in.
- `REPRENDRE ACCUEIL` / `RESUME INTRO` resumes a paused/declined phase.
- CALC, MAIL, PRONOTE, printing and explicit memory commands keep their own routes.

The optional directive uses the existing **general Mistral model/key**, with no
web/tool access in the welcome call. Personality text is passed through unchanged;
no owner preset/default text is edited. The structured output carries proposed
facts with catalogue topics and exact current-answer quotes. Python drops any
proposal belonging to a skipped topic, applies the remaining validated batch
atomically, and marks its origin `onboarding`. Sources retain the actual preceding
question so a short reply such as `10` remains understandable. Invalid output or
an API error pauses the optional phase and ordinary chat remains available.

These exchanges use a separate `onboarding` archive channel and do not enter the
generic background memory queue. A request not to memorize the current answer is
treated conservatively as a skip; that answer is not sent to the welcome model.
Normal visible transcript archives still follow the documented retention policy.
Private integration data is not used for the introduction; school-related facts
voluntarily given during this conversation are personal assertions, not PRONOTE
records or a grant of access.

The user is told they may skip/end; no exact birth date, contact information,
precise address, school identity or identifying details of friends are sought.
The natural-language policy is model-dependent: real wording, warmth and age
adaptation need live acceptance testing, not just schema/unit tests.

## Admin preview and storage

In the existing **Tester le Minitel → Conversation** page, enter a named profile
and press **Faire connaissance / reprendre l’accueil**. Continue with the normal
message field. This uses real model calls and writes that profile's memory; use a
synthetic profile for experiments. New profiles also follow this flow when their
first test message is sent. Completed welcomes are not silently reset by the button.

**Maintenance → Mémoire des utilisateurs** shows welcome status, exchange count
and covered/skipped topic IDs. The existing password gate protects saved facts,
source quotes and their question context. The export includes progress metadata,
not raw welcome history. No new top-level section or memory editor is added.

`user_onboarding` is an additive table in the existing users.sqlite3. Its version
is 1, independent of the identity/grant and memory schemas. Each profile stores
status/revision, a small topic map, current topic and at most 16 recent user/assistant
messages. Completed/declined phases clear that temporary history. Explicit memory
edits invalidate in-flight writes and clear older welcome history before reuse.
No data deletion/reset, manual profile import, new package or installer rerun is
required. The pre-existing JSON-to-SQLite memory upgrade still applies automatically
if that earlier release has not yet been installed; this feature does not bypass it.

Set `MINITEL_ONBOARDING=0` in .env and restart both app services to suspend the
feature globally without removing progress or memories. User authentication and
passwords remain deferred. Upgrading/rolling back this additive feature does not
convert or erase the underlying memory data.

## Acceptance

Offline tests use structured fake model output and real temporary SQLite files.
The terminal integration test runs the real UART decoder and conversation loop:
introduction -> short age answer -> SOMMAIRE/resume -> finish -> normal chat, with
one boot and no welcome turns entering the ordinary answer history. Other cases
cover ownership, opt-in, refusals, short-answer provenance, dialogue continuation,
API failure, malformed output, explicit forget and late/session-close writes.

On the Pi, create a fresh test username (or opt in an existing one), check the
introduction and adaptation to a 10-year-old/student/adult, decline a subject,
expand a hobby, pause/resume and inspect the resulting memories. Test a service
command and RETIENS/OUBLIE mid-conversation. Ordinary users/data are retained;
there is no need to clear existing profiles just to try the feature.

Offline validation (30 October 2026): locked all-extras sync; 775 tests and 400
subtests passed; Ruff, requirements-export and whitespace checks passed. The
assembled HTTP prompt is tested to preserve character only for welcome calls;
mail/factual calls retain their existing rules. No live model/Google calls,
messages sent, Pi deployment or physical/visual phone validation were performed.
