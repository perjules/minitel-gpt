# Profiles and private-service access

Names select profiles; they do not authenticate the operator. Anyone knowing a
name can select that profile until passwords are implemented. Administration
continues to require ADMIN_PASSWORD and CSRF tokens.

The [journal intime](JOURNAL_INTIME.md) has its own password-encrypted document
and independent unlock, available to named profiles only. It creates no file
until that user first sets up the feature. Neither selecting a profile nor the
admin password grants decryption access. The confirmed whole-profile deletion
also removes its encrypted journal; independent backups remain separate.

Blank input selects the reserved MinitelGPT profile. An unfamiliar name creates a
profile with no Gmail or PRONOTE access. Existing name normalization and memory
namespaces are retained. Display-name changes retain stable user and memory IDs.
Aliases must be unique; a disabled identity does not become a new identity.

The explicit name **MinitelGPT** (case-insensitive, normalized) also selects the
reserved profile, unless that alias already belongs to an older duplicate. Other
profiles cannot newly claim this name or alias. Admin selectors keep the default
selection blank and compare resolved identities, rather than display-name-derived
folder paths; submitting the test form or selecting another alias of the same
user does not create a profile or reset its conversation.

Older versions could create a separate named profile by submitting the default
display label from an admin test form. Such a duplicate is not merged, deleted or
silently redirected by this fix: its alias, memory path, grants and encrypted
journal remain intact. In **Utilisateurs et accès**, rename that non-reserved
profile and remove its `MinitelGPT` alias to distinguish it. The warning on its
card identifies this case. Afterwards, blank input and the explicit default name
both select #1. Inspect the duplicate's data and account assignments before
choosing whether to delete it.

Saved memory belongs only to the selected profile. There is no global/shared
memory or cross-profile memory command. The reserved MinitelGPT profile keeps its
legacy `shared/` directory to preserve existing data; other profiles cannot target
it from their conversations. Selecting a profile is still not authentication.

## Data

`users.sqlite3` lives under MINITEL_MEMORY_DIR, or
`/home/minitel/.local/share/3615-ia`, outside Git. The database is private (0600),
uses SQLite foreign keys and short transactions, and schema version 2. It contains
profiles, aliases, Gmail metadata, one-mailbox assignments, read/send permissions
and PRONOTE child grants. Memory tables now hold per-user saved content, embeddings
and bounded pending user statements; see [MEMORY.md](MEMORY.md). Their independent
`memory_schema` version is 1; the identity/grant schema remains version 2. No OAuth
secrets, passwords or mailbox bodies are stored by the account registry.
Connections are short-lived with a three-second lock
wait. An invalid database is reported, not silently replaced.

Each user has at most one assigned mailbox. Several users may explicitly share
the same mailbox, with separate read/send permissions. An unassigned profile never
inherits the default profile's mailbox. The account list shows each mailbox once
and lists all assigned profiles. Disabling or disconnecting an account affects all
its users; removing one profile's assignment affects only that profile.

Version 1 upgrades automatically on first database access. The migration backs up
the old database once as `users.before-shared-gmail.sqlite3`, then removes the
mailbox's reverse uniqueness constraint in a transaction. Existing profiles,
aliases, assignments, permissions and memory identifiers are retained. No mailbox
is assigned automatically. New databases start directly at version 2.

The existing memory files and saved language preferences are unchanged. No memory
migration, user password UI or background synchronization is added. Optional
creation/deletion email reports are configured separately below.

Gmail credentials are in the root-only, host-encrypted
`/etc/credstore.encrypted/minitel-gmail-accounts`, bounded to sixteen accounts.
The existing encrypted OAuth client configuration and HTTPS callback are reused.
The legacy `/etc/credstore.encrypted/minitel-gmail` is retained untouched as the
pre-migration credential backup. Once the account bundle exists, implicit legacy
reads/rotations and single-account enrollment writes are refused.
Systemd delivers the bundle as `gmail-accounts`, matching its embedded encryption
name; the legacy credential remains named `gmail`. The targeted token source accepts
the legacy delivery marker during import until the next service activation, but
always obtains the selected account's actual credentials from the root helper.

Root helpers accept validated account IDs and enrollment revisions only. Each
refresh rotation preserves the other accounts. Account revision comparisons reject
late enrollments and stale credential operations. OAuth attempts retain their
user/account target, PKCE and state in bounded RAM. Reauthorization to a different
Google mailbox is rejected; adding that mailbox requires a separate account.

