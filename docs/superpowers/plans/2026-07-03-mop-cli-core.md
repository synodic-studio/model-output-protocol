# MOP CLI/Core Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stateless `mop` console command (`mop check`, `mop rules`) backed by the existing rule/evaluator core, with a litellm-based provider-agnostic evaluator and repo-root-bounded `.mop/` rule discovery.

**Architecture:** `mop/cli.py` is a new stateless adapter over the existing core (`mop.rules`, `mop.evaluators`, `mop.types`) — the same core the stateful MCP adapter (`mop/mcp.py`) uses. Two prerequisites land first: `mop/evaluators.py` swaps two hardcoded pydantic-ai builders for one litellm builder (model = plain `"provider/model"` string), and a new `mop/discovery.py` resolves rules from packaged built-ins plus a discovered or explicit local layer.

**Tech Stack:** Python ≥3.11, litellm, pydantic v2, PyYAML, argparse (stdlib), pytest + pytest-asyncio (`asyncio_mode = "auto"` is already set — async tests need no marker, but existing files use `@pytest.mark.asyncio` and that's harmless).

**Spec:** `docs/superpowers/specs/2026-07-03-mop-cli-core-design.md`

## Global Constraints

- Python `>=3.11` (already in `pyproject.toml`).
- **Do not break patchbay-relay** (live consumer, separate repo): it calls `mop.rules.load_rules(rules_dir)` and `mop.evaluators.build_evaluator(rules=...)`, and configures via `MOP_RULES_DIR` and `MOP_EVALUATOR` env vars. `build_evaluator(*, rules)` must keep working with no new required args, and `MOP_EVALUATOR=deepseek|haiku` must keep working via an alias map.
- **Do not move or delete anything under the repo-root `rules/` directory.** It stays the dev corpus and the directory live hosts point `MOP_RULES_DIR` at. Built-ins are *copies* of the two core files, guarded by a sync test.
- Exit codes for `mop check`: 0 accepted, 1 rewritten, 2 rejected, 3 usage/runtime error. Exact values — tests assert them.
- Env vars: `MOP_EVALUATOR_MODEL` (new, litellm model string), `MOP_EVALUATOR` (legacy alias, kept), `MOP_<PROVIDER>_API_KEY` (isolation shim, passed as explicit `api_key=`, never exported).
- Run the full suite (`uv run pytest`) before every commit; never commit with failures.
- Commit AND push after each task (working branch is `develop`; direct commits are the norm in this repo). Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Use `uv` for all dependency changes (`uv add`, `uv remove`), never hand-edit installed environments.

---

### Task 1: litellm evaluator swap

Replace the two provider-specific builders in `mop/evaluators.py` with one litellm builder. Keep `build_evaluator(*, rules)` backward compatible.

**Files:**
- Modify: `mop/evaluators.py` (full rewrite, ~120 lines)
- Modify: `mop/__init__.py:10` and `mop/__init__.py:53-55` (exports)
- Modify: `pyproject.toml:7-14` (dependencies)
- Test: `tests/test_evaluators.py` (full rewrite)

**Interfaces:**
- Consumes: `mop.types.EvalLLMResponse`, `mop.types.verdict_from_eval_response(response, *, original_text)`, `mop.rules.Rule` (fields `.name`, `.guidance`).
- Produces (later tasks rely on these exact signatures):
  - `build_evaluator(*, rules: list[Rule], model: str | None = None) -> Evaluator` — Task 4's CLI calls this with `model=args.model`.
  - `build_litellm_evaluator(*, rules: list[Rule], model: str | None = None) -> Evaluator`
  - `resolve_model(model: str | None = None) -> str`
  - `DEFAULT_MODEL = "deepseek/deepseek-chat"`

- [ ] **Step 1: Add litellm and pydantic dependencies**

`pydantic` is currently only a transitive dependency (via pydantic-ai/fastapi) but `mop/types.py` imports it directly — make it explicit now since pydantic-ai is leaving in Task 6.

```bash
uv add "litellm>=1.55" "pydantic>=2"
```

Expected: `pyproject.toml` dependencies gain both entries; `uv.lock` updates. Do NOT remove `pydantic-ai[anthropic]` yet — that's Task 6, after verifying nothing else imports it.

- [ ] **Step 2: Rewrite tests/test_evaluators.py as failing tests**

Replace the entire file with:

```python
"""Evaluator wrapper — tests the litellm ADAPTER, not live LLMs."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mop import build_evaluator, build_litellm_evaluator
from mop.evaluators import DEFAULT_MODEL, resolve_model
from mop.types import Accepted, Rejected, Rewritten


def _fake_completion(payload: dict):
    """Mimic litellm.acompletion's response shape: choices[0].message.content."""
    message = MagicMock()
    message.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return AsyncMock(return_value=response)


def _patched(payload: dict):
    """Patch acompletion + force the json_object fallback path."""
    return (
        patch("litellm.acompletion", _fake_completion(payload)),
        patch("litellm.supports_response_schema", return_value=False),
    )


# ---------------------------------------------------------------------------
# Verdict mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_accepted_when_llm_says_accept():
    p1, p2 = _patched({"action": "accept"})
    with p1, p2:
        evaluator = build_litellm_evaluator(rules=[])
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)


@pytest.mark.asyncio
async def test_returns_rewritten_with_payload():
    p1, p2 = _patched({"action": "rewrite", "rewritten": "cleaned up"})
    with p1, p2:
        evaluator = build_litellm_evaluator(rules=[])
        v = await evaluator("messy", [], None)
    assert isinstance(v, Rewritten)
    assert v.rewritten == "cleaned up"


@pytest.mark.asyncio
async def test_returns_rejected_with_violations():
    p1, p2 = _patched({"action": "reject", "violations": ["rule-x"]})
    with p1, p2:
        evaluator = build_litellm_evaluator(rules=[])
        v = await evaluator("bad", [], None)
    assert isinstance(v, Rejected)
    assert v.violations == ["rule-x"]


@pytest.mark.asyncio
async def test_strips_markdown_fences_from_response():
    message = MagicMock()
    message.content = '```json\n{"action": "accept"}\n```'
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    with patch("litellm.acompletion", AsyncMock(return_value=response)), patch(
        "litellm.supports_response_schema", return_value=False
    ):
        evaluator = build_litellm_evaluator(rules=[])
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


def test_resolve_model_defaults(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.delenv("MOP_EVALUATOR", raising=False)
    assert resolve_model() == DEFAULT_MODEL


def test_resolve_model_explicit_arg_wins(monkeypatch):
    monkeypatch.setenv("MOP_EVALUATOR_MODEL", "anthropic/claude-haiku-4-5-20251001")
    assert resolve_model("openai/gpt-4o-mini") == "openai/gpt-4o-mini"


def test_resolve_model_env_var(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR", raising=False)
    monkeypatch.setenv("MOP_EVALUATOR_MODEL", "anthropic/claude-haiku-4-5-20251001")
    assert resolve_model() == "anthropic/claude-haiku-4-5-20251001"


def test_resolve_model_legacy_deepseek_alias(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.setenv("MOP_EVALUATOR", "deepseek")
    assert resolve_model() == "deepseek/deepseek-chat"


def test_resolve_model_legacy_haiku_alias(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.setenv("MOP_EVALUATOR", "haiku")
    assert resolve_model() == "anthropic/claude-haiku-4-5-20251001"


def test_resolve_model_unknown_legacy_raises(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.setenv("MOP_EVALUATOR", "gpt-4")
    with pytest.raises(ValueError, match="Unknown MOP_EVALUATOR"):
        resolve_model()


# ---------------------------------------------------------------------------
# API-key isolation shim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mop_prefixed_api_key_passed_explicitly(monkeypatch):
    monkeypatch.setenv("MOP_DEEPSEEK_API_KEY", "sk-isolated")
    fake = _fake_completion({"action": "accept"})
    with patch("litellm.acompletion", fake), patch(
        "litellm.supports_response_schema", return_value=False
    ):
        evaluator = build_litellm_evaluator(rules=[], model="deepseek/deepseek-chat")
        await evaluator("hello", [], None)
    assert fake.call_args.kwargs["api_key"] == "sk-isolated"


@pytest.mark.asyncio
async def test_no_api_key_kwarg_when_unset(monkeypatch):
    monkeypatch.delenv("MOP_DEEPSEEK_API_KEY", raising=False)
    fake = _fake_completion({"action": "accept"})
    with patch("litellm.acompletion", fake), patch(
        "litellm.supports_response_schema", return_value=False
    ):
        evaluator = build_litellm_evaluator(rules=[], model="deepseek/deepseek-chat")
        await evaluator("hello", [], None)
    assert "api_key" not in fake.call_args.kwargs


# ---------------------------------------------------------------------------
# Factory (build_evaluator) — backward-compatible entrypoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_evaluator_works_with_rules_only(monkeypatch):
    """patchbay-relay calls build_evaluator(rules=...) — must keep working."""
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.delenv("MOP_EVALUATOR", raising=False)
    p1, p2 = _patched({"action": "accept"})
    with p1, p2:
        evaluator = build_evaluator(rules=[])
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)


@pytest.mark.asyncio
async def test_build_evaluator_accepts_model_override():
    fake = _fake_completion({"action": "accept"})
    with patch("litellm.acompletion", fake), patch(
        "litellm.supports_response_schema", return_value=False
    ):
        evaluator = build_evaluator(rules=[], model="openai/gpt-4o-mini")
        await evaluator("hello", [], None)
    assert fake.call_args.kwargs["model"] == "openai/gpt-4o-mini"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_evaluators.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_litellm_evaluator' from 'mop'`.

- [ ] **Step 4: Rewrite mop/evaluators.py**

Replace the entire file with:

```python
"""LLM evaluator for MOP — litellm-backed, provider-agnostic.

`build_evaluator(rules, model=None)` is the main entrypoint. Model
selection precedence:

  explicit ``model=`` arg
  > MOP_EVALUATOR_MODEL env var (litellm "provider/model" string)
  > legacy MOP_EVALUATOR alias (deepseek | haiku — kept for existing
    hosts like patchbay-relay)
  > DEFAULT_MODEL

The builder returns an `Evaluator`-shaped callable suitable for passing
into `MOP(evaluator=...)`. The callable batches every active rule into a
single prompt and asks the LLM for one structured verdict per submission.

The structured-output schema (`mop.types.EvalLLMResponse`) is owned by
the protocol. Where the provider supports response schemas, litellm is
handed the pydantic class directly; otherwise we fall back to JSON mode
and parse the content manually. Either way the raw content is validated
through `EvalLLMResponse.model_validate_json()`.

API keys: litellm resolves keys via its standard per-provider env vars
(ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, ...). Additionally, an isolated
``MOP_<PROVIDER>_API_KEY`` (e.g. MOP_ANTHROPIC_API_KEY) is honored by
passing it explicitly as ``api_key=`` — it is never exported, so it
stays out of env inherited by spawned agent subprocesses.
"""

from __future__ import annotations

import os
from typing import Awaitable, Callable

from .rules import Rule
from .types import EvalLLMResponse, Verdict, verdict_from_eval_response

DEFAULT_MODEL = "deepseek/deepseek-chat"

MODEL_KEY = "MOP_EVALUATOR_MODEL"
LEGACY_EVALUATOR_KEY = "MOP_EVALUATOR"
_LEGACY_ALIASES = {
    "deepseek": "deepseek/deepseek-chat",
    "haiku": "anthropic/claude-haiku-4-5-20251001",
}


def resolve_model(model: str | None = None) -> str:
    """Resolve the litellm model string from arg > env > legacy alias > default."""
    if model:
        return model
    env_model = os.environ.get(MODEL_KEY)
    if env_model:
        return env_model
    legacy = os.environ.get(LEGACY_EVALUATOR_KEY, "").strip().lower()
    if legacy:
        if legacy not in _LEGACY_ALIASES:
            msg = (
                f"Unknown MOP_EVALUATOR={legacy!r}. Expected 'deepseek' or "
                f"'haiku', or set {MODEL_KEY} to a litellm model string."
            )
            raise ValueError(msg)
        return _LEGACY_ALIASES[legacy]
    return DEFAULT_MODEL


def _api_key_for(model: str) -> str | None:
    """Isolated key lookup: MOP_<PROVIDER>_API_KEY for 'provider/model' strings."""
    if "/" not in model:
        return None
    provider = model.split("/", 1)[0]
    return os.environ.get(f"MOP_{provider.upper()}_API_KEY")


def _build_query(
    rules: list[Rule], text: str, regex_hints: list[str], justification: str | None
) -> str:
    rule_lines = "\n".join(
        f"  - {r.name}: {(r.guidance or '').strip()}" for r in rules
    ) or "  (no active rules)"
    hint_line = (
        f"Regex prelim hits (advisory, not authoritative): {', '.join(regex_hints)}"
        if regex_hints
        else "Regex prelim: clean."
    )
    just_line = (
        f"\nThe agent has provided this justification for the message:\n"
        f"  {justification}\n"
        "Decide whether the justification clears the rule violation."
        if justification
        else ""
    )
    return (
        "You are a message gate. Active rules:\n"
        f"{rule_lines}\n\n"
        f"{hint_line}\n\n"
        "Message under review:\n"
        f"<message>\n{text}\n</message>\n"
        f"{just_line}\n\n"
        "Decide one of three actions:\n"
        "  - accept: message passes all rules, deliver as-is\n"
        "  - rewrite: message violates style but is fixable; provide the corrected text in 'rewritten'\n"
        "  - reject: message violates substantive rules; list the violated rule names in 'violations'\n\n"
        "Respond ONLY with a JSON object of the shape:\n"
        '  {"action": "accept" | "rewrite" | "reject",\n'
        '   "rewritten": string or null,\n'
        '   "violations": [string, ...]}\n'
    )


def _strip_fences(content: str) -> str:
    """Strip a single wrapping ```/```json code fence, if present."""
    stripped = content.strip()
    if stripped.startswith("```"):
        first_newline = stripped.index("\n") if "\n" in stripped else len(stripped)
        stripped = stripped[first_newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[: -3]
    return stripped.strip()


def build_litellm_evaluator(
    *, rules: list[Rule], model: str | None = None
) -> Callable[[str, list[str], str | None], Awaitable[Verdict]]:
    """Return an Evaluator-shaped callable backed by litellm."""
    model_id = resolve_model(model)
    api_key = _api_key_for(model_id)

    async def evaluate(
        text: str, regex_hints: list[str], justification: str | None
    ) -> Verdict:
        import litellm

        query = _build_query(rules, text, regex_hints, justification)
        kwargs: dict = {}
        if api_key:
            kwargs["api_key"] = api_key
        try:
            schema_ok = litellm.supports_response_schema(model=model_id)
        except Exception:
            schema_ok = False
        kwargs["response_format"] = (
            EvalLLMResponse if schema_ok else {"type": "json_object"}
        )
        response = await litellm.acompletion(
            model=model_id,
            messages=[{"role": "user", "content": query}],
            **kwargs,
        )
        content = response.choices[0].message.content or ""
        parsed = EvalLLMResponse.model_validate_json(_strip_fences(content))
        return verdict_from_eval_response(parsed, original_text=text)

    return evaluate


def build_evaluator(
    *, rules: list[Rule], model: str | None = None
) -> Callable[[str, list[str], str | None], Awaitable[Verdict]]:
    """Build the default evaluator. Backward-compatible entrypoint.

    Existing hosts call ``build_evaluator(rules=...)`` with no model and
    configure via env (MOP_EVALUATOR_MODEL, or legacy MOP_EVALUATOR).
    """
    return build_litellm_evaluator(rules=rules, model=model)
```

- [ ] **Step 5: Update mop/__init__.py exports**

In `mop/__init__.py`, change line 10 from:

```python
from .evaluators import build_deepseek_evaluator, build_evaluator, build_haiku_evaluator
```

to:

```python
from .evaluators import build_evaluator, build_litellm_evaluator
```

and in `__all__`, replace the two entries `"build_deepseek_evaluator"` and `"build_haiku_evaluator"` with `"build_litellm_evaluator"` (keep `"build_evaluator"`).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS, including the new `tests/test_evaluators.py`. If `evals/harness.py` or other files still reference `build_deepseek_evaluator`/`build_haiku_evaluator`, fix those references (harness imports only `build_evaluator`, which is unchanged).

- [ ] **Step 7: Commit and push**

```bash
git add mop/evaluators.py mop/__init__.py tests/test_evaluators.py pyproject.toml uv.lock
git commit -m "feat: litellm-backed provider-agnostic evaluator

Collapses the deepseek/haiku pydantic-ai builders into one
build_litellm_evaluator. Model is a plain litellm string via
MOP_EVALUATOR_MODEL (legacy MOP_EVALUATOR=deepseek|haiku still
works via alias). MOP_<PROVIDER>_API_KEY isolation preserved by
passing api_key= explicitly.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
```

---

### Task 2: Rule.active field, single-file loading, and layered merge

The merge semantic "a local `active: false` silences a built-in by name" is impossible today: inactive rules are dropped inside `load_rules()` and `Rule` has no `active` field. Add the field, add single-file loading, add `merge_rules`.

**Files:**
- Modify: `mop/rules.py` (Rule dataclass ~line 40, `load_rules` ~line 55; add `load_rules_file` and `merge_rules`)
- Modify: `mop/__init__.py` (exports)
- Test: `tests/test_rules.py` (append new tests)

**Interfaces:**
- Consumes: existing `Rule`, `load_rules(rules_dir, *, include_inactive=False)`.
- Produces (Task 3 relies on these exact signatures):
  - `Rule` gains field `active: bool = True` (frozen dataclass, defaulted — existing constructions keep working).
  - `load_rules_file(path: Path, *, include_inactive: bool = False) -> list[Rule]` — parses ONE yml file; does NOT append builtin lints.
  - `merge_rules(base: list[Rule], overlay: list[Rule]) -> list[Rule]` — dict-by-name, overlay wins, then drops `active=False`; dedupes builtin lints for free.

- [ ] **Step 1: Append failing tests to tests/test_rules.py**

Append to the end of `tests/test_rules.py` (it already imports `load_rules` and defines rule-YAML tmp fixtures; add imports as needed at the top: `from mop.rules import load_rules_file, merge_rules`):

```python
# ---------------------------------------------------------------------------
# Rule.active field, single-file loading, layered merge
# ---------------------------------------------------------------------------


def _write_rules_yml(path, entries):
    import yaml

    path.write_text(yaml.safe_dump({"rules": entries}))


def test_load_rules_populates_active_field(tmp_path):
    _write_rules_yml(
        tmp_path / "r.yml",
        [
            {"name": "on-rule", "detector": "llm", "guidance": "g"},
            {"name": "off-rule", "detector": "llm", "guidance": "g", "active": False},
        ],
    )
    rules = load_rules(tmp_path, include_inactive=True)
    by_name = {r.name: r for r in rules}
    assert by_name["on-rule"].active is True
    assert by_name["off-rule"].active is False


def test_load_rules_file_reads_single_file(tmp_path):
    target = tmp_path / "solo.yml"
    _write_rules_yml(target, [{"name": "solo-rule", "detector": "llm", "guidance": "g"}])
    # A sibling file that must NOT be picked up:
    _write_rules_yml(
        tmp_path / "other.yml", [{"name": "other-rule", "detector": "llm", "guidance": "g"}]
    )
    rules = load_rules_file(target)
    names = [r.name for r in rules]
    assert "solo-rule" in names
    assert "other-rule" not in names


def test_load_rules_file_does_not_append_builtin_lints(tmp_path):
    target = tmp_path / "solo.yml"
    _write_rules_yml(target, [{"name": "solo-rule", "detector": "llm", "guidance": "g"}])
    rules = load_rules_file(target)
    assert all(r.source_file != "<builtin>" for r in rules)


def test_merge_overlay_replaces_same_name():
    base = [Rule("a", "llm", {}, "base guidance", "base.yml")]
    overlay = [Rule("a", "llm", {}, "local guidance", "local.yml")]
    merged = merge_rules(base, overlay)
    assert len(merged) == 1
    assert merged[0].guidance == "local guidance"


def test_merge_unions_distinct_names():
    base = [Rule("a", "llm", {}, "g", "base.yml")]
    overlay = [Rule("b", "llm", {}, "g", "local.yml")]
    merged = merge_rules(base, overlay)
    assert [r.name for r in merged] == ["a", "b"]


def test_merge_local_inactive_silences_builtin():
    base = [Rule("a", "llm", {}, "g", "base.yml")]
    overlay = [Rule("a", "llm", {}, "g", "local.yml", active=False)]
    merged = merge_rules(base, overlay)
    assert merged == []


def test_merge_dedupes_builtin_lints():
    """load_rules appends registered builtin lints per call; merge dedupes them."""
    lint = Rule(
        "format-score-too-high", "deterministic", {"type": "builtin_lint"},
        "g", "<builtin>", lint=True,
    )
    merged = merge_rules([lint], [lint])
    assert len(merged) == 1
```

Note: `Rule` is constructed positionally as `Rule(name, detector, parameters, guidance, source_file)` — this matches the existing dataclass field order in `mop/rules.py:40-47`; `lint` and `active` are keyword defaults.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rules.py -v`
Expected: new tests FAIL (`ImportError` on `load_rules_file`/`merge_rules`, `TypeError: unexpected keyword argument 'active'`). Pre-existing tests still PASS.

- [ ] **Step 3: Implement in mop/rules.py**

Add `active` to the dataclass (after `lint`):

```python
@dataclass(frozen=True)
class Rule:
    name: str
    detector: str           # "llm" | "deterministic" | "regex" (legacy)
    parameters: dict
    guidance: str
    source_file: str
    lint: bool = False      # True for deterministic pattern checks (advisory)
    active: bool = True     # False only reachable via include_inactive=True
```

Refactor `load_rules` to delegate per-file parsing, and add the two new functions. Replace the body of `load_rules` and add below it:

```python
def _rules_from_file(
    path: Path, *, include_inactive: bool, source_label: str
) -> list[Rule]:
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    rules: list[Rule] = []
    for entry in data.get("rules", []):
        if not include_inactive and not _entry_is_active(entry):
            continue
        rules.append(
            Rule(
                name=entry["name"],
                detector=entry["detector"],
                parameters=entry.get("parameters", {}),
                guidance=entry.get("guidance", ""),
                source_file=source_label,
                lint=bool(entry.get("lint", False)),
                active=_entry_is_active(entry),
            )
        )
    return rules


def load_rules(rules_dir: Path, *, include_inactive: bool = False) -> list[Rule]:
    """(keep the existing docstring)"""
    rules: list[Rule] = []
    for path in sorted(rules_dir.rglob("*.yml")):
        rules.extend(
            _rules_from_file(
                path,
                include_inactive=include_inactive,
                source_label=str(path.relative_to(rules_dir)),
            )
        )
    for name, (guidance, check) in _BUILTIN_LINTS.items():
        rules.append(
            Rule(
                name=name,
                detector="deterministic",
                parameters={"type": "builtin_lint"},
                guidance=guidance,
                source_file="<builtin>",
                lint=True,
            )
        )
    return rules


def load_rules_file(path: Path, *, include_inactive: bool = False) -> list[Rule]:
    """Load rules from a single YAML file.

    Unlike ``load_rules``, does NOT append registered builtin lints —
    callers composing layers get those from the base layer and rely on
    ``merge_rules`` name-dedupe.
    """
    return _rules_from_file(
        path, include_inactive=include_inactive, source_label=str(path)
    )


def merge_rules(base: list[Rule], overlay: list[Rule]) -> list[Rule]:
    """Two-layer merge: overlay wins by name, everything else unions.

    Inactive rules are dropped AFTER the merge, so an overlay entry with
    ``active: false`` silences a same-named base rule. Load the overlay
    with ``include_inactive=True`` for that to work.
    """
    merged: dict[str, Rule] = {r.name: r for r in base}
    for rule in overlay:
        merged[rule.name] = rule
    return [r for r in merged.values() if r.active]
```

- [ ] **Step 4: Export from mop/__init__.py**

Change the rules import line to:

```python
from .rules import (
    Rule,
    collect_lint_hints,
    collect_regex_hints,
    load_rules,
    load_rules_file,
    merge_rules,
    register_builtin_lint,
)
```

and add `"load_rules_file"` and `"merge_rules"` to `__all__` (next to `"load_rules"`).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 6: Commit and push**

```bash
git add mop/rules.py mop/__init__.py tests/test_rules.py
git commit -m "feat: Rule.active field, single-file loading, layered merge

Groundwork for CLI rule discovery: merge_rules gives two-layer
override semantics (overlay wins by name, active:false silences,
builtin lints dedupe), and load_rules_file loads one yml without
appending builtin lints.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
```

---

### Task 3: Built-in rules as package data + discovery

Ship the two core rule files inside the package and add `.mop/` walk-up discovery bounded at the repo root.

**Files:**
- Create: `mop/rules_builtin/core-behavior.yml` (copy of `rules/core-behavior.yml`)
- Create: `mop/rules_builtin/core-voice.yml` (copy of `rules/core-voice.yml`)
- Create: `mop/discovery.py`
- Modify: `pyproject.toml` (package-data section)
- Modify: `mop/__init__.py` (exports)
- Test: `tests/test_discovery.py` (new)

**Interfaces:**
- Consumes: `load_rules`, `load_rules_file`, `merge_rules` from Task 2.
- Produces (Task 4 relies on these exact signatures):
  - `load_builtin_rules() -> list[Rule]`
  - `find_local_rules_dir(start: Path | None = None) -> Path | None`
  - `resolve_rules(*, rules_dir: Path | None = None, rules_file: Path | None = None, start: Path | None = None) -> list[Rule]`

**Decision baked in here:** built-ins are `core-behavior.yml` + `core-voice.yml` only. The `role-*`, `transitional-*`, `overlay-*`, and `personal` files are user/session-specific and stay repo-local (and the user is actively reconsidering the role-set concept). The repo `rules/` directory is untouched; a sync test pins the copies to their originals so drift fails the suite.

- [ ] **Step 1: Copy the built-in rule files**

```bash
mkdir -p mop/rules_builtin
cp rules/core-behavior.yml mop/rules_builtin/core-behavior.yml
cp rules/core-voice.yml mop/rules_builtin/core-voice.yml
```

- [ ] **Step 2: Declare package data in pyproject.toml**

Add after the `[tool.setuptools.packages.find]` section:

```toml
[tool.setuptools.package-data]
mop = ["rules_builtin/*.yml"]
```

- [ ] **Step 3: Write failing tests in tests/test_discovery.py**

```python
"""Rule discovery: packaged built-ins, .mop/ walk-up, layered resolution."""

import filecmp
from pathlib import Path

import yaml

from mop.discovery import find_local_rules_dir, load_builtin_rules, resolve_rules

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_mop_dir(directory: Path, entries: list[dict]) -> Path:
    mop_dir = directory / ".mop"
    mop_dir.mkdir(parents=True)
    (mop_dir / "local.yml").write_text(yaml.safe_dump({"rules": entries}))
    return mop_dir


# ---------------------------------------------------------------------------
# Built-ins
# ---------------------------------------------------------------------------


def test_builtin_rules_load_nonempty():
    rules = load_builtin_rules()
    assert rules, "packaged built-in rules should not be empty"
    names = {r.name for r in rules}
    assert "no-fabricated-attribution" in names      # from core-voice.yml
    assert "verify-before-asserting" in names        # from core-behavior.yml


def test_builtin_copies_stay_in_sync_with_rules_dir():
    """Guard against drift between rules/ (dev corpus) and packaged copies."""
    for name in ("core-behavior.yml", "core-voice.yml"):
        original = REPO_ROOT / "rules" / name
        packaged = REPO_ROOT / "mop" / "rules_builtin" / name
        assert filecmp.cmp(original, packaged, shallow=False), (
            f"{name}: mop/rules_builtin/ copy differs from rules/ original — "
            "re-copy it (cp rules/{name} mop/rules_builtin/{name})"
        )


# ---------------------------------------------------------------------------
# Walk-up discovery
# ---------------------------------------------------------------------------


def test_finds_mop_dir_in_start_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    mop_dir = _write_mop_dir(tmp_path, [])
    assert find_local_rules_dir(tmp_path) == mop_dir


def test_walks_up_to_repo_root(tmp_path):
    (tmp_path / ".git").mkdir()
    mop_dir = _write_mop_dir(tmp_path, [])
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert find_local_rules_dir(nested) == mop_dir


def test_stops_at_repo_root(tmp_path):
    _write_mop_dir(tmp_path, [])          # .mop ABOVE the repo root
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    nested = repo / "src"
    nested.mkdir()
    assert find_local_rules_dir(nested) is None


def test_returns_none_without_mop_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    assert find_local_rules_dir(tmp_path) is None


# ---------------------------------------------------------------------------
# resolve_rules layering
# ---------------------------------------------------------------------------


def test_resolve_defaults_to_builtins_when_nothing_found(tmp_path):
    (tmp_path / ".git").mkdir()
    resolved = resolve_rules(start=tmp_path)
    assert {r.name for r in resolved} == {r.name for r in load_builtin_rules()}


def test_resolve_local_addition_unions(tmp_path):
    (tmp_path / ".git").mkdir()
    _write_mop_dir(
        tmp_path, [{"name": "project-rule", "detector": "llm", "guidance": "g"}]
    )
    resolved = resolve_rules(start=tmp_path)
    assert "project-rule" in {r.name for r in resolved}


def test_resolve_local_override_replaces_builtin(tmp_path):
    (tmp_path / ".git").mkdir()
    builtin_name = next(
        r.name for r in load_builtin_rules() if r.source_file != "<builtin>"
    )
    _write_mop_dir(
        tmp_path,
        [{"name": builtin_name, "detector": "llm", "guidance": "local override"}],
    )
    resolved = resolve_rules(start=tmp_path)
    match = [r for r in resolved if r.name == builtin_name]
    assert len(match) == 1
    assert match[0].guidance == "local override"


def test_resolve_local_inactive_silences_builtin(tmp_path):
    (tmp_path / ".git").mkdir()
    builtin_name = next(
        r.name for r in load_builtin_rules() if r.source_file != "<builtin>"
    )
    _write_mop_dir(
        tmp_path,
        [{"name": builtin_name, "detector": "llm", "guidance": "", "active": False}],
    )
    resolved = resolve_rules(start=tmp_path)
    assert builtin_name not in {r.name for r in resolved}


def test_resolve_explicit_rules_file_bypasses_discovery(tmp_path):
    (tmp_path / ".git").mkdir()
    _write_mop_dir(
        tmp_path, [{"name": "discovered-rule", "detector": "llm", "guidance": "g"}]
    )
    explicit = tmp_path / "explicit.yml"
    explicit.write_text(
        yaml.safe_dump(
            {"rules": [{"name": "explicit-rule", "detector": "llm", "guidance": "g"}]}
        )
    )
    resolved = resolve_rules(rules_file=explicit, start=tmp_path)
    names = {r.name for r in resolved}
    assert "explicit-rule" in names
    assert "discovered-rule" not in names


def test_resolve_rejects_both_dir_and_file(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="at most one"):
        resolve_rules(rules_dir=tmp_path, rules_file=tmp_path / "x.yml")


def test_resolve_builtin_lints_appear_once(tmp_path):
    (tmp_path / ".git").mkdir()
    _write_mop_dir(tmp_path, [{"name": "x", "detector": "llm", "guidance": "g"}])
    resolved = resolve_rules(start=tmp_path)
    lint_names = [r.name for r in resolved if r.source_file == "<builtin>"]
    assert len(lint_names) == len(set(lint_names))
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mop.discovery'`.

- [ ] **Step 5: Implement mop/discovery.py**

```python
"""Rule discovery and layered resolution for CLI/standalone use.

Built-in rules ship as package data in ``mop/rules_builtin/``. Local
rules live in a ``.mop/`` directory holding ``*.yml`` files (same
schema as the repo ``rules/`` directory), discovered by walking up
from the starting directory to the repo root — the first directory
containing ``.git``, inclusive — and never past it.

Resolution is a two-layer merge (see ``mop.rules.merge_rules``):
built-ins are the base, the discovered or explicitly-passed local set
overlays it. Same-name replaces, everything else unions, and a local
``active: false`` silences a built-in by name.
"""

from __future__ import annotations

from importlib.resources import as_file, files
from pathlib import Path

from .rules import Rule, load_rules, load_rules_file, merge_rules


def load_builtin_rules() -> list[Rule]:
    """Load the rule files packaged in mop/rules_builtin/."""
    source = files("mop") / "rules_builtin"
    with as_file(source) as dir_path:
        return load_rules(Path(dir_path))


def find_local_rules_dir(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` (default cwd) looking for a ``.mop/`` directory.

    Stops at — and includes — the first directory containing ``.git``.
    Returns None if no ``.mop/`` exists within the repo, or if ``start``
    is outside any git repo entirely (the walk then ends at the
    filesystem root without crossing repo boundaries it can't see).
    """
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / ".mop"
        if candidate.is_dir():
            return candidate
        if (directory / ".git").exists():
            return None
    return None


def resolve_rules(
    *,
    rules_dir: Path | None = None,
    rules_file: Path | None = None,
    start: Path | None = None,
) -> list[Rule]:
    """Resolve the active rule set: built-ins + (explicit | discovered) local layer.

    ``rules_dir``/``rules_file`` are mutually exclusive explicit overrides
    that bypass discovery. The local layer is loaded with
    ``include_inactive=True`` so ``active: false`` entries can silence
    built-ins during the merge.
    """
    if rules_dir and rules_file:
        raise ValueError("Pass at most one of rules_dir / rules_file.")
    base = load_builtin_rules()
    if rules_file:
        overlay = load_rules_file(rules_file, include_inactive=True)
    elif rules_dir:
        overlay = load_rules(rules_dir, include_inactive=True)
    else:
        discovered = find_local_rules_dir(start)
        overlay = (
            load_rules(discovered, include_inactive=True) if discovered else []
        )
    return merge_rules(base, overlay)
```

- [ ] **Step 6: Export from mop/__init__.py**

Add the import (alphabetically with the others):

```python
from .discovery import find_local_rules_dir, load_builtin_rules, resolve_rules
```

and add `"find_local_rules_dir"`, `"load_builtin_rules"`, `"resolve_rules"` to `__all__`.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 8: Commit and push**

```bash
git add mop/rules_builtin/ mop/discovery.py mop/__init__.py pyproject.toml tests/test_discovery.py
git commit -m "feat: packaged built-in rules + repo-root-bounded .mop/ discovery

Built-ins are copies of rules/core-behavior.yml and rules/core-voice.yml
(sync test guards drift; repo rules/ stays the dev corpus for live
hosts). resolve_rules() layers a discovered or explicit local set over
built-ins with override/union/silence semantics.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
```

---

### Task 4: mop/cli.py — check() core and `mop check` command

**Files:**
- Create: `mop/cli.py`
- Modify: `mop/__init__.py` (export `check`)
- Test: `tests/test_cli.py` (new)

**Interfaces:**
- Consumes: `resolve_rules` (Task 3), `build_evaluator(*, rules, model=None)` (Task 1), `collect_lint_hints(text, rules)`, verdict types from `mop.types`.
- Produces:
  - `async def check(text: str, rules: list[Rule], evaluator: Evaluator, *, justification: str | None = None) -> Verdict`
  - `main(argv: list[str] | None = None) -> int` — Task 5 adds the `rules` subcommand into the same parser; Task 6 wires `[project.scripts]` to it.
  - Exit-code constants: `EXIT_ACCEPTED = 0`, `EXIT_REWRITTEN = 1`, `EXIT_REJECTED = 2`, `EXIT_ERROR = 3`.

**Input-source rule (spec: exactly one source):** positional `TEXT` and `--file` are mutually exclusive (error, exit 3, if both). If neither is given, read stdin; empty stdin (or an interactive tty with no input) is an error, exit 3. Explicit args win over piped stdin — a piped stdin alongside a positional arg is ignored, not an error, since "stdin was provided" can't be reliably distinguished from "running under a non-tty harness."

- [ ] **Step 1: Write failing tests in tests/test_cli.py**

```python
"""CLI adapter: exit codes, output contract, input-source validation."""

import json

import pytest
import yaml

from mop.cli import EXIT_ACCEPTED, EXIT_ERROR, EXIT_REJECTED, EXIT_REWRITTEN, check, main
from mop.rules import Rule
from mop.types import Accepted, Rejected, Rewritten


def _fake_evaluator(verdict, calls=None):
    async def evaluate(text, hints, justification):
        if calls is not None:
            calls.append({"text": text, "hints": hints, "justification": justification})
        return verdict

    return evaluate


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Isolated fake repo root so discovery never picks up real .mop dirs."""
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _patch_evaluator(monkeypatch, verdict, calls=None):
    monkeypatch.setattr(
        "mop.cli.build_evaluator",
        lambda *, rules, model=None: _fake_evaluator(verdict, calls),
    )


# ---------------------------------------------------------------------------
# check() core function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_passes_lint_hints_and_justification():
    calls = []
    lint_rule = Rule(
        "too-long", "deterministic", {"type": "word_count", "max": 2},
        "keep it short", "x.yml", lint=True,
    )
    v = await check(
        "three words here", [lint_rule], _fake_evaluator(Accepted(), calls),
        justification="because",
    )
    assert isinstance(v, Accepted)
    assert calls[0]["hints"] == ["too-long"]
    assert calls[0]["justification"] == "because"


# ---------------------------------------------------------------------------
# Exit codes and JSON contract
# ---------------------------------------------------------------------------


def test_accepted_exit_and_json(repo, monkeypatch, capsys):
    _patch_evaluator(monkeypatch, Accepted())
    code = main(["check", "hello world", "--json"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"verdict": "accepted", "rewritten": None, "violations": []}


def test_rewritten_exit_and_json(repo, monkeypatch, capsys):
    _patch_evaluator(monkeypatch, Rewritten(rewritten="better text"))
    code = main(["check", "worse text", "--json"])
    assert code == EXIT_REWRITTEN
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "rewritten"
    assert payload["rewritten"] == "better text"


def test_rejected_json_resolves_guidance(repo, monkeypatch, capsys):
    """Rejected carries names only; the CLI must look guidance up in the rule set."""
    mop_dir = repo / ".mop"
    mop_dir.mkdir()
    (mop_dir / "local.yml").write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {"name": "no-hype", "detector": "llm", "guidance": "No hype words."}
                ]
            }
        )
    )
    _patch_evaluator(monkeypatch, Rejected(violations=["no-hype"]))
    code = main(["check", "AMAZING!!!", "--json"])
    assert code == EXIT_REJECTED
    payload = json.loads(capsys.readouterr().out)
    assert payload["violations"] == [{"name": "no-hype", "guidance": "No hype words."}]


