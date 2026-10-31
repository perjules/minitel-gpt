# Explicitly shared profile facts

Phase one adds administrator-reviewed publication of individual **facts** from
named profiles. Each memory still belongs to exactly one user. Other users may
recall approved facts, but cannot change their owner's records. No new framework,
database service, dependency or account authentication is introduced.

## Use

Open **Maintenance → Mémoire des utilisateurs**, re-enter **ADMIN_PASSWORD**, then
choose **Afficher / modifier** on a named profile. The existing five-minute unlock
and CSRF protection cover publication as well as ordinary editing.

- For one existing fact, check or clear its sharing box and select
  **Appliquer le partage**. This preserves its original source quotes and origin.
- **Partager tous les faits actuels**, followed by its apply button, publishes all
  current active facts. Clear it and apply to unshare all. A stale form is refused;
  reload and review again if another session changed the profile or its memory.
- The add form can explicitly publish a new fact. The editor can explicitly approve
  a corrected fact through **Partager ce texte corrigé après relecture**. An ordinary
  content edit retains the existing admin behavior: origin becomes `admin` and
  obsolete source quotes are removed. Use the separate sharing control when only
  changing visibility.

Share-all is a decision about the facts currently shown, **not** a policy for future
learning. New automatic, welcome and terminal memories are private. Preferences,
notes, lists and tasks cannot be published through this feature. Facts about work,
birthdays or family may be published after review; thoughts and preferences remain
private in this phase. Classifying a sensitive statement as a fact does not make it
appropriate to share: the administrator must review the actual text.

The shared audience is every enabled profile, including anonymous/default
MinitelGPT. Names are selectors, not authentication; proper account access controls
remain future work. Publication grants no Gmail/PRONOTE access or send permission.
The separate MinitelGPT household-publication controls retain their existing behavior.

`MEMOIRE` / `MEMORY` still lists the selected user's own memories and marks their
published facts `[partagé]`. `MEMOIRE GLOBALE` remains the household inventory.
Questions about another person use normal conversational recall; there is no new
terminal command to publish or enumerate everyone else's records.

## Attribution and model context

Published text is not rewritten. Each recalled fact carries its stable owner ID,
current display name and update time. A separate context header identifies the
current speaker and explains that generic `User`, `I/my` or `je/mon` inside a
record refers to **its owner**, while an explicitly named third party stays that
third party. For example, Alice's `User's brother is Bob` is Alice's relationship,
even while Charlie is speaking. Alice's `Bob works at X` is an assertion by Alice
about Bob, not an assertion that Alice works there. Beliefs remain attributed
claims, not independently verified truths. Ambiguities/conflicts require clarification.

Python filters visibility before ranking. Shared facts form one foreign-owner
pool, using the existing cosine scan and lexical fallback. Mentioned owner names
and aliases help ranking; record identity includes the owner ID to avoid collisions
between local memory IDs. Personal, household and shared-profile contexts reuse one
question embedding and together stay within 4000 characters. Selection is bounded;
publication does not guarantee every fact is recalled for every question.

Only approved fact text/title and attribution metadata enter foreign recall.
Source quotes, pending statements, logs, raw vectors, journal intime and hidden
Rose data are excluded. Foreign facts are not supplied as the current user's
extraction/onboarding evidence or personal write targets. Relevant facts can help
the existing Gmail **recipient selector** resolve relationships against real mailbox
candidates. They do not enter the mail writer/router or confer account permissions.

## Edits, revocation and merge

Publication approves a particular kind/title/text fingerprint. Any later content
change from automatic extraction, onboarding or the terminal retracts publication;
the new text needs explicit review. An administrator may approve their edited text
in the same save. Unchanged records retain their flag. Deleting a record removes
it from recall. Disabled/deleted owners are excluded; renames use the current name.

Changing only sharing increments the memory revision and invalidates old worker
leases, without discarding pending eligible statements or vectors. Recall rechecks
visibility and owner metadata after network operations, so a concurrent unshare,
edit, rename, disable or delete cannot inject the old snapshot. Revocation affects
future recall: it cannot erase text already displayed, supplied to a model, or
retained in a conversation/archive/backup. Model interpretation is still fallible;
Python enforces visibility and write ownership, not perfect language understanding.

The offline merge tool retains publication on unchanged destination facts. Imported
source facts become private, because transferring ownership requires a fresh review.
Its read-only preview accepts both memory schema versions 1 and 2 without migrating.

## SQLite migration and recovery

On first ordinary memory access, memory schema **1 → 2** adds nullable
`memory_items.shared_hash`. Existing records start private. IDs, owners, content,
source quotes, embeddings, pending work and the independent identity/access schema
version 2 are preserved. JSON compatibility remains unchanged. Fresh databases
start at memory schema 2. No re-embedding or manual migration is necessary.

Before altering an existing schema-1 database, the app makes a private, full SQLite
backup at `<MINITEL_MEMORY_DIR>/users.before-profile-sharing.sqlite3`. Migration is
transactional; backup failure aborts it. The retained backup is not overwritten on
later accesses and may contain identity/access data as well as ordinary memory.
Keep it under the same local access rules as the existing database. Exports expose
the effective per-record `shared` boolean, not the internal approval hash; on the
default profile this boolean is false because household publication uses `origin`.

Normal deployment still follows [RUNTIME.md](RUNTIME.md). Keep the usual full data
backup before updating. Older code requiring memory schema 1 will refuse schema 2;
reverting the code alone does not downgrade the database. For recovery, stop both
services, preserve the current full data directory and SQLite sidecars, then
restore a verified pre-upgrade backup together with compatible code. A full DB
restore reverts **all** users and grants to that snapshot. Do not restore an old
database over work that must be retained, or delete the new column by hand.

## Offline validation

Synthetic temporary databases and mocked providers cover schema upgrade/backup
failure/concurrent access, single/bulk controls and stale forms, provenance,
private defaults, content-edit retraction, delayed workers, source attribution,
owner collisions/aliases/renames, concurrent revocation/disable/delete, malformed
or wrong-model vectors, shared context budgeting, explicit operations, onboarding,
Gmail recipient isolation, browser conversation wiring, exports and user merge.
No live provider call, actual user migration or physical Pi/Minitel validation is
part of these checks.

Validation on 31 October 2026: locked all-extras sync; **1054 tests passed**,
including 27 sharing cases. Ruff, requirements-export freshness, installer shell
syntax and whitespace checks passed. No push or Pi deployment was performed.
