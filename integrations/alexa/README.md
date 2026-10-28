# Alexa voice gateway — development prototype

This is **not enabled by installation**. It is a separate, read-only conversational
gateway: no terminal echo, admin API, Gmail, PRONOTE, shell, or tool invocation.
It calls Mistral directly with the existing `MISTRAL_KEY` and `MISTRAL_MODEL`
from the Pi's `.env`. It does not use the full interactive Minitel session.

## Local Pi test (no public access)

On the Pi, in the `minitel-gpt` checkout:

```bash
git fetch origin
git switch -c feature/alexa-voice-api --track origin/feature/alexa-voice-api
# Or fetch the branch into a separate worktree if you want to keep develop running.
python3 -c 'import secrets; print(secrets.token_urlsafe(36))'
```

Put the generated token in `minitel-gpt/.env` as `ALEXA_API_TOKEN=...`.
**Do not commit it or paste it into chat.** Keep `MISTRAL_KEY` configured.
Use the project's installed Python environment (which already has Flask,
requests and python-dotenv):

```bash
uv run python integrations/alexa/voice_api.py
```

In a second shell:

```bash
set -a; . ./.env; set +a
curl -sS -X POST http://127.0.0.1:8765/v1/ask \
  -H "Authorization: Bearer $ALEXA_API_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"question":"Pourquoi le ciel est-il bleu ?"}'
```

Expected: `{"answer":"..."}`. Missing/wrong token: HTTP 401.
The server binds **only to 127.0.0.1** and is not accessible from the tailnet
or public internet by default.

Run focused tests with `uv run pytest tests/test_alexa_voice_api.py -q`.

## Alexa side

The separate `perjules/minitel-alexa` repository, branch
`feature/alexa-integration`, expects two Lambda environment variables:

- `MINITEL_VOICE_URL`: exact HTTPS URL ending in `/v1/ask`
- `MINITEL_VOICE_TOKEN`: same long random token as `ALEXA_API_TOKEN`

**Before enabling a public endpoint**, confirm the Alexa-hosted runtime offers
a secure way to configure these values. Never commit the token to either Git
repository, interaction model, or a source file. If secure secret configuration
is not available in Alexa-hosted Lambda, stop and choose a suitable secret
storage/deployment method instead. With no configuration, the skill returns
a French 'not configured' response.

## Public connectivity — NOT activated

Only after verifying authentication and Lambda secret handling, explicitly
enable Tailscale Funnel for this **dedicated port 8765**, not the admin console
or an existing Pi service. Confirm the exact HTTPS URL and Funnel configuration
using the installed Tailscale version; do not expose a wildcard proxy or
disable authentication. Funnel is public even though Tailscale SSH/Serve is private.

The Pi endpoint accepts a single short question, enforces a 4 KiB request
limit, bearer authentication, serial execution, a 2-second request interval,
and a short upstream timeout. The Lambda client times out after 6 seconds.
No credentials or question bodies are intentionally logged.

This is a prototype, not a full production API. In particular, it does not
yet maintain multi-turn LLM conversation history, call Minitel tools, or support
public internet web search.
