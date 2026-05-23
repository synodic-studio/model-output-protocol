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

import yaml


@dataclass(frozen=True)
class Rule:
    name: str
    detector: str           # "llm" | "deterministic" | "regex" (legacy)
    parameters: dict
    guidance: str
    source_file: str
    lint: bool = False      # True for deterministic pattern checks (advisory)


def _entry_is_active(entry: dict) -> bool:
    """Whether a YAML rule entry is active. Missing `active:` defaults to True."""
    return bool(entry.get("active", True))


def load_rules(rules_dir: Path, *, include_inactive: bool = False) -> list[Rule]:
    """Load rules from *.yml files in `rules_dir`.

    By default, only rules with `active: true` (or no `active` field) are
    returned. Pass `include_inactive=True` to get every rule regardless
    of flag — useful for the Studio UI which wants to surface inactive
    rules so users can toggle them on. Ignores legacy `severity` /
    `on_violation` fields.
    """
    rules: list[Rule] = []
    for path in sorted(rules_dir.rglob("*.yml")):
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        for entry in data.get("rules", []):
            if not include_inactive and not _entry_is_active(entry):
                continue
            rules.append(
                Rule(
                    name=entry["name"],
                    detector=entry["detector"],
                    parameters=entry.get("parameters", {}),
                    guidance=entry.get("guidance", ""),
                    source_file=str(path.relative_to(rules_dir)),
                    lint=bool(entry.get("lint", False)),
                )
            )
    return rules


def _rule_matches(rule: Rule, text: str) -> bool:
    """Check whether a rule's deterministic detector fires on `text`.

    Supports regex and word_count types. Returns False for LLM rules.
    """
    if rule.detector == "llm":
        return False
    params = rule.parameters
    dtype = params.get("type")
    if dtype == "regex":
        return any(re.search(pat, text) for pat in params.get("patterns", []))
    if dtype == "word_count":
        return len(text.split()) > params.get("max", 0)
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
