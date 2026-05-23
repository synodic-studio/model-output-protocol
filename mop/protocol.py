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

from .audit import Auditor
from .rules import Rule, collect_lint_hints, collect_regex_hints
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
        auditor: Auditor | None = None,
    ) -> None:
        self.rules = rules
        self.evaluator = evaluator
        self.deliver = deliver
        self.max_justification_attempts = max_justification_attempts
        self.auditor = auditor

        # Per-session state.
        self.pending_message: str | None = None
        self.justification_attempts: int = 0
        self.sent_message_this_turn: bool = False

    # ─── Tools ────────────────────────────────────────────────────────

    async def submit_message(self, msg: str) -> Verdict:
        lint_hints = collect_lint_hints(msg, self.rules)
        verdict = await self.evaluator(msg, lint_hints, None)
        return await self._apply(verdict, source=msg, attempt=0, justification=None)

    async def submit_justification(self, justification: str) -> Verdict:
        if self.pending_message is None:
            raise NoPendingMessageError(
                "submit_justification called with no pending message"
            )
        pending = self.pending_message
        self.justification_attempts += 1
        attempt = self.justification_attempts

        if self.justification_attempts > self.max_justification_attempts:
            return await self._failed_open(pending, attempt=attempt, justification=justification)

        lint_hints = collect_lint_hints(pending, self.rules)
        verdict = await self.evaluator(pending, lint_hints, justification)
        return await self._apply(verdict, source=pending, attempt=attempt, justification=justification)

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

    async def _apply(
        self,
        verdict: Verdict,
        *,
        source: str,
        attempt: int,
        justification: str | None,
    ) -> Verdict:
        """State transitions + deliver-side-effect for any verdict."""
        self._audit(source, verdict, attempt, justification)
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

    async def _failed_open(
        self, pending: str, *, attempt: int, justification: str | None
    ) -> Verdict:
        """Justification budget exhausted. Deliver original + system note. Reset."""
        note = (
            f"MOP failed-open after {self.max_justification_attempts} "
            f"justification attempts — original delivered despite rule violations"
        )
        verdict = AcceptedFailedOpen(system_note=note)
        self._audit(pending, verdict, attempt, justification)
        await self.deliver(pending, note)
        self._reset_after_send()
        return verdict

    def _audit(
        self,
        source: str,
        verdict: Verdict,
        attempt: int,
        justification: str | None,
    ) -> None:
        if self.auditor is None:
            return
        self.auditor.record(
            original=source,
            verdict=verdict,
            rule_names=[r.name for r in self.rules],
            attempt=attempt,
            justification=justification,
        )

    def _reset_after_send(self) -> None:
        self.pending_message = None
        self.justification_attempts = 0
        self.sent_message_this_turn = True
