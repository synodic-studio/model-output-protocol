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


def test_rejected_carries_unresolved():
    v = Rejected(unresolved=["rule-a", "rule-b"])
    assert v.unresolved == ["rule-a", "rule-b"]


# ---------------------------------------------------------------------------
# serialize
# ---------------------------------------------------------------------------


def test_accepted_serialize():
    assert Accepted().serialize() == {"verdict": "accepted"}


def test_accepted_failed_open_serialize():
    v = AcceptedFailedOpen(system_note="ran out of tries")
    assert v.serialize() == {"verdict": "accepted_failed_open", "system_note": "ran out of tries"}


def test_rewritten_serialize():
    v = Rewritten(rewritten="cleaned")
    assert v.serialize() == {
        "verdict": "rewritten",
        "rewritten": "cleaned",
        "unresolved": [],
    }


def test_rewritten_serialize_carries_unresolved():
    v = Rewritten(rewritten="cleaned", unresolved=["still-bad"])
    assert v.serialize() == {
        "verdict": "rewritten",
        "rewritten": "cleaned",
        "unresolved": ["still-bad"],
    }


def test_rejected_serialize():
    v = Rejected(unresolved=["rule-a", "rule-b"])
    assert v.serialize() == {"verdict": "rejected", "unresolved": ["rule-a", "rule-b"]}


def test_gate_allow_and_block():
    a: Gate = Allow()
    b: Gate = Block(reason="no message sent this turn")
    assert isinstance(a, Allow)
    assert b.reason == "no message sent this turn"


def test_no_pending_message_error_is_exception():
    err = NoPendingMessageError()
    assert isinstance(err, Exception)


def test_eval_llm_response_defaults():
    """No `action` field; rewritten optional, unresolved defaults to []."""
    from mop.types import EvalLLMResponse

    r = EvalLLMResponse()
    assert r.rewritten is None
    assert r.unresolved == []
    r2 = EvalLLMResponse(rewritten="fixed", unresolved=["x"])
    assert r2.rewritten == "fixed"
    assert r2.unresolved == ["x"]


def test_verdict_derives_accept_when_unchanged_and_clean():
    from mop.types import EvalLLMResponse, verdict_from_eval_response

    v = verdict_from_eval_response(EvalLLMResponse(), original_text="hi")
    assert isinstance(v, Accepted)
    # rewritten == original also derives Accepted (no real change).
    v2 = verdict_from_eval_response(
        EvalLLMResponse(rewritten="hi"), original_text="hi"
    )
    assert isinstance(v2, Accepted)


def test_verdict_derives_clean_rewrite():
    from mop.types import EvalLLMResponse, verdict_from_eval_response

    v = verdict_from_eval_response(
        EvalLLMResponse(rewritten="cleaned"), original_text="messy"
    )
    assert isinstance(v, Rewritten)
    assert v.rewritten == "cleaned"
    assert v.unresolved == []


def test_verdict_derives_partial_rewrite():
    """Changed text + non-empty unresolved → Rewritten carrying the residual."""
    from mop.types import EvalLLMResponse, verdict_from_eval_response

    v = verdict_from_eval_response(
        EvalLLMResponse(rewritten="cleaned", unresolved=["rule-b"]),
        original_text="messy",
    )
    assert isinstance(v, Rewritten)
    assert v.rewritten == "cleaned"
    assert v.unresolved == ["rule-b"]


def test_verdict_derives_reject_when_unchanged_and_unresolved():
    from mop.types import EvalLLMResponse, verdict_from_eval_response

    v = verdict_from_eval_response(
        EvalLLMResponse(rewritten=None, unresolved=["rule-a"]),
        original_text="bad",
    )
    assert isinstance(v, Rejected)
    assert v.unresolved == ["rule-a"]
