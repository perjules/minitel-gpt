# Journal intime

The journal is a password-encrypted Markdown document, separate from ordinary
conversation archives and saved memory. It is available to named profiles only.
Names still select profiles without authenticating them; the journal password is
the additional access boundary. The admin password cannot decrypt a journal.

## Use on the Minitel

Type `JOURNAL INTIME`. First use requests a new password twice and creates the
encrypted file only after matching confirmation. There is no password-strength
policy: a one-character password is accepted. Empty input requests a password;
the 2000-character input ceiling is a resource bound. Passwords are case-sensitive
and are never stripped, normalized, echoed, logged, or sent to a model. The physical
terminal accepts its normal printable ASCII repertoire; choose a password you can
also type there if creating the journal through the browser.

On subsequent opening, enter the dedicated password. Only the latest nonempty
day is displayed, without an API call. An empty journal displays a short prompt.

| Input | Result |
| --- | --- |
| Free-text notes | AI drafts a new paragraph for today; no automatic save |
| `TEXTE : exact content` | Literal paragraph proposal, without an API call |
| `MODIFIER instructions` | Revise the pending proposal, or replace today's saved text if there is no pending proposal |
| `ENREGISTRER` | Save the reviewed proposal; add a paragraph or replace today, as shown in the preview |
| `ANNULER` | Discard proposal/deletion confirmation, preserving saved text |
| `LIRE 25/10/2026` | Read the specified date locally |
| `LIRE`, `DERNIER` | Read the latest nonempty day |
| `AUJOURD'HUI`, `LIRE HIER` | Read today or yesterday locally |
| `Montre-moi dimanche dernier` | Interpret only the date through AI, then read the day locally |
| `EFFACER AUJOURD'HUI`, then `OUI` | Delete today's whole entry, preserving all other days |
| `MOT DE PASSE` | Verify the current password; enter the new password twice; re-encrypt |
| `QUITTER`, `LOGOUT`, SOMMAIRE | Close and clear the private mode; LOGOUT also leaves the profile |
| RETOUR | Previous document page; during password input or a pending request, leave the private mode |

Colon shortcuts are accepted at the beginning of a submitted line:

| Shortcut | Full command |
| --- | --- |
| `A:` | `ANNULER` |
| `M: instructions` | `MODIFIER instructions`; `M:` alone asks for corrections |
| `E:` | `ENREGISTRER` |
| `L: date` | `LIRE date`; `L:` alone reads the latest nonempty day |
| `T: exact text` | `TEXTE : exact text`, without AI |
| `S:` | `EFFACER AUJOURD'HUI`, still requiring `OUI` |
| `P:` | `MOT DE PASSE` |
| `G:` | `AIDE` |
| `Q:` | `QUITTER` |

Case and leading spaces do not matter; the colon must immediately follow the
letter. A space after it is optional. Commands without arguments must occupy the
whole line: `A: some text`, `E: some text`, `Q: some text` and unknown shortcuts
produce a local error, without AI or a saved change. Text after `T:`/`TEXTE :`
is literal; embedded command-looking text is never interpreted again. Password
fields always treat the complete input literally, including `A:` or `E:`.
Full command words remain valid. ENVOI submits input and does not approve a save
by itself. ANNULATION clears the current input, including a hidden password
field; it does not abandon the draft. `A:` explicitly cancels the pending draft.

Every document page accepts commands immediately. SUITE goes forward; RETOUR
goes backward, including from the last page. At a boundary the current page stays
visible. A page counter identifies the current position. Partially typed input
survives navigation; ANNULATION clears it. Navigation reuses loaded text and
does not call Mistral or reload the file. The web console has the same next/
previous navigation and command entry without disabling the field on early pages.

Saving requires displaying all proposal pages, then explicitly entering E: or
ENREGISTRER from any page. Returning to a previous page does not undo that review;
revisions invalidate that review. A new proposal defaults to today's date even
while viewing an older day. Each save appends a blank-line-separated paragraph
under that date. Past days are read-only through the terminal or web console.
Dates use Europe/Paris. A proposal crossing midnight is retargeted to today and
requires a fresh preview/review. A deletion confirmation crossing midnight is
cancelled. Any intervening command cancels deletion confirmation.

There is a five-minute idle lock during password entry, reading, writing and
pagination. Key activity extends it. Locking clears keys, first-password
confirmation, proposals, loaded days and web page buffers. No draft autosave.
Transport loss also closes the private mode. An abandoned model response cannot
be displayed or saved by a new session. Unsaved text is lost on exit or restart.

The journal borrows the established hardware echo suppression/route restoration
from the private Rose adapter. It keeps local echo disabled throughout journal
mode and explicitly echoes ordinary input once. Password input is never echoed.
If the terminal cannot acknowledge disabling echo, no password is requested.

## Private browser console

Services → **Journal intime → Ouvrir**, the test-console navigation, or typing
`JOURNAL INTIME` in the conversation test opens `/journal`. Select the named
profile and enter the journal password. Use the existing Tailscale HTTPS
administration address; direct unencrypted LAN access is rejected. Localhost
HTTP is allowed for an SSH tunnel/local development. A local reverse proxy must
therefore provide HTTPS on its external side. Do not expose a plaintext local
proxy remotely.

Admin authentication and CSRF are required in addition to the journal password.
Passwords/content are POST bodies, never query parameters or Flask cookies.
Only opaque server-session selectors enter cookies. The console uses no browser
storage, clears its field after submit and clears the display on idle lock,
page exit and history restoration. Responses prohibit caching. The independent
server timer clears session/page contents without waiting for another request.
Profile changes, reset and admin logout invalidate the corresponding private
session. The bounded pool holds at most eight sessions.

## Storage and encryption

