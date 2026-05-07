"""Hooks: protocol_prompt + stop."""

import pytest

from mop.hooks import protocol_prompt, stop
from mop.protocol import MOP
from mop.rules import Rule
from mop.types import Accepted, Allow, Block


def test_protocol_prompt_mentions_submit_message():
    rules: list[Rule] = []
    prompt = protocol_prompt(rules)
    assert "submit_message" in prompt
    assert "submit_justification" in prompt


def test_protocol_prompt_lists_rules_when_provided():
    rules = [
        Rule(name="no-emojis", detector="regex", parameters={}, guidance="No emojis.", source_file="x.yml"),
        Rule(name="brevity", detector="llm", parameters={}, guidance="Be concise.", source_file="x.yml"),
    ]
    prompt = protocol_prompt(rules)
    assert "no-emojis" in prompt
    assert "No emojis." in prompt
    assert "brevity" in prompt
    assert "Be concise." in prompt


def test_protocol_prompt_no_rules_says_so():
    prompt = protocol_prompt([])
    # The agent should know there are no active rules — silence is wrong
    assert "no active rules" in prompt.lower() or "0 rules" in prompt.lower()


@pytest.fixture
def silent_evaluator():
    async def _e(text, regex_hints, justification):
        return Accepted()
    return _e


@pytest.fixture
def noop_deliver():
    async def _d(text, system_note=None):
        pass
    return _d


def test_stop_blocks_when_no_message_sent(silent_evaluator, noop_deliver):
    mop = MOP(rules=[], evaluator=silent_evaluator, deliver=noop_deliver)
    assert mop.sent_message_this_turn is False
    gate = stop(mop)
    assert isinstance(gate, Block)
    assert "submit_message" in gate.reason.lower()


def test_stop_allows_when_message_sent_and_resets_flag(silent_evaluator, noop_deliver):
    mop = MOP(rules=[], evaluator=silent_evaluator, deliver=noop_deliver)
    mop.sent_message_this_turn = True
    gate = stop(mop)
    assert isinstance(gate, Allow)
    # Flag resets so the next turn starts clean.
    assert mop.sent_message_this_turn is False


@pytest.mark.asyncio
async def test_stop_block_then_submit_then_stop_allows(silent_evaluator, noop_deliver):
    """Real-turn sequence: agent tries to stop without sending → blocked,
    then sends → flag flips → next stop allows. This is the canonical
    'agent forgot to send, was reminded' loop."""
    mop = MOP(rules=[], evaluator=silent_evaluator, deliver=noop_deliver)

    # 1st stop: agent hasn't sent anything → Block
    gate1 = stop(mop)
    assert isinstance(gate1, Block)
    assert mop.sent_message_this_turn is False

    # Agent reacts to the block by sending a message
    await mop.submit_message("ack")
    assert mop.sent_message_this_turn is True

    # 2nd stop: allowed, flag resets so next turn starts clean
    gate2 = stop(mop)
    assert isinstance(gate2, Allow)
    assert mop.sent_message_this_turn is False
