"""Haiku-via-pydantic-ai evaluator wrapper. Tests the ADAPTER, not live haiku."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mop import build_haiku_evaluator
from mop.types import Accepted, EvalLLMResponse, Rejected, Rewritten


def _fake_agent_returning(response: EvalLLMResponse):
    """Helper: build a MagicMock that mimics pydantic-ai's Agent.run result shape."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(output=response))
    return agent


@pytest.mark.asyncio
async def test_evaluator_returns_accepted_when_llm_says_accept():
    """The adapter hands mop's EvalLLMResponse to pydantic-ai and translates back."""
    fake_agent = _fake_agent_returning(EvalLLMResponse(action="accept"))
    with patch("mop.haiku._build_eval_agent", return_value=fake_agent):
        evaluator = build_haiku_evaluator(rules=[])
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)


@pytest.mark.asyncio
async def test_evaluator_returns_rewritten_with_payload():
    fake_agent = _fake_agent_returning(
        EvalLLMResponse(action="rewrite", rewritten="cleaned up")
    )
    with patch("mop.haiku._build_eval_agent", return_value=fake_agent):
        evaluator = build_haiku_evaluator(rules=[])
        v = await evaluator("messy", [], None)
    assert isinstance(v, Rewritten)
    assert v.rewritten == "cleaned up"


@pytest.mark.asyncio
async def test_evaluator_returns_rejected_with_violations():
    fake_agent = _fake_agent_returning(
        EvalLLMResponse(action="reject", violations=["rule-x"])
    )
    with patch("mop.haiku._build_eval_agent", return_value=fake_agent):
        evaluator = build_haiku_evaluator(rules=[])
        v = await evaluator("bad", [], None)
    assert isinstance(v, Rejected)
    assert v.violations == ["rule-x"]


@pytest.mark.asyncio
async def test_evaluator_raises_runtime_error_when_no_api_key(monkeypatch):
    monkeypatch.delenv("MOP_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    evaluator = build_haiku_evaluator(rules=[])
    with pytest.raises(RuntimeError, match="API key"):
        await evaluator("anything", [], None)
