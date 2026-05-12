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


@pytest.mark.asyncio
async def test_submit_justification_with_no_pending_raises(
    accept_evaluator, deliver
):
    from mop.types import NoPendingMessageError

    mop = MOP(rules=[], evaluator=accept_evaluator, deliver=deliver)
    with pytest.raises(NoPendingMessageError):
        await mop.submit_justification("any justification")


@pytest.mark.asyncio
async def test_justification_can_flip_rejected_to_accepted(
    deliver, deliveries
):
    """First call rejects; second (with justification) accepts."""
    calls = {"n": 0}

    async def evaluator(text, regex_hints, justification):
        calls["n"] += 1
        if justification is None:
            return Rejected(violations=["test-rule"])
        return Accepted()

    mop = MOP(rules=[], evaluator=evaluator, deliver=deliver)
    verdict1 = await mop.submit_message("borderline")
    assert isinstance(verdict1, Rejected)
    assert mop.pending_message == "borderline"
    assert mop.justification_attempts == 0
    verdict2 = await mop.submit_justification("here's why this is fine")
    assert isinstance(verdict2, Accepted)
    assert deliveries == [("borderline", None)]
    assert mop.pending_message is None
    assert mop.justification_attempts == 0


@pytest.mark.asyncio
async def test_justification_loop_failed_open_after_cap(
    deliver, deliveries
):
    """4 rejected justifications, 5th attempt failed-opens."""

    async def always_reject(text, regex_hints, justification):
        return Rejected(violations=["stubborn-rule"])

    mop = MOP(
        rules=[],
        evaluator=always_reject,
        deliver=deliver,
        max_justification_attempts=4,
    )
    await mop.submit_message("nope")
    assert mop.pending_message == "nope"

    for i in range(4):
        v = await mop.submit_justification(f"attempt {i + 1}")
        assert isinstance(v, Rejected)
        assert mop.justification_attempts == i + 1

    # 5th attempt: failed-open.
    v = await mop.submit_justification("last try")
    assert isinstance(v, AcceptedFailedOpen)
    assert "failed-open" in v.system_note.lower()

    # Deliver was called once with the original + system note.
    assert deliveries == [("nope", v.system_note)]

    # State reset.
    assert mop.pending_message is None
    assert mop.justification_attempts == 0
    assert mop.sent_message_this_turn is True


@pytest.mark.asyncio
async def test_replacing_pending_with_new_submit_message(
    deliver, deliveries
):
    """If agent submits a fresh message instead of justifying, pending is replaced."""
    rejections = {"first": True}

    async def evaluator(text, regex_hints, justification):
        if rejections["first"]:
            rejections["first"] = False
            return Rejected(violations=["r"])
        return Accepted()

    mop = MOP(rules=[], evaluator=evaluator, deliver=deliver)
    verdict1 = await mop.submit_message("rejected one")
    assert isinstance(verdict1, Rejected)
    assert mop.pending_message == "rejected one"

    verdict2 = await mop.submit_message("fresh attempt")
    assert isinstance(verdict2, Accepted)
    assert mop.pending_message is None
    assert deliveries == [("fresh attempt", None)]
