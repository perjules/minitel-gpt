# Gmail prototype — 3615 IA

> Model selection and reasoning for Gmail tasks: see [GMAIL_MODELS.md](GMAIL_MODELS.md).

> Multi-user profiles and Gmail/PRONOTE assignments: see [USERS.md](USERS.md).
> Its migration and account-selection instructions supersede the old shared-mailbox setup below.


Optional single-account Gmail reading, replies and **new messages to recent correspondents**, shared by the authenticated
admin console `/gmail` and the physical Minitel. Disabled until encrypted enrollment
is installed and both services are activated. Existing web search, PRONOTE, boot,
personality and serial/navigation paths remain available.

## Colon shortcuts

| Shortcut | Action |
| --- | --- |
| `A:` | ANNULER: discard the pending draft/mail session |
| `M: instructions` | MODIFIER; `M:` alone opens the existing editing preview |
| `E:` | ENVOYER: requires full review of the current draft and its current ticket |
| `L: 2` | LIRE message 2; `L:` alone asks for a number without AI |
| `T: exact text` | TEXTE: append a literal paragraph to the pending draft without AI |
| `B:` | BROUILLON: restore the latest local draft; fresh review is required |
| `Q:` | QUITTER: leave mail, preserving the latest saved draft |

Full words remain accepted. A shortcut is recognized only at the start of the
submitted line, ignoring leading whitespace/case, with the colon immediately
after its letter. Space after the colon is optional. `A: some text`, `E: some
text`, `Q: some text` and unknown shortcuts are rejected locally, with no model,
send, cancellation or edit. Literal contents after `T:`/`TEXTE:` are never parsed
as further commands. Existing numbered literal edits and `R:` remain supported;
the old unnumbered `A: text` append alias is replaced by `T: text`.

Shortcuts are handled before welcome, memory or school routing. They cannot turn
an editing instruction such as `T: RETIENS ceci` into a saved memory. ENVOI still
only submits input; `E:` is explicit send approval, subject to the same review,
account permissions, stale-ticket and duplicate-send checks as ENVOYER. The
ANNULATION function key clears the current input; `A:` cancels the draft itself.
Both the web test console and physical terminal use these same session rules.

## Document navigation

Mail lists, search results, selected messages and draft previews accept a command
on every physical page: there is no need to finish the document before entering
`L: 1`, `REPONDRE ...`, `M: ...`, `A:` or another supported command. SUITE moves
forward and RETOUR moves backward, including from the last page. Boundaries stay
on the current page. The page counter and input prompt remain available, and
partially typed input survives page changes. SOMMAIRE leaves the session.

Paging operates on the already loaded document: it does not refetch Gmail or call
AI. Incoming mail/list pages need not all be read before selecting or replying.
Outgoing drafts still require displaying every page of the current preview and
explicit send approval; after full review, sending is permitted from any page.
An early send refusal redisplays the pending draft so its remaining pages can be
reviewed. Revisions retain their existing stale-ticket and review invalidation.
The web mail console already provides previous/next buttons and command input
on each page; these same approval rules continue to apply.

## Local draft recovery

Each completed draft and successful revision is atomically saved as readable,
unencrypted UTF-8 JSON, mode 0600, outside the checkout. There is one slot per
Minitel profile **and** assigned Gmail account; profiles sharing the same mailbox
do not share drafts. The file is under the profile's existing memory folder:
`$MINITEL_MEMORY_DIR/<memory-key>/mail-draft-<account-hash>.json` (the default root
is `~/.local/share/3615-ia`). Deleting a profile also removes its draft files.
Drafts have no automatic expiry. Users discard sensitive text with ANNULER / A:.

- BROUILLON / B: restores the last saved recipient, subject, text, personal
  sign-off and reply context. No model call, OAuth refresh or Gmail fetch is
  needed to restore it; current local account grants must still allow access.
  A new review ticket is created, and all preview pages must be read before E:.
- SOMMAIRE, QUITTER / Q:, logout, session expiry, UART disconnect and service
  restarts clear transient state and approval, preserving the recovery file.
  Reading another message or listing mail also leaves it available through B:.
- ANNULER / A: explicitly deletes the slot. Starting a new draft replaces the
  previous unsent one only after the new draft has successfully completed and
  been saved. Failed generation or failed atomic replacement preserves the old
  file. A storage failure is reported rather than silently claiming a save.
- Restored replies retain threading headers, thread ID and the original selected
  history quoted after ENVOYER. No other mailbox messages or attachments are saved
  for reply context. Credentials and review/approval tickets are never saved.
- Sending durably marks the attempt **before** the single Gmail send call.
  Confirmed success removes that exact saved revision. If the process dies, the
  send is uncertain, or cleanup fails, B: shows the preserved text and Message-ID
  with an uncertain-send warning and **no approval ticket**. That attempt cannot
  be resent or revised. Check Gmail / Envoyés, then A: to remove the marker;
  compose a fresh message only if another send is actually needed.
