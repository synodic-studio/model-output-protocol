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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml


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
    detector: str           # "llm" | "deterministic" | "regex" (legacy)
    parameters: dict
    guidance: str
    source_file: str
    lint: bool = False      # True for deterministic pattern checks (advisory)
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


def _rule_matches(rule: Rule, text: str) -> bool:
    """Check whether a rule's deterministic detector fires on `text`.

    Supports regex, word_count, and builtin_lint types. Returns False
    for LLM rules.
    """
    if rule.detector == "llm":
        return False
    params = rule.parameters
    dtype = params.get("type")
    if dtype == "regex":
        return any(re.search(pat, text) for pat in params.get("patterns", []))
    if dtype == "word_count":
        return len(text.split()) > params.get("max", 0)
    if dtype == "builtin_lint":
        entry = _BUILTIN_LINTS.get(rule.name)
        if entry is not None:
            return entry[1](text)
    return False


def collect_regex_hints(text: str, rules: list[Rule]) -> list[str]:
    """Run all regex/word_count detectors against `text`. Return matching rule names.

    Only checks rules whose detector is NOT "llm" — in practice this means
    legacy deterministic rules (without `lint: true`). New code should
    use `collect_lint_hints` instead, which only checks entries with
    `lint: True`.

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
