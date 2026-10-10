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

The first change replaces the default birthday/1980s personality with this
character through the existing preset configuration. Other presets and the
upstream application remain available. No personality harness, idle scenes,
additional animation, tools or new dependencies are introduced at this stage.

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
  messages and optional fictional idle scenes in later iterations.
- Preserve useful answers, uncertainty, technical details and honest real status.
- Explore narrowly scoped useful tools, such as search or printing, independently
  of the character's imaginary capabilities.
- Keep Python dependencies and resource use modest, adopt an isolated `uv`
  environment, and use Ruff plus proportionate integration/regression checks.

These are plans, not implemented features. Fictional hacking and telemetry never
grant real permissions or establish real access. The current preset is a simple
single-prompt implementation; it does not yet provide structural separation of
factual reasoning and presentation.

Development takes place on `develop`. Stable changes reach `main` after physical
Minitel validation. Valuable upstream fixes and improvements will be considered
as the fork evolves.
