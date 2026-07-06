# MOP rule mechanics — decisions & status (2026-07-05)

Status: **design decisions, not yet implemented.** Captures a grilling
session that refined the composition model from
`docs/patchbay/research-composition-model.md` and settled the evaluation
flow. Supersedes nothing that shipped; extends the CLI/core work in
`2026-07-03-mop-cli-core-design.md`. This doc is the durable record —
when implementation starts, fold it into a proper spec.

## Where the shipped code stands (baseline)

The `2026-07-03` CLI work is complete and on `develop`: `mop check` /
`mop rules`, litellm evaluator, packaged builtins, `.mop/` discovery
(stops at the *first* `.mop/` walking up), hardcoded two-layer
`[builtin, local]` merge. Detectors today: `detector: llm |
deterministic | regex` with a second `parameters.type` level. Deterministic
matches are **advisory hints** to the LLM, not authoritative.

The decisions below change three of those baselines: detector taxonomy,
multi-level proximity merge, and deterministic authority.

## Settled decisions

### D1 — Detector taxonomy: `{llm, regex, script}`

Flatten today's `detector: deterministic` + `parameters.type` into a
single detector field:

- **`llm`** — model judges it (carries a `prompt`).
- **`regex`** — declarative pattern match (carries `patterns`).
- **`script`** — arbitrary code returning pass/fail. The catch-all:
  word/length counts, high-entropy secret scans, wordlist lookups,
  JSON/schema validation, markdown/AST structure checks all collapse to
  `script`. No further deterministic kinds are needed.

Possible future sugar (not now): a first-class **`terms`** allow/deny
wordlist detector (Vale-vocab-style), because it is common and ugly as
raw regex. Stays a `script` until proven worth promoting.

### D2 — Composition: proximity, in-place, import-for-reuse

- Rules are **defined in place** in YAML and auto-discovered. No central
  registration step.
- One knob per rule: **`is_active`, default true.**
- **Precedence = proximity.** MOP walks up from the invocation directory
  collecting each `.mop/` it passes; **nearest wins, builtin is the
  floor.** (top/mid/bot invoked in bot → bot > mid > top > builtin.)
- **Overrides are just rules.** To silence or replace a shipped/inherited
  rule, drop a **same-name** entry in a closer `.mop/` — `active: false`
  to silence, or new content to replace. No separate override config
  (this is the SwiftLint "config overrides shipped defaults" behavior,
  expressed as ordinary rule files).
- **`@import` is a reuse primitive only.** An `imports:` list in a rule
  file references a shared rule file (e.g. `~/.mop/house.yml`) so it need
  not be copied. Orthogonal to precedence — NOT the ordering backbone.
- **No mandatory central ordering file.** A `rulesets:` list survives
  only as a rare escape hatch for when proximity isn't the order wanted.

### D3 — Deterministic rules are authoritative

A `regex`/`script` match **rejects on its own** — no LLM discretion
(this is the change from today's advisory behavior). Rationale: a
deterministic secret-leak check must not be overridable by a coin-flip
LLM. The single LLM call judges only the `llm`-type rules.

### D4 — One LLM call; verdict = best-effort rewrite + residual

- **Exactly one `llm` call per message, never interleaved** with
  deterministic checks. Deterministic hits are passed *into* that call as
  hard constraints, so a single rewrite repairs deterministic + llm
  violations together.
- **Verdict shape: best-effort rewrite + a list of unresolved
  violations** — "here's what I could fix, the rest is up to you." This
  collapses accept/rewrite/reject into one graceful shape:
  - empty residual, text unchanged → **accept**
  - empty residual, text changed → **clean rewrite**
  - non-empty residual, text changed → **partial** (fixed what it could)
  - non-empty residual, text unchanged → **reject** (couldn't fix anything)
- The evaluator (cheap small model, already called) does the rewrite —
  bouncing a bare rule back would force the expensive frontier model to
  redo work. **Judgement always runs; only the rewrite *output* is
  skippable**, via a `--no-rewrite` verdict-only flag for CI/lint callers.

### D5 — Model aliases

MOP defines its own `small`/`medium`/`large` litellm aliases (house
convention — pi/patchbay-voice resolve these via pi's config, not raw
litellm, so MOP must populate its own alias map). Default `small` =
**DeepSeek V4 Flash via deepseek**. Confirm the exact litellm model id at
build time.

## Worked example (validates against the current `mop/rules.py` schema)

Builtin ships two rules; a project adds local rules and imports a shared
file.

`mop/rules_builtin/core.yml`
```yaml
rules:
  - name: no-leaked-secrets        # regex → authoritative (D3)
    active: true
    detector: regex
    parameters:
      patterns: ["SECRET-[A-Za-z0-9]{6,}", "(?i)aws_secret_access_key"]
    guidance: Strip the secret. Refer to it by name.
  - name: no-fabricated-attribution  # llm
    active: true
    detector: llm
    parameters:
      prompt: |
        Does this attribute a quote/claim to a named person or source
        that wasn't in the user's input?
    guidance: Attribute only to sources in the user's input.
```

`<repo>/.mop/rules.yml`
```yaml
imports:
  - ~/.mop/house.yml               # D2: reuse by reference
rules:
  - name: label-options-for-reference   # new local rule
    detector: llm
    parameters:
      prompt: |
        Does this present 2+ options without labeling each with a
        letter or number?
    guidance: Prefix each option with a letter or number (A/B/C).
  - name: no-fabricated-attribution     # override: silence the builtin
    active: false
```

`~/.mop/house.yml`
```yaml
rules:
  - name: no-em-dash
    detector: regex
    parameters: { patterns: ["—"] }
    guidance: Replace the em dash with a comma, colon, or period.
```

Resolved active set inside `<repo>` (builtin → imported → local wins):
`no-leaked-secrets`, `no-em-dash`, `label-options-for-reference`;
`no-fabricated-attribution` silenced.

Trace — agent tries to send:
> "Got it, I'll wire it up. Key is SECRET-abc123 — ping me."

1. Deterministic (no LLM): `no-leaked-secrets` matches → reject;
   `no-em-dash` matches → reject.
2. One LLM call judges `label-options-for-reference` (no options → no
   fire), told about the two deterministic hits so its rewrite fixes them.
3. Combined verdict:
   ```json
   {"verdict": "rewritten",
    "rewritten": "Got it, I'll wire it up. I've got the API key. Ping me.",
    "unresolved": []}
   ```
   One LLM call total; deterministic rules rejected on their own; the
   rewrite repaired both.

## Open questions (not blocking; resolve before/at implementation)

- **Q1 — import precedence.** When a file both `imports:` a set and
  defines a same-named rule, which wins? Lean: the importing file's own
  rule wins over its imports (imports sit "below" the file's own entries).
- **Q2 — walk-up depth.** Collect *all* `.mop/` levels cwd→repo-root
  (implied by the top/mid/bot example), or only the nearest? Lean: all
  levels, stacked by proximity. Confirm.
- **Q3 — `terms` detector.** First-class, or stays a `script`? Lean:
  stays a `script` until a real need appears.
- **Q4 — verdict field naming.** `unresolved` vs `violations` vs
  `residual` for the still-broken list; and whether `verdict` stays an
  enum or is derived from (text-changed?, residual-empty?).

## Next step

Either finish Q1–Q4 in another short grilling pass, or promote this doc
to a full implementation spec (`2026-07-05-...` → tasks) and build.
Nothing here is committed to code yet.
