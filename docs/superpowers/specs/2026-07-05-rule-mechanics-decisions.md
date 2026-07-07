# MOP rule mechanics — decisions & status (2026-07-05, simplified 2026-07-06)

Status: **IMPLEMENTED on `develop` (2026-07-06).** See
`2026-07-06-rule-mechanics-impl-spec.md` for the shipped task breakdown and
commit refs. Captures a grilling session that
refined the composition model (from earlier Vale-style research, since removed
as superseded — the decisions here and in `docs/adr/` are the record)
and settled the evaluation flow. A second pass on 2026-07-06 deliberately
*cut scope* to ship sooner (see "Scope cut" below). Extends the CLI/core work
in `2026-07-03-mop-cli-core-design.md`. This is the durable decisions record;
the build-ready task breakdown lives in `2026-07-06-rule-mechanics-impl-spec.md`.

## Where the shipped code stands (baseline)

The `2026-07-03` CLI work is complete and on `develop`: `mop check` /
`mop rules`, litellm evaluator, packaged builtins, `.mop/` discovery,
hardcoded two-layer `[builtin, local]` merge. Detectors today:
`detector: llm | deterministic | regex` with a second `parameters.type`
level. Deterministic matches are **advisory hints** to the LLM, not
authoritative.

The decisions below change three baselines — detector taxonomy (D1),
deterministic authority (D3), and the verdict shape (D4) — and **flip the
builtin default from on to opt-in** (D6).

## Scope cut (2026-07-06) — what we are NOT building

To reach a shippable v1, these earlier ideas are **dropped**, not deferred
with ceremony. Nothing in v1 references them; revisit only if a real need
appears:

- **`imports:`** — no rule-file reuse-by-reference.
- **Walk-up stacking / proximity precedence** — no collecting multiple
  `.mop/` levels, no "nearest wins" ordering.
- **`rulesets:` central ordering file** — gone.
- **`.mop/config.yml`** — no config file at all in v1. The only local
  surface is a `.mop/` folder of rule YAML. The one wholesale switch
  (builtins on/off) lives in the CLI, not a file (D6).

Result: composition is just **two flat layers** — optional builtins (opt-in)
under your local `.mop/` rules — which is essentially the already-shipped
merge, minus the un-built magic.

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

**In scope for v1.** Possible future sugar (not now): a first-class
**`terms`** allow/deny wordlist detector (Vale-vocab-style) — stays a
`script` until proven worth promoting.

### D2 — Composition: two flat layers, no magic

- Rules are **defined in place** in `.mop/*.yml` and auto-discovered. No
  central registration step, no imports, no tree walking.
- One knob per rule: **`is_active`, default true.**
- **Layering is fixed and flat:** optional builtins (base) → local `.mop/`
  rules (on top). Local overrides a same-name builtin. That's the whole
  precedence story.
- **Overrides are just rules.** To silence or replace a builtin, drop a
  **same-name** entry in `.mop/` — `active: false` to silence, or new
  content to replace.

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
  collapses accept/rewrite/reject into one graceful shape, and **is also
  our severity model** (S1 below — no separate `severity` field):
  - empty `unresolved`, text unchanged → **accept**
  - empty `unresolved`, text changed → **clean rewrite**
  - non-empty `unresolved`, text changed → **partial** (fixed what it could)
  - non-empty `unresolved`, text unchanged → **reject** (couldn't fix anything)
- **Field naming (was Q4):** the still-broken list is named **`unresolved`**.
  The **`verdict` label is derived**, not stored — computed from
  (text-changed?, `unresolved`-empty?) so the label can never disagree
  with the data. It is still printed for caller convenience.
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

### D6 — Builtins are opt-in; empty means warn, not silent

MOP imposes nothing by default and refuses to pretend it acted when it
has nothing to enforce.

- **Builtins off by default.** `mop check` runs **only** local `.mop/`
  rules. `mop check --builtins` loads the packaged rules as a base under
  the local rules.
- **Empty case → warn + accept (exit 0).** No `--builtins` *and* no local
  rules found = nothing to enforce: print `no active rules — MOP enforced
  nothing` to stderr and let the message through (there was genuinely
  nothing to reject). It does **not** hard-fail a fresh/empty repo. A
  future `--require-rules` flag can make empty fatal if a caller wants it.
- **Discoverable in `--help`:** both `--builtins` and the empty-warning
  behavior are documented there.

### S1 — Severity: intentionally none

No `severity: warning | error` field. The response *type* already encodes
it: `accept` = nothing flagged, `rewrite` = fixable, `reject` = can't fix.
An accept is **silent** (no "FYI" notes channel in v1) — if something is
worth saying, it's worth rewriting; if not, don't nag.

## Worked example (validates against the current `mop/rules.py` schema)

Builtin ships two rules; a project adds local rules and overrides one builtin.
No imports, no config file.

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

Resolved active set inside `<repo>`:
- `mop check` → `label-options-for-reference` only (builtins not loaded).
- `mop check --builtins` → `no-leaked-secrets` + `label-options-for-reference`;
  `no-fabricated-attribution` silenced by the local same-name `active: false`.

Trace — `mop check --builtins`, agent tries to send:
> "Got it, I'll wire it up. Key is SECRET-abc123 — ping me."

1. Deterministic (no LLM): `no-leaked-secrets` matches → reject.
2. One LLM call judges `label-options-for-reference` (no options → no
   fire), told about the deterministic hit so its rewrite fixes it.
3. Combined verdict (label derived, `unresolved` empty → clean rewrite):
   ```json
   {"verdict": "rewritten",
    "rewritten": "Got it, I'll wire it up. I've got the API key. Ping me.",
    "unresolved": []}
   ```

## Open questions

**None design-level.** S1, Q1 (imports) and Q2 (walk-up) are resolved by the
scope cut; Q3 (`terms`) is future sugar; Q4 is resolved in D4. Remaining
uncertainty is empirical — whether the model *works* in practice — which
only a build answers. Confirm the exact DeepSeek litellm id at build time (D5).

## Next step

Build. Task breakdown in `2026-07-06-rule-mechanics-impl-spec.md`:
D1 detector flatten → D3 authoritative → D4 verdict shape → D5 alias →
D6 `--builtins` + empty-warn.
