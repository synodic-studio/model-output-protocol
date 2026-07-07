"""Evaluator wrapper — tests the litellm ADAPTER, not live LLMs."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mop import build_evaluator, build_litellm_evaluator
from mop.evaluators import DEFAULT_MODEL, resolve_model
from mop.types import Accepted, Rejected, Rewritten


def _fake_completion(payload: dict):
    """Mimic litellm.acompletion's response shape: choices[0].message.content."""
    message = MagicMock()
    message.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return AsyncMock(return_value=response)


def _patched(payload: dict):
    """Patch acompletion + force the json_object fallback path."""
    return (
        patch("litellm.acompletion", _fake_completion(payload)),
        patch("litellm.supports_response_schema", return_value=False),
    )


# ---------------------------------------------------------------------------
# Verdict mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_accepted_when_llm_says_accept():
    p1, p2 = _patched({"action": "accept"})
    with p1, p2:
        evaluator = build_litellm_evaluator(rules=[])
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)


@pytest.mark.asyncio
async def test_returns_rewritten_with_payload():
    p1, p2 = _patched({"action": "rewrite", "rewritten": "cleaned up"})
    with p1, p2:
        evaluator = build_litellm_evaluator(rules=[])
        v = await evaluator("messy", [], None)
    assert isinstance(v, Rewritten)
    assert v.rewritten == "cleaned up"


@pytest.mark.asyncio
async def test_returns_rejected_with_unresolved():
    # No rewrite + non-empty unresolved → derived Rejected.
    p1, p2 = _patched({"rewritten": None, "unresolved": ["rule-x"]})
    with p1, p2:
        evaluator = build_litellm_evaluator(rules=[])
        v = await evaluator("bad", [], None)
    assert isinstance(v, Rejected)
    assert v.unresolved == ["rule-x"]


@pytest.mark.asyncio
async def test_retries_without_response_format_when_provider_rejects_it():
    """A provider that rejects response_format (e.g. DeepSeek) is retried once
    without it — the prompt already asks for JSON."""
    good = _fake_completion({"rewritten": None, "unresolved": []})
    call_kwargs = []

    async def flaky_acompletion(*args, **kwargs):
        call_kwargs.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError(
                "DeepseekException - This response_format type is unavailable now"
            )
        return await good(*args, **kwargs)

    with patch("litellm.acompletion", flaky_acompletion), patch(
        "litellm.supports_response_schema", return_value=False
    ):
        evaluator = build_litellm_evaluator(rules=[], model="deepseek/deepseek-v4-flash")
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)
    assert len(call_kwargs) == 2  # first with response_format, retry without
    assert "response_format" not in call_kwargs[1]


@pytest.mark.asyncio
async def test_steps_down_from_json_schema_to_json_object():
    """When json_schema is rejected but json_object works (DeepSeek v4-flash),
    the ladder lands on json_object — NOT the prompt-only last resort."""
    from mop.types import EvalLLMResponse

    good = _fake_completion({"rewritten": None, "unresolved": []})
    seen = []

    async def acompletion(*args, **kwargs):
        rf = kwargs.get("response_format")
        seen.append(rf)
        if rf is EvalLLMResponse:  # json_schema form rejected by the provider
            raise RuntimeError(
                "DeepseekException - This response_format type is unavailable now"
            )
        return await good(*args, **kwargs)

    with patch("litellm.acompletion", acompletion), patch(
        "litellm.supports_response_schema", return_value=True
    ):
        evaluator = build_litellm_evaluator(rules=[], model="deepseek/deepseek-v4-flash")
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)
    # Tried json_schema (pydantic) then json_object — stopped there, no None.
    assert seen == [EvalLLMResponse, {"type": "json_object"}]


