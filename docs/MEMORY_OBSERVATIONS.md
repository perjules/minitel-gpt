# Memory observations, 1 November 2026

Subsequent change: the default profile is now read-only **until** the session is
authorized through password-gated [ACTIVER MEMOIRE](MEMORY.md). The investigation
below describes the prior behavior. Ordinary answers still cannot confirm writes.

Investigation of runtime attribution, false save claims on the default profile,
and an apparent MEMOIRE navigation block after the first profile-sharing release.
No actual user data or live provider was used during investigation.

## Findings and local corrections

- Shared records already retained Alice's owner ID/name while the selected
  session remained default #1. The old prompt put its only session-identity hint
  inside recalled reference material, and omitted it when no relevant memories
  were found. The anonymous default label MinitelGPT also failed to distinguish a
  visitor from the appliance. This left model interpretation under-specified;
  no programmatic switch to Alice was found. Runtime identity/capabilities now
  travel separately from reference text on ordinary model calls, with explicit
  anonymous/default and shared-owner distinctions. This is a prompt correction,
  not proof of perfect future model wording.
- The published routing recognizes literal **Souviens toi** and **Souviens-toi**:
  the default session locally refuses writes and a named session can save. That
  exact false acknowledgment was not reproduced offline. However, **Mémorise**,
  **Enregistre** and **Store** were absent from relevant routing checks and could
  bypass local memory handling, producing an ordinary answer without a write.
  Those forms now reach memory handling; imperative variants also bypass welcome.
  Ordinary generation is explicitly told it cannot confirm a write. Default
  household memory remains admin-managed/read-only at the terminal.
- MEMOIRE used the older ordinary-response pager, which ignored typed commands
  between pages, accepted only SUITE/SOMMAIRE, and showed no SUITE instruction on
  the final/single page. Its native inventory itself makes no model call. Memory
  responses now use the existing mail/document reader: numbered pages, visible
  navigation on every page, backward navigation, early command entry and SOMMAIRE.
  No serial-driver deadlock was established by the offline reproduction.

The change adds no SQL migration, account authentication or dependency. It does
not permit terminal writes to household or other users' memory. Published facts,
ordinary private data, sources, journals, integration grants and exports retain
their existing boundaries. Nothing is pushed as part of this investigation.

## Validation and physical follow-up

Synthetic regression cases exercise terminal and browser default write refusals,
named writes with welcome enabled/disabled, actual provider request payloads with
and without shared facts, session identity through the school planner/public
fallback, and the real UART conversation loop's one-page/long inventories and
early commands. Providers and UART are simulated; no paid probe is involved.

Locked all-extras sync, **1081 passing tests** (27 new regression cases), Ruff,
requirements-export freshness and whitespace checks completed successfully. Remote
develop was rechecked and had no concurrent commits at the local review point.

After publication/update, check on the physical terminal:

1. From the empty-name/default session, ask about a published Alice fact. It
   should answer about Alice without claiming Alice is the current visitor.
2. Try **SOUVIENS-TOI que …** and **MÉMORISE que …**. Default should explain
   **lecture seule**; a named profile should acknowledge a successful personal
   write, visible immediately through MEMOIRE/admin.
3. From a named profile, open MEMOIRE, navigate both ways, enter another command
   before reaching the end, and leave through physical or typed SOMMAIRE. Even a
   single-page inventory should show its page number and command/navigation footer.

An actual hard stall still requires physical evidence. If it persists, capture
the screen and approximate time together with service logs; exact request text
is needed if a literal Souviens-toi still produces a false success. Do not treat
offline navigation success as validation of the Pi/UART/Minitel link.
