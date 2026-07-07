# Rule Mining Notes — Initial Draft (2026-05-03)

Mined from a corpus of ~63 personal feedback memories from one heavy LLM-agent
user. Generalizing to MOP core required heavy filtering — most of the corpus
was project-specific or personal-style, not universal.

## Litmus test for "is this a MOP rule or lint?"

A **rule** (LLM-evaluated directive) or **lint** (deterministic pattern
check) belongs in MOP **if and only if the violation is detectable in the
message text the agent emits.** If the entry is really about *what the agent
did with tools* (ran tests, verified files, hit endpoints), MOP can only
catch the lie about it — not the actual gap. That belongs in the hook layer.

### Layered model

- **MOP** = output filter. Catches what's IN the message.
- **Stop hooks** = action filter. Block "declare done" until prerequisites met.
- **Tool wrappers / harness** = enforce tool-use patterns.
- **Pre-push hooks** = quality gates before code leaves repo.
- **Skills / agent identity** = recipes + role definition. Workflow-specific
  text rules belong here too (e.g., reply-blocks-in-Cobalt-docs).

## Rule buckets

> **Update (2026-07-07):** the enduring-vs-transitional split was dropped as a
> category — a future consideration, not a current organizing axis. Rules live
> in flat files regardless of whether they patch model-era behavior. The triage
> below is kept as historical mining analysis; the "enduring/transitional"
> column labels are just how they were sorted at draft time.

## Triage breakdown

| Category | Count | Outcome |
|---|---|---|
| Behavior rules | 3 | → `core-behavior.yml` |
| Voice rules | 3 | → `core-voice.yml` |
| Behavior rules | 3 | → `behavior.yml` |
| Voice rules | 4 | → `voice.yml` |
| Overlay candidates | 1 | → `overlay-one-thing-at-a-time.yml` |
| Hook layer (not MOP) | ~5 | tracked separately for future hook design |
| Skill layer (not MOP) | ~3 | belongs in workflow-specific skills |
| Project/tooling-specific | ~30 | NOT in MOP — agent identity / config |
| Misc | ~3 | NOT in MOP |

## False-positive flags to review rule-by-rule

These rules need real tests before promotion past Audit mode.

### `length-cap-chat` (200 word limit)
- **Risk:** Some users want detailed explanations in chat. 200 is arbitrary.
- **Mitigation:** Configurable threshold per channel.

### `no-cheerleading-phrases`
- **Risk:** Some users want warm acknowledgment. Regex catches sincere "great catch."
- **Mitigation:** Severity may need to be `warn` not `violation` in default config.

### `no-completed-without-findings-pings`
- **Risk:** Some users want positive confirmation for trust (security scans).
- **Mitigation:** Per-task config — silent for routine, confirming for high-stakes.

### `no-permission-asking-for-doable-work`
- **Risk:** Asking permission is *correct* for irreversible actions or when user is new.
- **Mitigation:** Strong carve-outs in detector prompt (deploy, delete, novel work).

### `links-for-references`
- **Risk:** Over-linking is its own noise. Internal references don't need URLs.
- **Mitigation:** Detector scoped to *external* references only.

### `acknowledgment-without-action`
- **Risk:** "Noted" can be appropriate when user shared context (no action expected).
- **Mitigation:** Detector distinguishes "user requested action" from "user shared context."

### `verify-before-asserting`
- **Risk:** Over-verification is annoying — agents shouldn't grep before every claim.
- **Mitigation:** Scope to file/state/external-system claims, not general code reasoning.

### `no-empty-future-commitments`
- **Risk:** "Plan: X, Y, Z" framing must be allowed.
- **Mitigation:** Detector explicitly accepts plan-shaped framing.

### Roles (removed)
- The `role-*.yml` stubs were deleted — the per-persona layer was dropped, since
  composition treats roles as ordinary rules, not a special axis.

## Rules considered and routed elsewhere

These rules are valid and important but do NOT belong in MOP.

- `strict_typing_tests` → hook layer (stop hook: can't claim done without test run)
- `partial-completion / always_deploy` → hook layer (can't claim deployed without push)
- `reply-blocks-in-Cobalt-docs` → workflow skill (Cobalt/Obsidian writing)
- `no-overengineering-script-suggestions` → too narrow; replaced by `no-empty-future-commitments`
- All Cobalt/Obsidian/Endurain/Hugo/Cloudflare specifics → agent identity layer
- All git/branch/repo conventions → agent identity layer
- `self_healing_principle` → engineering principle, not output rule
- `hooks_warn_not_block` → hook authoring convention, not output rule
