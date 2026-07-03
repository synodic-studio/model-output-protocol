# MOP CLI/core mode — design

## Problem

MOP currently has exactly one integration shape: an in-process MCP server
(`mop/mcp.py`) mounted into a Claude Agent SDK host, where the agent's
only path to the user is the `submit_message` tool. That works well for
SDK-native harnesses, but it's not usable by:

- A CLI/script that wants to check a piece of text against MOP's rules
  without running a full agent session.
- Any "other integration method" (future Channels MCP, Claude plugin,
  etc.) that would rather call a stable core function than reimplement
  rule loading and evaluation.

This spec covers a **CLI/core mode**: a `mop check` command backed by a
reusable core function, so MOP's rule-evaluation logic is available
outside the MCP/agent-session shape. It reuses the existing evaluation
primitives (`mop.rules`, `mop.evaluators`, `mop.types`) — no new
evaluation logic, just a new caller.

Two prerequisites surfaced during design and are included here because
`mop check` can't be used standalone without them: an evaluator backend
that doesn't hardcode specific LLM providers, and a way for MOP to find
rules when invoked from an arbitrary project directory instead of this
repo.

## Non-goals

- **Rule composability / conflict resolution across styles.** The
  layered precedence idea sketched in
  `docs/patchbay/vale-style-example-4-merge-semantics.md` (core →
  transitional → role → overlay → personal) is a separate project.
  This spec only needs a two-layer version of that idea (built-in vs.
  local — see "Rule discovery" below); it does not implement
  `--profile` or multi-style composition.
- **Live interception for non-MOP-native harnesses.** Whether a hook in
  some other agent harness can intercept a message before display is a
  separate feasibility question (tracked against the unresolved
  "Channels compatibility" section of `docs/architecture.md`). `mop
  check` is a one-shot check command; it does not attempt to solve
  streaming/hook interception.
- **Proactive rule-context injection into agent system prompts.**
  Extending `protocol_prompt()` to embed rule content is a separate,
  smaller piece of work, orthogonal to this one.

## Architecture

A new module, `mop/cli.py`, exposes:

- `check(text: str, rules: list[Rule], evaluator: Evaluator, *, justification: str | None = None) -> Verdict` —
  pure function. Calls `mop.rules.collect_lint_hints(text, rules)` for
  advisory hints, then `evaluator(text, hints, justification)`, and
  returns the raw `Verdict`. No I/O, no process state.
