"""LLM evaluators for MOP — pluggable backends for rule evaluation.

`build_evaluator(rules)` is the main entrypoint. It reads `MOP_EVALUATOR`
to pick the backend:

  MOP_EVALUATOR=deepseek  (default)  → DeepSeek v4 flash via OpenAI-compatible API
  MOP_EVALUATOR=haiku                 → Anthropic Claude Haiku via Anthropic API

Each builder returns an `Evaluator`-shaped callable suitable for passing
into `MOP(evaluator=...)`. The callable batches every active rule into a
single prompt and asks the LLM for one structured verdict per submission.

The structured-output schema (`mop.types.EvalLLMResponse`) is owned by
the protocol. Adapters hand it to their LLM library for output decoding,
then call `verdict_from_eval_response()` to translate the response into
a runtime `Verdict`.

API keys:
  DeepSeek: MOP_DEEPSEEK_API_KEY or DEEPSEEK_API_KEY
  Haiku:    MOP_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY

Model overrides:
  MOP_DEEPSEEK_MODEL   default: deepseek-chat
  MOP_HAIKU_MODEL      default: claude-haiku-4-5-20251001
"""

from __future__ import annotations

import os
from typing import Awaitable, Callable

from .rules import Rule
from .types import EvalLLMResponse, Verdict, verdict_from_eval_response

DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_HAIKU_MODEL = "claude-haiku-4-5-20251001"

EVALUATOR_KEY = "MOP_EVALUATOR"


# ---------------------------------------------------------------------------
# DeepSeek (OpenAI-compatible)
# ---------------------------------------------------------------------------


def _build_deepseek_agent():
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider

    api_key = os.environ.get("MOP_DEEPSEEK_API_KEY") or os.environ.get(
        "DEEPSEEK_API_KEY"
    )
    if not api_key:
        raise RuntimeError(
            "No API key for MOP deepseek evaluator. "
            "Set MOP_DEEPSEEK_API_KEY or DEEPSEEK_API_KEY."
        )
    model_id = os.environ.get("MOP_DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
    model = OpenAIModel(
        model_id,
        provider=OpenAIProvider(
            base_url="https://api.deepseek.com",
            api_key=api_key,
        ),
    )
    return Agent(model, output_type=EvalLLMResponse)


def build_deepseek_evaluator(
    *, rules: list[Rule]
) -> Callable[[str, list[str], str | None], Awaitable[Verdict]]:
    """Return an Evaluator-shaped callable wrapping DeepSeek v4 flash."""
    _agent = None

    async def evaluate(
        text: str, regex_hints: list[str], justification: str | None
    ) -> Verdict:
        nonlocal _agent
        if _agent is None:
            _agent = _build_deepseek_agent()

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
        query = (
            "You are a message gate. Active rules:\n"
            f"{rule_lines}\n\n"
            f"{hint_line}\n\n"
            "Message under review:\n"
            f"<message>\n{text}\n</message>\n"
            f"{just_line}\n\n"
            "Decide one of three actions:\n"
            "  - accept: message passes all rules, deliver as-is\n"
            "  - rewrite: message violates style but is fixable; provide the corrected text in 'rewritten'\n"
            "  - reject: message violates substantive rules; list the violated rule names in 'violations'\n"
        )
        result = await _agent.run(query)
        return verdict_from_eval_response(result.output, original_text=text)

    return evaluate


# ---------------------------------------------------------------------------
# Haiku (Anthropic)
# ---------------------------------------------------------------------------


def _build_haiku_agent():
    from pydantic_ai import Agent
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    api_key = os.environ.get("MOP_ANTHROPIC_API_KEY") or os.environ.get(
        "ANTHROPIC_API_KEY"
    )
    if not api_key:
        raise RuntimeError(
            "No API key for MOP haiku evaluator. Set MOP_ANTHROPIC_API_KEY "
            "(preferred — kept out of spawned subprocesses) or ANTHROPIC_API_KEY."
        )
    model_id = os.environ.get("MOP_HAIKU_MODEL", DEFAULT_HAIKU_MODEL)
    model = AnthropicModel(model_id, provider=AnthropicProvider(api_key=api_key))
    return Agent(model, output_type=EvalLLMResponse)


def build_haiku_evaluator(
    *, rules: list[Rule]
) -> Callable[[str, list[str], str | None], Awaitable[Verdict]]:
    """Return an Evaluator-shaped callable wrapping Haiku via pydantic-ai."""
    _agent = None

    async def evaluate(
        text: str, regex_hints: list[str], justification: str | None
    ) -> Verdict:
        nonlocal _agent
        if _agent is None:
            _agent = _build_haiku_agent()

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
        query = (
            "You are a message gate. Active rules:\n"
            f"{rule_lines}\n\n"
            f"{hint_line}\n\n"
            "Message under review:\n"
            f"<message>\n{text}\n</message>\n"
            f"{just_line}\n\n"
            "Decide one of three actions:\n"
            "  - accept: message passes all rules, deliver as-is\n"
            "  - rewrite: message violates style but is fixable; provide the corrected text in 'rewritten'\n"
            "  - reject: message violates substantive rules; list the violated rule names in 'violations'\n"
        )
        result = await _agent.run(query)
        return verdict_from_eval_response(result.output, original_text=text)

    return evaluate


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_evaluator(
    *, rules: list[Rule]
) -> Callable[[str, list[str], str | None], Awaitable[Verdict]]:
    """Build an evaluator based on the MOP_EVALUATOR environment variable.

    Defaults to ``deepseek``. Pass ``MOP_EVALUATOR=haiku`` to use Anthropic
    instead.
    """
    backend = os.environ.get(EVALUATOR_KEY, "deepseek").strip().lower()
    if backend == "haiku":
        return build_haiku_evaluator(rules=rules)
    if backend == "deepseek":
        return build_deepseek_evaluator(rules=rules)
    msg = f"Unknown MOP_EVALUATOR={backend!r}. Expected 'deepseek' or 'haiku'."
    raise ValueError(msg)
