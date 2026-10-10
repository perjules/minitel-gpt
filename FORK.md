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

The terminal starts with its logo and a language prompt. Submit `en`/`English`
or `fr`/`French` with **ENVOI** (case-insensitive). Blank, unrecognised or timed-out
input silently selects French. A static, fictional 21-line mainframe startup
then occupies one screen. **SUITE** opens the question prompt; **SOMMAIRE** returns
to language selection, and **GUIDE** retains access to administration. Boot-screen
inactivity returns to the start screen after five minutes.

The language applies to terminal messages and the model's default answer language;
function keys always retain their French names. French question/loading messages
retain the active preset's custom wording; English uses the terminal's translated
wording. Language selection and startup diagnostics are never conversation turns.
**SOMMAIRE** resets both language selection and conversation history.

Static UI and boot text live in `config/terminal_ui.json`. Edit that JSON and restart
the chat service to change the text. These diagnostics describe imaginary machinery,
not actual measurements. The standalone personality harness and idle scenarios are
not integrated; no new runtime dependencies are required.

The default lives in `config/prompts.default.json`. Upstream copies it to
`config/prompts.json` only when the latter does not exist. Updating the repository
therefore preserves an existing installation's active personality. To use the
fork's default on an existing installation, back up `config/prompts.json`, then
copy `config/prompts.default.json` to it and restart the chat service. This replaces
local preset configuration; alternatively add/select the new preset in the admin UI.

## Planned direction

- Develop and play-test the character without requiring a physical terminal.
- Separate factual reasoning and conversation history from fictional performance.
- Add coherent startup/recovery/shutdown diagnostics, reviewed situation-dependent
  messages and optional fictional idle scenes beyond the initial static startup.
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

The checks run the conversation loop with a simulated UART and model, including
Videotex/VT100 function keys, language defaults, the SUITE gate, screen dimensions,
session resets and factual-history isolation. They make no live API calls and
do not replace validation on the physical Minitel.
