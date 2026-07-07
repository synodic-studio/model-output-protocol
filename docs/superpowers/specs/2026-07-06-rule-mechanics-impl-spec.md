# MOP rule mechanics — implementation spec (2026-07-06)

Build-ready task breakdown for the decisions in
`2026-07-05-rule-mechanics-decisions.md`. Scope is deliberately minimal
(see that doc's "Scope cut"): two flat layers, opt-in builtins, no imports,
no config file, no tree walking.

Baseline is `develop` @ the shipped `2026-07-03` CLI/core work. Symbols
referenced below are real: `mop/rules.py` (`Rule`, `load_rules`,
`merge_rules`, `_rule_matches`, `register_builtin_lint`), `mop/types.py`
(`EvalLLMResponse`, `Verdict`, `verdict_from_eval_response`),
`mop/evaluators.py` (`resolve_model`, `_build_query`,
`build_litellm_evaluator`), and the `mop check` CLI.

## Design note settled at spec time: what `script` means (D1)

`script` detectors are **Python checks registered in-process by name**,
exactly like today's `register_builtin_lint(name, guidance, check)` — YAML
references them by rule name, it does **not** carry inline code. Rationale:
executing arbitrary code strings pulled from a `.mop/*.yml` file is a code
-injection surface we will not ship in v1. If user-authored scripts are
ever wanted, that's a separate, sandboxed feature. So `regex` is fully
declarative-in-YAML; `script` is "reference a registered check." This is
a small narrowing of D1, not a new decision.

## Tasks

### T1 — Flatten detector taxonomy to `{llm, regex, script}` (D1)

- `Rule.detector` becomes one of `"llm" | "regex" | "script"`. Drop the
  `parameters.type` second level and the legacy `"deterministic"` value.
- `regex` rules carry `parameters.patterns: [str, ...]`.
- `script` rules resolve their check from the `register_builtin_lint`
  registry by `rule.name` (rename the registry concept to "script checks"
  but keep the mechanism). Migrate `word_count` etc. to registered scripts.
- Update `_rule_matches` to switch on the flat `detector`. Update packaged
  `mop/rules_builtin/*.yml` to the new shape.
- **Acceptance:** every packaged rule loads under the new schema; a regex
  rule and a script rule each fire correctly in `_rule_matches`; no
  reference to `"deterministic"` or `parameters.type` remains.

### T2 — Deterministic rules are authoritative (D3)

- Split evaluation into two phases in the CLI/core path:
  1. **Deterministic phase** (no LLM): run every active `regex`/`script`
     rule via `_rule_matches`. Each match is a **hard violation**.
  2. **LLM phase:** one call judging only `llm`-type rules.
- Deterministic matches are no longer advisory hints — they stand on their
  own regardless of what the LLM says. Rename `collect_regex_hints` /
  `collect_lint_hints` usage accordingly (these now collect *authoritative*
  hits, not hints).
- **Acceptance:** a message matching a `regex` rule is never `accepted`,
  even if the LLM would accept it; with zero `llm` rules active, a
  deterministic match alone produces a reject/rewrite verdict with no LLM
  call needed for the decision (the LLM may still be called once to
  *rewrite* — see T3).

### T3 — Verdict shape: best-effort rewrite + `unresolved`, derived label (D4)

- **One LLM call**, given: the active `llm` rules AND the deterministic
  hard-violation names as constraints, asked to return a best-effort
  rewrite plus a list of what it could **not** fix.
- Rename the residual field `violations` → **`unresolved`** across
  `EvalLLMResponse`, `Rejected.serialize`, `verdict_from_eval_response`,
  `_build_query`'s JSON contract, and the CLI JSON output. (Breaking wire
  change — this is a pre-1.0 protocol, acceptable.)
- **Derive the verdict label** from (text-changed?, `unresolved`-empty?)
  rather than trusting an LLM-emitted `action`:
  - unchanged + empty → `accepted`
  - changed + empty → `rewritten`
  - changed + non-empty → `rewritten` (partial; `unresolved` populated)
  - unchanged + non-empty → `rejected`
  Still emit the label for callers; it is computed, not stored.
- `--no-rewrite` flag: run judgement, skip emitting `rewritten` (verdict-
  only for CI/lint callers). Judgement always runs.
- **Acceptance:** the four (changed × empty) combinations map to the right
  label; deterministic + llm violations are repaired in a single rewrite;
  `--no-rewrite` returns a label + `unresolved` with no `rewritten` body.

### T4 — MOP-owned model aliases (D5)

- Add a `small`/`medium`/`large` → litellm-model map that MOP populates
  itself (do not rely on pi's config). `small` = DeepSeek V4 Flash via
  deepseek — **confirm the exact litellm model id before merging.**
- Extend `resolve_model` precedence so a bare `small`/`medium`/`large`
  (from `--model` or `MOP_EVALUATOR_MODEL`) resolves through MOP's map
  before falling through to a raw `provider/model` string. Keep the
  existing arg > env > legacy > default order.
- **Acceptance:** `resolve_model("small")` returns the DeepSeek V4 Flash
  litellm id; a raw `provider/model` string still passes through
  unchanged; `DEFAULT_MODEL` updated to the `small` target.

### T5 — `--builtins` opt-in + empty-warn + `--help` (D6)

- Builtins **off by default**. `mop check` loads only local `.mop/` rules.
  `mop check --builtins` merges packaged rules as the base under local
  (`merge_rules(builtins, local)`).
- **Empty case:** no `--builtins` and no local `.mop/` rules → print
  `no active rules — MOP enforced nothing` to **stderr**, deliver the
  message unchanged, **exit 0**. Do not hard-fail.
- Document `--builtins` and the empty-warning behavior in `--help`.
- (Future, not v1: `--require-rules` to make the empty case fatal.)
- **Acceptance:** `mop check` on a repo with no `.mop/` warns to stderr and
  exits 0; `--builtins` loads packaged rules; a local same-name
  `active: false` still silences a builtin when `--builtins` is on;
  `mop check --help` shows both.

## Suggested order & review

Build T1 → T2 → T3 → T4 → T5; T3 depends on T1/T2, T5 depends on T1.
Each task ships as its own commit on `develop` with tests green, pushed as
it lands (house cadence). After T5, run the whole-branch review before
declaring v1.

## Known residual risk

The whole point of building now is empirical: whether a cheap `small`
model reliably (a) judges `llm` rules and (b) produces clean combined
rewrites is unknown until real messages flow through. Expect to tune
`_build_query` prompt wording after first contact. That is accepted, not a
blocker.
