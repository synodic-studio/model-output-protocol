"""Evaluator wrappers — tests the ADAPTERS, not live LLMs."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mop import build_deepseek_evaluator, build_evaluator, build_haiku_evaluator
from mop.types import Accepted, EvalLLMResponse, Rejected, Rewritten


def _fake_agent_returning(response: EvalLLMResponse):
    """Helper: build a MagicMock that mimics pydantic-ai's Agent.run result shape."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=MagicMock(output=response))
    return agent


# ---------------------------------------------------------------------------
# Haiku adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_haiku_returns_accepted_when_llm_says_accept():
    fake_agent = _fake_agent_returning(EvalLLMResponse(action="accept"))
    with patch("mop.evaluators._build_haiku_agent", return_value=fake_agent):
        evaluator = build_haiku_evaluator(rules=[])
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)


@pytest.mark.asyncio
async def test_haiku_returns_rewritten_with_payload():
    fake_agent = _fake_agent_returning(
        EvalLLMResponse(action="rewrite", rewritten="cleaned up")
    )
    with patch("mop.evaluators._build_haiku_agent", return_value=fake_agent):
        evaluator = build_haiku_evaluator(rules=[])
        v = await evaluator("messy", [], None)
    assert isinstance(v, Rewritten)
    assert v.rewritten == "cleaned up"


@pytest.mark.asyncio
async def test_haiku_returns_rejected_with_violations():
    fake_agent = _fake_agent_returning(
        EvalLLMResponse(action="reject", violations=["rule-x"])
    )
    with patch("mop.evaluators._build_haiku_agent", return_value=fake_agent):
        evaluator = build_haiku_evaluator(rules=[])
        v = await evaluator("bad", [], None)
    assert isinstance(v, Rejected)
    assert v.violations == ["rule-x"]


@pytest.mark.asyncio
async def test_haiku_raises_runtime_error_when_no_api_key(monkeypatch):
    monkeypatch.delenv("MOP_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    evaluator = build_haiku_evaluator(rules=[])
    with pytest.raises(RuntimeError, match="API key"):
        await evaluator("anything", [], None)


# ---------------------------------------------------------------------------
# DeepSeek adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deepseek_returns_accepted_when_llm_says_accept():
    fake_agent = _fake_agent_returning(EvalLLMResponse(action="accept"))
    with patch("mop.evaluators._build_deepseek_agent", return_value=fake_agent):
        evaluator = build_deepseek_evaluator(rules=[])
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)


@pytest.mark.asyncio
async def test_deepseek_returns_rewritten_with_payload():
    fake_agent = _fake_agent_returning(
        EvalLLMResponse(action="rewrite", rewritten="cleaned up")
    )
    with patch("mop.evaluators._build_deepseek_agent", return_value=fake_agent):
        evaluator = build_deepseek_evaluator(rules=[])
        v = await evaluator("messy", [], None)
    assert isinstance(v, Rewritten)
    assert v.rewritten == "cleaned up"


@pytest.mark.asyncio
async def test_deepseek_returns_rejected_with_violations():
    fake_agent = _fake_agent_returning(
        EvalLLMResponse(action="reject", violations=["rule-x"])
    )
    with patch("mop.evaluators._build_deepseek_agent", return_value=fake_agent):
        evaluator = build_deepseek_evaluator(rules=[])
        v = await evaluator("bad", [], None)
    assert isinstance(v, Rejected)
    assert v.violations == ["rule-x"]


@pytest.mark.asyncio
async def test_deepseek_raises_runtime_error_when_no_api_key(monkeypatch):
    monkeypatch.delenv("MOP_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    evaluator = build_deepseek_evaluator(rules=[])
    with pytest.raises(RuntimeError, match="API key"):
        await evaluator("anything", [], None)


# ---------------------------------------------------------------------------
# Factory dispatch (build_evaluator)
# ---------------------------------------------------------------------------


def test_build_evaluator_defaults_to_deepseek(monkeypatch):
    """No MOP_EVALUATOR set should use deepseek."""
    monkeypatch.delenv("MOP_EVALUATOR", raising=False)
    fn = build_evaluator(rules=[])
    # Verify by calling it with a patched _build_deepseek_agent
    fake_agent = _fake_agent_returning(EvalLLMResponse(action="accept"))
    from mop import evaluators as ev
    with patch("mop.evaluators._build_deepseek_agent", return_value=fake_agent):
        import asyncio
        v = asyncio.run(fn("hello", [], None))
    assert isinstance(v, Accepted)


def test_build_evaluator_deepseek_when_envar_set(monkeypatch):
    monkeypatch.setenv("MOP_EVALUATOR", "deepseek")
    fn = build_evaluator(rules=[])
    fake_agent = _fake_agent_returning(EvalLLMResponse(action="accept"))
    with patch("mop.evaluators._build_deepseek_agent", return_value=fake_agent):
        import asyncio
        v = asyncio.run(fn("hello", [], None))
    assert isinstance(v, Accepted)


def test_build_evaluator_haiku_when_envar_set(monkeypatch):
    monkeypatch.setenv("MOP_EVALUATOR", "haiku")
    fn = build_evaluator(rules=[])
    fake_agent = _fake_agent_returning(EvalLLMResponse(action="accept"))
    with patch("mop.evaluators._build_haiku_agent", return_value=fake_agent):
        import asyncio
        v = asyncio.run(fn("hello", [], None))
    assert isinstance(v, Accepted)


def test_build_evaluator_raises_on_unknown_backend(monkeypatch):
    monkeypatch.setenv("MOP_EVALUATOR", "gpt-4")
    with pytest.raises(ValueError, match="Unknown MOP_EVALUATOR"):
        build_evaluator(rules=[])


@pytest.mark.asyncio
async def test_build_evaluator_deepseek_runs(monkeypatch):
    """Integration check: factory returns a callable that works when patched."""
    monkeypatch.setenv("MOP_EVALUATOR", "deepseek")
    fake_agent = _fake_agent_returning(EvalLLMResponse(action="accept"))
    with patch("mop.evaluators._build_deepseek_agent", return_value=fake_agent):
        evaluator = build_evaluator(rules=[])
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)


@pytest.mark.asyncio
async def test_build_evaluator_haiku_runs(monkeypatch):
    """Integration check: factory returns a callable that works when patched."""
    monkeypatch.setenv("MOP_EVALUATOR", "haiku")
    fake_agent = _fake_agent_returning(EvalLLMResponse(action="accept"))
    with patch("mop.evaluators._build_haiku_agent", return_value=fake_agent):
        evaluator = build_evaluator(rules=[])
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)
