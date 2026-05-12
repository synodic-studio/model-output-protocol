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
    detector: str           # "regex" | "llm" | "word_count" (passed via parameters.type)
    parameters: dict
    guidance: str
    source_file: str


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
                )
            )
    return rules


def collect_regex_hints(text: str, rules: list[Rule]) -> list[str]:
    """Run all regex/word_count detectors against `text`. Return matching rule names.

    These are advisory hints fed to the LLM eval as context. They are NOT
    authoritative — the LLM may still accept text that matches a regex,
    or rewrite/reject text that doesn't.
    """
    hints: list[str] = []
    for rule in rules:
        if rule.detector != "regex":
            continue
        params = rule.parameters
        dtype = params.get("type")
        matched = False
        if dtype == "regex":
            matched = any(re.search(pat, text) for pat in params.get("patterns", []))
        elif dtype == "word_count":
            matched = len(text.split()) > params.get("max", 0)
        if matched:
            hints.append(rule.name)
    return hints
