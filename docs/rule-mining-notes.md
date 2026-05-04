# Rule Mining Notes — Initial Draft (2026-05-03)

Mined from a corpus of ~63 personal feedback memories from one heavy LLM-agent user
(Adrien). Generalizing to MOP core required heavy filtering — most of the corpus
was project-specific or personal-style, not universal.

## Triage breakdown

| Category | Count | Outcome |
|---|---|---|
| Universal behavior (applies to most users) | ~12 | → `core/behavior.yml` |
| Universal voice (style most users want) | ~7 | → `core/voice.yml` |
| Personal style (one user's voice) | ~5 | → `personal.yml.example` |
| Project-specific (Cobalt, Endurain, Hugo, etc.) | ~30 | NOT in MOP — these are agent-behavior config, not output-style |
| Tooling-specific (tuist, wrangler, mise) | ~6 | NOT in MOP |
| Misc (cost, hooks, calendar) | ~3 | NOT in MOP |

## False-positive risks to review

These are rules I included in core where reasonable users would disagree.
Each needs a real test before shipping in Rewrite/StrictRetry mode.

### `length-cap-chat` (200 word limit)
- **Risk:** Some users want detailed explanations in chat. 200 is arbitrary.
- **Mitigation:** Configurable threshold. Could also be channel-aware (longer for email-shaped channels, shorter for SMS-shaped).

### `no-cheerleading-phrases`
- **Risk:** Cheerleading is cultural. Some users genuinely want warm acknowledgment. The regex catches "great catch" which can be sincere.
- **Mitigation:** Make this a `warn` instead of `violation` in default config; let users promote to violation.

### `no-completed-without-findings-pings`
- **Risk:** Some users want positive confirmation ("scan ran, all clear") for trust. Removing it can feel like the agent stopped working.
- **Mitigation:** Per-task config — silent for routine scans, confirming for security/financial actions.

### `no-permission-asking-for-doable-work`
- **Risk:** Asking permission is *correct* for irreversible actions, novel work, or new users still building trust with the agent. The LLM detector needs strong examples to get this right.
- **Mitigation:** Carve-out exceptions in the prompt (deploy, delete, novel task). Strong test corpus before turning on Reject mode.

### `links-for-references`
- **Risk:** Not every reference needs a link (named functions, shared local files). Over-linking is its own noise.
- **Mitigation:** LLM detector should focus on *external* references the user can't access without a URL.

### `acknowledgment-without-action`
- **Risk:** Sometimes "noted" IS the right response (user shared context, agent doesn't need to act yet). Detector must not penalize good listening.
- **Mitigation:** Detector prompt distinguishes "user requested action" from "user shared context."

### `verify-before-asserting`
- **Risk:** Over-verification is annoying — agents shouldn't grep the repo before every claim. Detector must scope to assertions where the recall→reality gap is meaningful.
- **Mitigation:** Limit detection to claims about file/state/external-system existence, not to general code reasoning.

### Roles are stubs
- All role files contain only intent + 3 example rules. Real role enforcement needs a corpus of role-tagged feedback to mine. v0 ships them as documentation of intent.

## What did NOT make it into MOP

The following feedback patterns are project-specific or agent-behavior, not
output-style. They don't belong in MOP — they belong in the agent's own
configuration.

- All Cobalt / Obsidian path conventions
- Endurain, Hevy, Hugo, Cloudflare specifics
- Calendar, calendaring, reminder system specifics
- Branch naming, git workflow, repo conventions
- Self-healing patterns (these belong in the agent harness)
- TestFlight / fastlane / mise specifics
- Tailscale exposure, port selection, infra security

These are rich material for the **agent identity** layer (SOUL.md style files
in Fanta), not for MOP's universal rule set.
