"""MOP hooks — pure functions consumed by the host.

`protocol_prompt(rules)` is a pure function that returns a system-prompt
fragment describing the MOP protocol to the agent. NOT a CC SessionStart
hook — the host calls it at SDK-init and concatenates the result into
ClaudeAgentOptions.system_prompt. Same effect (the protocol is in
context every turn), simpler lifecycle (no plugin needed).

`stop(mop)` is the body of a CC Stop hook callback. The host registers
it via ClaudeAgentOptions.hooks, closing over the per-session MOP
instance. Returns Gate (Allow | Block). On Allow it also flips
sentMessageThisTurn back to False so the next turn starts clean.
"""

from __future__ import annotations

from .rules import Rule
from .types import Allow, Block, Gate


def protocol_prompt(rules: list[Rule]) -> str:
    """Return the protocol-aware system prompt fragment."""
    lines = [
        "OUTBOUND MESSAGE PROTOCOL",
        "",
        "You are running in non-interactive mode. The user only receives "
        "messages you explicitly send via the `submit_message` tool. "
        "Plain text in your final response is NOT delivered to the user.",
        "",
        "Send at least one message per turn. Typically one message is enough; "
        "multiple are allowed (e.g. progress updates). Each accepted/rewritten "
        "submit is delivered to the user immediately, in the order called.",
        "",
        "If `submit_message` returns:",
        "  - accepted              — your message was delivered as-is.",
        "  - rewritten             — your message was rewritten and the rewrite was "
        "delivered. Use the rewrite as your reference for what the user saw.",
        "  - rejected(unresolved)  — your message was NOT delivered. Call "
        "`submit_justification(reason)` to argue your case (up to 4 attempts; "
        "after that MOP failed-opens and delivers your original with a warning to the user).",
        "  - acceptedFailedOpen    — failed-open path: your original was delivered, "
        "but the user got a warning that MOP let it through despite rules.",
        "",
        "Other tools: `get_rules(filter?)` lists active rules, "
        "`get_status()` returns your current pending message, sent-this-turn "
        "flag, and justification attempts (for self-recovery).",
        "",
    ]
    if rules:
        lines.append(f"ACTIVE RULES ({len(rules)}):")
        for r in rules:
            guidance = (r.guidance or "").strip().replace("\n", " ")
            lines.append(f"  - {r.name}: {guidance}")
    else:
        lines.append("ACTIVE RULES: no active rules (0 rules currently configured).")
    return "\n".join(lines)


from .protocol import MOP


def stop(mop: MOP) -> Gate:
    """CC Stop hook body. Block turn-end if no message was sent this turn.

    The host registers this via ClaudeAgentOptions.hooks, closing over the
    per-session MOP instance:

        async def stop_hook(input, tool_use_id, context):
            gate = stop(mop)
            if isinstance(gate, Block):
                return {"decision": "block", "reason": gate.reason}
            return {}

    On Allow, the sent flag is reset so the next turn starts clean.
    """
    if mop.sent_message_this_turn:
        mop.sent_message_this_turn = False
        return Allow()
    return Block(
        reason=(
            "no message sent this turn — call submit_message before stopping. "
            "If you have nothing to say, send a brief acknowledgement."
        )
    )
