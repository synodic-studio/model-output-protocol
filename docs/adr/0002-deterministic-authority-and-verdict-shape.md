# ADR-0002 — Deterministic authority & best-effort-rewrite verdict

- **Status:** Accepted — implemented on `develop` (commit `0e6f505`, 2026-07-06)
- **Date:** 2026-07-06
- **Context source:** `../superpowers/specs/2026-07-05-rule-mechanics-decisions.md`

## Context

Originally a deterministic (regex) match was an **advisory hint** fed to the
LLM, which still made the final accept/reject call. That means a coin-flip LLM
could rationalize a matched secret-leak pattern as "example text" and accept it.
A deterministic check must not be overridable by a probabilistic judge.

Separately, the old verdict was a hard three-way `accept | rewrite | reject`
emitted by the LLM. When the LLM couldn't fix everything it returned a bare
rejection with no rewrite — forcing the expensive frontier model to redo work
the cheap evaluator could have done.

## Decision

**Deterministic rules are authoritative.** In the CLI/core path, `regex`/`script`
rules run first; a match is a hard violation. Then **one** LLM call judges the
`llm` rules and produces a best-effort rewrite that also removes the
deterministic violations. Deterministic rules are **re-checked against the
rewrite**, so a fix only counts if it actually cleared the pattern.

**The verdict is derived, not declared.** The LLM returns only
`{rewritten, unresolved}` (no `action` field). The disposition is computed from
two facts — did the text change? is `unresolved` empty? — so the label can never
disagree with the data:

| text changed | unresolved empty | verdict | exit |
|---|---|---|---|
| no | yes | accepted | 0 |
| yes | yes | rewritten (clean) | 1 |
| yes | no | rewritten (partial — carries residual) | 1 |
| no | no | rejected | 2 |

The residual list is named **`unresolved`** ("here's what I fixed, the rest is
up to you"). `--no-rewrite` runs judgement but never applies a rewrite
(verdict-only, for CI/lint callers).

## Consequences

- Deterministic guarantees hold regardless of the model. Verified offline: a
  `regex` rule rejects (exit 2) with **zero LLM calls**.
- The cheap evaluator does the rewrite, not the frontier model.
- **Scope (v1):** only the CLI/core path is deterministic-authoritative. The
  MCP gate (`protocol.py`) still treats deterministic hits as advisory — a
  named, temporary inconsistency to reconcile when the gate is next touched.
  Only the shared type field rename (`violations` → `unresolved`) landed there.
- A purely LLM-side partial (model fixes rule A but not rule B, both `llm`) is
  representable (`Rewritten.unresolved`) and needs no further code — it depends
  only on the model returning `unresolved` alongside `rewritten`.
