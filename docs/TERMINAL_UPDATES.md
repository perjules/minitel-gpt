# Mainframe maintenance from the Minitel

Implementation packages (local milestone commits, then publish to develop):

1. Share the existing admin update comparison/privileged queue call. Add a
   conservative terminal preflight and offline Git/queue tests. Keep the runtime
   transaction, sudo permissions, branch selection and rollback unchanged.
2. Add exact local commands, two isolated optional text-generation calls, explicit
   keyboard confirmation, paced automatic playback and a one-use restart display
   hint. Test the actual UART command route, cancellation, failure, status loss,
   restart and separation from chat/memory.
3. Document operation and physical acceptance. Run locked dependency sync, the
   full offline pytest suite, Ruff, requirements-export and whitespace checks.
   Publish develop after these pass; main still requires physical validation.

## Use

After selecting a profile, enter **UPDATE**, **MISE A JOUR** or **MAJ**, then
ENVOI. Commands are case-insensitive; MISE À JOUR is accepted too. They work
locally before ordinary chat, welcome, calculator and integration routing.

The terminal checks the installed update branch and retrieves up to twenty pending
change subjects. A single call to the configured general text provider translates
these into an absurd France Télécom maintenance dossier in the Minitel's voice.
Git terminology stays off the screen. If no revisions are available, it stops
without asking to update. Failed verification is reported as unavailable, never
as “up to date”. No private profile or integration data enters this call.

Python then asks **Mettre a jour le mainframe ? OUI/NON**. Only OUI or YES followed
by ENVOI authorizes the operation. NON, NO, ANNULER, CANCEL or inactivity cancels;
other text asks again. SOMMAIRE/GUIDE during the check, announcement or preparation
leaves without queuing an update. An abandoned worker can only finish fetching or
generating text; it cannot launch anything later.

After confirmation, a second single model call prepares a longer mainframe repair
sequence: vacuum tubes, transistor polish, core alignment, punched-card fine-tuning,
attention layers and matrix multiplication. These are the Alcatel mainframe's
problems: the Matra Minitel terminal remains impeccable. No owner preset or boot
prompt is rewritten. The dedicated prompt is `config/update_prompt.txt`.

Once that text is ready, the installation is rechecked and the same fixed
`sudo -n /usr/local/sbin/minitel-runtime update` command used by the admin console
queues the deployment. The show begins as soon as the helper acknowledges it;
the helper's existing three-second launch delay is retained. No installation or
sudoers change is required for this feature. Install it through the existing admin
updater once, then terminal updates become available.

The procedure scrolls automatically at roughly one line every 2.5 seconds, without
SUITE pagination. If it runs out, numbered local inspections extend it without
replaying the repair sequence or making more model calls. During this foreground
screen only, deployment.json is read about
once per second. Real queued/preparing/activating phases are displayed separately
from the fictional repair text. There is no fabricated percentage or ETA.

The updater eventually restarts the terminal service. A one-use hint lets that
new process begin the usual boot sequence without first requiring SOMMAIRE. Boot
still accepts SUITE or continues after its usual thirty-second wait, then asks for
a profile. An ordinary startup still waits for SOMMAIRE. No user identity, model
text or deployment command is stored in the hint, and it cannot replay an update.

SOMMAIRE or GUIDE during an already queued update leaves the display; **the
deployment continues** independently. Disconnecting the terminal also does not
cancel or replay it. A failure, ten seconds without readable fresh deployment
status, or thirty minutes without completion ends the theatre with an explicit
unconfirmed result and a pointer to Maintenance. A reported ready status is
checked for its commit; unchanged code is reported as already current. On the
physical Pi, a successful restart normally replaces this process before it sees
the final ready status.

## Contracts and limits

- The LLM writes inert display text only. It receives bounded change subjects,
  not branch names, hashes, usernames, histories, memories or account credentials.
  Requests use the configured general Mistral or Claude model. Linkup is a search
  provider and is never called for theatre.
- The announcement keeps the boot generator's eight-second/700-token budget.
  The longer procedure gets twenty seconds and 1800 tokens for 40–60 lines, with
  thirty seconds of interruptible local waiting. Both require a complete response.
  Missing keys, API errors, truncated output, terminal
  controls, Markdown, excessive text, questions or Git jargon use static themed
  text. No retries or paid calls during playback.
- The terminal journal records `update_theatre phase=announcement|procedure`
  with `status=generated`, line count and duration, or `status=fallback` and a
  fixed reason such as `timeout`, `incomplete_response` or `vocabulary`. No model
  text, provider body or credentials are logged. Inspect recent attempts with:

  ```bash
  sudo journalctl --namespace=minitel-gpt -u minitel-chatgpt -n 200 --no-pager | grep -E 'update_theatre|Update theatre'
  ```

- The check and generation share the existing bounded worker/keyboard wait
  mechanism. Workers never write to the UART or invoke sudo. Only the terminal
  thread, after explicit confirmation, can queue the fixed update operation.
- A busy deployment, missing/malformed runtime installation, wrong branch,
  checkout differing from the deployed version, tracked edits, failed fetch or
  divergent history prevents a terminal update. Fetch and transport children have
  a sixty-second limit; local checks have five-second limits. The root helper
  retains its own final checks, staging, activation and restoration transaction.
- As with the admin updater, the helper fetches the installed branch again at
  application time. New remote changes arriving after the preview may be included.
  This feature does not switch branches or introduce a pinned-release contract.
- Anyone deliberately using the physical terminal can confirm this maintenance
  operation, matching the existing local power controls. Username selection is
  still not authentication. No new password gate or remote route is introduced.
- Update commands, confirmations and theatre never enter conversational history,
  saved memories or automatic memory learning. Existing archives are untouched.
- The private, atomic `terminal-update.json` hint sits at the application data root
  (`MINITEL_MEMORY_DIR`, normally `~/.local/share/3615-ia`). It is distinct from
  per-user memory. Another process on the same kernel boot can consume it once,
  within thirty minutes, with fresh deployment status. UART reconnects in the same
  process do not consume it. Failed launch or a displayed final result clears it.
  Failed storage prevents launch. The small adjacent lock file is reusable.
- No dependency, permanent service, background monitor, schema migration or
  deployment architecture change is added. Admin CSRF/authentication and the
  hidden mainframe power commands retain their existing behavior.

## Physical acceptance (not established by offline tests)

1. Install through Maintenance; enter UPDATE on the Minitel with no newer remote
   code. Confirm a current result and no update prompt. Try MAJ/MISE A JOUR too.
2. With a real pending update, inspect the France Télécom announcement. Answer
   NON; verify the deployed version does not change. Test SOMMAIRE while checking
   and while preparing the second sequence; neither should start maintenance.
3. Confirm OUI. Check the paced scrolling, long-running local extension and
   correct Alcatel/Matra identities. Confirm the ordinary boot starts automatically
   after the service restart and the deployed version is the expected one.
4. Check English dialogue, model unavailability/static fallback, serial disconnect
   during preparation/playback and a controlled deployment preparation failure.
   Verify restoration and the truthful error/status screen independently of the
   comic narration. Inspect the existing deployment journal/admin status.
5. Confirm normal power-on still waits for SOMMAIRE, existing local power commands
   still require confirmation, ordinary conversations retain their context, and
   no maintenance theatre appears in personal memory.
