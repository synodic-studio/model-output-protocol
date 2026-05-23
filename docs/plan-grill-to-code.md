# Grill → Code Plan

Tracking work from the grill-with-docs session through implementation.

## Phase 0: Context Capture & Documentation ✅

- [x] Create `CONTEXT.md` with resolved terms (lint, rule, verdict, hint, evaluator, failed-open)
- [x] Update `docs/architecture.md` — MOP name clarification, AcceptedFailedOpen as escape hatch, lint/rule split, updated checklist
- [x] Update `rules/README.md` — split schema into LLM Rule / Lint, add `lint: true` field
- [x] Update `docs/rule-mining-notes.md` — litmus test acknowledges lint/rule split

## Phase 1: Lint/Rule Schema Split ✅

- [x] `mop/rules.py` — `Rule` dataclass gets `lint` bool field (default `False`)
- [x] `mop/rules.py` — `load_rules()` parses `lint: true` from YAML entry
- [x] `mop/rules.py` — `collect_lint_hints()` added, only checks lint entries; `collect_regex_hints()` kept for backward compat
- [x] `mop/protocol.py` — `submit_message` and `submit_justification` use `collect_lint_hints`
- [x] `mop/format_score.py` — fix import path (`from mop.display_metrics import ...`)
- [x] Tests for `Rule` default lint field, YAML parsing of `lint: true`, `collect_lint_hints` behavior, backward compat
- [x] Tests for `display_metrics` (42 tests covering all functions)
- [x] Tests for `format_score` (25 tests covering all penalty terms)

## Phase 2: LLM Eval Harness

- [ ] `evals/harness.py` — add `--llm` / `--llm-model` flags
- [ ] `evals/harness.py` — when `--llm`, load `build_haiku_evaluator` and run LLM rules against counterexamples
- [ ] `evals/harness.py` — report LLM eval results alongside lint results

## Phase 3: Cleanup

- [ ] Add `Verdict.serialize()` method to consolidate `mcp._verdict_payload` and `audit.verdict_to_dict()`

## Phase 4: Wire format_score as built-in lint

- [ ] `mop/rules.py` — built-in lints (format_score as a pluggable hint source)
- [ ] Evaluator prompt includes format score when threshold exceeded
