# MOP Architecture — Design Notes

Date: 2026-05-06

## System position

MOP sits between the agent and the user on the output side. It intercepts `raw_text` after a turn completes and either passes it through, rewrites it, or triggers a retry.

```
agent → [MOP eval] → user
              ↓ (reject)
         inject guidance → agent (resume same session)
```

Counterpart: **HOP** (`human-output-protocol`) sits on the input side.

---

## Verdict types

| Verdict | Action |
|---------|--------|
| Accept | Pass `raw_text` to user unchanged |
| Reject | Suppress turn, inject rule `guidance:` as new user message, retry (up to `MOP_MAX_RETRIES`) |
| Edit | Call Haiku to rewrite `raw_text`; fall back to original if rewrite fails |

Structural check always runs first: empty `raw_text` → Reject regardless of rules.

---

## Modes

| Mode | Behavior |
|------|----------|
| `passthrough` | Bypass all evaluation |
| `audit` | Evaluate after delivery (fire-and-forget thread), log violations to `MOP_LOG_PATH` |
| `enforce` | Synchronous eval before delivery; Reject/Edit applied before yielding `TurnFinal` |

Set via `MOP_MODE` env var. Default: `audit`.

---

## Rule format

```yaml
name: rule-id
detector: llm | deterministic
parameters:
  # llm:
  prompt: "Does this message...?"
  # deterministic:
  type: regex | word_count
  patterns: [...]     # for regex
  max: 200            # for word_count
severity: warn | violation
on_violation: warn | reject | edit
guidance: "..."       # injected on Reject
rationale: "..."
sunset_check: "..."   # for transitional rules only
```

Active rules in `rules/active/`. Pending (not yet activated) in `rules/pending/`.
Start with 1–2 active rules. Promote pending rules only with audit data supporting it.

---

## Channels compatibility

**Problem:** MOP buffers all `TextDelta` events until `TurnFinal` before evaluating. Channels stream partial output as it arrives. These are mutually exclusive.

**Current behavior:** `cc-sdk-mop` harness has `supports_inflight_push=False`. Channel mode bypasses MOP.

**Intended resolution (in priority order):**

1. **Audit-only in channel mode** — stream through without blocking, log violations. Enforcement only in one-shot turns. Clean, no UX impact.
2. **Streaming deterministic eval** — regex and word-count rules can fire mid-stream; fast-reject before full output lands. LLM rules remain post-hoc.
3. **Post-hoc correction push** — after TurnFinal fires a violation in channel mode, push a Haiku-rewritten correction as a follow-up message. Weird UX; reserve for explicit opt-in.

Do not buffer-then-stream in channel mode — defeats the purpose of channels.

---

## Standalone package strategy

MOP logic is currently embedded in `patchbay-relay`. Target: extract to a pure package.

```
model-output-protocol/
└── mop/
    ├── eval.py     # mop.eval(text, rules) → Verdict  (pure function)
    ├── rewrite.py  # mop.rewrite(text, guidance) → str
    ├── rules.py    # YAML loading, rule validation
    └── verdicts.py # Verdict dataclass
```

`patchbay-relay` becomes a thin adapter: calls `mop.eval(final.raw_text, rules)`, wraps result into harness retry logic.

Benefits:
- Testable without patchbay (`TurnRequest`/`TurnFinal` not needed in pure eval tests)
- Reusable in any harness or CLI wrapper
- Evals module can be used standalone for rule authoring

---

## Stop hook

`mop_stop_hook.py` (lives in `patchbay-relay/scripts/`) is a Claude Code `Stop` hook. It reads the session NDJSON transcript and blocks stopping if the last assistant turn produced no text content.

Wire in `.claude/settings.json`:
```json
{
  "hooks": {
    "Stop": [{
      "matcher": "",
      "hooks": [{"type": "command", "command": "python3 /path/to/mop_stop_hook.py"}]
    }]
  }
}
```

---

## Implementation status (2026-05-06)

- [x] Audit mode (fire-and-forget eval, violation log)
- [x] Enforce mode — Reject verdict (retry loop, guidance injection, `resume_session_id`)
- [x] Enforce mode — Edit verdict (Haiku rewrite, fallback to original)
- [x] Structural empty-message check
- [x] Stop hook script
- [x] 14 tests, all passing
- [ ] Channels compatibility (audit-only in channel mode)
- [ ] Extract to standalone package
- [ ] Streaming deterministic eval in channel mode