def test_rejected_human_output_shows_guidance(repo, monkeypatch, capsys):
    _patch_evaluator(monkeypatch, Rejected(violations=["unspecified"]))
    code = main(["check", "text"])
    assert code == EXIT_REJECTED
    out = capsys.readouterr().out
    assert "rejected" in out
    assert "unspecified" in out


# ---------------------------------------------------------------------------
# Input sources
# ---------------------------------------------------------------------------


def test_file_input(repo, monkeypatch, capsys, tmp_path):
    target = tmp_path / "msg.txt"
    target.write_text("from a file")
    calls = []
    _patch_evaluator(monkeypatch, Accepted(), calls)
    code = main(["check", "--file", str(target)])
    assert code == EXIT_ACCEPTED
    assert calls[0]["text"] == "from a file"


def test_text_and_file_together_errors(repo, monkeypatch, capsys, tmp_path):
    target = tmp_path / "msg.txt"
    target.write_text("x")
    _patch_evaluator(monkeypatch, Accepted())
    code = main(["check", "positional", "--file", str(target)])
    assert code == EXIT_ERROR


def test_stdin_input(repo, monkeypatch, capsys):
    import io

    _patch_evaluator(monkeypatch, Accepted())
    monkeypatch.setattr("sys.stdin", io.StringIO("piped text"))
    code = main(["check", "--json"])
    assert code == EXIT_ACCEPTED


