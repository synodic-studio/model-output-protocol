"""MOP class behavior — state transitions + deliver/evaluator injection."""

import pytest

from mop.protocol import MOP
from mop.types import (
    Accepted,
    AcceptedFailedOpen,
    Rejected,
    Rewritten,
)


@pytest.fixture
def deliveries():
    """Capture all (text, system_note) tuples passed to deliver()."""
    return []


@pytest.fixture
def deliver(deliveries):
    async def _deliver(text: str, system_note: str | None = None) -> None:
        deliveries.append((text, system_note))
    return _deliver


@pytest.fixture
def accept_evaluator():
    async def _eval(text, regex_hints, justification):
        return Accepted()
    return _eval


@pytest.fixture
def reject_evaluator():
    async def _eval(text, regex_hints, justification):
        return Rejected(violations=["test-rule"])
    return _eval


@pytest.fixture
def rewrite_evaluator():
    async def _eval(text, regex_hints, justification):
        return Rewritten(rewritten=f"REWRITTEN({text})")
    return _eval


@pytest.mark.asyncio
async def test_initial_state(accept_evaluator, deliver):
    mop = MOP(rules=[], evaluator=accept_evaluator, deliver=deliver)
    assert mop.pending_message is None
    assert mop.justification_attempts == 0
    assert mop.sent_message_this_turn is False


@pytest.mark.asyncio
async def test_accepted_calls_deliver_and_sets_flag(
    accept_evaluator, deliver, deliveries
):
    mop = MOP(rules=[], evaluator=accept_evaluator, deliver=deliver)
    v = await mop.submit_message("hello")
    assert isinstance(v, Accepted)
    assert deliveries == [("hello", None)]
    assert mop.sent_message_this_turn is True
    assert mop.pending_message is None


@pytest.mark.asyncio
async def test_rejected_does_not_deliver_and_sets_pending(
    reject_evaluator, deliver, deliveries
):
    mop = MOP(rules=[], evaluator=reject_evaluator, deliver=deliver)
    v = await mop.submit_message("bad text")
    assert isinstance(v, Rejected)
    assert v.violations == ["test-rule"]
    assert deliveries == []
    assert mop.pending_message == "bad text"
    assert mop.sent_message_this_turn is False


@pytest.mark.asyncio
async def test_rewritten_delivers_rewrite_not_original(
    rewrite_evaluator, deliver, deliveries
):
    mop = MOP(rules=[], evaluator=rewrite_evaluator, deliver=deliver)
    v = await mop.submit_message("original")
    assert isinstance(v, Rewritten)
    assert v.rewritten == "REWRITTEN(original)"
    assert deliveries == [("REWRITTEN(original)", None)]
    assert mop.sent_message_this_turn is True
    assert mop.pending_message is None


@pytest.mark.asyncio
async def test_successful_send_clears_pending(
    accept_evaluator, deliver
):
    mop = MOP(rules=[], evaluator=accept_evaluator, deliver=deliver)
    # Simulate a prior rejected message left in pending.
    mop.pending_message = "prior rejected"
    mop.justification_attempts = 2
    v = await mop.submit_message("fresh attempt")
    assert isinstance(v, Accepted)
    assert mop.pending_message is None
    assert mop.justification_attempts == 0