## Restart a profile's welcome

**Services → Utilisateurs et accès → Réinitialiser l’accueil** restarts a named
profile's welcome without deleting saved personal memory or changing access.
Confirm the button, then reconnect/use SOMMAIRE or start Faire connaissance in
the test console. New clear conflicting answers replace the corresponding older
facts; unrelated memories remain. See [ONBOARDING.md](ONBOARDING.md) for the
reset/resume distinction and handling of in-flight requests.

## Delete a test profile

In **Services → Utilisateurs et accès**, expand **Supprimer le profil et toutes
ses mémoires** for a named user. Retype its displayed name exactly and submit.
Maintenance → Mémoire des utilisateurs also links to this page. The reserved
MinitelGPT profile cannot be deleted. Authenticated POST, CSRF, exact confirmation
and the current profile revision are checked on the server.

This deliberately removes the named identity, aliases, Gmail assignment and
PRONOTE grants, saved memories/notes/lists/tasks, vectors, pending learning events,
welcome progress/history and that profile's complete archive directory, including
old JSON backups. It does not disconnect shared Gmail accounts, erase their
credentials, change PRONOTE account configuration, or affect other profiles.
Independent whole-database backups, exported files and operational journals are
outside this per-profile deletion; manage those separately when needed.

The profile is disabled and its revision advanced before cleanup, so active
sessions and late writes lose access. Cleanup holds the existing profile lock and
a short database transaction. If archive removal fails, the profile remains
disabled; reload and retry rather than assuming deletion succeeded. The name
remains reserved until cleanup completes. SQLite and the filesystem do not share
an atomic transaction: a disk/database error may leave partially removed files
and a disabled profile requiring retry. Archive removal does not follow links
outside the validated profile folder.

A small additive `user_identity_sequence` counter retains the highest allocated
numeric ID, preventing SQLite from reusing a deleted ID for an unrelated profile.
It contains no names or personal data and leaves schema versions unchanged.
Explicitly entering a deleted name again creates a fresh, empty profile with a
new numeric identity and no service access. Old sessions cannot bind to it.

## Automatic user reports

In **Services → Gmail → Rapports automatiques**, enter one destination address.
Gmail must be active and the reserved default profile must have an assigned,
enabled Gmail account with send permission. That dedicated account sends the
reports; no other profile's mailbox is used as a fallback. The destination only
needs to be a syntactically valid email address. It does not need to be connected,
enrolled, present in recent correspondence, or verified. Saving it sends no test
message. Clear the field to disable reporting.

The setting lives in the private registry's additive `user_mail_reports` table,
not in OAuth credentials, cookies or per-profile memory. It applies to creations
from the terminal, console and admin UI, and to completed admin deletions. Reusing
a name/alias, editing access, disabling a user, resetting welcome or a failed
deletion sends no report. Initial creation of the reserved default profile does
not report itself. Each report contains the action, display name, numeric user ID
and event time in Europe/Paris; no memories, conversations or account secrets.

A single lazy daemon sender per application process handles at most 32 pending
events, outside user transactions. Profile changes do not wait for Gmail. Before
each send it rechecks activation, the configured destination and the exact default
profile/account binding. Removing send permission, disconnecting/reassigning the
sender, clearing the destination or disabling Gmail stops affected pending reports.
Changing the destination discards reports queued for the old address.

Delivery is best-effort: the queue is in RAM and is lost on a process restart;
full queues, unavailable accounts and failed/uncertain sends are recorded in the
service logs without rolling back user changes. Each send has a 20-second deadline
and is not automatically retried. Logs contain action/ID/status, not report names,
destinations or bodies. The normal interactive email draft still requires review
and literal ENVOYER; fixed creation/deletion reports are the sole opt-in automatic
send path and do not use the model. Already delivered reports remain in the
sender/recipient mailboxes after a profile is deleted.

Offline checks use real registry transactions and synthetic mail clients, covering
concurrent first use, deletion failures, permissions, arbitrary recipient addresses,
configuration persistence, CSRF, the bounded queue and uncertain sends. On the Pi,
configure an owner-controlled destination, create one disposable user, verify one
creation email, then delete it and verify one deletion email. Reopening a user or
leaving the report address blank should send nothing.

## Rollout on the Pi

