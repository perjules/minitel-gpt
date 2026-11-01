# Durable conversation sessions

Ordinary conversations now have an archive in the existing `users.sqlite3`, under
`MINITEL_MEMORY_DIR` (normally `/home/minitel/.local/share/3615-ia`). There is no new
service, dependency, vector database, account password or hosted session store.
The terminal is still the product; Maintenance is the inspection interface.

## What is recorded

Each session belongs to a stable numeric user ID, not its display name. A session
starts lazily on its first recorded interaction, with a random ID, terminal/browser
surface, timestamps, profile revision and writer identity. Merely opening an admin
form or selecting a name does not create an empty transcript.

Messages contain visible user/assistant text, role, channel, timestamp, request
ID, learning status and normalized web references (title/URL only). Ordinary chat,
welcome exchanges, explicit memory commands and ordinary calculator theatre/events
are recorded. Configured/detectable secrets are redacted; this is not a guarantee
of recognizing every arbitrary secret someone types.

- Gmail retains its existing profile JSONL archive, outside the session tables.
- PRONOTE private turns, journal intime, Rose, password entry and raw provider/tool
  payloads are excluded. The private journal remains encrypted and separate.
- Accepted ordinary input is committed before generation, after private routing;
  an interrupted request therefore leaves its question. The final recorder reuses
  the request ID and does not duplicate it. Answers are recorded when returned to
  the frontend; an interruption during terminal display can leave an archived
  answer that was only partly displayed.
- If the provider unexpectedly routes an ordinary request to a private school
  tool, its provisional input is removed and its private answer is not recorded.

SOMMAIRE, LOGOUT, idle timeout and terminal disconnect close the session. The next
conversation gets a new archive ID. A process killed before cleanup leaves an open
row; next activity recovers a dead writer as interrupted, closing at its last
activity. A live writer in another frontend is not closed by this recovery.
Browser pool expiry is checked on later browser requests, with a 20-minute idle
limit. Changed/disabled profiles also retire stale sessions. Closure does not
resume old model history: the existing bounded live context remains unchanged.

## One learning pipeline

Only eligible ordinary USER statements from named profiles enter automatic
learning. Assistant text is never evidence. A bounded excerpt of a preceding
ordinary assistant question can clarify an answer, with evidence still required
verbatim from the user. Onboarding and RETIENS keep their existing dedicated
writers; their archive messages are not fed through automatic extraction again.
The anonymous/default profile still has automatic learning disabled.

The existing event-triggered worker processes six statements per batch, with a
48-event dispatch queue and at most two passes per invocation. Pending SQL message
rows retain additional statements until queue space becomes available. Closure
makes even short batches ready; next activity retries interrupted/failed work.
There is no polling daemon or second whole-transcript mining process. A remaining
backlog waits for later activity, and missing API credentials leave it pending.

Message IDs and session IDs enter automatic-memory source metadata alongside the
saved quote/date. Reviewed statements are marked done, even if the model finds
nothing worth retaining, so closing a session does not review them a second time.
Failures leave work retryable and do not break ordinary conversation.

Every input captures the memory barrier before generation. Explicit forgetting,
correction and new facts accepted during a restarted welcome supersede older pending statements; filling
the dispatch queue cannot revive them. Existing profile revisions, memory revisions
and extraction leases also fence late work. Deleting/pruning a transcript cancels
its pending jobs and invalidates an extraction already in flight. Saved facts and
source excerpts survive transcript deletion/expiry; their old message ID is then
only historical provenance, not a live foreign key.

## Legacy JSONL archives

Import is lazy, bounded to one file chunk (100 lines / 128 KiB) on profile startup,
ordinary activity and a profile's session-admin visit. Repeated visits continue
large imports. Unknown profile folders and private-mode subdirectories are not
scanned. Original files are never changed or removed.

Only supported visible ordinary channels are imported, with timezone-aware dates.
Private flags, Gmail/other unsupported channels, recognizable old school requests
and their following answers, private-mode entry and memory-unlock commands are
excluded. The private-turn exclusion state survives chunk boundaries. Old text
that was historically mislabeled and lacks recognizable private markers cannot
be classified perfectly; inspect the original archive when in doubt.

