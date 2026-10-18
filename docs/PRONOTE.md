# PRONOTE on 3615 IA

Read-only local access to academic records, timetable, school-life information
and canteen menus through **PronotePy 2.15.7 / ParentClient**. Enrollment uses a parent mobile-app QR code, followed
by token login. Every successful login saves the rotated token on the Pi.
There is no ENT password scraping and no school write operation.

## Install and connect on the Pi

Natural-language school queries and direct-mode AI fallback require Mistral
**Conversations**. The deterministic `PRONOTE` commands work with any configured
provider, including when the AI provider/key is unavailable. The same enabled
PRONOTE configuration, enrollment and authorized aliases are required in both paths.

```bash
cd /home/minitel/minitel-gpt
sudo bash scripts/install_pronote.sh
```

This optional script installs the adapter in `.venv`, keeps access to the
existing system Python dependencies, and points `admin-ui` and
`minitel-chatgpt` at that interpreter through systemd drop-ins. Both services
restart. The normal installer and other installations remain unchanged.
PRONOTE is disabled until you enable it in its admin page.

“Dépendance installée : oui” means the tested SDK version is installed.
“Intégration : désactivée” means PRONOTE tools are not offered to Mistral and
ordinary conversation does not use the school adapter. It does not prevent
parent-account enrollment or the administrative child-list check. After
enrollment, add the permitted child aliases, check **Activer pour Mistral
Conversations**, then **Enregistrer**. Enabling does not repair a failed login.

For an existing installation, pull the updated `develop` branch and rerun
`sudo bash scripts/install_pronote.sh`; this upgrades the pinned dependency
and restarts both services. A source-only pull/restart retains the old package.
Do not upgrade only the package: the adapter's strict version check must match.

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
   `Quelles sont les notes de Sam ?`
   An unspecified period selects the most recent one that has started; the
   answer names the period used. You can still request another exact period.
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

### Shared tools

Both the normal natural-language router and direct mode call `Pronote.execute`.
All arguments are validated before authentication; unknown keys/tools and
unconfigured child aliases are rejected.

| Function | Published data |
| --- | --- |
| `pronote_list_children()` | Authorized aliases only |
| `pronote_get_homework(child, from, to)` | Dates, subjects, descriptions, done status, safe filenames |
| `pronote_get_grades(child, period)` | Marks/scales, coefficients, comments, test class average/min/max, bonus/optional flags |
| `pronote_get_timetable(child, from, to)` | Times, subjects, teachers, rooms, cancellations, status and test flags |
| `pronote_get_averages(child, period)` | Student/class subject averages, class min/max, scale |
| `pronote_get_report(child, period)` | Published report averages, teachers, subject/general comments |
| `pronote_get_evaluations(child, period)` | Competency titles/dates, teachers, descriptions, coefficients and acquisition levels |
| `pronote_get_tests(child, from, to)` | Timetable test flags, excluding canceled lessons |
| `pronote_get_absences(child, period)` | Times, hours/days, justification and reasons |
| `pronote_get_delays(child, period)` | Dates, minutes, justification and reasons |
| `pronote_get_punishments(child, period)` | Nature, date, circumstances/reasons, work and detention schedule/duration |
| `pronote_get_announcements(child, from, to)` | Titles, authors, publication dates, categories, read/survey flags, content and safe filenames |
| `pronote_get_menus(child, from, to)` | Meals and food names by day |
| `pronote_get_briefing(child, from, to)` | Daily lessons/changes, upcoming test flags, homework and recent announcements |

Dates are inclusive ISO `YYYY-MM-DD`, resolved using **Europe/Paris**.
Homework defaults to today through seven days ahead (eight inclusive days),
announcements to the last eight days, tests to today through six days ahead,
and timetable/menus/briefing to today. Homework, announcements and menus accept
at most 31 inclusive days; timetable/tests at most seven. A briefing accepts
one day and reads its homework/tests through six days ahead and announcements
from seven days before that day through that day.

Periods default to the most recent started period. Future periods are never
chosen implicitly. When starts tie, the shorter interval wins, then the name.
This avoids relying on sometimes missing parent `current_period` metadata.
Omitted/null/blank/`current`/`latest` selectors use that default; an explicit
period must match its actual name. Errors return available names for clarification.
An empty current period stays empty. `pronote_get_report(period="published")`
checks up to eight started periods, newest first, for the latest published report;
no published report produces `report_unavailable`. An explicit report period
never substitutes another report. `BULLETIN` uses the published selector.

No averages are calculated. Nonnumeric values such as `Absent` and `Non noté`,
scales and decimal coefficients retain their supplied values. The SDK grade
`comment` can be a test description; it is not a separate report appreciation.
Report grading scales are not supplied by this SDK. Competency evaluations are
separate from numeric marks. Test flags provide no assessment description.

