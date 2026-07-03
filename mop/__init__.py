"""MOP — Model Output Protocol.

Stateful protocol library that gates an agent's user-facing output.
Hosts construct an `MOP` instance with an injected evaluator + deliver
callable, mount the in-process MCP server returned by `build_mcp_server`,
and register the Stop hook via the host's runtime.
"""

from .audit import Auditor, JsonlAuditor
from .cli import check
from .discovery import find_local_rules_dir, load_builtin_rules, resolve_rules
from .evaluators import build_evaluator, build_litellm_evaluator
from .format_score import format_score
from .hooks import protocol_prompt, stop
from .mcp import build_mcp_server, build_tool_handlers
from .protocol import MOP
from .rules import (
    Rule,
    collect_lint_hints,
    collect_regex_hints,
    load_rules,
    load_rules_file,
    merge_rules,
    register_builtin_lint,
)
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
    "build_evaluator",
    "build_litellm_evaluator",
    "check",
    "protocol_prompt",
    "stop",
    "Auditor",
    "JsonlAuditor",
    "Rule",
    "find_local_rules_dir",
    "load_builtin_rules",
    "load_rules",
    "load_rules_file",
    "merge_rules",
    "resolve_rules",
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
