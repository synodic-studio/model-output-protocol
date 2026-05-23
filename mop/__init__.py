"""MOP — Model Output Protocol.

Stateful protocol library that gates an agent's user-facing output.
Hosts construct an `MOP` instance with an injected evaluator + deliver
callable, mount the in-process MCP server returned by `build_mcp_server`,
and register the Stop hook via the host's runtime.
"""

from .audit import Auditor, JsonlAuditor
from .evaluators import build_deepseek_evaluator, build_evaluator, build_haiku_evaluator
from .format_score import format_score
from .hooks import protocol_prompt, stop
from .mcp import build_mcp_server, build_tool_handlers
from .protocol import MOP
from .rules import Rule, collect_lint_hints, collect_regex_hints, load_rules, register_builtin_lint
from .types import (
    Accepted,
    AcceptedFailedOpen,
    Allow,
    Block,
    Deliver,
    EvalLLMResponse,
    Evaluator,
    Gate,
    NoPendingMessageError,
    Rejected,
    Rewritten,
    Verdict,
    verdict_from_eval_response,
)

# ─── Register built-in lints ───────────────────────────────────────────

_FORMAT_SCORE_THRESHOLD = 20.0


def _format_check(text: str) -> bool:
    result = format_score(text)
    return result["format_score"] > _FORMAT_SCORE_THRESHOLD


register_builtin_lint(
    "format-score-too-high",
    "Message has a high format score (long-winded, prose-heavy, or wraps"
    " badly). Consider breaking it into structured shorter pieces.",
    _format_check,
)

__all__ = [
    "MOP",
    "build_mcp_server",
    "build_tool_handlers",
    "build_deepseek_evaluator",
    "build_evaluator",
    "build_haiku_evaluator",
    "protocol_prompt",
    "stop",
    "Auditor",
    "JsonlAuditor",
    "Rule",
    "load_rules",
    "collect_lint_hints",
    "collect_regex_hints",
    "Accepted",
    "AcceptedFailedOpen",
    "Rewritten",
    "Rejected",
    "Verdict",
    "Allow",
    "Block",
    "Gate",
    "NoPendingMessageError",
    "Evaluator",
    "Deliver",
    "EvalLLMResponse",
    "verdict_from_eval_response",
]