`<MINITEL_MEMORY_DIR>/<stable-profile-memory-key>/journal.md.enc` is created only
on first successful setup. The default root remains `~/.local/share/3615-ia`,
outside Git. The profile's existing directory lock coordinates writes with
deletion and offline edits. Display-name changes keep the stable identity/path.
Deleting the profile also removes its encrypted journal; the admin confirmation
explicitly lists it. Independent backups are not deleted by profile removal.

V1 is a 44-byte binary header (`>8sQ16s12s`: magic `3615JI01`, stable numeric
user ID, salt, nonce) followed by AES-256-GCM ciphertext including its 16-byte
authentication tag. The complete header is authenticated as associated data.
The payload is UTF-8 Markdown. The profile ID prevents swapping another user's
ciphertext into this journal. A copied file can still be decrypted independently
with the password and offline helper; it does not require the Pi or registry.

The 32-byte key is derived with scrypt (`N=65536, r=8, p=1`, about 64 MiB working
memory), with a random 16-byte salt. These fixed V1 costs cannot be inflated by
editing a file header. Every write uses a fresh random 12-byte nonce. Password
changes also generate a fresh salt/key. There is no separate password verifier,
master key, host key, recovery credential or password reset bypass. A forgotten
password means the contents cannot be recovered.

Files and temporary ciphertext use 0600 permissions. Writes flush/fsync, replace
atomically and fsync the directory. Failed writes before replacement preserve
the old ciphertext; a durability error after replacement requires checking the
file before retrying. A SHA-256 fingerprint detects stale/concurrent writes;
reopen rather than overwriting a journal edited elsewhere. Symlink/nonregular
files and invalid/tampered/oversize documents are refused, never replaced with
an empty journal. Current limits: 2 MiB Markdown document, 2000 characters of
input, 12000 characters per proposal/AI rewrite context. Large existing days
remain readable and appendable; full external editing remains available.

The readable Markdown has a fixed `# Journal intime` title and unique
`## YYYY-MM-DD` day headings in chronological order. Free-text body paragraphs
are retained. Date headings are structural and cannot be inserted inside a new
paragraph through the terminal. Markdown is canonicalized on a saved edit.

## AI and privacy boundary

AI composition uses stateless Mistral `/v1/chat/completions` with only the user's
instructions and the paragraph/day being revised. New paragraphs do not send
older entries. Natural-language date lookup sends only the request and today's
date; reading itself is local. The journal never uses Conversations, tools,
ordinary chat history, saved-memory retrieval or automatic learning. Model
responses cannot choose file paths, identities, dates to write, or authorization.
The API key is the existing Mistral key; journal passwords/keys never enter calls.
The configured general Mistral model is used; Gmail task overrides do not apply.

Private inputs, displayed entries and proposals bypass transcript recording,
memory extraction/embeddings and printer context. Errors are fixed public
messages; HTTP payloads and arbitrary exception strings are not exposed/logged.
The first-use explanation discloses cloud processing; `TEXTE :` avoids it.
Mistral's retention/training policies still apply to assisted drafting. No ZDR
configuration or provider guarantee is implied by local encryption.

Passwords, keys and readable text necessarily exist in RAM during use. Python
cannot promise forensic zeroization of every allocation. This protects stored
files, not an unlocked/compromised Pi or browser, screen observation, keystroke
capture, or a weak password against offline guessing. Both production service
units disable ordinary core dumps with `LimitCORE=0`. For strict RAM-only
handling, check the Pi's effective swap/hibernation/crash-dump configuration:
use no disk-backed swap or encrypted swap; zram stays in RAM. This feature does
not silently change global swap settings. Encrypted backups may retain deleted
entries and old passwords; SD-card deletion is not forensic secure erasure.

## External editing and backups

Back up the complete `.enc` file; no other secret file is required. The offline
helper prompts through `getpass` and accepts no password on its command line.
An explicit export is plaintext and is the user's responsibility; keep it off
the Pi where practical. Editors may create swap/backup files too.

```bash
# Run with the project's interpreter, from the checkout. Substitute the path.
.venv/bin/python scripts/journal.py export /path/to/journal.md.enc --output /safe/path/diary.md
# Edit the exported Markdown, including past days if desired, then:
.venv/bin/python scripts/journal.py import /path/to/journal.md.enc --input /safe/path/diary.md
# Or change a password offline:
.venv/bin/python scripts/journal.py password /path/to/journal.md.enc
```

Export refuses an existing destination. Import validates Markdown and atomically
re-encrypts it, without creating plaintext or backup copies itself. An encrypted
copy can be edited on a trusted computer and restored to its original location.
After external editing/password changes, close and reopen existing sessions.
Restore an intact ciphertext backup on corruption; never reset the existing file
automatically. Old backups still require their original passwords.

## Deployment and acceptance

`cryptography` is now a core dependency (already used by Gmail); the normal
updater prepares it even without Gmail. No new SDK, process, service, sudo rule
or schema migration. Existing wheel-only ARM installation rules remain in place.
The runtime import probe includes cryptography. Source changes stay on develop;
no stable main promotion without physical acceptance.

Offline tests cover encryption, corruption, short-password confirmation/change,
atomic-write failure, concurrent writers, user deletion/recreation, today-only
edits, midnight, idle lock, late responses, literal entry, bounded stateless
requests, browser authentication/CSRF/transport, private logs/cookies, pagination,
terminal routing/echo restoration and offline editing. They use synthetic data.

On the actual Pi/Minitel: measure unlock latency/memory use; check swap/core
configuration; create a disposable named journal; verify hidden double entry,
two additions, AI revisions and full-page review; read yesterday; verify only
today can be erased; change a password; lock/reopen; test timeout, SOMMAIRE,
disconnect and power cycle. Check HTTPS from the phone. These physical checks and
live drafting quality are separate from automated validation.
