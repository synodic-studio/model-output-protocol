"""MCP tool wrappers around a MOP instance.

`build_mcp_server(mop)` returns a `McpSdkServerConfig` ready to plug
into `ClaudeAgentOptions.mcp_servers`. Tools close over the MOP instance,
so all state lives in the host process — one MOP per CC session.

The four tools mirror MOP's protocol surface:
  - submit_message(message)
  - submit_justification(justification)
  - get_rules(filter?)
  - get_status()

Tool results are JSON-serialized verdict payloads inside the MCP
text-content envelope, so the agent sees structured data it can branch on.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Awaitable, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

from .protocol import MOP
from .types import (
    Accepted,
    AcceptedFailedOpen,
    NoPendingMessageError,
    Rejected,
    Rewritten,
    Verdict,
)


def _verdict_payload(v: Verdict) -> dict[str, Any]:
    """Serialize a Verdict to a dict the agent can branch on."""
    if isinstance(v, Accepted):
        return {"verdict": "accepted"}
    if isinstance(v, AcceptedFailedOpen):
        return {"verdict": "accepted_failed_open", "system_note": v.system_note}
    if isinstance(v, Rewritten):
        return {"verdict": "rewritten", "rewritten": v.rewritten}
    if isinstance(v, Rejected):
        return {"verdict": "rejected", "violations": v.violations}
    raise ValueError(f"unknown verdict type: {type(v).__name__}")


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _err(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


# Public for unit testing — returns the per-tool async handlers without the
# SDK wrapping. `build_mcp_server` is the production entry that wraps these
# in @tool decorators and registers them with create_sdk_mcp_server.
def build_tool_handlers(
    mop: MOP,
) -> dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]:
    async def submit_message(args: dict[str, Any]) -> dict[str, Any]:
        msg = args["message"]
        verdict = await mop.submit_message(msg)
        return _ok(_verdict_payload(verdict))

    async def submit_justification(args: dict[str, Any]) -> dict[str, Any]:
        try:
            verdict = await mop.submit_justification(args["justification"])
        except NoPendingMessageError as e:
            return _err(f"no pending message to justify: {e}")
        return _ok(_verdict_payload(verdict))

    async def get_rules(args: dict[str, Any]) -> dict[str, Any]:
        regex_filter = args.get("filter")
        rules = mop.get_rules(regex_filter)
        return _ok({"rules": [asdict(r) for r in rules]})

    async def get_status(args: dict[str, Any]) -> dict[str, Any]:
        return _ok(mop.get_status())

    return {
        "submit_message": submit_message,
        "submit_justification": submit_justification,
        "get_rules": get_rules,
        "get_status": get_status,
    }


def build_mcp_server(mop: MOP, *, name: str = "mop", version: str = "1.0.0"):
    """Return an McpSdkServerConfig for ClaudeAgentOptions.mcp_servers."""
    handlers = build_tool_handlers(mop)

    @tool(
        "submit_message",
        "Submit a message for delivery to the user. Returns one of "
        "{accepted, rewritten, rejected, accepted_failed_open}. Only "
        "accepted/rewritten/accepted_failed_open actually deliver to the user.",
        {"message": str},
    )
    async def _submit_message(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["submit_message"](args)

    @tool(
        "submit_justification",
        "Argue why a previously-rejected message should still be delivered. "
        "Returns the same verdict shape as submit_message. Capped at 4 "
        "attempts before MOP failed-opens and delivers your original with "
        "a warning to the user.",
        {"justification": str},
    )
    async def _submit_justification(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["submit_justification"](args)

    @tool(
        "get_rules",
        "List active rules. Optional regex filter matches rule names and guidance.",
        {"filter": str | None},
    )
    async def _get_rules(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["get_rules"](args)

    @tool(
        "get_status",
        "Return current MOP state: pending_message (most recent rejection), "
        "sent_this_turn (have you delivered anything this turn?), "
        "just_attempts (how many justification calls used).",
        {},
    )
    async def _get_status(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["get_status"](args)

    return create_sdk_mcp_server(
        name=name,
        version=version,
        tools=[_submit_message, _submit_justification, _get_rules, _get_status],
    )
