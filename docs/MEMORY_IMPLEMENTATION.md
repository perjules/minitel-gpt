# Per-user memory implementation — 30 October 2026

Approved scope: SQLite storage, Mistral semantic recall, periodic/session-end
consolidation, explicit memory commands, and a gated read-only Maintenance page.
No shared memory, passwords for terminal users, encryption, graph database,
new dependency, daemon, polling service or change to Gmail/PRONOTE access rights.

## Milestones and contracts

1. **Plan** (this commit): inspect the current storage/session/runtime/admin
   contracts; record the implementation and acceptance criteria.
2. **SQLite storage**: add versioned memory tables to the existing users.sqlite3
   without changing the identity/grant schema. Keep stable user IDs, memory IDs,
   language and revisions. Import each legacy JSON once under its existing lock;
   preserve its bytes in a private legacy backup. A marker prevents old JSON-only
   code from silently editing an obsolete copy. Malformed sources are reported
   without overwriting them. Transactions and revision checks protect edits.
3. **Semantic recall**: batch embeddings via the existing Mistral key and HTTP
   library. Store float32 vectors, text hash and embedding model. Query only the
   selected user's active memories, combine semantic and lexical selection with
   a small preference allowance and the existing context budget. No-match is
   valid. Timeout/missing/stale vectors fall back locally. Explicit list/show
   operations stay offline. Embeddings are rebuildable, never authoritative text.
4. **Automatic consolidation**: persist eligible user statements, not model
   claims, in bounded batches. Trigger after six ordinary user turns, on close,
   and on a later interaction/startup for interrupted work. One bounded worker
   per process and a database lease prevent duplicate processing across terminal
   and admin. No perpetual worker loop. Extract only grounded facts/preferences,
   with evidence from supplied user statements; validate IDs and source quotes.
   Default profile, disabled profiles, Gmail, PRONOTE, theatre, secrets and explicit
   memory commands are excluded. Explicit changes invalidate older queued and
   in-flight extraction. Failures retain pending work with bounded retry delay.
   Completed batches are deleted; old transcript archives are never mined.
5. **Admin page**: Maintenance -> User memory. Metadata per profile (including
   disabled profiles); re-enter ADMIN_PASSWORD for a short session-bound gate
   before contents or JSON exports, individual or all profiles. CSRF on unlock
   and lock, no passwords/content in cookies, no browser caching. Read-only:
   no delete/edit or bulk transcript export. Show origins, source evidence,
   indexing/processing health and storage locations. No new top-level section.
6. **Acceptance and documentation**: mocked extraction/embedding tests, migration
   restart/concurrency/failure cases, ownership, stale-vector/timeout fallback,
   explicit forget versus delayed extraction, bounded queues/leases/retry,
   lifecycle triggers and excluded channels, gate/expiry/CSRF/export tests.
   Run locked environment sync, pytest, Ruff, requirements-export and whitespace
   checks. Document JSON export/manual SQL cleanup and pre-migration recovery.
   Publish develop after checks; no main promotion, live API/email operations or
   Pi validation are implied by offline tests.

## Operational limits and choices

- Memory content stays local but selected statements/queries are sent to Mistral
  for extraction, embeddings and answering. Embedding requests use short deadlines.
- Automatic statements are compact and evidenced; model-provided confidence is
  not treated as proof. Ambiguous/hypothetical assertions should be omitted.
- Existing explicit notes, lists, tasks and full-answer saves remain supported.
- A record's owner always comes from Python/SQLite, never model output. Memory
  cannot grant permissions to Gmail, PRONOTE or any tool.
- Optional user passwords remain deferred. Selecting another known username is
  still possible; this feature does not claim strong terminal authentication.
- Legacy backups and transcripts remain outside Git and are not silently pruned.
- Physical acceptance: existing memories survive update; two named users remain
  isolated; reworded recall works; explicit forgetting survives pending work;
  SOMMAIRE/LOGOUT stay responsive; phone viewer/export and offline behavior work.