def test_empty_input_errors(repo, monkeypatch, capsys):
    import io

    _patch_evaluator(monkeypatch, Accepted())
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code = main(["check"])
    assert code == EXIT_ERROR


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_justify_passes_justification(repo, monkeypatch):
    calls = []
    _patch_evaluator(monkeypatch, Accepted(), calls)
    main(["check", "text", "--justify", "the user asked for raw logs"])
    assert calls[0]["justification"] == "the user asked for raw logs"


def test_rule_filter_restricts_rule_set(repo, monkeypatch):
    """--rule NAME evaluates against only that rule."""
    captured_rules = []

    def fake_builder(*, rules, model=None):
        captured_rules.extend(rules)
        return _fake_evaluator(Accepted())

    monkeypatch.setattr("mop.cli.build_evaluator", fake_builder)
    mop_dir = repo / ".mop"
    mop_dir.mkdir()
    (mop_dir / "local.yml").write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {"name": "only-this", "detector": "llm", "guidance": "g"},
                    {"name": "not-this", "detector": "llm", "guidance": "g"},
                ]
            }
        )
    )
    code = main(["check", "text", "--rule", "only-this"])
    assert code == EXIT_ACCEPTED
    assert [r.name for r in captured_rules] == ["only-this"]


def test_rule_filter_unknown_name_errors(repo, monkeypatch, capsys):
    _patch_evaluator(monkeypatch, Accepted())
    code = main(["check", "text", "--rule", "does-not-exist"])
    assert code == EXIT_ERROR
    assert "does-not-exist" in capsys.readouterr().err


