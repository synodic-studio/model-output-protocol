"""Verdict + Gate + MOPError discriminated unions."""

import pytest

from mop.types import (
    Verdict,
    Accepted,
    AcceptedFailedOpen,
    Rewritten,
    Rejected,
    Gate,
    Allow,
    Block,
    NoPendingMessageError,
)


def test_accepted_is_verdict():
    v: Verdict = Accepted()
    assert isinstance(v, Accepted)


def test_accepted_failed_open_carries_system_note():
    v = AcceptedFailedOpen(system_note="failed-open after 4 attempts")
    assert v.system_note == "failed-open after 4 attempts"


def test_rewritten_carries_rewritten_text():
    v = Rewritten(rewritten="cleaned up version")
    assert v.rewritten == "cleaned up version"


def test_rejected_carries_violations():
    v = Rejected(violations=["rule-a", "rule-b"])
    assert v.violations == ["rule-a", "rule-b"]


def test_gate_allow_and_block():
    a: Gate = Allow()
    b: Gate = Block(reason="no message sent this turn")
    assert isinstance(a, Allow)
    assert b.reason == "no message sent this turn"


def test_no_pending_message_error_is_exception():
    err = NoPendingMessageError()
    assert isinstance(err, Exception)


def test_eval_llm_response_validates_action_literal():
    from mop.types import EvalLLMResponse
    import pydantic

    EvalLLMResponse(action="accept")
    EvalLLMResponse(action="rewrite", rewritten="...")
    EvalLLMResponse(action="reject", violations=["x"])
    with pytest.raises(pydantic.ValidationError):
        EvalLLMResponse(action="totally-invalid")


def test_verdict_from_eval_response_accept():
    from mop.types import EvalLLMResponse, verdict_from_eval_response

    v = verdict_from_eval_response(EvalLLMResponse(action="accept"), original_text="hi")
    assert isinstance(v, Accepted)


def test_verdict_from_eval_response_rewrite_uses_rewritten():
    from mop.types import EvalLLMResponse, verdict_from_eval_response

    v = verdict_from_eval_response(
        EvalLLMResponse(action="rewrite", rewritten="cleaned"),
        original_text="messy",
    )
    assert isinstance(v, Rewritten)
    assert v.rewritten == "cleaned"


def test_verdict_from_eval_response_rewrite_falls_back_to_original_if_empty():
    from mop.types import EvalLLMResponse, verdict_from_eval_response

    v = verdict_from_eval_response(
        EvalLLMResponse(action="rewrite", rewritten=None),
        original_text="messy",
    )
    assert isinstance(v, Rewritten)
    assert v.rewritten == "messy"


def test_verdict_from_eval_response_reject_carries_violations():
    from mop.types import EvalLLMResponse, verdict_from_eval_response

    v = verdict_from_eval_response(
        EvalLLMResponse(action="reject", violations=["rule-a"]),
        original_text="bad",
    )
    assert isinstance(v, Rejected)
    assert v.violations == ["rule-a"]
