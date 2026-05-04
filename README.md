# MOP — Model Output Protocol

**A filter that sits between an LLM agent and its human user, enforcing communication rules before output reaches them.**

The structural counterpart to [MCP (Model Context Protocol)](https://modelcontextprotocol.io). MCP defines how agents receive context from tools and services. MOP defines how agents deliver context to humans — the other half of the loop.

## The problem

LLM agents drift. Voice rules in the system prompt get crowded out by task instructions. Reminders in CLAUDE.md decay across long sessions. The result: messages that are too long, too short, too cheerleady, ask permission instead of acting, narrate process instead of stating outcomes.

Stuffing more rules in the system prompt does not fix this. The agent's context is already saturated with the task.

MOP solves it by moving voice enforcement *out* of the agent and into a thin filter layer the agent's output passes through before reaching the human.

## How it works

The agent sends a message via a `send-to-user` tool. MOP evaluates it against configured rules and returns one of three verdicts:

- **Accept** — message passes, delivered as-is.
- **Rewrite** — message violates style rules; a fast model (Haiku) reforms it. Original + rewritten + reasons logged.
- **Reject** — message violates behavioral rules (asking permission to do work it can do, idle praise, narrating instead of acting). Tool result returns guidance; agent must do more work before sending again.

The tool result *is* the delivered version, so the agent sees what the human actually saw — references like "do option b" resolve naturally in the agent's own context.

## Modes

Configurable per-chat:

- **Passthrough** — always Accept. Baseline, no behavior change.
- **Audit** — log violations but always Accept. Zero-risk drift intel.
- **Rewrite** — Haiku reforms style violations, rejects behavioral ones.
- **StrictRetry** — reject all violations, agent must retry.

Start in Audit mode. Gather drift data. Tune rules. Promote to Rewrite when ready.

## Status

Alpha. Active development. First integration target: [patchbay-relay](https://github.com/synodic-studio/patchbay-relay).

## Companion

[HOP — Human Output Protocol](https://github.com/synodic-studio/human-output-protocol). The decoder side: helps humans compose more productive messages to high-context agents from low-bandwidth interfaces (mobile, voice).

Together, MOP and HOP form the I/O contract for the agent-human interface. Like Swift's `Codable`, but for human bandwidth limits.

## License

MIT