def test_model_flag_reaches_builder(repo, monkeypatch):
    seen = {}

    def fake_builder(*, rules, model=None):
        seen["model"] = model
        return _fake_evaluator(Accepted())

    monkeypatch.setattr("mop.cli.build_evaluator", fake_builder)
    main(["check", "text", "--model", "openai/gpt-4o-mini"])
    assert seen["model"] == "openai/gpt-4o-mini"


def test_evaluator_exception_exits_error(repo, monkeypatch, capsys):
    async def boom(text, hints, justification):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(
        "mop.cli.build_evaluator", lambda *, rules, model=None: boom
    )
    code = main(["check", "text"])
    assert code == EXIT_ERROR
    assert "provider exploded" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mop.cli'`.

- [ ] **Step 3: Implement mop/cli.py**

```python
"""MOP CLI adapter — stateless one-shot checks over the shared core.

``mop check`` evaluates one piece of text against the resolved rule set
(packaged built-ins + discovered/explicit local layer) and reports the
verdict. Unlike the stateful MCP adapter, there is no pending message
and no justification-attempt budget: ``--justify`` attaches a
justification to the single evaluation call.

Exit codes: 0 accepted, 1 rewritten, 2 rejected, 3 usage/runtime error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .discovery import resolve_rules
from .evaluators import build_evaluator
from .rules import Rule, collect_lint_hints
from .types import Accepted, Evaluator, Rejected, Rewritten, Verdict

EXIT_ACCEPTED = 0
EXIT_REWRITTEN = 1
EXIT_REJECTED = 2
EXIT_ERROR = 3


async def check(
    text: str,
    rules: list[Rule],
    evaluator: Evaluator,
    *,
    justification: str | None = None,
) -> Verdict:
    """Pure one-shot check: lint hints + one evaluator call, raw Verdict out.

    ``rules`` must be the same resolved set the evaluator was built with —
    evaluators bake rules into their prompt; this parameter feeds lint-hint
    collection (and, in the CLI renderer, guidance lookup).
    """
    hints = collect_lint_hints(text, rules)
    return await evaluator(text, hints, justification)


def _read_input(args: argparse.Namespace) -> str:
    if args.text is not None and args.file is not None:
        raise ValueError("Pass TEXT or --file, not both.")
    if args.text is not None:
        return args.text
    if args.file is not None:
        return Path(args.file).read_text()
    if sys.stdin.isatty():
        raise ValueError("No input: pass TEXT, --file, or pipe text on stdin.")
    text = sys.stdin.read()
    if not text.strip():
        raise ValueError("Empty input.")
    return text


def _violations_payload(names: list[str], rules: list[Rule]) -> list[dict]:
    guidance_by_name = {r.name: r.guidance for r in rules}
    return [{"name": n, "guidance": guidance_by_name.get(n, "")} for n in names]


def _render(verdict: Verdict, rules: list[Rule], *, as_json: bool) -> int:
    if isinstance(verdict, Rewritten):
        payload = {
            "verdict": "rewritten",
            "rewritten": verdict.rewritten,
            "violations": [],
        }
        human = f"rewritten\n\n{verdict.rewritten}"
        code = EXIT_REWRITTEN
    elif isinstance(verdict, Rejected):
        violations = _violations_payload(verdict.violations, rules)
        payload = {"verdict": "rejected", "rewritten": None, "violations": violations}
        lines = "\n".join(f"  {v['name']}: {v['guidance']}" for v in violations)
        human = f"rejected\n{lines}"
        code = EXIT_REJECTED
    else:
        # Accepted. (AcceptedFailedOpen is unreachable in one-shot mode —
        # it only arises from the MCP adapter's justification budget.)
        payload = {"verdict": "accepted", "rewritten": None, "violations": []}
        human = "accepted"
        code = EXIT_ACCEPTED
    print(json.dumps(payload) if as_json else human)
    return code


def _run_check(args: argparse.Namespace, rules: list[Rule]) -> int:
    if args.rule:
        rules = [r for r in rules if r.name == args.rule]
        if not rules:
            raise ValueError(f"No active rule named {args.rule!r}.")
    text = _read_input(args)
    evaluator = build_evaluator(rules=rules, model=args.model)
    verdict = asyncio.run(
        check(text, rules, evaluator, justification=args.justify)
    )
    return _render(verdict, rules, as_json=args.as_json)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mop", description="MOP — check text against model-output rules."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="Evaluate text; exit 0/1/2/3.")
    check_p.add_argument("text", nargs="?", help="Text to check (or use --file/stdin)")
    check_p.add_argument("--file", type=Path, help="Read the text from a file")
    check_p.add_argument("--rules-dir", type=Path, help="Explicit rules dir (skips discovery)")
    check_p.add_argument("--rules-file", type=Path, help="Explicit rules file (skips discovery)")
    check_p.add_argument("--rule", help="Restrict evaluation to one named rule")
    check_p.add_argument("--model", help="litellm model string (default: MOP_EVALUATOR_MODEL)")
    check_p.add_argument("--justify", metavar="REASON", help="Attach a justification")
    check_p.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        rules = resolve_rules(rules_dir=args.rules_dir, rules_file=args.rules_file)
        return _run_check(args, rules)
    except Exception as exc:  # argparse errors exit(2) on their own before this
        print(f"mop: {exc}", file=sys.stderr)
        return EXIT_ERROR
```

