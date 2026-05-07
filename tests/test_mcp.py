"""MCP server construction. Tests at the @tool-handler level (no live SDK)."""

import json

import pytest

from mop.mcp import build_tool_handlers
from mop.protocol import MOP
from mop.types import Accepted, Rejected


@pytest.fixture
def deliveries():
    return []


@pytest.fixture
def mop_instance(deliveries):
    async def evaluator(text, regex_hints, justification):
        if "bad" in text:
            return Rejected(violations=["no-bad-words"])
        return Accepted()

    async def deliver(text, system_note=None):
        deliveries.append((text, system_note))

    return MOP(rules=[], evaluator=evaluator, deliver=deliver)


@pytest.mark.asyncio
async def test_submit_message_handler_returns_mcp_content(mop_instance, deliveries):
    handlers = build_tool_handlers(mop_instance)
    result = await handlers["submit_message"]({"message": "hello"})
    # MCP tool result shape: {"content": [{"type": "text", "text": "..."}], "is_error"?: bool}
    assert "content" in result
    assert result["content"][0]["type"] == "text"
    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "accepted"
    assert deliveries == [("hello", None)]


@pytest.mark.asyncio
async def test_submit_message_rejected_serializes_violations(mop_instance):
    handlers = build_tool_handlers(mop_instance)
    result = await handlers["submit_message"]({"message": "bad input"})
    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "rejected"
    assert payload["violations"] == ["no-bad-words"]


@pytest.mark.asyncio
async def test_submit_justification_no_pending_returns_error_result(mop_instance):
    handlers = build_tool_handlers(mop_instance)
    result = await handlers["submit_justification"]({"justification": "I want to"})
    assert result.get("is_error") is True
    assert "no pending" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_get_status_returns_current_state(mop_instance):
    handlers = build_tool_handlers(mop_instance)
    # Force some state.
    await handlers["submit_message"]({"message": "bad"})  # rejected, pending set
    result = await handlers["get_status"]({})
    payload = json.loads(result["content"][0]["text"])
    assert payload["pending"] == "bad"
    assert payload["sent_this_turn"] is False
    assert payload["just_attempts"] == 0


@pytest.mark.asyncio
async def test_get_rules_returns_list(mop_instance):
    handlers = build_tool_handlers(mop_instance)
    result = await handlers["get_rules"]({})
    payload = json.loads(result["content"][0]["text"])
    assert payload["rules"] == []  # mop_instance has no rules
