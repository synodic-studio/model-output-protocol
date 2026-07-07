"""MOP rule loading and prelim regex hint collection.

Rules live in YAML files under a flat `rules/` directory. Each rule may
carry an `active: true|false` flag (default `true`). `load_rules()`
returns only active rules — inactive rules are visible in the Studio UI
but never reach the evaluator. The LLM's verdict is the disposition;
legacy `severity` and `on_violation` fields on a rule are silently
ignored if present.

Regex hints are a non-authoritative prelim pass. Any rule whose
detector is `regex` and whose pattern matches the message contributes
its name to the hints list. The hints feed into the LLM eval as
context, not as an enforcing gate.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

# Default wall-clock ceiling for a `script` detector's subprocess. A check
# that hangs must not wedge the gate; overshooting counts as a failure to
# run, which surfaces as an error rather than a silent pass.
SCRIPT_TIMEOUT_SECONDS = 5.0


# Built-in lint: any registered check is automatically added to loaded rules
BuiltinLint = Callable[[str], bool]
_BUILTIN_LINTS: dict[str, tuple[str, BuiltinLint]] = {}


def register_builtin_lint(name: str, guidance: str, check: BuiltinLint) -> None:
    """Register a built-in lint check.

    ``check(text)`` returns True when the lint fires. Registrations are
    global and auto-injected by ``load_rules()``.
    """
    _BUILTIN_LINTS[name] = (guidance, check)


@dataclass(frozen=True)
class Rule:
    name: str
    detector: str           # "llm" | "regex" | "script" | "length"
    parameters: dict
    guidance: str
    source_file: str
    lint: bool = False      # True for MOP's bundled deterministic checks
    active: bool = True     # False only reachable via include_inactive=True


def _entry_is_active(entry: dict) -> bool:
    """Whether a YAML rule entry is active. Missing `active:` defaults to True."""
    return bool(entry.get("active", True))


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
    """Load rules from *.yml files in `rules_dir`.

    By default, only rules with `active: true` (or no `active` field) are
    returned. Pass `include_inactive=True` to get every rule regardless
    of flag — useful for the Studio UI which wants to surface inactive
    rules so users can toggle them on. Ignores legacy `severity` /
    `on_violation` fields.

    Built-in lints (registered via ``register_builtin_lint()``) are
    automatically appended to every result.
    """
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
                detector="script",
                parameters={},
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


def _run_script(rule: Rule, text: str) -> bool:
    """Run a `script` detector against `text`, return True if it fires.

    Two dispatch cases:
      - ``parameters.command`` present → run it as a subprocess, pipe
        `text` on stdin. Exit 0 = pass (no violation); non-zero = the
        rule fires. Same trust model as a git pre-commit hook: it runs
        code the user placed in their own repo's ``.mop/``.
      - otherwise → resolve an in-process check registered via
        ``register_builtin_lint`` by the rule's name (MOP's bundled
        deterministic checks keep this fast path — no process spawn).

    A command that cannot be run (missing, timeout, crash) raises rather
    than silently passing — a broken deterministic check must be loud.
    """
    command = rule.parameters.get("command")
    if command is None:
        entry = _BUILTIN_LINTS.get(rule.name)
        return entry[1](text) if entry is not None else False
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    try:
        completed = subprocess.run(
            argv,
            input=text,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"script rule {rule.name!r} failed to run {command!r}: {exc}"
        ) from exc
    return completed.returncode != 0


def _rule_matches(rule: Rule, text: str) -> bool:
    """Check whether a rule's deterministic detector fires on `text`.

    Handles `regex` (declarative patterns), `script` (subprocess or
    registered check), and `length` (character/word caps). Returns False
    for `llm` rules.
    """
    if rule.detector == "regex":
        return any(re.search(pat, text) for pat in rule.parameters.get("patterns", []))
    if rule.detector == "script":
        return _run_script(rule, text)
    if rule.detector == "length":
        return _exceeds_length(rule.parameters, text)
    return False


def _exceeds_length(params: dict, text: str) -> bool:
    """A `length` rule fires when a configured char/word cap is exceeded.

    `parameters` may carry `max_chars` and/or `max_words`; the rule fires
    if any present cap is exceeded. This is the configurable-per-interface
    "prevent huge messages" primitive (set a tight cap for chat, a loose
    one for a web UI) — declarative, so no external script and fast enough
    to run against the whole eval corpus.
    """
    max_chars = params.get("max_chars")
    if max_chars is not None and len(text) > max_chars:
        return True
    max_words = params.get("max_words")
    if max_words is not None and len(text.split()) > max_words:
        return True
    return False


def collect_regex_hints(text: str, rules: list[Rule]) -> list[str]:
    """Run all deterministic detectors against `text`. Return matching rule names.

    Only checks rules whose detector is NOT "llm" — i.e. `regex` and
    `script` rules. New code should use `collect_lint_hints` instead,
    which only checks entries with `lint: True`.

    These are advisory hints fed to the LLM eval as context. They are NOT
    authoritative — the LLM may still accept text that matches a regex,
    or rewrite/reject text that doesn't.
    """
    hints: list[str] = []
    for rule in rules:
        if rule.detector == "llm":
            continue
        if _rule_matches(rule, text):
            hints.append(rule.name)
    return hints


def collect_lint_hints(text: str, rules: list[Rule]) -> list[str]:
    """Run all lint entries against `text`. Return matching rule names.

    Only checks entries where `lint=True`. LLM rules and legacy
    deterministic rules without the lint flag are skipped. This is
    the preferred collection pathway for new code.
    """
    hints: list[str] = []
    for rule in rules:
        if not rule.lint:
            continue
        if _rule_matches(rule, text):
            hints.append(rule.name)
    return hints