### Direct keyboard mode

Type `PRONOTE`, then **ENVOI**. One authorized child is selected automatically;
with several, choose an alias or its displayed number. No child is selected
arbitrarily. `ENFANT` lists choices and clears the retained result. Selecting a
child or changing alias configuration clears previous detail/print data.

| Command | Result |
| --- | --- |
| `NOTES` | Current-period published grades |
| `MOYENNES` | Subject averages |
| `DEVOIRS` | Upcoming homework |
| `COURS` | Today's lessons |
| `CONTROLES` | Upcoming timetable assessment flags |
| `ABSENCES` / `RETARDS` | Current-period attendance records |
| `BULLETIN` | Latest published report |
| `COMPETENCES` | Current-period competency evaluations |
| `INFOS` | Recent school announcements/surveys |
| `CANTINE` | Today's meals |
| `BILAN` | Daily combined briefing |
| `SANCTIONS` | Current-period disciplinary records |
| `ENFANT` | Select another authorized child |
| `AIDE` | Compact command list |
| `RETOUR` | Restore normal conversation |

Commands ignore case and accents; singular `NOTE`, `MOYENNE`, `DEVOIR`,
`CONTROLE`, `INFO`, and `MENU` are accepted. `QUITTER`/`SORTIR` exit too.
The menu and local errors use compact French wording, including in an English
session; AI fallback retains the selected session language.

Examples:

```text
COURS
DEMAIN
SEMAINE
2026-10-16
DEVOIRS DEMAIN
CANTINE LUNDI
NOTES
MATHS
MOYENNES Trimestre 1
BULLETIN Trimestre 1
```

`DEMAIN`, `HIER`, `AUJOURDHUI`, `SEMAINE`, ISO dates and weekday names refine the
current dated view, or follow its command on the same line. A weekday means its
next occurrence including today. `SEMAINE` means this Monday through Sunday;
`BILAN SEMAINE` is rejected because a briefing is one day. A subject word after
`NOTES` filters loaded marks locally (`MATHS` also matches `Mathématiques`).
A period follows the numeric/period command; `PERIODE exact-name` refines it.
If a period-selection error supplied names, their typed case/accents are matched
locally to those exact names.

`NOTES` displays **one test per screen**, with a `Note n/total` counter.
While this grade view is active, the **SUITE** key (or typed `SUIVANT`/`SUITE`)
advances and the **RETOUR** key (or typed `PRECEDENT`) goes back. Moving back
from the first test returns to the PRONOTE command menu; moving forward from the
last test keeps that last test visible. Navigation uses loaded data, including
subject-filtered results, without PRONOTE/LLM requests. `QUITTER`/`SORTIR` leave
PRONOTE immediately; the typed command `RETOUR` also retains its exit meaning.
`LIRE n` opens complete details for a test in the displayed selection. Exceptional
long fields are visibly shortened to keep one physical page, with a detail hint;
original fields remain in the loaded result. `IMPRIMER` prints every loaded test
in the current selection, rather than just the visible test.

Reports, competencies and announcements show numbered entries first. Daily
lessons use short time/subject/room lines; long homework and grade comments are
shortened only in the initial screen view, with `TEXTE` showing the loaded text. `LIRE 2`
shows one loaded entry; `TEXTE` shows all loaded details. Paging/detail/filtering
makes no network or model request. Results, menus and errors stay visible while
waiting for the next command; the input hint is appended below the last page
without clearing it. `IMPRIMER` prints the complete retained
result, including all loaded pages, through the existing text printer; weekly
printing includes full lessons even though the screen shows a compact weekly
summary. It does not print/download attachments. Printer installation and
hardware readiness remain separate requirements.

**ENVOI** submits, **SUITE** pages loaded text, **SOMMAIRE** resets the whole
session and **GUIDE** keeps its existing administration behavior. **RETOUR**
exits direct mode, including during a read or on a paginated result. Typing
`RETOUR`, `SORTIR` or `QUITTER` followed by ENVOI also exits at those waits,
without requiring SUITE first. Exit works even if configuration cannot be read,
and removes earlier private school turns from the LLM's active conversation
while preserving public discussion. Normal-mode school questions remain available.
Ordinary
conversation retains its existing correction-key behavior. Reads run in the
existing single background-worker/wait contract; abandoned replies cannot enter
another session and overlapping requests get a recoverable busy message.

Unrecognized meaningful input gets one call to the existing school LLM
orchestrator, with current view/date/period context and tools restricted to the
selected child. No web search is offered and the backend still validates every
call. With another provider or no AI key, use `AIDE`; deterministic commands
continue working. There is no recursive fallback. This path does not keep an
independent conversational history: ambiguous requests should name the feature.