(`asdict` is imported now because Task 5's `rules` subcommand uses it; if the linter complains before Task 5 lands, drop it here and add it in Task 5.)

- [ ] **Step 4: Export check from mop/__init__.py**

Add:

```python
from .cli import check
```

and `"check"` to `__all__`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 6: Commit and push**

```bash
git add mop/cli.py mop/__init__.py tests/test_cli.py
git commit -m "feat: mop check — stateless CLI adapter over the shared core

async check() core (lint hints + one evaluator call) plus an argparse
main with the spec's exit-code contract (0 accepted / 1 rewritten /
2 rejected / 3 error), input-source validation, --rule/--model/--justify
flags, and rejected-verdict guidance lookup in both human and --json
output.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
```

---

### Task 5: `mop rules` subcommand

**Files:**
- Modify: `mop/cli.py` (add subparser + `_run_rules`)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `resolve_rules`, `main()` parser from Task 4.
- Produces: `mop rules [--rules-dir PATH] [--rules-file PATH] [--json]` — default output is `name: guidance` per line; `--json` emits the full rule objects (`dataclasses.asdict`). Always exits 0 on success, 3 on error.

- [ ] **Step 1: Append failing tests to tests/test_cli.py**

```python
# ---------------------------------------------------------------------------
# mop rules
# ---------------------------------------------------------------------------


def test_rules_lists_merged_set(repo, capsys):
    mop_dir = repo / ".mop"
    mop_dir.mkdir()
    (mop_dir / "local.yml").write_text(
        yaml.safe_dump(
            {"rules": [{"name": "project-rule", "detector": "llm", "guidance": "local g"}]}
        )
    )
    code = main(["rules"])
    assert code == 0
    out = capsys.readouterr().out
    assert "project-rule: local g" in out


def test_rules_json_emits_full_objects(repo, capsys):
    code = main(["rules", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list) and payload
    sample = payload[0]
    assert {"name", "detector", "parameters", "guidance", "source_file", "lint", "active"} <= set(sample)


def test_rules_reflects_local_silencing(repo, capsys):
    from mop.discovery import load_builtin_rules

    builtin_name = next(
        r.name for r in load_builtin_rules() if r.source_file != "<builtin>"
    )
    mop_dir = repo / ".mop"
    mop_dir.mkdir()
    (mop_dir / "local.yml").write_text(
        yaml.safe_dump(
            {"rules": [{"name": builtin_name, "detector": "llm", "guidance": "", "active": False}]}
        )
    )
    main(["rules", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert builtin_name not in {r["name"] for r in payload}


def test_rules_builtin_lints_appear_once(repo, capsys):
    main(["rules", "--json"])
    payload = json.loads(capsys.readouterr().out)
    names = [r["name"] for r in payload]
    assert len(names) == len(set(names))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v -k "rules"`
Expected: FAIL — argparse error: `invalid choice: 'rules'` (surfaces as SystemExit; the new tests error/fail).

- [ ] **Step 3: Implement in mop/cli.py**

Add to `_build_parser()` after the `check` subparser:

```python
    rules_p = sub.add_parser("rules", help="Print the resolved active rule set.")
    rules_p.add_argument("--rules-dir", type=Path, help="Explicit rules dir (skips discovery)")
    rules_p.add_argument("--rules-file", type=Path, help="Explicit rules file (skips discovery)")
    rules_p.add_argument("--json", action="store_true", dest="as_json")
```

Add the runner:

```python
def _run_rules(rules: list[Rule], *, as_json: bool) -> int:
    if as_json:
        print(json.dumps([asdict(r) for r in rules], indent=2))
    else:
        for rule in rules:
            print(f"{rule.name}: {(rule.guidance or '').strip()}")
    return 0
```

And route it in `main()` between `resolve_rules` and `_run_check`:

```python
        rules = resolve_rules(rules_dir=args.rules_dir, rules_file=args.rules_file)
        if args.command == "rules":
            return _run_rules(rules, as_json=args.as_json)
        return _run_check(args, rules)
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit and push**

```bash
git add mop/cli.py tests/test_cli.py
git commit -m "feat: mop rules — print the resolved active rule set

Exposure half of rule-context priming: harnesses can shell out to
'mop rules' and paste the resolved (built-in + local, post-merge)
set into a system prompt. Also the debugging surface for 'why
did/didn't rule X fire'.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
```

---

### Task 6: Console script, pydantic-ai removal, docs

**Files:**
- Modify: `pyproject.toml` (`[project.scripts]`; remove `pydantic-ai[anthropic]` if verified unused)
- Modify: `README.md` (CLI usage section)
- Modify: `docs/architecture.md` (adapters + CLI, evaluator env vars)
- Modify: `rules/README.md` (pointer to `.mop/` local rules)

**Interfaces:**
- Consumes: `mop.cli:main` (returns int; setuptools console scripts pass the return value to `sys.exit`).
- Produces: the installed `mop` command.

- [ ] **Step 1: Add the console script**

In `pyproject.toml`, add:

```toml
[project.scripts]
mop = "mop.cli:main"
```

- [ ] **Step 2: Verify pydantic-ai is now unused, then remove it**

```bash
rtk grep -rn "pydantic_ai" mop evals tests scripts --include="*.py"
```

Expected: no matches (Task 1 removed the only consumer). If matches exist, migrate them first — do not remove the dependency while imports remain. Then:

```bash
uv remove "pydantic-ai[anthropic]"
uv sync
```

- [ ] **Step 3: Smoke-test the installed command**

```bash
uv run mop rules | head -5
uv run mop --help
echo "hello" | uv run mop check --json; echo "exit: $?"
```

Expected: `mop rules` prints built-in rule names+guidance; `--help` shows both subcommands; the `check` call either returns a real verdict (if an API key is configured for the default model) or prints an error to stderr and exits 3 — both prove the wiring. Do not treat a provider auth failure here as a bug.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS (confirms nothing depended on pydantic-ai transitively, e.g. via imports in `mop/__init__.py`).

- [ ] **Step 5: Update docs**

- `README.md`: add a "CLI" section after the existing tool table showing:
  ```bash
  echo "Sounds great, shipping it!" | mop check --json
  mop check --file draft.md --model anthropic/claude-haiku-4-5-20251001
  mop rules            # resolved active rule set (built-ins + .mop/)
  ```
  plus one sentence each on exit codes (0/1/2/3), `.mop/` discovery (walk-up, stops at repo root), and `MOP_EVALUATOR_MODEL`.
- `docs/architecture.md`: in the evaluator section, replace the `MOP_EVALUATOR=deepseek|haiku` description with the litellm model-string convention (`MOP_EVALUATOR_MODEL`, legacy alias kept); add `mop/cli.py` to the adapters/components table; tick the relevant implementation-status checkboxes if the list has matching items (add "CLI adapter (`mop check` / `mop rules`)" as a checked item if not).
- `rules/README.md`: add a short "Local project rules (`.mop/`)" subsection: same YAML schema, discovered by walk-up bounded at the repo root, overrides built-ins by name, `active: false` silences a built-in.

- [ ] **Step 6: Commit and push**

```bash
git add pyproject.toml uv.lock README.md docs/architecture.md rules/README.md
git commit -m "feat: mop console script; drop pydantic-ai; document CLI mode

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
```

---

## Deferred / explicitly out of scope (from the spec's Non-goals)

- Multi-layer style composition (`--profile`, ordered rule-set lists) — two-layer merge only.
- Live interception for non-MOP-native harnesses.
- Injecting rule content into `protocol_prompt()` — `mop rules` covers exposure only.
