"""MOP filter — evaluates a message against active rules.

Entry point: `evaluate(text, rules_dir)` returns a `Verdict`.

Detectors:
  - deterministic/regex: re.search against patterns list
  - deterministic/word_count: len(text.split()) > max
  - llm: async call to a configurable LLM backend

LLM backend is configurable via MopConfig.llm_backend:
  - "stub": always returns Accept (default — zero dependency)
  - "haiku": claude-haiku-4-5 via Anthropic SDK
  - "gemma4": local ollama model (gemma4:e4b or gemma4:e2b)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

ACTIVE_RULES_DIR = Path(__file__).resolve().parent.parent / "rules" / "active"


class Action(str, Enum):
    ACCEPT = "accept"
    EDIT = "edit"
    REJECT = "reject"


@dataclass
class Verdict:
    action: Action
    rule: str | None = None
    reason: str | None = None
    guidance: str | None = None

    def __bool__(self) -> bool:
        return self.action == Action.ACCEPT


@dataclass
class MopConfig:
    rules_dir: Path = field(default_factory=lambda: ACTIVE_RULES_DIR)
    llm_backend: Literal["stub", "haiku", "gemma4"] = "stub"
    llm_model: str | None = None
    log_violations: bool = True


@dataclass
class _Rule:
    name: str
    detector: str
    parameters: dict
    severity: str
    on_violation: str
    guidance: str
    source_file: str


def _load_rules(rules_dir: Path) -> list[_Rule]:
    if yaml is None:
        raise ImportError("PyYAML required: pip install pyyaml")
    rules: list[_Rule] = []
    for path in sorted(rules_dir.rglob("*.yml")):
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        for entry in data.get("rules", []):
            rules.append(
                _Rule(
                    name=entry["name"],
                    detector=entry["detector"],
                    parameters=entry.get("parameters", {}),
                    severity=entry.get("severity", "warn"),
                    on_violation=entry.get("on_violation", "warn"),
                    guidance=entry.get("guidance", ""),
                    source_file=str(path.relative_to(rules_dir)),
                )
            )
    return rules


def _eval_deterministic(rule: _Rule, text: str) -> bool:
    params = rule.parameters
    dtype = params.get("type")
    if dtype == "regex":
        return any(re.search(pat, text) for pat in params.get("patterns", []))
    if dtype == "word_count":
        return len(text.split()) > params.get("max", 0)
    return False


async def _eval_llm_stub(rule: _Rule, text: str) -> bool:
    logger.debug("LLM eval stub — rule %s always returns False (no violation)", rule.name)
    return False


async def _eval_llm_haiku(rule: _Rule, text: str) -> bool:
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — falling back to stub for %s", rule.name)
        return False
    prompt = rule.parameters.get("prompt", "")
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=64,
        messages=[{
            "role": "user",
            "content": (
                f"{prompt.strip()}\n\nMessage to evaluate:\n<message>\n{text}\n</message>\n\n"
                "Reply with a single JSON object: {\"violation\": true} or {\"violation\": false}."
            ),
        }],
    )
    raw = resp.content[0].text.strip()
    try:
        return bool(json.loads(raw).get("violation"))
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Haiku returned non-JSON for rule %s: %r", rule.name, raw[:80])
        return False


async def _eval_llm_gemma4(rule: _Rule, text: str) -> bool:
    import urllib.request
    prompt = rule.parameters.get("prompt", "")
    model = "gemma4:e4b"
    body = json.dumps({
        "model": model,
        "prompt": (
            f"{prompt.strip()}\n\nMessage:\n{text}\n\n"
            "Reply with JSON only: {\"violation\": true} or {\"violation\": false}."
        ),
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        raw = data.get("response", "").strip()
        return bool(json.loads(raw).get("violation"))
    except Exception as exc:
        logger.warning("Gemma4 eval failed for rule %s: %s — defaulting to no violation", rule.name, exc)
        return False


async def evaluate(text: str, config: MopConfig | None = None) -> Verdict:
    """Evaluate text against all active rules. Returns the first violation found, or Accept."""
    cfg = config or MopConfig()
    rules = _load_rules(cfg.rules_dir)

    for rule in rules:
        fired = False

        if rule.detector == "deterministic":
            fired = _eval_deterministic(rule, text)
        elif rule.detector == "llm":
            if cfg.llm_backend == "haiku":
                fired = await _eval_llm_haiku(rule, text)
            elif cfg.llm_backend == "gemma4":
                fired = await _eval_llm_gemma4(rule, text)
            else:
                fired = await _eval_llm_stub(rule, text)

        if fired:
            action = Action.REJECT if rule.on_violation == "reject" else Action.EDIT
            verdict = Verdict(
                action=action,
                rule=rule.name,
                reason=f"Rule '{rule.name}' fired",
                guidance=rule.guidance.strip() if rule.guidance else None,
            )
            if cfg.log_violations:
                logger.info(
                    "MOP violation: rule=%s action=%s",
                    rule.name,
                    action.value,
                )
            return verdict

    return Verdict(action=Action.ACCEPT)
