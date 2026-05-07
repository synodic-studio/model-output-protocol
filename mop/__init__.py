"""MOP — Model Output Protocol.

Evaluates agent output against active rules and rewrites violations.
"""

from .filter import (
    Action,
    AcceptedVerdict,
    MopConfig,
    RejectedVerdict,
    RewrittenVerdict,
    evaluate,
    justify,
)
from .rewrite import TelegramMessage, rewrite

# v2 protocol entry points (for patchbay's cc-sdk-mop harness).
from .hooks import protocol_prompt, stop
from .mcp import build_mcp_server, build_tool_handlers
from .protocol import MOP
from .rules import Rule, collect_regex_hints, load_rules
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

__all__ = [
    # v2 protocol
    "MOP",
    "build_mcp_server",
    "build_tool_handlers",
    "protocol_prompt",
    "stop",
    "Rule",
    "load_rules",
    "collect_regex_hints",
    # types
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
    # legacy (existing exports — keep)
    "AcceptedVerdict",
    "RejectedVerdict",
    "RewrittenVerdict",
    "TelegramMessage",
    "evaluate",
    "justify",
    "MopConfig",
    "Action",
    "rewrite",
]
