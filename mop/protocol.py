"""MOP — the stateful protocol object.

One instance per CC session. Holds:
  - the rule set (loaded once at construction)
  - the LLM evaluator closure (host-provided)
  - the deliver closure (host-provided)
  - per-session state (pending_message, sent_message_this_turn, justification_attempts)

Tools (`submit_message`, `submit_justification`, `get_rules`, `get_status`)
are async methods on this class. The MCP wrapper in `mop/mcp.py` exposes
them via @tool decorators.

State transitions are centralized in `apply()` so submit_message and
submit_justification stay consistent.
"""

from __future__ import annotations

from .rules import Rule, collect_regex_hints
from .types import (
    Accepted,
    AcceptedFailedOpen,
    Deliver,
    Evaluator,
    NoPendingMessageError,
    Rejected,
    Rewritten,
    Verdict,
)


class MOP:
    """Stateful protocol object. One per CC session."""

    def __init__(
        self,
        *,
        rules: list[Rule],
        evaluator: Evaluator,
        deliver: Deliver,
        max_justification_attempts: int = 4,
    ) -> None:
        self.rules = rules
        self.evaluator = evaluator
        self.deliver = deliver
        self.max_justification_attempts = max_justification_attempts

        # Per-session state.
        self.pending_message: str | None = None
        self.justification_attempts: int = 0
        self.sent_message_this_turn: bool = False

    # ─── Tools ────────────────────────────────────────────────────────

    async def submit_message(self, msg: str) -> Verdict:
        regex_hints = collect_regex_hints(msg, self.rules)
        verdict = await self.evaluator(msg, regex_hints, None)
        return await self._apply(verdict, source=msg)

    async def submit_justification(self, justification: str) -> Verdict:
        if self.pending_message is None:
            raise NoPendingMessageError(
                "submit_justification called with no pending message"
            )
        pending = self.pending_message
        self.justification_attempts += 1

        if self.justification_attempts > self.max_justification_attempts:
            return await self._failed_open(pending)

        regex_hints = collect_regex_hints(pending, self.rules)
        verdict = await self.evaluator(pending, regex_hints, justification)
        return await self._apply(verdict, source=pending)

    def get_rules(self, regex_filter: str | None = None) -> list[Rule]:
        if regex_filter is None:
            return list(self.rules)
        import re

        pat = re.compile(regex_filter)
        return [
            r
            for r in self.rules
            if pat.search(r.name) or pat.search(r.guidance)
        ]

    def get_status(self) -> dict:
        return {
            "pending": self.pending_message,
            "sent_this_turn": self.sent_message_this_turn,
            "just_attempts": self.justification_attempts,
        }

    # ─── Internal ─────────────────────────────────────────────────────

    async def _apply(self, verdict: Verdict, *, source: str) -> Verdict:
        """State transitions + deliver-side-effect for any verdict."""
        if isinstance(verdict, Accepted):
            await self.deliver(source, None)
            self._reset_after_send()
            return verdict
        if isinstance(verdict, Rewritten):
            await self.deliver(verdict.rewritten, None)
            self._reset_after_send()
            return verdict
        if isinstance(verdict, Rejected):
            self.pending_message = source
            return verdict
        # AcceptedFailedOpen is only produced by _failed_open(), not the evaluator.
        # If we ever get one here it's a bug — pass through without state change.
        return verdict

    async def _failed_open(self, pending: str) -> Verdict:
        """Justification budget exhausted. Deliver original + system note. Reset."""
        note = (
            f"MOP failed-open after {self.max_justification_attempts} "
            f"justification attempts — original delivered despite rule violations"
        )
        await self.deliver(pending, note)
        self._reset_after_send()
        return AcceptedFailedOpen(system_note=note)

    def _reset_after_send(self) -> None:
        self.pending_message = None
        self.justification_attempts = 0
        self.sent_message_this_turn = True
