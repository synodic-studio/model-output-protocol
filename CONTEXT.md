# Model Output Protocol (MOP)

> **Editor's note — revised 2026-07-06.** This glossary was rewritten to
> match the shipped system (see [`docs/adr/`](docs/adr/)). The evaluation
> model and terminology changed materially since the first draft — the
> specific corrections are listed under [Corrections](#corrections) at the
> bottom, newspaper-style, so the deltas are traceable rather than silently
> overwritten.

An output gate that sits between an LLM agent and its user, enforcing
communication discipline — accepting, rewriting, or rejecting messages
before they reach the human. Counterpart to **HOP** (Human Output
Protocol) on the input side.

MOP is not a competing protocol to MCP (Model Context Protocol). The
name is a playful inversion; MOP *may use* MCP to operate (e.g. via
MCP tool definitions in a host harness).

## Language

### Detector
How a rule decides whether it fires. Four kinds:
- `llm` — the evaluator model judges it (carries a `prompt`).
- `regex` — declarative pattern match (carries `patterns`).
- `script` — external command (message on stdin, exit code = verdict) or
  a bundled in-process check.
- `length` — character/word caps (`max_chars` / `max_words`).

`regex`, `script`, and `length` are **deterministic**.
_Avoid_: "lint" — a retired term (see Corrections).

### Rule
A single named check with a `detector`, evaluated against a message.
`llm` rules are judged by the evaluator model; deterministic rules fire
by pattern or computation.

### Deterministic authority
A deterministic rule that matches is an **authoritative** violation — it
stands on its own; the evaluator cannot wave it away. The single LLM call
judges only the `llm` rules. *(Changed 2026-07-06 — see Corrections.)*

### Verdict
**Derived** from two facts — did the text change? is `unresolved` empty? —
never declared by the model:
- **Accepted** — nothing changed, nothing unresolved.
- **Rewritten** — text changed; may carry an `unresolved` residual (the
  "partial" case: fixed what it could, the rest is up to the agent).
- **Rejected** — nothing was fixable; `unresolved` lists what's still
  violated.
- **AcceptedFailedOpen** — MOP's escape hatch after repeated justification
  failures (MCP-gate path only); delivers the original with a warning
  system note. **Not** an evaluator output.

### unresolved
The list of rule names a verdict left for the agent to handle — "here's
what I fixed, the rest is up to you." *(Renamed from `violations`
2026-07-06.)*

### Evaluator
The LLM callable (litellm-backed; default tier `small` =
`deepseek/deepseek-v4-flash`) that judges `llm` rules and produces the
best-effort rewrite. MOP is evaluator-agnostic — the host injects any
callable matching the `Evaluator` signature. *(Was Anthropic Haiku via
pydantic-ai — see Corrections.)*

### Justification loop / Failed-open
MCP-gate concepts (`protocol.py`): after a Rejected verdict the agent may
call `submit_justification` to argue its case; each attempt re-runs the
evaluator with the justification appended. When the budget is exhausted
(default 4), MOP **fails open** — delivers the original with a system note
and resets. These apply to the stateful MCP gate, not the stateless
`mop check` CLI.

## Testing a rule (evals)

**Dev:** "I added an `llm` rule to catch fabricated quotes. How do I test it?"

**Domain expert:** "Add a counterexample YAML in
`evals/counterexamples/voice/` — put the offending text in `text`, list the
rule in `expected_violations` (and a clean control with the rule in
`expected_clean`). Then:

- Deterministic rules: `python evals/harness.py --rule <name>` (no model
  needed).
- `llm` rules: add `--llm` (needs an API key or a local model). The harness
  evaluates the rule even if it ships `active: false`.

A rule 'fires' when the message is not accepted as-is — under the
best-effort-rewrite model a clean rewrite *counts as firing* (the rule
caught it and the evaluator fixed it)."

**Dev:** "What if a `regex` rule matches but the message is actually fine?"

**Domain expert:** "Then it rejects — deterministic rules are authoritative
now, not advisory. Write the pattern tightly, or use an `llm` rule when the
call needs judgement. The counterexample corpus is how you catch a
misfiring pattern before it ships."

## Corrections

*Deltas from the original draft, most-consequential first (2026-07-06):*

- **Deterministic checks: advisory → authoritative.** The draft said
  regex/word-count "lints" only fed *hints* and never produced a verdict.
  They are now authoritative: a match rejects on its own
  ([ADR-0002](docs/adr/0002-deterministic-authority-and-verdict-shape.md)).
- **"Lint" retired; "detector" taxonomy.** The rule/lint split is gone.
  Every entry is a rule with one of four detectors — `llm`, `regex`,
  `script`, `length`
  ([ADR-0001](docs/adr/0001-detector-taxonomy-and-composition.md)).
- **`word_count` removed; `length` added.** The old `parameters.type:
  word_count` is gone; message-length capping is now the first-class,
  per-interface-configurable `length` detector.
- **Verdict is derived, not declared; `violations` → `unresolved`.** The
  evaluator returns `{rewritten, unresolved}`; the label is computed from
  (text-changed?, unresolved-empty?). `Rewritten` can carry a residual.
- **Evaluator: Haiku/pydantic-ai → litellm/`deepseek-v4-flash`.** The
  reference evaluator is litellm-backed with MOP-owned `small`/`medium`/
  `large` tiers ([ADR-0003](docs/adr/0003-evaluator-model-tiers-and-structured-output.md)).
- **"Hint" is no longer advisory.** Deterministic hits are passed into the
  one LLM call as *hard constraints* to repair, not soft suggestions.