Import checkpoints store file size, modification time and byte offset. Each
original line has an idempotent request ID. Malformed lines are skipped and counted;
changed files and oversized individual records stop with an inspectable status.
Files modified in the last minute wait rather than racing a possible old writer.
Imported messages are **history only**, never eligible for learning. Completed or
expired checkpoints survive session deletion, preventing re-import from preserved
originals. The importer does not scan Rose, merged-profile private directories or
encrypted journals.

## Retention and size

`MINITEL_SESSION_RETENTION_DAYS=365` sets the retention period after closure. Use a
positive integer in the environment shared by both services, then restart them.
Open/importing sessions are not pruned. Dead writers first become interrupted.

On startup/activity, local maintenance prunes at most 20 expired sessions per
invocation. Completed pruning runs at most daily; a remaining backlog continues
on subsequent activity. No cron job, extra daemon, paid cleanup call or automatic
VACUUM is introduced. SQLite reuses freed pages; the database file need not shrink
immediately. Originals, Gmail JSONL, manual backups and durable memories have their
own lifecycle and are not erased by this retention setting. Retention is therefore
not an erasure guarantee for every historical copy of a conversation.

## Administration and ownership

Open **Maintenance → Sessions de conversation**, first unlocking ordinary memory
with ADMIN_PASSWORD in **Mémoire des utilisateurs**. The same five-minute unlock
and existing admin login/CSRF checks apply. It is not Linux sudo authentication.

Select a profile, then a session. Lists and messages are paginated in groups of 40;
JSON export streams pages instead of loading the entire transcript into RAM. The
viewer displays learning/import state. Closed sessions can be deleted after typing
their exact ID. Live sessions must be closed first. Session deletion preserves
already-saved memory, which can be corrected/deleted separately in the memory
editor or with OUBLIE. Ordinary-memory exports contain no transcripts.

All archive reads, exports and deletion constrain both user ID and session ID.
Shared profile facts grant no access to conversation archives. Current name
selection is still not authentication; individual account controls remain future
work. Administrators can inspect disabled profiles without re-enabling them.

User deletion cascades its SQL sessions/messages/import checkpoints and pending
linked events; a delayed recorder cannot recreate the deleted profile. A later
profile with the same name gets a different numeric ID. The exceptional offline
merge tool transfers session ownership before deleting the source, preserves
session/message IDs, retires old live sessions, transfers import checkpoints with
the copied JSONL filenames and fences both old profile instances. Destination
welcome status and integration permissions retain their existing precedence.

## Migration and recovery

Memory schema **2 → 3** adds `conversation_sessions`, `conversation_messages`,
`conversation_imports`, `conversation_maintenance` and nullable
`memory_events.message_id`. The identity/access schema remains version 2. Existing
memory, grants, publication approvals, embeddings and legacy JSON compatibility
are preserved. No API is required for migration.

Before upgrading an existing memory database, a private full SQLite backup is
created at `users.before-sessions.sqlite3`, without overwriting an earlier backup.
The additive migration is transactional; backup failure aborts it. Fresh memory
databases start at schema 3. A schema-1 database first follows the existing sharing
migration and preserves its separate pre-sharing backup.

Older code limited to memory schema 2 refuses schema 3; code rollback does not
downgrade data. Follow [RUNTIME.md](RUNTIME.md): stop both services, preserve the
current complete data directory/SQLite sidecars, and restore a verified compatible
backup with compatible code. Restoring a full DB also reverts all users, grants
and later writes. Do not drop the new tables/column on a live installation.

## Validation

Offline tests use synthetic profiles, temporary databases and mocked providers.
They cover migration/backup, ownership, input before failure, deduplication,
periodic/closing extraction, barriers, backlog refill, citations, retention,
resumable/private-safe import, gated administration, deletion and merges. Run all
checks in [TESTING.md](TESTING.md). Physical Pi/Minitel validation remains separate:
check SOMMAIRE/logout/disconnect, reopen history in Maintenance, and confirm that
normal chat, onboarding, mail, school, journal and Rose still behave on hardware.