Normal mode still accepts, for example:

- `Quels devoirs a Alex demain ?`
- `Quelle est la moyenne de classe du dernier contrôle de maths d'Alex ?`
- `Y a-t-il des cours annulés pour Sam cette semaine ?`
- `Combien d'absences non justifiées ce trimestre pour Alex ?`
- `Résume les dernières annonces scolaires de Sam.`

### Sessions, bounds and privacy

Each uncached read opens a fresh parent session, resolves the authorized child
against that session's children, saves the rotated token immediately and closes
HTTP resources. Thread and account-file locks serialize terminal/admin/CLI
access. There is no automatic refresh/retry with expired child IDs. HTTP requests
use at most ten seconds, bounded by the operation deadline. Enrollment, token
storage and private file permissions retain their existing behavior.

A briefing uses **one login and one selected child**. A single weekly lesson read
serves both its daily timetable and upcoming tests. Other components fail
independently and the result declares `partial`; an expired session fails the
operation rather than continuing with stale authentication.

The read allowlist adds `PageBulletins`, `DernieresEvaluations`, `PagePresence`,
`PageActualites` and `PageMenus` to the existing endpoints. In pinned 2.15.7,
announcement content uses `PageActualites` with `genreRequeteActualite=1`;
read-state changes use the blocked `SaisieActualites`. No homework completion,
survey answer, message, announcement-read or other school write is permitted.
Only explicitly selected object fields leave the service. Credentials, account
URLs, identifiers and attachment URLs never enter model payloads. Link attachment
names are omitted; file metadata includes only a sanitized filename/type.

School output is transient: direct mode never records requests/results in the
persistent memory journal. Normal-mode private school answers and their paired
questions are excluded from journal/recent memory and public web history.
School requests still use Mistral `store=false`; this does not guarantee absence
of all provider retention. Logs retain fixed tool/status and secret-free error
metadata, never school records or arbitrary SDK exception messages.

The existing school orchestrator keeps its two-round/four-call limit, handles
repeated calls locally, and makes final synthesis with no tools. Errors are not
retried. Public requests retain their factual/web adapter and strip private turns.
No new runtime dependency is required.

Cache remains ten minutes, in memory only, scoped by operation, child, dates or
period, configuration, enrollment UUID and school date. A network outage can use
that exact cached scope for up to 24 hours with its timestamp and stale warning.
Authentication/child errors never use stale fallback. Re-enrollment or changed
mappings invalidate the relevant scope. Each process has its own cache.

Results are capped at 100 records and 24,000 JSON characters for items/extra
metadata, with declared record truncation. Existing homework descriptions retain
their 2,000-character limit. New scalar text fields are capped at 500 characters,
announcement text at 2,000, list metadata at 30 elements, attachments at ten and
detention schedules at twenty. A briefing retains up to twenty records per
component, declaring component truncation. These caps can omit long comments.

Announcement content/attachment metadata is fetched only for the **five newest
entries in a requested range**, bounding extra server requests. Older entries
have `content_error=detail_limit`; narrow the date range to obtain their content.
Other detail-fetch failures keep metadata and declare a partial result. Survey
content is the SDK's first content block, not an interactive survey form.
Schools can disable features or leave them unpublished. An empty successful
list means no published records in that exact scope; it is not evidence that a
feature is globally unsupported. An unrecognized/malformed server response is
reported as such, rather than silently converted to an empty list.

Screen output uses vertical lists and 39-column wrapping. Existing character
normalization runs at the terminal boundary; unusual names/accents remain
readable ASCII. Weekly timetables show daily counts and status/test exceptions;
select a day for full detail. The serial link and real keyboard/CRT still need
physical usability checks.

## Error codes

Enrollment failures show a secret-free diagnostic on the admin page and CLI,
and write the same diagnostic to `logs/admin.log`: `stage` (QR login, token
login, credential save or child listing), exception class, last SDK request
name when available, and an integer PRONOTE error code when supplied. Exception
messages, response bodies, traceback locals, QR/PIN/token data and account
identities are omitted. The ordinary model tools still return error codes only.

```bash
tail -n 100 /home/minitel/minitel-gpt/logs/admin.log
```

`qr_login` with `QRCodeDecryptError` indicates QR decryption failure: check the
four-digit confirmation code and use a fresh QR. `token_login` with `MFAError`
indicates a problem during the immediate token/device check; supply the account
security PIN if required. A generic `enrollment_failed` by itself does not
identify the cause. Report the diagnostic, not the QR or credentials.