- A stale browser or terminal cannot send, revise or delete a newer saved
  revision from another session. B: reloads the current slot. Switching to a
  different Gmail account does not expose the previous account's draft; assigning
  that account back makes it recoverable again.

These are local recovery files, not entries in Gmail's Drafts folder. OAuth
scopes remain exactly `gmail.readonly` and `gmail.send`; no new dependencies,
background worker, database migration or Google consent step is required.

## Validation status

The full suite passes **623 offline tests** (28 October 2026), including mocked REST/model responses,
Flask console checks and simulated UART workflows. New composition uses synthetic
correspondents and validates the existing review/send gate without contacting Google
or sending real messages. Model selection quality, real metadata-fetch latency and
an owner-controlled new-message exchange still require live acceptance. Physical
Minitel acceptance is separate from both offline simulations and admin-console tests.
Browser OAuth includes synthetic state/PKCE, expiry, replay, exact-scope, failure,
encrypted-storage, privileged-activation and callback-log checks. A real Web-client
enrollment through the phone/Tailscale HTTPS address has not been performed here.

Record personal setup progress and live results in the installation's private project
record; this guide documents generic setup and acceptance. Automated tests use fake
clients and never send real email. No private email contents or account identifiers
are recorded here.

## 1. Google project and encrypted enrollment

### Google Cloud, on your computer

