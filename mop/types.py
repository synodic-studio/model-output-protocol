"""MOP discriminated-union types.

Verdict — what `submit_message` / `submit_justification` return:
  - Accepted              : LLM said ok; deliver() was called with the original text
  - AcceptedFailedOpen    : justification budget exhausted; original delivered with a system note
  - Rewritten             : evaluator rewrote; deliver() got the rewritten text (may carry `unresolved`)
  - Rejected              : nothing fixable; original is now `pending_message`, agent must justify `unresolved`

Gate — what the Stop hook returns:
  - Allow                 : agent may end the turn
  - Block(reason)         : agent must continue (typically because no message was sent this turn)

EvalLLMResponse — the protocol-owned structured-output schema that every
LLM adapter (litellm evaluator via `mop.evaluators.build_litellm_evaluator`
today, others later) must produce. Adapters hand this schema to their
LLM library for structured-output decoding, then call
`verdict_from_eval_response()` to get a runtime Verdict. Keeping the
schema here (not in any specific host) means the wire protocol is one
definition for all future adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Union

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
    """Evaluator produced a rewrite; deliver() was called with the rewritten text.

    ``unresolved`` lists any rule names the rewrite could NOT clear (the
    "partial" case — best-effort fix applied, the rest is up to the agent).
    Empty for a clean rewrite.
    """

    rewritten: str
    unresolved: list[str] = field(default_factory=list)

    def serialize(self) -> dict:
        return {
            "verdict": "rewritten",
            "rewritten": self.rewritten,
            "unresolved": list(self.unresolved),
        }


@dataclass(frozen=True)
class Rejected:
    """Nothing could be fixed; agent must call submit_justification.

    ``unresolved`` lists the rule names still violated (Q4: the residual
    "you deal with these" set).
    """

    unresolved: list[str]

    def serialize(self) -> dict:
        return {"verdict": "rejected", "unresolved": list(self.unresolved)}


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
# Every LLM adapter must produce this shape. The reference litellm
# evaluator (`mop.evaluators.build_litellm_evaluator`) hands this class
# to litellm's structured-output support. Future adapters (Gemma, GPT,
# local models) do the equivalent for their library and also produce this
# schema. The runtime Verdict is built from it via
# `verdict_from_eval_response()`.

class EvalLLMResponse(BaseModel):
    """Structured output schema every LLM evaluator must produce.

    No `action` field — the disposition is DERIVED (see
    `verdict_from_eval_response`), so the label can never disagree with the
    data. The model returns only its best-effort rewrite plus the rule
    names it could not fix.
    """

    rewritten: str | None = None       # best-effort corrected text; null = no change
    unresolved: list[str] = []         # rule names the model could not fix


def verdict_from_eval_response(
    response: "EvalLLMResponse", *, original_text: str
) -> "Verdict":
    """Derive a runtime Verdict from (did-text-change?, is-unresolved-empty?).

    - unchanged + empty      → Accepted
    - changed + empty        → Rewritten (clean)
    - changed + non-empty    → Rewritten (partial; carries unresolved)
    - unchanged + non-empty  → Rejected
    """
    rewritten = response.rewritten
    changed = rewritten is not None and rewritten.strip() != original_text.strip()
    unresolved = list(response.unresolved)
    if not unresolved:
        return Rewritten(rewritten=rewritten, unresolved=[]) if changed else Accepted()
    if changed:
        return Rewritten(rewritten=rewritten, unresolved=unresolved)
    return Rejected(unresolved=unresolved)


# ─── Closure type aliases ─────────────────────────────────────────────
# Documentation only; runtime uses Callable directly.

# Evaluator: the LLM call that decides accepted | rewritten | rejected.
#   The reference adapter wraps litellm into this signature.
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
