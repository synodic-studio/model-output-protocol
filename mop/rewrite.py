"""MOP rewrite — reformats a violating message using a pydantic-ai agent.

Uses structured output to guarantee the rewritten text is returned cleanly
without preamble or meta-commentary. Falls back to the original text on
any failure so the caller always gets a deliverable response.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TelegramMessage(BaseModel):
    """Reformatted message with all substantive content preserved."""

    text: str


_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        from pydantic_ai import Agent

        _agent = Agent(
            "anthropic:claude-haiku-4-5-20251001",
            output_type=TelegramMessage,
            system_prompt=(
                "You are a message formatter for a Telegram chat interface. "
                "Rewrite the given message to fix the stated style violation while "
                "preserving ALL substantive content and technical accuracy. "
                "Return only the rewritten message text."
            ),
        )
    return _agent


async def rewrite(text: str, rule_name: str, guidance: str) -> str:
    """Rewrite text to fix a style violation. Returns the original on any failure."""
    prompt = f"Rule violated: {rule_name}\nGuidance: {guidance}\n\nMessage to rewrite:\n{text}"
    try:
        result = await _get_agent().run(prompt)
        return result.output.text
    except Exception as exc:
        logger.warning("MOP rewrite failed (rule=%s): %s — delivering original", rule_name, exc)
        return text