Use the complete release on develop, not an intermediate implementation commit.
An already-installed Gmail extension receives the root helper through the normal
runtime update. No additional package, sudo rule or database daemon is required.
The runtime checks that the interpreter imports sqlite3.

1. Update through Maintenance, refresh the console, then open Services →
   Utilisateurs et accès (`/users`). Create your profile and the other known names.
2. Open Services → Gmail. Select **Importer l’ancien compte partagé sans
   l’attribuer**. This retains its existing Google authorization and imports it as
   an unassigned account. The database is backed up once to
   `users.before-multi.sqlite3`; the legacy encrypted token file is unchanged.
3. Return to Utilisateurs et accès, assign that mailbox explicitly to its owner,
   and choose read-only or read/send. Save. No personal mailbox is automatically
   assigned to the default profile.
4. In Gmail, choose the MinitelGPT profile in the Google connection form, connect
   the dedicated device Gmail account, then Apply. Apply assigns read-only access
   unless **Autoriser aussi l’envoi** is checked. Both services restart after the
   response so systemd delivers the updated credential bundle.
5. Connect additional mailboxes in the same way. To share an already connected
   mailbox (including the dedicated device account), select it for each intended
   profile in Utilisateurs et accès and save. No new Google authorization is needed;
   choose read-only or read/send separately for each profile. Configure allowed
   PRONOTE child aliases there as well.
6. Check each mailbox using the explicit account selector and read-only connection
   check. The user-mode Gmail console honors the selected user's grants.

Opening a profile or making a request never silently falls back to another
mailbox. Grant changes invalidate old sessions, cached views and approval tickets;
reopen the session after changing access. SOMMAIRE retains identity and resets the
conversation; LOGOUT selects another identity. Already dispatched external requests
cannot be recalled and are never replayed after reconnect/reset.

A failed Google consent preserves existing accounts. Credential storage happens
before the database update; an interrupted callback may leave an unassigned
account. Use **Synchroniser les comptes enregistrés** to recover its metadata,
then explicitly assign it. Synchronization does not grant access. If Apply fails,
retry Apply. A local disconnect removes grants first; failure to erase ciphertext
leaves a disabled account, not restored access. It does not revoke Google's grant.
Reauthorization of a shared account replaces its credentials for all assigned
profiles and invalidates their old sessions; it preserves each profile's grants.

## Recovery and downgrade

Back up the SQLite database using sqlite3's backup API (or with services stopped),
not by copying it while writes are active. Back up encrypted credential files and
retain the Pi's systemd host key using the existing secure installation procedure.
Never export plaintext token data. No backup/restore web feature is added here.

Normal code rollback is not a user-data rollback. The runtime refuses a target
release without multi-account support while the account bundle exists. For an
intentional downgrade, stop both application services, preserve the current
SQLite and encrypted account bundle, restore matching old data and legacy
credential delivery under root, and only then deploy the old release. Removing
or renaming the account bundle without restoring matching code/units is unsafe.
Do not downgrade merely to regain automatic shared-mailbox access.
Schema-version-1 code cannot open a version-2 database. Returning to that code
requires restoring matching database data with both services stopped; a normal
code rollback does not restore the pre-migration database backup.

## Acceptance

Offline checks use synthetic accounts and mocked Google/systemd operations. On
hardware, test blank/default, two named users and an unassigned name. Verify
correct inbox and From address; read-only send denial; LOGOUT and SOMMAIRE;
expired/revoked authorization; grant removal during a session; and restart
persistence. Real sends require an explicit owner-controlled recipient and review.
Operational tracing contains only user/account IDs, operation and outcome. Existing
private transcript rules remain; no duplicate mail-body archive is introduced.

## Offline validation — 29 October 2026

The complete suite passed: **652 tests and 400 subtests**. Ruff, requirements export
freshness, installer shell syntax and git whitespace checks passed. Seventeen
focused account tests exercise real SQLite transactions/concurrency and mocked
private-service boundaries, including stale-result rejection and migration backup.
No live Google requests, real email sends, Pi deployment, systemd commands or
physical Minitel checks were performed as part of this implementation.

## Shared-mailbox correction — 30 October 2026

The complete suite passed: **658 tests**. Ruff, requirements export freshness and
git whitespace checks passed. Added coverage includes concurrent version-1
migration and backup, shared assignments, one-mailbox-per-user enforcement,
independent send permissions, session invalidation, account-wide controls and
reauthorization targeting. No live Google requests or Pi deployment were performed.
