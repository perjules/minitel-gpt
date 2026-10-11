# PRONOTE on 3615 IA

Read-only local access to homework and published grades through **PronotePy
2.15.6 / ParentClient**. Enrollment uses a parent mobile-app QR code, followed
by token login. Every successful login saves the rotated token on the Pi.
There is no ENT password scraping and no school write operation.

## Install and connect on the Pi

Keep the existing Mistral **Conversations** provider selected. Claude, Linkup
and the Chat Completions rollback adapter do not offer local PRONOTE tools.

```bash
cd /home/minitel/minitel-gpt
sudo bash scripts/install_pronote.sh
```

This optional script installs the adapter in `.venv`, keeps access to the
existing system Python dependencies, and points `admin-ui` and
`minitel-chatgpt` at that interpreter through systemd drop-ins. Both services
restart. The normal installer and other installations remain unchanged.
PRONOTE is disabled until you enable it in its admin page.

1. Open **Paramètres → PRONOTE** in the existing admin UI, or `/pronote`.
2. Generate a new mobile-app connection QR in your PRONOTE **parent** space.
   Decode it locally and paste its complete JSON (`url`, `login`, `jeton`;
   optional fields such as `avecPageConnexion` are accepted and ignored); enter the
   four-digit confirmation code you chose when generating it. Do not paste
   these data into the Minitel, a chat, or a public QR-decoding website.
   This page accepts decoded JSON, not a photograph or a mobile-app export.
3. Choose a local account name, e.g. `famille`, and connect it. Leading/trailing
   whitespace is removed and uppercase letters normalized to lowercase, including
   account names in the child configuration. If your account
   requires a separate security PIN, supply it in the optional field.
   Enrollment checks token login and device registration immediately.
4. The page displays the account's exact child names locally. Add an explicit
   alias mapping and enable the integration:

   ```json
   {
     "Alex": {"account": "famille", "name": "NOM Alex"},
     "Sam": {"account": "famille", "name": "NOM Sam"}
   }
   ```

   Names must match PRONOTE exactly and uniquely. Each alias can reference a
   different parent account, allowing children at different schools. No child
   is implicitly allowed. No terminal restart is needed for mapping changes.
5. Test in the admin personality preview, then on the Minitel:
   `Quels sont les devoirs d'Alex pour demain ?`
   `Quelles sont les notes de Sam ce trimestre ?`
   An ambiguous child prompts a choice instead of selecting the first child.
   Function keys remain **ENVOI, SUITE, SOMMAIRE**, in either language.

Without a custom `FLASK_SECRET`, the admin now generates a private persistent
session-signing key rather than using the old public default. That change
invalidates old admin sessions once; sign in again with your existing password.

## Local diagnostics / enrollment

Run as the same `minitel` user as the services. Credentials and configuration
are under `config/pronote/`, directory mode 0700, JSON files mode 0600, excluded
from Git. QR and security PINs are never saved. Account names and aliases may
be command arguments; PINs and QR contents must not be.

```bash
.venv/bin/python scripts/pronote_setup.py status
.venv/bin/python scripts/pronote_setup.py children famille
.venv/bin/python scripts/pronote_setup.py read homework Alex --from 2026-10-12 --to 2026-10-12
.venv/bin/python scripts/pronote_setup.py read grades Sam --period 'Trimestre 1'
```

For CLI enrollment, place decoded QR JSON in a private local temporary file:

```bash
chmod 600 /tmp/pronote-qr.json
.venv/bin/python scripts/pronote_setup.py enroll famille --qr-file /tmp/pronote-qr.json
rm /tmp/pronote-qr.json
```

PINs are requested without terminal echo. For CLI configuration,
`configure --file /path/to/local-settings.json` accepts
`{"enabled": true, "children": {"Alex": {"account": "famille", "name": "NOM Alex"}}}`.
The diagnostics are local admin operations, never AI tools. Their school
output is private; do not upload it as a debug fixture or project record.

## Runtime behavior

Only three functions are offered to the model:

| Function | Data |
| --- | --- |
| `pronote_list_children()` | Explicitly allowed aliases only |
| `pronote_get_homework(child, from, to)` | Inclusive date range, subject, plain description, existing done status |
| `pronote_get_grades(child, period)` | Published grade strings, scales, coefficients, dates and comments |

