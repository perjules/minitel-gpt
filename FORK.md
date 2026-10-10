# MinitelGPT: purpose of this fork

This fork builds on [jherard-fr/minitel-gpt](https://github.com/jherard-fr/minitel-gpt),
which connects an original Minitel terminal to modern AI services. See the original
[README](README.md) for the upstream application and installation instructions.

The aim is a useful conversational assistant with a coherent retro-futuristic
identity: a Minitel terminal linked to an imagined atomic-age mainframe. It combines
earnest technological optimism, deadpan machine diagnostics, cinematic hacker
jargon and occasional unsettling composure. Vintage presentation does not impose
a fictional 1989 cutoff on factual knowledge.

## Initial scope

The default birthday/1980s personality is replaced with this character through
the existing preset configuration. Other presets remain available.

The character's self-name is **Minitel 722079V**, identifying the owner's Matra
Communication Quimper terminal (model 722 079 V, hardware reference XB0006B-2).
**3615 IA** remains the service identifier. Its imagined remote infrastructure is
**ALCATEL MF-83**, with MF-83 as the short code: mothballed, long forgotten, at an
undisclosed location, yet mostly operational through persistent fallbacks. Its
resources are immense by 1970s-1980s standards and hopelessly outdated today.
This does not identify the Matra terminal as Alcatel equipment or a historical
Alcatel mainframe product.

The model internally understands that this machinery and its telemetry are fiction.
Theatrical reports remain completely earnest, without winks, joke explanations or
visible fiction labels. Factual answers, actual-status and out-of-character questions,
real errors and tool results remain truthful; fictional readings are never evidence.
This prompt policy does not provide structural separation of reasoning and presentation.

Reference: the personality workshop's updated v0.4 harness, archive version 38,
10 October 2026, specifically `data/world.json` and `prompts/personality.txt`.
The policy is adapted to the existing single chat prompt and independent boot request.

The terminal starts with its logo and a language prompt. Submit `en`/`English`
or `fr`/`French` with **ENVOI** (case-insensitive). Blank, unrecognised or timed-out
input silently selects French. A fictional mainframe startup aims for roughly one
screen and scrolls automatically when longer. **SUITE** opens the question prompt;
**SOMMAIRE** returns to language selection, and **GUIDE** retains access to administration. Boot-screen
inactivity returns to the start screen after five minutes.

The language applies to terminal messages and the model's default answer language;
function keys always retain their French names. French question/loading messages
retain the active preset's custom wording; English uses the terminal's translated
wording. Language selection and startup diagnostics are never conversation turns.
**SOMMAIRE** resets both language selection and conversation history.

After language submission, a short waiting message appears while one independent
request generates boot diagnostics, aiming for 12-18 lines without a hard line-count
limit. It uses the configured Mistral or Claude model and credentials, with an
eight-second HTTP request timeout and no
retry. The dedicated prompt is `config/boot_prompt.txt`; its nine bilingual examples
in `config/boot_examples.json` are reviewed selections from the personality lab
(i05, i09, l11, l13, i16, l25, l04, l12 and l24). These are alternate examples,
not consecutive stages to copy into one startup.

Python rejects incomplete/empty responses, control characters, Markdown and explicit
function-key instructions. It transliterates accents using the existing ASCII helper
and wraps long lines to 39 columns without truncating their text. It then adds
a blank separator and the final **SUITE** instruction itself. Boot display uses
Videotex rouleau mode (`ESC 3A 69 43`), so the ending stays visible even when the
sequence exceeds 24 rows. Page mode (`ESC 3A 6A 43`) is restored on every exit;
protocol acknowledgements are consumed rather than becoming question input.
These commands follow the [STUM protocol](https://jbellue.github.io/stum1b/);
physical-terminal scrolling still requires validation. On failure, missing
credentials/prompt files, or a search-only Linkup configuration, the existing static
21-line startup is used. A language-screen timeout also uses static text: leaving
the terminal unattended does not repeatedly spend boot-generation API calls.

The complete displayed boot sequence remains in a separate local variable for the
session. It is never added to the chat prompt or factual history. Restarting the
chat service or returning to **SOMMAIRE** discards it. A future paired shutdown may
use that same text; no shutdown generator or hardware-state parser is implemented.

Static UI/fallback text live in `config/terminal_ui.json`. Edit the prompt/examples
or JSON and restart the service to refine the performance. Generated wording is
not newly approved phrase-bank content. Checks ensure display integrity, not humour,
semantic consistency or translation quality; live review is still needed. These
diagnostics describe imaginary machinery, not actual measurements. The standalone
personality harness and idle scenarios are not integrated; no new runtime
dependencies are required.

The default lives in `config/prompts.default.json`. Upstream copies it to
`config/prompts.json` only when the latter does not exist. Updating the repository
therefore preserves an existing installation's active personality. To use the
fork's default on an existing installation, back up `config/prompts.json`, then
copy `config/prompts.default.json` to it and restart the chat service. This replaces
local preset configuration; alternatively add/select the new preset in the admin UI.

To apply only this workshop's chat-personality update while preserving the other
local presets, active selection, logo and UI wording, run from the repository:

```bash
cp -n config/prompts.json config/prompts.before-workshop.json
python3 - <<'PY'
import json
from pathlib import Path
path = Path("config/prompts.json")
local = json.loads(path.read_text(encoding="utf-8"))
defaults = json.loads(Path("config/prompts.default.json").read_text(encoding="utf-8"))
local["presets"]["mainframe"]["system"] = defaults["presets"]["mainframe"]["system"]
path.write_text(json.dumps(local, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
sudo systemctl restart minitel-chatgpt
```

The mainframe preset must exist locally; select it in administration if another
preset is active. Generated boot prompts and static headings update with the pull
and service restart independently of the locally stored chat personality.

## Planned direction

- Develop and play-test the character without requiring a physical terminal.
- Separate factual reasoning and conversation history from fictional performance.
- Add coherent startup/recovery/shutdown diagnostics, reviewed situation-dependent
  messages and optional fictional idle scenes beyond the initial generated startup.
- Preserve useful answers, uncertainty, technical details and honest real status.
- Explore narrowly scoped useful tools, such as search or printing, independently
  of the character's imaginary capabilities.
- Keep Python dependencies and resource use modest, adopt an isolated `uv`
  environment, and use Ruff plus proportionate integration/regression checks.

These further steps are plans, not implemented features. Fictional hacking and telemetry never
grant real permissions or establish real access. The current preset is a simple
single-prompt implementation; it does not yet provide structural separation of
factual reasoning and presentation.

Development takes place on `develop`. Stable changes reach `main` after physical
Minitel validation. Valuable upstream fixes and improvements will be considered
as the fork evolves.

## Startup regression checks

Using an environment with the existing runtime dependencies (`requests`,
`python-dotenv`, `pyserial`; `pyfiglet` is optional):

```bash
python3 -m unittest discover -s tests -v
```

The checks run the conversation loop with a simulated UART and mocked provider
responses, including Videotex/VT100 function keys, language defaults, the SUITE gate,
screen dimensions, variable-length boot scrolling, mode restoration and protocol
acknowledgements, session resets, factual-history isolation, completion reasons,
timeouts and fallback behavior. They make no live API calls and do not replace
live-model review or validation on the physical Minitel.