- `main()` — thin argparse wrapper that resolves rules (see "Rule
  discovery"), builds an evaluator (see "Evaluator backend"), reads
  input, calls `check()`, and renders output per the "Output contract"
  below.

This makes the CLI one of several **adapters** over the same core the
MCP path already uses:

```
                    ┌─────────────────────┐
                    │   mop core          │
                    │  rules / evaluators │
                    │  / types            │
                    └──────────┬──────────┘
                 ┌─────────────┼──────────────┐
                 │             │              │
          mop/mcp.py      mop/cli.py      (future: Channels
        (stateful,       (stateless,       MCP, Claude plugin,
     justification loop)  one-shot)         other adapters)
```

The CLI adapter is intentionally stateless: no `pending_message`, no
justification-attempt budget. Those are session concepts that belong to
the stateful MCP adapter. The CLI's `--justify` flag (below) is a
single manual override, not the 4-attempt loop.

## Command interface

Console-script entry point `mop`, added to `pyproject.toml`
`[project.scripts]`, invoking `mop.cli:main`.

```
mop check [TEXT] [--file PATH] [--rules-dir PATH] [--rules-file PATH]
          [--rule NAME] [--model MODEL] [--justify REASON] [--json]
```

**Input** (exactly one source; error if zero or more than one given):
- stdin (primary — `echo "$TEXT" | mop check`, robust for arbitrary
  multi-line text from any calling process)
- positional `TEXT` argument (convenience for quick manual checks)
- `--file PATH`

**Rule selection:**
- Default: built-in rules + local discovery (see "Rule discovery").
- `--rules-dir PATH` / `--rules-file PATH`: explicit path, bypasses
  discovery, still layers over built-ins per the merge semantics below.
- `--rule NAME`: restrict evaluation to a single named rule (mirrors
  `evals/harness.py`'s existing `--rule` flag for consistency).

**Evaluator selection:**
- `--model MODEL` / `MOP_EVALUATOR_MODEL` env var — see "Evaluator
  backend."

## Output contract

**Default (human-readable):** verdict line, then rewritten text if
rewritten, then — if rejected — each violated rule name with its
guidance text. `Rejected` only carries rule *names*
(`mop.types.Rejected.violations: list[str]`); guidance lives on the
loaded `Rule` objects, so rendering requires looking up each violated
name against the resolved rule set (built-in + local, per "Rule
discovery") and pulling its `.guidance` field. Legible whether a human
reads it directly or an agent reads it as its own tool/subprocess
result.

**`--json`:**
```json
{
  "verdict": "accepted | rewritten | rejected",
  "rewritten": "string or null",
  "violations": [
    {"name": "rule-name", "guidance": "..."}
  ]
}
```
Violations are emitted as `{name, guidance}` pairs (not bare names) so
a calling script or adapter gets the full picture without separately
loading the rule files itself.

**Exit codes:**
| Code | Meaning |
|---|---|
| 0 | Accepted |
| 1 | Rewritten — caller must decide whether to use the rewritten text |
| 2 | Rejected |
| 3 | Usage or runtime error (bad args, no API key, rules dir not found, etc.) |

This lets a shell pipeline branch on exit code alone without parsing
JSON, while `--json` serves callers that want the full detail (rewrite
text, violation names).

## Rejected handling

One-shot invocation, so no multi-attempt justification loop by default.
`--justify "reason"` performs a single re-evaluation pass with the
justification attached (mirrors the mechanic `submit_justification`
uses, without the 4-attempt budget). If still rejected, exits 2.

## Evaluator backend

`mop/evaluators.py` currently hardcodes two provider-specific builders
(`build_deepseek_evaluator` wrapping a pydantic-ai OpenAI-compatible
client, `build_haiku_evaluator` wrapping a pydantic-ai Anthropic
client), selected via a `MOP_EVALUATOR=deepseek|haiku` enum baked into
the package. This couples MOP's core to specific provider SDKs.

**Change:** collapse both into a single `build_litellm_evaluator(rules,
model=...)` using `litellm.acompletion(model=..., ...)`. The model
becomes a plain string passed straight through to litellm's own
`"provider/model"` convention (e.g. `"deepseek/deepseek-chat"`,
`"anthropic/claude-haiku-4-5-20251001"`) — MOP no longer contains any
provider-specific code or API-key-name knowledge; litellm resolves keys
by its own per-provider convention.

- Config surface: `MOP_EVALUATOR_MODEL` env var / `--model` flag.
  Default stays a small/cheap model for now (e.g.
  `deepseek/deepseek-chat`) — trivially changed later since it's just a
  string, not an enum branch.
- Structured output: request `EvalLLMResponse` via litellm's
  `response_format` where supported; fall back to JSON-mode + manual
  `EvalLLMResponse.model_validate_json(...)` parsing for providers
  where structured output isn't natively supported.
- This is a shared-core change — both the CLI and the existing MCP path
  go through `build_evaluator`, so both benefit and both need their
  tests updated (`tests/test_evaluators.py` currently mocks pydantic-ai
  agents; needs to mock litellm instead).
- `litellm` becomes a new dependency in `pyproject.toml`.
  `pydantic-ai[anthropic]` is a candidate for removal if
  `mop/evaluators.py` was its only consumer in this package — verify
  during implementation, don't assume.

## Rule discovery

Modeled directly on an existing internal precedent: smart config
discovery for the Swift quality tools (walks the directory tree for a
project config, falls back to a shared default when none is found,
shipped as both an importable core and a standalone executable so it
works uniformly across shells, CI, and Claude Code subagents — see
`documentarian/corpus/synodic-tools/proof-smart-config-discovery-for-swift-quality-tools.md`).
Same shape applies here.

- **Convention:** a `.mop/` directory containing `*.yml` files, same
  flat `rules:`-list schema the existing `rules/` directory uses.
- **Walk-up, bounded at repo root:** starting from cwd, check each
  directory upward for `.mop/`. Stop at (and include) the directory
  containing `.git`. Never walk past the repo boundary. If nothing is
  found by then, fall back to built-in rules only.
- **Explicit override:** `--rules-dir` / `--rules-file` accepts a
  direct path from anywhere, skipping discovery entirely.
- **Merge semantics (two layers only, for now):** built-in rules are
  the base layer; a discovered or explicitly-passed local rule set
  layers on top. Same-name entry replaces the built-in; everything
  else unions. `active: false` in a local rule can silence a built-in
  by name. This is the two-layer subset of the precedence idea already
  sketched in `docs/patchbay/vale-style-example-4-merge-semantics.md`
  — not the full 5-layer system, just built-in vs. local.
- **Packaging consequence:** built-in rules must ship inside the
  installable package as package data (e.g. `mop/rules_builtin/*.yml`,
  loaded via `importlib.resources`), since `pyproject.toml` currently
  only packages `mop*` and the existing `rules/` directory lives at the
  repo root, outside the package. Exact migration path (move vs.
  symlink vs. build-time copy) is an implementation-phase decision, not
  specified further here.

## Testing

- `tests/test_cli.py`, following the existing `tests/test_evaluators.py`
  pattern: inject a fake evaluator, assert exit code + JSON shape for
  each verdict type (accepted / rewritten / rejected / error), plus
  input-source validation (zero/multiple sources given) and rule-merge
  behavior (built-in-only, local-override-by-name, local-addition).
  Include a case asserting the rejected `--json` output resolves
  `{name, guidance}` pairs correctly from the loaded rule set, not just
  bare violation names.
- `tests/test_evaluators.py` updated to mock litellm instead of
  pydantic-ai agents.
- Add a discovery test fixture: nested temp directories with a `.git`
  marker and a `.mop/` at varying depths, asserting walk-up stops at
  the repo boundary and falls back to built-ins when nothing is found.

## Open items for implementation

- Exact package-data migration path for built-in rules
  (`rules/` → `mop/rules_builtin/`).
- Confirm whether `pydantic-ai[anthropic]` can be dropped entirely once
  `mop/evaluators.py` is on litellm.
- Default model string for `MOP_EVALUATOR_MODEL` — currently proposed
  as `deepseek/deepseek-chat`, explicitly a placeholder ("small, for
  now") per the user's framing, not a considered choice.
