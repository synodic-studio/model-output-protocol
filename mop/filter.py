"""MOP filter — evaluates a message against active rules.

Entry points:
  evaluate(text, config)             → Verdict (AcceptedVerdict | RejectedVerdict | RewrittenVerdict)
  justify(original, reason, config)  → AcceptedVerdict | RejectedVerdict

`evaluate` checks outbound text; `justify` checks whether a stated reason for an action
is permitted by active rules (never shown to the user — purely internal gate).

Verdict cases:
  AcceptedVerdict        — no rule fired
  RejectedVerdict        — rule fired, on_violation=reject; carries violations list
  RewrittenVerdict       — rule fired, on_violation=edit; carries rewritten text once filled

Detectors:
  - deterministic/regex: re.search against patterns list
  - deterministic/word_count: len(text.split()) > max
  - llm: pydantic-ai call to a configurable LLM backend

LLM backend is configurable via MopConfig.llm_backend:
  - "stub": always returns Accept (default — zero dependency)
  - "haiku": claude-haiku-4-5 via pydantic-ai / Anthropic SDK
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

from pydantic import BaseModel

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

ACTIVE_RULES_DIR = Path(__file__).resolve().parent.parent / "rules" / "active"

_HAIKU_MODEL = "claude-haiku-4-5-20251001"


class Action(str, Enum):
    ACCEPT = "accept"
    EDIT = "edit"
    REJECT = "reject"


# ---------------------------------------------------------------------------
# Verdict — three-case discriminated union
# ---------------------------------------------------------------------------

class Verdict(BaseModel):
    """Base verdict. Concrete subclasses carry case-specific payloads."""
    action: Action
    rule: str | None = None
    reason: str | None = None
    guidance: str | None = None

    def __bool__(self) -> bool:
        return self.action == Action.ACCEPT


class AcceptedVerdict(Verdict):
    action: Literal[Action.ACCEPT] = Action.ACCEPT


class RejectedVerdict(Verdict):
    action: Literal[Action.REJECT] = Action.REJECT
    violations: list[str] = []


class RewrittenVerdict(Verdict):
    action: Literal[Action.EDIT] = Action.EDIT
    rewritten: str | None = None
    violations: list[str] = []


# ---------------------------------------------------------------------------
# Config + internal rule type
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

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


class _EvalResult(BaseModel):
    action: Literal["accept", "reject", "rewrite"]
    reason: str | None = None
    rewritten: str | None = None  # populated only when action="rewrite"


_eval_agent = None


def _get_eval_agent():
    global _eval_agent
    if _eval_agent is None:
        from pydantic_ai import Agent
        _eval_agent = Agent(_HAIKU_MODEL, result_type=_EvalResult)
    return _eval_agent


async def _eval_llm_haiku(rule: _Rule, text: str) -> _EvalResult:
    """Classify and optionally rewrite via pydantic-ai + haiku. Billed to ANTHROPIC_API_KEY."""
    prompt = rule.parameters.get("prompt", "")
    on_violation = rule.on_violation
    query = (
        f"{prompt.strip()}\n\n"
        f"On violation, the configured action is: '{on_violation}'.\n\n"
        "Message to evaluate:\n<message>\n"
        f"{text}\n</message>\n\n"
        "Choose one of three actions:\n"
        "- 'accept': the message does not violate the rule\n"
        "- 'reject': the message violates the rule and on_violation='reject'\n"
        "- 'rewrite': the message violates the rule and on_violation is not 'reject'; "
        "provide a corrected version of the message in the 'rewritten' field\n\n"
        "If action is 'rewrite', the 'rewritten' field must contain the full corrected text."
    )
    try:
        result = await _get_eval_agent().run(query)
        return result.data
    except Exception as exc:
        logger.warning("haiku eval failed for rule %s: %s — defaulting to no violation", rule.name, exc)
        return _EvalResult(action="accept", reason="eval error — defaulting to accept")


async def _eval_llm_gemma4(rule: _Rule, text: str) -> bool:
    import urllib.request
    prompt = rule.parameters.get("prompt", "")
    body = json.dumps({
        "model": "gemma4:e4b",
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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def evaluate(text: str, config: MopConfig | None = None) -> Verdict:
    """Evaluate text against all active rules. Returns AcceptedVerdict if none fire.

    Otherwise returns RejectedVerdict (if any rule rejects) carrying ALL rule
    names that fired, OR RewrittenVerdict (if rules only rewrote) with the
    final rewritten text and all rule names that fired. Rewrites are chained:
    each rule evaluates the latest rewritten text.

    Empty text always returns RejectedVerdict regardless of rules.
    """
    if not text.strip():
        return RejectedVerdict(
            rule="empty-message",
            violations=["empty-message"],
            reason="Response was empty",
            guidance=(
                "Your response was empty. You must send at least one text message per turn. "
                "Write a response and try again."
            ),
        )

    cfg = config or MopConfig()
    rules = _load_rules(cfg.rules_dir)

    violations: list[str] = []
    rejected_rules: list[str] = []
    current_text = text
    last_rewrite: str | None = None
    last_guidance: str | None = None
    last_reason: str | None = None

    for rule in rules:
        guidance = rule.guidance.strip() if rule.guidance else None
        fired = False
        rewritten_text: str | None = None

        if rule.detector == "deterministic":
            fired = _eval_deterministic(rule, current_text)
        elif rule.detector == "llm":
            if cfg.llm_backend == "haiku":
                llm_result = await _eval_llm_haiku(rule, current_text)
                if llm_result.action != "accept":
                    fired = True
                    if llm_result.action == "rewrite":
                        rewritten_text = llm_result.rewritten
            elif cfg.llm_backend == "gemma4":
                fired = await _eval_llm_gemma4(rule, current_text)
            else:
                fired = await _eval_llm_stub(rule, current_text)

        if not fired:
            continue

        if cfg.log_violations:
            logger.info("MOP violation: rule=%s on_violation=%s", rule.name, rule.on_violation)
        violations.append(rule.name)
        last_guidance = guidance
        last_reason = f"Rule '{rule.name}' fired"

        if rule.on_violation == "reject":
            rejected_rules.append(rule.name)
        elif rewritten_text is not None:
            last_rewrite = rewritten_text
            current_text = rewritten_text

    if not violations:
        return AcceptedVerdict()

    if rejected_rules:
        return RejectedVerdict(
            rule=rejected_rules[-1],
            violations=violations,
            reason=last_reason,
            guidance=last_guidance,
        )

    return RewrittenVerdict(
        rule=violations[-1],
        violations=violations,
        rewritten=last_rewrite,
        reason=last_reason,
        guidance=last_guidance,
    )


async def justify(
    original_text: str,
    reason: str,
    rule_names: list[str],
    config: MopConfig | None = None,
) -> AcceptedVerdict | RejectedVerdict:
    """Evaluate a reason against a specific subset of rules.

    Used when the agent wants to assert that a response is safe despite apparent
    violations — it submits a justification and MOP decides whether to accept it.

    Only the rules named in `rule_names` are evaluated (matched by name). If no
    named rules exist in the active set, returns AcceptedVerdict (nothing to check
    against). Rules with on_violation='edit' are treated as 'reject' here — justify
    never rewrites, it only approves or denies.

    Returns AcceptedVerdict if the reason satisfies all named rules, or
    RejectedVerdict listing which rules were not satisfied.
    """
    if not reason.strip():
        return RejectedVerdict(
            rule="empty-justification",
            violations=["empty-justification"],
            reason="Justification was empty",
            guidance="Provide a non-empty reason for why this response is acceptable.",
        )

    cfg = config or MopConfig()
    all_rules = _load_rules(cfg.rules_dir)
    rule_map = {r.name: r for r in all_rules}

    target_rules = [rule_map[n] for n in rule_names if n in rule_map]
    if not target_rules:
        return AcceptedVerdict()

    violations: list[str] = []
    last_guidance: str | None = None

    for rule in target_rules:
        guidance = rule.guidance.strip() if rule.guidance else None
        fired = False

        if rule.detector == "deterministic":
            fired = _eval_deterministic(rule, reason)
        elif rule.detector == "llm":
            if cfg.llm_backend == "haiku":
                llm_result = await _eval_llm_haiku(rule, reason)
                fired = llm_result.action != "accept"
            elif cfg.llm_backend == "gemma4":
                fired = await _eval_llm_gemma4(rule, reason)
            else:
                fired = await _eval_llm_stub(rule, reason)

        if fired:
            if cfg.log_violations:
                logger.info("MOP justify rejected: rule=%s", rule.name)
            violations.append(rule.name)
            last_guidance = guidance

    if not violations:
        return AcceptedVerdict()

    return RejectedVerdict(
        rule=violations[-1],
        violations=violations,
        reason=f"Justification did not satisfy rules: {', '.join(violations)}",
        guidance=last_guidance,
    )