1. Open [Google Cloud Console](https://console.cloud.google.com/), create/select
   a project used only for this personal Gmail integration, and enable **Gmail API**.
2. In **Google Auth Platform → Branding**, set the app name `3615 IA`, your support
   address and developer contact. In **Audience**, choose **External** for a personal
   Gmail account. If the Audience page already says **User type: External**, this
   step is complete; leave it as it is. External permits personal Google accounts;
   Internal restricts access to an organization. A Workspace account may require
   administrator approval.
3. Open **Google Auth Platform → Data Access → Add or remove scopes** (project
   settings, not the individual client page), and request exactly these two scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`

   Google assigns the categories automatically: `gmail.readonly` is **Restricted**
   and `gmail.send` is **Sensitive**. Select both, click **Update**, then save.
   If they are missing, enable Gmail API or use the dialog's manual scope entry.

   Do not add `gmail.modify`, `gmail.compose`, broad `mail.google.com`, profile or
   identity scopes. Gmail-hosted drafts are not used; local recovery files need no
   additional Google permission or reauthorization.
4. For authorization from the **admin console**, create a **Web application**
   OAuth client with the exact HTTPS callback described in
   [Browser enrollment](#browser-enrollment-through-the-admin-console), and download
   its JSON. The older command-line enrollment below still requires a separate
   **Desktop app** client with a loopback callback. A TV/limited-input client does
   not support the Gmail scopes used here.
   The downloaded JSON is **application configuration, not an account token**.
   The Pi obtains tokens during browser enrollment and encrypts them automatically;
   do not try to obtain or paste a token yourself. If the client form offers an
   AI-agent checkbox, select it for this assistant; it adds no Gmail permissions.
5. Set the personal-use production project's **Audience → Publishing status** to
   **In production** before the durable enrollment. This does not publish your Pi.
   **Testing expires Gmail authorization and its refresh token after seven days.**
   If you enrolled while Testing, publish and enroll again; do not assume the old
   token becomes durable. Production tokens can still expire or be revoked.
   Click **Publish app** on Audience and confirm; the visible **In production**
   label means this step is complete. If publishing is disabled with a Branding
   configuration warning, saving the Branding draft alone was not sufficient.
   Provide published information pages for your installation. This fork's separate
   public information repository provides these example Branding values:

   | Branding field | Value |
   | --- | --- |
   | Application homepage | `https://perjules.github.io/3615-ia-info/` |
   | Privacy policy | `https://perjules.github.io/3615-ia-info/privacy.html` |
   | Authorized domain | `perjules.github.io` |

   These pages are in a separate public repository and describe the app/data flow
   without account, family, OAuth or network details. Domain ownership verification,
   if requested, is a separate Google step; these URLs do not guarantee approval.
6. Google documents a verification exemption for personal apps with fewer than
   100 users. An unverified-app warning and lifetime user cap can remain; continue
   only for your own known project/account. Public distribution is outside this
   prototype. Workspace policy or Advanced Protection may block authorization.

References (browser flow checked 28 October 2026):
[Audience and seven-day expiry](https://support.google.com/cloud/answer/15549945),
[personal-use verification exceptions](https://support.google.com/cloud/answer/13464323),
[desktop OAuth/PKCE/loopback](https://developers.google.com/identity/protocols/oauth2/native-app),
[web OAuth and redirect validation](https://developers.google.com/identity/protocols/oauth2/web-server),
[device-flow allowed scopes](https://developers.google.com/identity/protocols/oauth2/limited-input-device#allowedscopes).

### Browser enrollment through the admin console

This is the recommended connection/reconnection path for a phone on the tailnet.
The Gmail extension must already be installed (`sudo bash scripts/install_gmail.sh`
for a first installation). An existing Gmail installation can receive this code
through the admin updater: its root-owned helper is updated with the runtime.
There are no new dependencies, sudo rules, persistent workers or OAuth tunnels.
The existing Desktop command-line flow remains available below.

**One-time private HTTPS setup on the Pi:** inspect existing routes first:

```sh
tailscale serve status
tailscale funnel status
```

Use a free, dedicated HTTPS port so this does not replace an Alexa endpoint or
another service on port 443. The example uses **9443**; check it is unused. With
MagicDNS and HTTPS certificates enabled on the tailnet:

```sh
sudo tailscale serve --bg --https=9443 http://127.0.0.1:8080
tailscale serve status
```

If Tailscale prints an HTTPS enablement link, follow it. Use **Serve**, never
Funnel, for this console. The route stays private to devices allowed by your
tailnet policy; allow TCP 9443 if your policy is restricted. HTTPS provisioning
publishes the machine's DNS name in certificate transparency, not the admin
contents. Serve uses the existing Tailscale daemon and resumes after reboot.
The command does not change the console's existing LAN/8080 listener.

Copy the full hostname printed by Serve. For example, the console URL is:

```text
https://minitelgpt.example-tailnet.ts.net:9443/services/gmail
```

In the same Google project, **Google Auth Platform → Clients → Create client →
Web application**, add this exact **Authorized redirect URI**, using your hostname:

```text
https://minitelgpt.example-tailnet.ts.net:9443/services/gmail/oauth/callback
```

The scheme, hostname, port and path must match. Google's browser flow requires a
named HTTPS host: the old `http://100.x.y.z:8080` address cannot be its callback.
Google's server does not need to access the Pi: the browser on your connected
phone follows the return URL. Download the Web client's JSON to that phone or
computer; an existing Desktop client's JSON cannot substitute for it. Keep the
project **In production** and retain exactly `gmail.readonly` and `gmail.send`.

Open the HTTPS console, sign in using ADMIN_PASSWORD, then **Services → Gmail →
Configuration initiale / changer le client Google**. Upload the JSON and paste
that exact callback URI. The Pi validates and encrypts the client configuration
to `/etc/credstore.encrypted/minitel-gmail-oauth` (root-only, 0600), separately from
the account enrollment. No client secret is displayed back to the browser.

**Every connection/reconnection after that:**

1. Open **Services → Gmail** through its saved HTTPS address, with Tailscale on.
2. Select **Connecter / réautoriser Gmail avec Google**, choose the shared account
   and grant the two permissions on Google's page.
3. Google returns to the console. Select **Appliquer l’autorisation enregistrée**.
   This reloads encrypted credentials for both services after a three-second delay;
   the Minitel session is interrupted. Wait a few seconds and reload the page.
4. Enable Gmail if needed and select **Vérifier la connexion (lecture seule)**.

Enrollment remains single-account; it does not create per-user account mappings.
The existing activation checkbox remains separate from authorization. The new
refresh token replaces the old encrypted enrollment only after scopes, refresh
token and Gmail account profile have been checked. No email is sent or marked read.
Until Apply, already cached access tokens can still refer to the prior account.

Authorization attempts use state bound to the signed admin browser session and
PKCE, expire after ten minutes and are consumed once. At most eight attempts are
kept in RAM, with no cleanup worker. Restarting the admin service invalidates any
pending attempt; start again. Tokens, client secrets and PKCE verifiers are never
put in cookies or page HTML. The callback query is excluded from the Flask access
log and the browser is redirected to a clean URL with no-referrer/no-store headers.
Any additional proxy/access logger must likewise omit callback query strings.
Only a loopback proxy's HTTPS forwarding header is trusted, and the request host
must match the saved callback. No global forwarded-header trust is enabled.

The existing fixed-purpose root credential helper handles client encryption,
enrollment replacement and delayed activation. It accepts no paths or commands
from the browser. Apply refuses during a deployment. A Google denial, expired
attempt or token-exchange failure preserves the prior encrypted account token.
If activation fails after enrollment was saved, retry Apply; do not reauthorize
repeatedly. Logging out cancels that browser's pending attempt.

References: [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve),
[Serve flags and persistence](https://tailscale.com/docs/reference/tailscale-cli/serve),
[HTTPS certificates](https://tailscale.com/docs/how-to/set-up-https-certificates).

### Install code on the Pi without promoting main

The tested Gmail implementation is published on `develop`. Update that branch
on the Pi; enrollment remains a separate owner-controlled setup step.

```sh
cd /home/minitel/minitel-gpt
git status --short
git switch develop
git pull --ff-only origin develop
sudo bash scripts/install_gmail.sh
```

The earlier code-only `gmail-prototype.bundle` remains an alternative transfer
for an offline Pi with baseline `6687d04`; it contains no credentials.

Start from a clean `develop` checkout; if the fast-forward fails, inspect the
concurrent commits and integrate them normally. Do not reset/discard local work.
The admin's existing update button follows `main`; do not use it for this prototype.
The install script checks the **actual Pi's OS, Python >=3.11, running systemd and
systemd-creds >=250** before proceeding. Raspberry Pi OS Bookworm's Python 3.11 /
systemd 252 meets the version requirements; an older image may need an OS upgrade.
All Python dependencies, including cryptography, live in the shared root-owned
uv runtime; no Debian Python packages are inherited. Cryptography must have a
compatible wheel; installation fails before switching services if none exists.
Gmail's dependency extra preserves existing PRONOTE/printer extras. See
[RUNTIME.md](RUNTIME.md) for setup and rollback. Unsupported encrypted credentials
stop setup; **there is no plaintext fallback**. Installing the extra can restart
running services, but it does not activate Gmail or change enrollment.

In all commands below, replace `PI` with the Pi's hostname or IP address.
Upload the Desktop client JSON over SSH (not through chat or the admin console).
For example, in a terminal on your **laptop/computer** (PowerShell works on Windows):

```sh
scp ~/Downloads/client_secret_DESKTOP.json minitel@PI:/tmp/minitel-gmail-client.json
```

Use the actual path/name of your downloaded JSON. A Windows example is
`scp "C:\Users\YOUR_NAME\Downloads\YOUR_CLIENT.json" minitel@PI:/tmp/minitel-gmail-client.json`.

On the Pi, install it outside Git and remove the temporary OAuth configuration:

```sh
sudo install -o root -g root -m 0600 /tmp/minitel-gmail-client.json /etc/minitel-gmail/client.json
rm /tmp/minitel-gmail-client.json
```

This file is OAuth client configuration, not an account token. No intermediate
plaintext **token** file is created at any point. Keep the downloaded client JSON
private on your computer too.

### Authorize once using the computer's browser

**Window 1 — on your laptop/computer**, run this SSH tunnel and leave the window
open throughout enrollment. Replace `PI` with the Pi's hostname/IP, and enter your
usual SSH password if asked. A blank window after login is normal: `-N` opens no
interactive shell; it keeps forwarding the browser callback to the Pi.

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8765:127.0.0.1:8765 minitel@PI
```

**Window 2 — on your laptop/computer**, connect separately to the Pi:

```sh
ssh minitel@PI
```

Then run these commands **in that Pi SSH session**:

```sh
cd /home/minitel/minitel-gpt
sudo .venv/bin/python scripts/gmail_enroll.py /etc/minitel-gmail/client.json
```

Open the printed Google authorization URL **on the computer with that tunnel**.
Sign in only on Google's page and grant both displayed permissions. The browser
redirects to `http://127.0.0.1:8765/oauth2callback`, tunneled to the Pi. The server
validates the random state; Google's OAuth library supplies S256 PKCE. Callback
access logging is disabled; the authorization code never appears in logs.
The callback window is five minutes. A wrong state is refused. Port 8765 must be
free on both ends; no phone HTTPS enrollment is implemented.

When the Pi prints **`Encrypted enrollment saved. Activate/restart both services;
run the read-only probe.`**, enrollment succeeded. You do not need to copy a token
or authorize again. Close **Window 1's tunnel** with **Ctrl+C**; keep Window 2's
Pi session for the following checks. The tunnel is not used during normal operation.

A successful enrollment fetches your canonical Gmail address and writes **only
verified encrypted credentials**, atomically, to:

- `/etc/credstore.encrypted/minitel-gmail` (root, 0600).
- Host encryption key managed by systemd outside Git, normally
  `/var/lib/systemd/credential.secret`.

Use `systemd-creds` with the **host key**, never the null key. Encryption protects
against accidental copying/leakage. Extraction of the whole SD card including
its key, or compromise of the running service, remains an accepted risk.
See [systemd credentials](https://systemd.io/CREDENTIALS/).

### Prove live reading and automatic renewal before use

**Step 1 — on the Pi:** copy this entire manual **read-only** probe as one block.
The trailing backslashes continue the same command over several lines. It prints
counts/status only, not tokens or mail content. It fetches up to ten metadata
entries, reads the first body if any, and renews the access token again in memory.
It cannot send:

```sh
sudo systemd-run --wait --pipe --collect --uid=minitel \
  --property=LoadCredentialEncrypted=gmail:/etc/credstore.encrypted/minitel-gmail \
  --property=PrivateMounts=yes --working-directory=/home/minitel/minitel-gpt \
  /opt/minitel-gpt/venv/bin/python scripts/gmail_probe.py --renew
```

Successful output includes JSON like this (counts vary with your inbox), followed
by `Finished with result: success` and exit status `0/SUCCESS`:

```json
{"authorization": "ok", "inbox_metadata_count": 10, "selected_body_characters": 1190, "forced_renewal": true}
```

This establishes account access, retrieval and forced access-token renewal; it does
not test sending. If the probe fails, stop before activation and diagnose the
reported error. If a selected message exceeds the body limit, use a small
owner-controlled inbox test message and rerun.

**Step 2 — on the Pi:** enable Gmail in both normal services:

```sh
sudo bash scripts/install_gmail.sh --activate
systemctl is-active admin-ui minitel-chatgpt
```

The installer activates and restarts both services itself. Expect **`active` on
two lines**, one per service. That confirms the services are running; the next
step checks the actual interface.

**Step 3 — try mail:** refresh the admin console and open **Paramètres → Gmail**,
or type **`MAIL`** at the Minitel's question prompt and press **ENVOI**. Expect a
numbered inbox list. **`LIRE 1`** normally opens its full readable text; **`RESUMER`**
optionally requests an AI summary. Exceptionally long messages may open as a
labelled automatic summary; **`TEXTE`** always gives the full cached body. Use
**SUITE** to page through it. Reading leaves Gmail's read/unread status unchanged.

**Step 4 — on the Pi:** verify unattended operation after reboot:

```sh
sudo reboot
```

The SSH connection closes normally. Wait for the Pi to return, reconnect SSH if
needed, and try **`MAIL`** again through the admin console or Minitel. No browser,
tunnel, new Google consent or unlock operation should be required. For a stronger
renewal check, repeat the read-only probe after reboot and again after the normal
access-token lifetime has elapsed. Record these results separately from forced
renewal; they are not implied by offline tests.

Services use `LoadCredentialEncrypted=gmail:...` and `PrivateMounts=yes`. Access
tokens remain in RAM. A small root-owned zero-argument sudo helper reads the
current encrypted credential through local pipes and atomically persists a
replacement refresh token if Google returns one. A shared lock serializes renewal
across admin and terminal; replacement uses compare-and-swap and verified ciphertext.
The helper/module are installed root-owned outside the writable checkout. It has
no arbitrary file operations and rejects interactive token dumping.

## 2. Read, search and summarize

Use the authenticated admin's **Paramètres → Gmail**, or type commands at the
Minitel's ordinary question prompt and press the function key **ENVOI**:

| Command | Result |
| --- | --- |
| `MAIL` / `check my mail` | Up to ten recent inbox messages; numbered sender, subject, Paris date/time, lu/non lu |
| `RECHERCHE is:unread` | Up to ten unread matches using Gmail search |
| `RECHERCHE from:alice@example.org subject:réunion` | Sender/topic search, no fabricated address |
| `RECHERCHE after:2026/10/01 before:2026/10/13` | Date boundaries converted to Europe/Paris midnight |
| `LIRE 2` | Read actual message ID bound to displayed number 2 |
| `TEXTE` | Full cached readable body of selected message |
| `RESUMER` | Labelled AI summary of selected body |

Natural mail questions use a bounded user-request-only classifier. If a reference
is ambiguous, choose a displayed number. **LIRE defaults to full readable text**,
with no AI call; **RESUMER** explicitly requests a labelled summary. Exceptionally
long messages (more than **6,000 body characters**, by default) open as a labelled
automatic summary with a TEXTE hint. Set `GMAIL_AUTO_SUMMARY_CHARS` in the private
`.env` to change the threshold; **0 disables automatic summaries entirely**.
Restart services/the console after changing it. `TEXTE` always opens the full
cached readable body, and paging/refetch-free switching back from a summary does
not invoke AI. Failed automatic summaries fall back to full text without retrying.
Plain-text MIME wins over HTML. HTML conversion loads no images/resources. Attachment
names/types are listed, and their contents are not fetched or interpreted.
Reading uses GET only and **does not change read/unread status**.

Session limits: ten results, three loaded messages, each body up to 64 KiB; API
responses up to 1 MiB. Oversized content is refused visibly with guidance to open
Gmail. AI context contains at most the first 12,000 characters of the selected
body and explicitly discloses that limit. Draft body generation is bounded too.
Only selected sender/subject/body and necessary user instructions go to Mistral;
no bulk inbox, attachments, credentials or ordinary web/PRONOTE history are sent.

Use **SUITE** on the physical device to see each page. Admin `SUITE`/`RETOUR`
operate on the same bounded server-memory output. Paging never fetches mail or
calls Mistral. Server restart, logout, idle expiry (checked on access after 20 minutes in admin), or
**SOMMAIRE** discards mail state. No mailbox database or background sync is used.

Acceptance: check numbering after search/reordering, decoded French text,
HTML-only/short/long messages, Paris time including DST, attachment metadata,
unchanged unread flag in Gmail, full-text last page and labelled summary limits.
On the physical Minitel check 39-column wrapping, accents/transliteration, 18-row
pages, function-key **SUITE**, **SOMMAIRE** during requests and ordinary web/PRONOTE
queries after mail. Simulated UART tests do not replace this physical check.

## Automatic user reports

**Services → Gmail → Rapports automatiques** optionally sends fixed creation and
deletion notices from the default profile's dedicated Gmail account to one
configured address. The destination needs no Gmail enrollment or verification.
The default account still needs send permission. A blank recipient disables this
feature. These notifications do not go through the interactive draft review;
ordinary user-written emails retain the existing ENVOYER gate. See
[USERS.md](USERS.md#automatic-user-reports) for event scope, delivery behavior and
the owner-controlled live check.

## 3. Reply, revise, cancel, review and explicitly send

Select an owner-controlled test message, then:

1. `REPONDRE remercier et confirmer mardi` generates **one local draft**. The
   recipient comes from the selected message's single valid Reply-To, or From
   only when Reply-To is absent. Multiple valid choices require `DESTINATAIRE n`;
   malformed/duplicate headers block sending and ask for a different valid source.
2. Read the entire recipient address, subject and outgoing body through **SUITE**.
   Python appends the exact outgoing signature:
   **Envoyé par Minitel 722079V (138121) — 3615 IA**.
   Initial drafts include the current session username as the personal sign-off;
   an unnamed/shared session uses **MinitelGPT**. Python adds this default only to
   fresh drafts. `MODIFIER` can replace or remove the personal sign-off, and later
   revisions do not force the original name back in. The enrolled Gmail account's
   From address remains unchanged. Python always restores the automatic Minitel
   footer, including when an edit asks to remove or change it; the complete outgoing
   reply text remains visible during review. The original message is appended
   automatically only after approved `ENVOYER`, beneath the reply and signature,
   with its sender/date and plain-text `>` quotation markers. This includes the
   full selected body and any earlier history already embedded in it, independently
   of the model's shorter context limit. It is not shown again on the Minitel and
   requires no extra model call or mailbox fetch. Earlier thread messages absent
   from the selected body and attachments are not added. New messages never inherit
   a selected email's history. `Re:`, In-Reply-To, References, Message-ID and the
   Gmail thread ID are retained. After `MODIFIER`, the next
   editing instruction directly revises the existing draft instead of being
   classified as a new message. Failed edits retain this editing state and keep
   the previous review invalid. ANNULER deletes the saved draft; SOMMAIRE leaves
   mail while preserving the latest completed revision for BROUILLON / B:.

   Literal edits require no model call. `MODIFIER` shows numbered paragraphs,
   independent of the terminal's wrapped screen rows. The personal sign-off is
   editable and numbered; the mandatory Minitel footer is separate and unnumbered.

   | Command | Alias | Result |
   |---|---|---|
   | `TEXTE: texte` | `T: texte` | Append exact text before the tracked personal sign-off, or at the end of the body if none is tracked |
   | `AJOUTER 1: texte` | `A1: texte` | Insert after paragraph 1 |
   | `REMPLACER 1: texte` | `R1: texte` | Replace paragraph 1 |
   | `REMPLACER TOUT: texte` | `R: texte` | Replace the complete body, including its personal sign-off |

   `AJOUTER: texte` remains accepted for compatibility. `A:` now exclusively
   means ANNULER; `A: texte` is rejected without cancelling or editing anything.
   `TEXTE` **without a colon** retains its existing full-message reading behavior.

   Commands are case-insensitive; spaces around a paragraph number are optional.
   The first colon separates the command from the text; further colons remain
   literal. One optional space after the colon is syntax. Ordinary outer whitespace
   is trimmed, as in other terminal input. Empty text, invalid targets and control
   characters leave the draft unchanged. Numbering is preview-only and never sent.
   Every successful edit builds a new MIME/review ticket and requires rereading
   before ENVOYER. Recipient, subject and reply threading remain unchanged.
   The terminal displays `Envoye par Minitel 722079V (138121) - 3615 IA`; the actual MIME
   body retains the Unicode signature. Neither persona nor interface instructions
   are included in the outgoing email.
3. `MODIFIER` asks for changes; `MODIFIER rendre plus court` or conversational
   revisions create a new revision. Any attempted change invalidates review.
   Reread every page of the new draft. `ANNULER` discards it and the mail session.
4. Type **`ENVOYER`** or **`E:`** and press the function key **ENVOI**, or use the admin
   **ENVOYER** button only after reaching the final review page. The function key
   **ENVOI alone does not approve sending**. The LLM has no send tool and cannot
   approve anything; only this literal user command sends the stored reviewed
   revision, without regeneration.
5. Success appears only after Gmail returns confirmation, or a Sent lookup verifies
   the exact generated outgoing Message-ID after an uncertain response. A successful
   send clears the draft; repeated ENVOYER cannot send it again. Otherwise the draft
   is retired and the interface instructs you to verify Gmail / Envoyés, displaying
   its Message-ID. **Do not create/send another reply until you verify the outcome.**

Cancellation/reset can abandon generation; an explicitly approved send already
submitted to Gmail cannot be recalled by SOMMAIRE. Network uncertainty is never
resolved by automatically retrying the send. Local drafts survive restart, reset
and logout as described below. Reply-all, CC/BCC, sending aliases, attachments,
full-thread context and multiple accounts are not implemented. New composition
to recent correspondents is described in Stage 1 below.

Owner-controlled exchange acceptance: send a small inbound message from another
account you own; read it; draft and revise; cancel one draft; create another;
verify sending is refused before full review; review and explicitly ENVOYER once.
Verify receipt, one copy in Sent, exact signature, actual recipient/subject/body,
Gmail thread placement, In-Reply-To and References in the received original source.
Verify duplicate confirmation does not resend. Repeat the workflow on the physical
Minitel. Real send testing is manual, never part of the automated suite.

## 4. New message to a recent correspondent (Stage 1)

No selected message is required, and a previous selection/draft does not turn this
request into a reply. Type either:

```text
ECRIRE Alice : confirmer le rendez-vous mardi
```

or a natural request such as **« Écris un mail à Alice pour confirmer le rendez-vous
mardi »**. In the shortcut, the colon separates the recipient description from the
writing instructions. An incomplete shortcut shows that syntax without making an
interpretation call. If a natural request lacks content, the assistant asks what to
write. If new versus reply intent is unclear, it asks rather than guessing.

Python retrieves metadata from up to **50 recent sent, received and archived
messages**, excluding drafts, spam and trash. This lookup is independent of MAIL's
ten results. It reads only From, Reply-To, To, Cc, Subject and date metadata; no
bodies, snippets or attachments, and no read-status changes. Five workers reuse
separate HTTP sessions within the existing request deadline. Missing metadata or
invalid headers are visibly disclosed as partial coverage. There is no automatic
widening to older mail.

Candidates retain actual validated addresses, decoded name variants, header roles/
direction, last seen and up to two **short subject examples for identification**.
From and Reply-To remain distinct if their addresses differ. Identical addresses
are deduplicated conservatively; the local part is preserved and the domain is
case-normalized. Distinct addresses with the same name remain separate. Obviously
automated/no-reply addresses are marked in clarification/review.

The canonical enrolled address is excluded. To exclude explicitly known self-aliases,
optionally configure `GMAIL_SELF_ALIASES=alias1@example.org,alias2@example.org` in the
private `.env` and restart both services. This does not permit sending as an alias,
infer Gmail dot/plus aliases, query account settings or request new permissions.

The model receives the compact quoted records plus your recipient description and
returns grounded candidate IDs with unique/ambiguous/no-match status. Python accepts
only actual IDs and recovers the stored address; model-generated addresses and
confidence values cannot authorize a recipient. Email/header strings remain
untrusted data. The catalogue is cached in bounded session RAM for **ten minutes**
and discarded by SOMMAIRE, ANNULER, logout/session expiry or restart.

- A unique supported match goes directly to the draft with the full address visible.
- Ambiguity shows numbered real names/addresses and provenance. Use **DESTINATAIRE 2**
  or clarify in words; the original writing instructions are retained. Displayed
  candidate pages use existing navigation without refetching or calling the model.
  More than six plausible matches asks for more detail instead of a long menu.
- A missing match explains the last-50-message boundary. Use a more precise name or
  type a complete email address directly. A single valid user-supplied address is
  accepted immediately, even when absent from recent messages, without a recipient
  lookup or confirmation question. The original writing instructions are retained.
  This also works with `ECRIRE person@example.org : instructions` or in place of a
  numbered recipient choice. Malformed/multiple addresses are rejected; the full
  draft still requires review and literal ENVOYER before sending.
- The compact catalogue is capped at 64,000 serialized characters, and model input
  at 24,000 catalogue characters. Overflow asks for a complete email address;
  Python validates that literal address directly rather than silently dropping
  candidates or adding a name-matching engine.

After resolution, the model generates a fresh subject and plain-text body from your
instructions. Identification subjects and the previously selected body are **not**
provided to drafting. Python builds a fresh From/To/Date/Message-ID and appends the
same reviewed signature. New-message MIME has **no In-Reply-To or References**, and
the Gmail send request omits **threadId**. The selected subject is not reused or
automatically prefixed with Re:. Gmail can still independently group similar mail.

Review, revise, cancel and **ENVOYER** work exactly as for replies. Use **MODIFIER
instructions** for body/subject changes, or describe a recipient change in words,
for example **« change le destinataire pour Alice Martin »**. A recipient change
reruns grounded resolution. Any attempted edit/new target invalidates previous
review, including failures. The model has no send tool; ENVOYER sends the stored,
reviewed MIME without another generation call. Uncertain and duplicate sends retain
the same protections as replies. Reply selection remains required for REPONDRE only.

### Deploy Stage 1 after publication

Stage 1 uses the existing Gmail pair of scopes and existing encrypted credentials.
**No new Google consent/enrollment, dependency install or root-helper migration is
needed.** These commands apply once the local changes have been explicitly published
to develop; do not expect unpublished commits to appear in a normal pull:

```sh
cd /home/minitel/minitel-gpt
git status --short
git pull --ff-only origin develop
sudo systemctl restart admin-ui minitel-chatgpt
```

### Owner-controlled live acceptance before Stage 2

1. Use a second account/address you control. Exchange a small message with the
   enrolled account so that the address appears among the recent 50 messages.
2. In **Paramètres → Gmail**, leave no selected message and request a NEW message
   with ECRIRE or natural language. Verify the displayed full recipient and fresh
   subject/body. Try an ambiguous name and resolve it without losing instructions.
3. Change the body/subject and, if practical, the recipient to another controlled
   correspondent. Confirm old review cannot send; reread all pages. Cancel one draft,
   then make another and explicitly ENVOYER once after review.
4. Verify receipt, correct recipient/body, exact Minitel signature and one sent copy.
   Inspect the received original source: fresh Message-ID/Date and no In-Reply-To or
   References. Offline tests check omission of threadId in the request; Gmail's UI
   grouping alone does not establish our deliberate threading behavior.
5. Verify duplicate ENVOYER does not resend. Check that existing replies still target
   the selected Reply-To/From and retain their reply headers. Report success/failures
   without sharing private contents or credentials. When the physical terminal is
   available, repeat composition, clarification, pagination, review and reset there.

**Stage 2 — saved Google Contacts is deliberately gated on that live Stage 1 exchange.**
It is not implemented or enabled by Stage 1. Do not add contacts.readonly or
contacts.other.readonly yet. Stage 2 will require People API, optional Contacts
scope support and backward-compatible encrypted re-enrollment/helper migration;
those instructions will accompany that implementation. The current two-scope
validator remains unchanged and would reject extra scopes. Other Contacts is a
separate later opt-in, not part of saved Contacts.

References: [Gmail list/search](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list),
[metadata fields](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get),
[send message resource](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send).

## Reconnect, diagnostics and disabling

The optional startup [memory selector](MEMORY.md) also selects the separate
plaintext conversation archive. Visible Gmail requests/results can appear there;
mailbox caches, draft internals, OAuth credentials and API payloads are not archived.
Memory records are deliberately saved only, and are not automatically added to
outgoing messages. Gmail's encrypted enrollment and send/review gate are unchanged.

`reconnect_required` means refresh authorization expired/revoked or service
credentials are absent. Use the admin console's Connect/Reauthorize then Apply
flow above, or repeat desktop enrollment and `install_gmail.sh --activate`
to restart **both** services with the replaced encrypted credential. No account
password or token is entered in the admin/chat. OAuth errors are sanitized rather
than logging token response bodies. Never run token-dump commands or copy private
configuration into tickets/logs; mail body/state is not in cookies or public LLM history.

For service setup failures use `systemctl status admin-ui minitel-chatgpt` and
`journalctl -u admin-ui -u minitel-chatgpt`; ordinary application logs remain
`logs/admin.log` and `logs/chatgpt.log`. Keep the admin endpoint on your trusted
network/SSH tunnel and use your configured private ADMIN_PASSWORD.

To disable this optional integration without changing PRONOTE's drop-in:

```sh
sudo rm /etc/systemd/system/admin-ui.service.d/gmail.conf
sudo rm /etc/systemd/system/minitel-chatgpt.service.d/gmail.conf
sudo systemctl daemon-reload
sudo systemctl restart admin-ui minitel-chatgpt
```

Leave `GMAIL_ENABLED` absent/false in `.env`. Revoke this app in your Google
account if disconnecting permanently; removing local ciphertext alone does not
revoke Google's authorization. Never commit OAuth configuration, credentials or
host keys. Default auth locations are all outside Git.

Offline verification (use a development checkout; see [TESTING.md](TESTING.md)):

```sh
uv run --locked --all-extras pytest -v
bash -n scripts/install_gmail.sh
```
