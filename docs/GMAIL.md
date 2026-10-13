# Gmail prototype — 3615 IA

Optional single-account Gmail reading and **replies only**, shared by the authenticated
admin console `/gmail` and the physical Minitel. Disabled until encrypted enrollment
is installed and both services are activated. Existing web search, PRONOTE, boot,
personality and serial/navigation paths remain available.

## Validation status

Implementation passed 121 offline unit, mocked REST, Flask and simulated UART tests.
On **13 October 2026 (Europe/Paris)**, the owner confirmed Google Production setup,
encrypted enrollment on the actual Pi, live reading and access-token renewal. The
read-only probe retrieved ten metadata entries and a 1,190-character selected body,
then reported `forced_renewal: true` and exited successfully. Activation left both
services `active`; inbox listing and selected-message reading worked, and the owner
confirmed authorization survived a Pi reboot without another login.

The chat does not establish which interface was used for every interaction; detailed
admin-console and physical Minitel acceptance remain separate checks. Draft/revise/
cancel/send, receipt, threading and signature verification are **not yet confirmed**.
Automated tests use fake clients and never send real email. No private email contents
or account identifiers are recorded in this guide.

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
   identity scopes. Gmail drafts are not used; the outgoing draft lives in Python RAM.
4. Under **Clients**, create a **Desktop app** OAuth client and download its JSON.
   Do not create a Web application or TV/limited-input client. Desktop loopback
   callbacks are supported; no public redirect domain, Google password, app password
   or pasted authorization code is needed.
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
   The owner's public information pages are now online; use these Branding values:

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

References checked 12 October 2026:
[Audience and seven-day expiry](https://support.google.com/cloud/answer/15549945),
[personal-use verification exceptions](https://support.google.com/cloud/answer/13464323),
[desktop OAuth/PKCE/loopback](https://developers.google.com/identity/protocols/oauth2/native-app),
[device-flow allowed scopes](https://developers.google.com/identity/protocols/oauth2/limited-input-device#allowedscopes).

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
The install script checks the **actual Pi's OS, Python >=3.10, running systemd and
systemd-creds >=250** before proceeding. Raspberry Pi OS Bookworm's Python 3.11 /
systemd 252 meets the version requirements; an older image may need an OS upgrade.
It also requires Debian's `python3-cryptography >=38`, avoiding a Rust build on
ARMv6. The Google libraries use the existing shared `.venv` so PRONOTE remains installed.
Unsupported encrypted credentials stop setup; **there is no plaintext fallback**.
The first install prepares dependencies/helper only and does not restart services.

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
  /home/minitel/minitel-gpt/.venv/bin/python scripts/gmail_probe.py --renew
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
numbered inbox list. **`LIRE 1`** opens the first listed message. A long message may
show a labelled summary; **`TEXTE`** gives its full cached readable body. Use
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
is ambiguous, choose a displayed number. Selected messages longer than 800
characters default to an explicitly labelled AI summary; `TEXTE` gives the body.
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

## 3. Reply, revise, cancel, review and explicitly send

Select an owner-controlled test message, then:

1. `REPONDRE remercier et confirmer mardi` generates **one local draft**. The
   recipient comes from the selected message's single valid Reply-To, or From
   only when Reply-To is absent. Multiple valid choices require `DESTINATAIRE n`;
   malformed/duplicate headers block sending and ask for a different valid source.
2. Read the entire recipient address, subject and outgoing body through **SUITE**.
   Python appends the exact outgoing signature:
   **Envoyé depuis mon Minitel — 3615 IA**.
   The terminal displays `Envoye depuis mon Minitel - 3615 IA`; the actual MIME
   body retains the Unicode signature. Neither persona nor interface instructions
   are included in the outgoing email.
3. `MODIFIER` asks for changes; `MODIFIER rendre plus court` or conversational
   revisions create a new revision. Any attempted change invalidates review.
   Reread every page of the new draft. `ANNULER` discards it and the mail session.
4. Type **`ENVOYER`** and press the function key **ENVOI**, or use the admin
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
resolved by automatically retrying the send. Drafts are intentionally lost on
restart, reset and logout. Reply-all, CC/BCC, aliases, attachments, new composition,
full-thread context and multiple accounts are not implemented.

Owner-controlled exchange acceptance: send a small inbound message from another
account you own; read it; draft and revise; cancel one draft; create another;
verify sending is refused before full review; review and explicitly ENVOYER once.
Verify receipt, one copy in Sent, exact signature, actual recipient/subject/body,
Gmail thread placement, In-Reply-To and References in the received original source.
Verify duplicate confirmation does not resend. Repeat the workflow on the physical
Minitel. Real send testing is manual, never part of the automated suite.

## Reconnect, diagnostics and disabling

`reconnect_required` means refresh authorization expired/revoked or service
credentials are absent. Repeat desktop enrollment and `install_gmail.sh --activate`
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

Offline verification (use the existing shared venv with optional requirements):

```sh
.venv/bin/python -m unittest discover -s tests -v
bash -n scripts/install_gmail.sh
```
