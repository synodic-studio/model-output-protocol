# ADR-0001 — Detector taxonomy & rule composition

- **Status:** Accepted — implemented on `develop` (commit `51d497a`, 2026-07-06)
- **Date:** 2026-07-06
- **Context source:** `../superpowers/specs/2026-07-05-rule-mechanics-decisions.md`

## Context

The original rule schema used `detector: llm | deterministic | regex` with a
second `parameters.type` level (`regex` / `word_count` / `builtin_lint`). It was
clunky and had two ways to say "deterministic." A grilling session also
explored a heavier composition model (Vale-style ordered `rulesets:`, `@import`
reuse, walk-up proximity precedence, a `.mop/config.yml`). That was more
machinery than the problem needed and slowed shipping.

## Decision

**Detectors are exactly three:**
- `llm` — model judges it (carries `parameters.prompt`).
- `regex` — declarative pattern match (carries `parameters.patterns`).
- `script` — the catch-all for anything beyond regex. Dispatches two ways:
  `parameters.command` → external subprocess (message on **stdin**, exit `0` =
  pass, non-zero = fires); otherwise an in-process check registered by rule
  name via `register_builtin_lint` (MOP's bundled checks keep this fast path —
  no process spawn).

**Composition is two flat layers, no magic:**
- Optional **built-ins** (base) + one local **`.mop/`** layer on top. Local
  overrides a same-name built-in; `active: false` silences one.
- One knob per rule: `is_active`, default `true`.
- **Dropped, not deferred:** `imports:`, walk-up/proximity stacking,
  `rulesets:`, and any `.mop/config.yml`. The only local surface is a `.mop/`
  folder of rule YAML.

## Consequences

- Simpler mental model; `regex` is fully declarative, `script` covers the rest.
- **Security:** a `script` command runs code from the user's own repo `.mop/`
  — same trust model as a git pre-commit hook. MOP does **not** execute inline
  code strings from YAML. Running `mop check` in an untrusted clone executes
  that clone's scripts (documented in `--help`); accepted, not blocked.
- `word_count` / `builtin_lint` parameter-types are retired; `register_builtin_lint`
  is **kept** (converting bundled checks to subprocesses would add a process
  spawn to the hot path for no user benefit).
- A future `terms` allow/deny wordlist detector may be added as sugar over
  `script`; not now.
