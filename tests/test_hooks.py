"""Hooks: protocol_prompt + stop."""

from mop.hooks import protocol_prompt
from mop.rules import Rule


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
