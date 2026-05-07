"""MOP — Model Output Protocol.

Stateful protocol library that gates an agent's user-facing output.
Hosts construct an `MOP` instance with an injected evaluator + deliver
callable, mount the in-process MCP server returned by `build_mcp_server`,
and register the Stop hook via the host's runtime.
"""

from .haiku import build_haiku_evaluator
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
    "MOP",
    "build_mcp_server",
    "build_tool_handlers",
    "build_haiku_evaluator",
    "protocol_prompt",
    "stop",
    "Rule",
    "load_rules",
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