@pytest.mark.asyncio
async def test_non_response_format_error_is_not_retried():
    """An unrelated error (e.g. auth) propagates without a silent retry."""
    calls = []

    async def boom(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("AuthenticationError: bad key")

    with patch("litellm.acompletion", boom), patch(
        "litellm.supports_response_schema", return_value=False
    ):
        evaluator = build_litellm_evaluator(rules=[], model="deepseek/deepseek-chat")
        with pytest.raises(RuntimeError, match="AuthenticationError"):
            await evaluator("hello", [], None)
    assert len(calls) == 1  # no retry on unrelated errors


@pytest.mark.asyncio
async def test_strips_markdown_fences_from_response():
    message = MagicMock()
    message.content = '```json\n{"action": "accept"}\n```'
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    with patch("litellm.acompletion", AsyncMock(return_value=response)), patch(
        "litellm.supports_response_schema", return_value=False
    ):
        evaluator = build_litellm_evaluator(rules=[])
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


def test_resolve_model_defaults(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.delenv("MOP_EVALUATOR", raising=False)
    assert resolve_model() == DEFAULT_MODEL


def test_resolve_model_explicit_arg_wins(monkeypatch):
    monkeypatch.setenv("MOP_EVALUATOR_MODEL", "anthropic/claude-haiku-4-5-20251001")
    assert resolve_model("openai/gpt-4o-mini") == "openai/gpt-4o-mini"


def test_resolve_model_env_var(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR", raising=False)
    monkeypatch.setenv("MOP_EVALUATOR_MODEL", "anthropic/claude-haiku-4-5-20251001")
    assert resolve_model() == "anthropic/claude-haiku-4-5-20251001"


def test_resolve_model_small_alias(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.delenv("MOP_EVALUATOR", raising=False)
    assert resolve_model("small") == "deepseek/deepseek-v4-flash"


def test_resolve_model_tier_aliases_cover_all_three():
    assert resolve_model("small") == "deepseek/deepseek-v4-flash"
    assert resolve_model("medium") == "deepseek/deepseek-v4-pro"
    assert resolve_model("large") == "anthropic/claude-sonnet-5"


def test_resolve_model_alias_via_env(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR", raising=False)
    monkeypatch.setenv("MOP_EVALUATOR_MODEL", "small")
    assert resolve_model() == "deepseek/deepseek-v4-flash"


def test_resolve_model_default_is_small(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.delenv("MOP_EVALUATOR", raising=False)
    assert resolve_model() == "deepseek/deepseek-v4-flash"


def test_resolve_model_raw_provider_model_passes_through():
    assert resolve_model("openai/gpt-4o-mini") == "openai/gpt-4o-mini"


def test_resolve_model_legacy_deepseek_alias(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.setenv("MOP_EVALUATOR", "deepseek")
    assert resolve_model() == "deepseek/deepseek-chat"


def test_resolve_model_legacy_haiku_alias(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.setenv("MOP_EVALUATOR", "haiku")
    assert resolve_model() == "anthropic/claude-haiku-4-5-20251001"


def test_resolve_model_unknown_legacy_raises(monkeypatch):
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.setenv("MOP_EVALUATOR", "gpt-4")
    with pytest.raises(ValueError, match="Unknown MOP_EVALUATOR"):
        resolve_model()


# ---------------------------------------------------------------------------
# API-key isolation shim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mop_prefixed_api_key_passed_explicitly(monkeypatch):
    monkeypatch.setenv("MOP_DEEPSEEK_API_KEY", "sk-isolated")
    fake = _fake_completion({"action": "accept"})
    with patch("litellm.acompletion", fake), patch(
        "litellm.supports_response_schema", return_value=False
    ):
        evaluator = build_litellm_evaluator(rules=[], model="deepseek/deepseek-chat")
        await evaluator("hello", [], None)
    assert fake.call_args.kwargs["api_key"] == "sk-isolated"


@pytest.mark.asyncio
async def test_no_api_key_kwarg_when_unset(monkeypatch):
    monkeypatch.delenv("MOP_DEEPSEEK_API_KEY", raising=False)
    fake = _fake_completion({"action": "accept"})
    with patch("litellm.acompletion", fake), patch(
        "litellm.supports_response_schema", return_value=False
    ):
        evaluator = build_litellm_evaluator(rules=[], model="deepseek/deepseek-chat")
        await evaluator("hello", [], None)
    assert "api_key" not in fake.call_args.kwargs


# ---------------------------------------------------------------------------
# Factory (build_evaluator) — backward-compatible entrypoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_evaluator_works_with_rules_only(monkeypatch):
    """patchbay-relay calls build_evaluator(rules=...) — must keep working."""
    monkeypatch.delenv("MOP_EVALUATOR_MODEL", raising=False)
    monkeypatch.delenv("MOP_EVALUATOR", raising=False)
    p1, p2 = _patched({"action": "accept"})
    with p1, p2:
        evaluator = build_evaluator(rules=[])
        v = await evaluator("hello", [], None)
    assert isinstance(v, Accepted)


# ---------------------------------------------------------------------------
# Parse failure raises instead of fabricating a Rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_failure_raises_runtime_error():
    """Malformed LLM output must raise RuntimeError, not fabricate Rejected."""
    message = MagicMock()
    message.content = "not valid json at all"
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    with patch("litellm.acompletion", AsyncMock(return_value=response)), patch(
        "litellm.supports_response_schema", return_value=False
    ):
        evaluator = build_litellm_evaluator(rules=[])
        with pytest.raises(RuntimeError, match="unparseable output"):
            await evaluator("hello", [], None)


@pytest.mark.asyncio
async def test_build_evaluator_accepts_model_override():
    fake = _fake_completion({"action": "accept"})
    with patch("litellm.acompletion", fake), patch(
        "litellm.supports_response_schema", return_value=False
    ):
        evaluator = build_evaluator(rules=[], model="openai/gpt-4o-mini")
        await evaluator("hello", [], None)
    assert fake.call_args.kwargs["model"] == "openai/gpt-4o-mini"
