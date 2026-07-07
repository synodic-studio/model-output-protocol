# ADR-0005 — Integration shapes and the streaming constraint

- **Status:** Accepted — 2026-07-07
- **Related:** [architecture.md](../architecture.md) ("Channels compatibility"),
  [integration.md](../integration.md), [ADR-0002](0002-deterministic-authority-and-verdict-shape.md)

## Context

MOP has two adapter surfaces already built — an in-process MCP gate
([`mop/mcp.py`](../mop/mcp.py) + [`mop/hooks.py`](../mop/hooks.py) +
[`mop/protocol.py`](../mop/protocol.py)) and a stateless CLI
([`mop/cli.py`](../mop/cli.py), `mop check`). What was undecided is *how a real
host chooses between them*, and what happens on hosts that stream tokens to the
user before a message is final. A survey of four candidate hosts (Hermes,
patchbay-relay, Pi/pi-mono, Claude Code — see
[integration.md](../integration.md)) forced the decision.

## Decision

**1. Two integration shapes, chosen by one property of the host.**

- **Shape 1 — in-process gate.** Host is Python and exposes a seam where the
  finalized response string is available before delivery. MOP is imported;
  full accept/rewrite/reject. Register at the host's existing output-transform
  point (Hermes `transform_llm_output`; patchbay `_send_response`), or drive it
  agent-side via the MCP `submit_message` tools for Agent-SDK hosts.
- **Shape 2 — out-of-band CLI gate / audit-only.** Host is not Python, or has
  no output-rewrite seam. Shell out to `mop check` on captured output, or run
  the host behind a Shape-1 Python relay. On streaming hosts this degrades to
  audit-only.

The selector is **not** "how big is the host" but "is there a Python seam with
the full response string pre-delivery." A host lacking one (Pi's TS event bus is
observe-only for assistant text; stock Claude Code has no assistant-message
hook) cannot be enforced in-process without an upstream change.

**2. Streaming is authoritative over enforcement mode.** A host that streams
tokens to the user during generation has already shown text by the time MOP
produces a verdict. Therefore:

- Gated surfaces run with **streaming off / full buffering** when hard
  enforcement (reject/redact) is required.
- With streaming on, MOP is **audit-only** for reject/redact — it may still
  rewrite where the host supports editing the already-streamed message (Hermes
  does), but the pre-verdict text was transiently visible, so this is not a
  guarantee. Do not claim reject enforcement on a streaming surface.

This generalizes the existing "Channels compatibility" note in
[architecture.md](../architecture.md) from a channels-specific caveat to the
governing rule for every host.

**3. Sequencing.** Hermes first (live, Python, hook contract already matches the
verdict shape, zero core edits). Pi and Claude Code are audit-only or need an
upstream/SDK change. patchbay is a revival of a removed integration, not a new
build.

## Consequences

- The MCP gate (`mcp.py`) is positioned specifically for **Agent-SDK hosts**
  (Claude Code built on `claude_agent_sdk`), not for hosts that already own
  their delivery loop — those use a plain in-process filter at their transform
  seam, which is simpler than the `submit_message` protocol.
- MOP needs no per-host code: hosts inject `deliver` + evaluator (Shape 1) or
  call the CLI (Shape 2). The four surveyed hosts required **zero** changes to
  MOP itself.
- The audit path ([`mop/audit.py`](../mop/audit.py), `MOP_AUDIT_LOG`) is the
  fallback deliverable on every streaming host — even where MOP can't enforce,
  it can record verdicts to build the eval corpus.
- Follow-up work this implies (not scheduled here): channel-mode audit support
  in the harness, and an optional upstream `message_finalize` gate event in Pi.