Homework defaults to today through seven days from today, using **Europe/Paris**
for school dates, and accepts at most 31 inclusive days. Grades default to
PRONOTE's current period. If a period cannot be resolved uniquely, available
period names are returned for clarification. An explicit period must match an
actual name; no invented trimester IDs. Grade strings such as `Absent` and
`Non noté` and decimal coefficients stay strings; no averages are computed.
Attachment downloads, attachment URLs, teacher identities, account metadata,
other students and class averages are excluded. Links in descriptions are
omitted. An empty successful result means no published records in that exact
scope; it does not mean there is no schoolwork elsewhere.

Each uncached read opens a fresh parent session, resolves the allowed child
against that session's children, saves the new token immediately and closes
its HTTP session. Thread and account file locks serialize authentication across
terminal, admin and CLI processes. There is no automatic session refresh/retry
that could replay stale child IDs. The pinned SDK hook applies HTTP timeouts
(up to ten seconds per request, bounded by the operation deadline), suppresses
credential-bearing SDK logs, and rejects PRONOTE write methods. The terminal's
existing worker deadline and SOMMAIRE cancellation still apply; a late worker
may finish token housekeeping but cannot display or store its answer.

Cache: ten minutes, in memory only, scoped by operation, child, dates/period,
configuration, enrollment UUID and school date. A network outage can return the
same cached scope for up to 24 hours, marked **possibly outdated**, with its
original retrieval timestamp. Authentication errors and missing children never
use this fallback. New enrollment or mapping changes invalidate the relevant
cache. Each process has its own cache. Results are bounded to 100 records,
24,000 JSON characters and 2,000 characters per homework description;
truncation is declared in the display footer. Cached/live state and retrieval
time are added locally, so the model cannot silently omit them.

School questions first use a tool-only Conversations request with web search
absent. Functions execute locally; only sanitized school results reach Mistral,
using `store=false`. Enrollment, tokens, PINs, full child names from configuration,
account URLs and raw SDK objects never enter model payloads or logs.
There are at most two function rounds/four calls under the existing total
answer deadline. Tool descriptions and homework are evidence, not instructions.
Public questions pass to the existing factual/web adapter after the local
planner returns without a school call. The public request removes prior school
answers and their paired questions. This adds one planning request to public
questions while PRONOTE is enabled. School data cannot be supplied to the
hosted web-search tool by these requests. SOMMAIRE clears conversational memory.
Disabling PRONOTE restores the normal request path and still strips private
school turns before public search.

## Error codes

- `dependency_missing` / `dependency_version`: run the optional installer;
  do not upgrade PronotePy independently of its tested adapter.
- `not_enrolled`, `authentication_required`, `enrollment_failed`: regenerate
  a QR and re-enroll, checking the parent space and security PIN. A failed
  second enrollment login keeps the initial token for recovery.
- `unknown_child`, `child_unavailable`: check aliases and exact names.
- `select_period`: choose one returned period name.
- `busy`, `timeout`, `unavailable`: wait or check the Pi's network/school
  service. These errors expose no credential-bearing SDK exception text.

## Validation and design provenance

Offline tests mock the school server and Mistral, exercise the pinned real SDK
hooks, authentication rotation/failure, cross-process locking, child isolation,
cache outages, function limits/privacy, admin authentication/CSRF and serial
pagination. They preserve the existing boot/personality/web-search tests.
A live account, real model function-call response and physical Pi/Minitel test
still require enrollment and the Pi's API key; no live success is claimed.

The retrieved `evaluate Pronote` recommendations specified ParentClient, pinned
2.x, QR/token rotation, local read-only tools, explicit child allowlists, atomic
storage, locking and short caching. Its final proposal was not fully available
through thread retrieval. The ten-minute TTL, 24-hour maximum stale age, date
and result limits, two-round/four-call cap and isolated planner are explicit
implementation defaults, not claims about unseen text.

Primary references checked for this adapter:
[PronotePy quickstart](https://pronotepy.readthedocs.io/en/stable/quickstart.html),
[the pinned SDK release](https://github.com/bain3/pronotepy/tree/v2.15.6),
and [Mistral Conversations entry contracts](https://docs.mistral.ai/api/endpoint/beta/conversations).