Version 2.15.7 changes the login-challenge handling for PRONOTE 2026.
The older 2.15.6 can raise `CryptoError` after `Identification` even when the
initial QR-field decryption succeeds. This matches upstream
[issue 346](https://github.com/bain3/pronotepy/issues/346); a fresh QR and live
retry are still needed to confirm enrollment on a particular school server.

- `dependency_missing` / `dependency_version`: run the optional installer;
  do not upgrade PronotePy independently of its tested adapter.
- `not_enrolled`, `authentication_required`, `enrollment_failed`: regenerate
  a QR and re-enroll, checking the parent space and security PIN. A failed
  second enrollment login keeps the initial token for recovery.
- `unknown_child`, `child_unavailable`: check aliases and exact names.
- `select_period`: choose one returned period name.
- `feature_unavailable`: the SDK reports that the school does not support the feature.
- `report_unavailable`: unpublished/unavailable report in the requested scope.
- `malformed_response`: missing or invalid SDK/server fields; do not treat as no records.
- `permission_denied`: an explicit HTTP 403; other unclassified school refusals remain `unavailable`.
- `busy`, `timeout`, `unavailable`: wait or check the Pi's network/school
  service. These errors expose no credential-bearing SDK exception text.

For a chat lookup failure, inspect `logs/chatgpt.log`. It reports the fixed tool
name, call count, repetition flag and success/error classification, plus safe
PRONOTE stage/exception diagnostics when the local server read fails. It does
not log the question, tool arguments, child alias, school records or tokens.
The old `Local tool call limit` could mask a successful result or an earlier
read error because the final model request still allowed another function call.
Use the returned PRONOTE error code and safe log metadata to identify any
remaining read issue; reaching the limit alone did not prove a successful read.

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
[the pinned SDK release](https://github.com/bain3/pronotepy/tree/v2.15.7),
and [Mistral Conversations entry contracts](https://docs.mistral.ai/api/endpoint/beta/conversations).

## Offline checks and manual acceptance

The baseline at `5e22118` passes 244 tests with the runtime and optional Gmail,
PRONOTE and printing dependencies installed. Run the full offline suite and lint:

```bash
.venv/bin/python -m pytest -v
.venv/bin/ruff check services/pronote_records.py services/pronote_mode.py tests/test_pronote_expansion.py tests/test_pronote_terminal_mode.py
.venv/bin/ruff check --select F services/pronote.py services/pronote_answers.py services/minitel_chatgpt.py scripts/pronote_setup.py
```

The expanded suite passes **284 tests**. Expansion tests use synthetic data and pinned real SDK objects for averages,
unpublished reports and announcement content. They cover every new tool/command,
child changes, stale isolation, date bounds, partial failures, endpoint writes,
thread serialization, real terminal-loop routing, function keys, full printing
and worker cancellation. No live school/model, Pi, printer or Minitel success is
claimed by these checks.

Local CLI diagnostics now accept every tool suffix, for example:

```bash
.venv/bin/python scripts/pronote_setup.py read averages Alex
.venv/bin/python scripts/pronote_setup.py read report Alex --period published
.venv/bin/python scripts/pronote_setup.py read announcements Alex --from 2026-10-12 --to 2026-10-14
.venv/bin/python scripts/pronote_setup.py read briefing Alex
.venv/bin/python scripts/terminal_console.py
```

These diagnostics read real school data when enrolled; keep their output private.
The console shares the actual dispatcher/rendering/key decoder but has no 1200
baud pacing. On the Pi and then the physical Minitel:

1. Confirm enrollment/version and authorized aliases in administration. Open
   `PRONOTE` without an AI key and try `DEVOIRS`, `NOTES`, `MOYENNES`, `COURS`.
2. Try `DEMAIN`, `SEMAINE`, an ISO day and a weekday; check weekly counts against
   PRONOTE, then inspect one day. Check accents, long names and SUITE paging.
3. Inspect `BULLETIN`/`LIRE 1`/`TEXTE`, `COMPETENCES`, `ABSENCES`, `RETARDS`,
   `SANCTIONS`, `INFOS`, `CANTINE` and `BILAN`. Confirm published/unavailable
   distinctions, survey read state unchanged and actual class/competency values.
4. Switch via `ENFANT`; verify details/printing cannot display the previous child's
   records. Test an unknown alias/date/period, reset and re-entry.
5. Exit with RETOUR, including during a read/page; reset with SOMMAIRE and check
   that ordinary AI, Gmail, calculator, memory and GUIDE still work normally.
6. With Mistral configured, try an ordinary school question and a direct-mode
   summary. Verify private school content is absent from local memory journals.
7. If the printer is installed, print multi-page homework and a weekly timetable;
   verify first/last records and accents on paper. Printer hardware acceptance
   is separate from text-format checks.
8. Compare two consecutive uncached reads with live school data; verify token
   rotation, cache timestamps and recoverable timeout/outage behavior.
