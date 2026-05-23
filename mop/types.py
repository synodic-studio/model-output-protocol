"""MOP discriminated-union types.

Verdict — what `submit_message` / `submit_justification` return:
  - Accepted              : LLM said ok; deliver() was called with the original text
  - AcceptedFailedOpen    : justification budget exhausted; original delivered with a system note
  - Rewritten             : LLM rewrote; deliver() was called with the rewritten text
  - Rejected              : LLM rejected; original is now `pending_message`, agent must justify

Gate — what the Stop hook returns:
  - Allow                 : agent may end the turn
  - Block(reason)         : agent must continue (typically because no message was sent this turn)

EvalLLMResponse — the protocol-owned structured-output schema that every
LLM adapter (Haiku via pydantic-ai today, others later) must produce.
Adapters hand this schema to their LLM library for structured-output
decoding, then call `verdict_from_eval_response()` to get a runtime
Verdict. Keeping the schema here (not in any specific host) means the
wire protocol is one definition for all future adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Union

from pydantic import BaseModel


@dataclass(frozen=True)
class Accepted:
    """LLM accepted the message; deliver() was called with the original text."""

    def serialize(self) -> dict:
        return {"verdict": "accepted"}


@dataclass(frozen=True)
class AcceptedFailedOpen:
    """Justification budget exhausted; original was delivered with a system note."""

    system_note: str

    def serialize(self) -> dict:
        return {"verdict": "accepted_failed_open", "system_note": self.system_note}


@dataclass(frozen=True)
class Rewritten:
    """LLM rewrote the message; deliver() was called with the rewritten text."""

    rewritten: str

    def serialize(self) -> dict:
        return {"verdict": "rewritten", "rewritten": self.rewritten}


@dataclass(frozen=True)
class Rejected:
    """LLM rejected the message; agent must call submit_justification."""

    violations: list[str]

    def serialize(self) -> dict:
        return {"verdict": "rejected", "violations": list(self.violations)}


Verdict = Union[Accepted, AcceptedFailedOpen, Rewritten, Rejected]


@dataclass(frozen=True)
class Allow:
    """Stop hook decision: agent may end the turn."""


@dataclass(frozen=True)
class Block:
    """Stop hook decision: agent must continue."""

    reason: str


Gate = Union[Allow, Block]


class NoPendingMessageError(Exception):
    """submit_justification was called with no pending_message."""


# ─── Protocol-owned LLM structured-output schema ──────────────────────
# Every LLM adapter must produce this shape. The reference Haiku adapter
# (`mop.haiku`) hands this class to pydantic-ai's `output_type=`. Future
# adapters (Gemma, GPT, local models) do the equivalent for their library
# and also produce this schema. The runtime Verdict is built from it via
# `verdict_from_eval_response()`.

class EvalLLMResponse(BaseModel):
    """Structured output schema every LLM evaluator must produce."""

    action: Literal["accept", "rewrite", "reject"]
    rewritten: str | None = None       # required when action == "rewrite"
    violations: list[str] = []         # rule names; required when action == "reject"


def verdict_from_eval_response(
    response: "EvalLLMResponse", *, original_text: str
) -> "Verdict":
    """Map an EvalLLMResponse to a runtime Verdict.

    `original_text` is needed when action="rewrite" but `rewritten` came
    back empty — we fall back to the original rather than delivering an
    empty string.
    """
    if response.action == "accept":
        return Accepted()
    if response.action == "rewrite":
        return Rewritten(rewritten=response.rewritten or original_text)
    return Rejected(violations=response.violations or ["unspecified"])


# ─── Closure type aliases ─────────────────────────────────────────────
# Documentation only; runtime uses Callable directly.

# Evaluator: the LLM call that decides accepted | rewritten | rejected.
#   The reference adapter wraps pydantic-ai + Haiku into this signature.
Evaluator = Callable[
    [str, list[str], "str | None"],  # text, regex_hints, justification
    Awaitable["Verdict"],
]

# Deliver: how a verdict's deliverable text reaches the user channel.
#   A typical host body sends to Telegram, Slack, a web UI, etc.
Deliver = Callable[
    [str, "str | None"],  # text, system_note (optional, e.g. failed-open warning)
    "Awaitable[None]",
]
