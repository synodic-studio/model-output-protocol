# MOP — Model Output Protocol

**A filter that sits between an LLM agent and its human user, enforcing communication rules before output reaches them.**

The structural counterpart to [MCP (Model Context Protocol)](https://modelcontextprotocol.io). MCP defines how agents receive context from tools and services. MOP defines how agents deliver context to humans — the other half of the loop.

## The problem

LLM agents drift. Voice rules in the system prompt get crowded out by task instructions. Reminders in CLAUDE.md decay across long sessions. The result: messages that are too long, too short, too cheerleady, ask permission instead of acting, narrate process instead of stating outcomes.

Stuffing more rules in the system prompt does not fix this. The agent's context is already saturated with the task.

MOP solves it by moving voice enforcement *out* of the agent and into a thin protocol layer the agent must call to reach the human.

## How it works

MOP exposes itself to the agent as four MCP tools — typically mounted **in-process** via `claude_agent_sdk.create_sdk_mcp_server`:

| Tool | Purpose |
|---|---|
| `submit_message(text)` | The agent's only path to the user. Triggers an LLM evaluation against the active rules. |
| `submit_justification(reason)` | Argues for delivering a previously-rejected message. Bounded by `max_justification_attempts = 4`. |
| `get_rules(filter?)` | Read-only — returns active rule names + descriptions, optionally filtered by regex. |
| `get_status()` | Returns `(pending_message, sent_this_turn, justification_attempts)` for self-recovery. |

A single Haiku call evaluates each submission and returns one of four `Verdict` types:

- **`Accepted`** — message is delivered as-is via the host's injected `deliver(text, system_note?)` callable.
- **`Rewritten(rewritten)`** — Haiku reformed the message; the rewritten version is delivered, and the agent learns the diff via the tool result.
- **`Rejected(violations)`** — message becomes `pending_message`; the agent must call `submit_justification` to argue for delivery.
- **`AcceptedFailedOpen(system_note)`** — justification budget exhausted; original is delivered with a system-note bubble warning the user that rules were bypassed.

A `Stop` hook gates turn-end on `sent_message_this_turn`, ensuring the agent sends *something* every turn instead of silently completing.

The agent never streams text directly to the user. The MCP tool result is what the agent sees, so references like "do option b" resolve naturally in its own context.

## Integration shape

MOP is transport-agnostic and LLM-agnostic. Hosts inject:

- **`evaluator(text, regex_hints, justification?)`** — async callable returning a Verdict. patchbay-relay wires this to Haiku via pydantic-ai.
- **`deliver(text, system_note?)`** — async callable that gets the message in front of the user. patchbay-relay wires this to Telegram.

Plus `mop.protocol_prompt(rules)` — a pure function the host concatenates into `ClaudeAgentOptions.system_prompt` so the agent knows the protocol exists.

See `mop/protocol.py` for the `MOP` class and `mop/mcp.py` for the in-process MCP wiring.

## Status

Alpha. Live in [patchbay-relay](https://github.com/synodic-studio/patchbay-relay) on the `cc-sdk-mop` harness.

## Evals

Rules are validated against a corpus of counterexamples in [`evals/`](evals/). Each rule has positive and negative example messages it should (or should not) flag. Run `uv run python evals/harness.py` to check the corpus against the deterministic rules.

## Companion

[HOP — Human Output Protocol](https://github.com/synodic-studio/human-output-protocol). The decoder side: helps humans compose more productive messages to high-context agents from low-bandwidth interfaces (mobile, voice).

Together, MOP and HOP form the I/O contract for the agent-human interface. Like Swift's `Codable`, but for human bandwidth limits.

## License

MIT
