# Where MOP can intercept outbound messages — harness survey

Research findings (2026-07-03) on the question: **in which real agent harnesses can MOP
intercept an outbound message BEFORE the user sees it, and by what mechanism?**

MOP's mechanic requires three things from a harness:

1. A **discrete, complete message** at interception time (not a token stream the user is
   already watching).
2. The ability to **withhold or replace** that message before the user's surface renders it.
3. A **feedback path to the agent** (tool result, hook denial reason, retry exception) so the
   reject → justify loop and "agent sees the rewrite" property work.

A harness that gives all three is a *native* fit. One that gives 1–2 but where the feedback
path must be built by the host is a *middleware* fit. One that renders text to the user before
any hook fires is *post-hoc only* — the fallback documented in
[`docs/architecture.md` → Channels compatibility](../architecture.md).

---

## Summary table

| Harness | Interception point | Complete message? | Can withhold/replace? | Agent feedback loop? | MOP fit |
|---|---|---|---|---|---|
| Claude Agent SDK (Python/TS) host process | Host consumes `AssistantMessage` stream; forced `submit_message` tool + Stop hook | Yes (tool call) | Yes (host owns delivery) | Yes (tool result) | **Native** — shipped (patchbay-relay `cc-sdk-mop`) |
| Claude Code channels (v2.1.80+, research preview) | Channel MCP server's `reply` tool handler, or `PreToolUse` hook on `mcp__<plugin>__reply` | Yes (single tool call with full `text`) | Yes (tool handler / hook deny) | Yes (tool result / deny reason) | **Native** |
| Telegram/Slack/Discord bot relays (generic) | Relay process composes message fully before platform API send | Yes | Yes | Host-built | **Native / middleware** — the easy case |
| Claude Code interactive CLI (terminal) | `MessageDisplay` hook (display-rewrite only); no pre-display blocking hook | Per-message | Replace display only; cannot suppress or block; transcript + model keep original | No | **Post-hoc only** (display-rewrite is a partial, one-way variant) |
| LangGraph / LangChain agents (non-streaming `invoke`) | `after_agent` / `after_model` middleware; host owns the return value | Yes | Yes (middleware replaces final message) | Partial (`can_jump_to`, host-built retry) | **Middleware** |
| LangGraph (token streaming to user) | `after_agent` runs after tokens already streamed | No | Only if host buffers the stream (forfeits streaming UX) | — | **Middleware if buffered; post-hoc if truly streaming** |
| Pydantic AI (`run` / `run_sync`) | `@agent.output_validator` + `ModelRetry`; host owns result | Yes | Yes | Yes (`ModelRetry` ≈ reject loop) | **Middleware** |
| Pydantic AI (`run_stream`) | Validators run on partials; `ModelRetry` broken in streaming (open issues) | No | Only if host buffers | Broken | **Post-hoc only** (or buffer) |
| OpenAI Agents SDK (non-streaming `Runner.run`) | Output guardrails run before caller receives `final_output` | Yes | Block only (tripwire exception); guardrails cannot rewrite — host wrapper needed for rewrite | Host-built | **Middleware** |
| OpenAI Agents SDK (`Runner.run_streamed`) | Output guardrails fire **after** text already streamed | No | No | No | **Post-hoc only** (acknowledged upstream, issue #495) |
| claude.ai surfaces (Claude in Slack / Claude Tag, Claude Code on the web) | None exposed — delivery managed end-to-end by Anthropic | — | No | No | **Impossible** |

---

## 1. Claude Code hooks (interactive CLI)

Current hook catalog (~30 events as of the 2.1.x line):
[Hooks reference](https://code.claude.com/docs/en/hooks).

**Key finding: no hook can block or gate assistant text before it renders in the terminal.**
Assistant text streams to the terminal as generated; `Stop` fires after the response is
complete and displayed. `Stop` can block *turn-end* (which is exactly how MOP's
sent-this-turn gate works) but cannot modify or suppress the message that was just shown.

Two hooks are adjacent to interception, neither sufficient alone:

- **`MessageDisplay`** (new in the 2.1.x line) — fires for every assistant message that
  streams text. Its `hookSpecificOutput.displayContent` **replaces the rendered text on
  screen**. Limits, per the docs:
  - *Display-only*: "the transcript and what Claude sees keep the original." The agent never
    learns the rewrite happened, so MOP's "agent references resolve to the delivered text"
    property is lost.
  - Non-blocking: listed under events with no decision control — on hook failure or timeout
    (default lowered to 10s) "the original text is displayed." It cannot suppress a message
    or reject-and-loop.
  - No matcher support; fires on every text-bearing message.

  This is a *cosmetic one-way rewrite*, not a gate. It could carry MOP's **rewrite** verdict
  (deterministic lints comfortably inside 10s; a fast Haiku call is borderline), but never
  the **reject** verdict, and it silently fails open to the original.

- **`PreToolUse`** — full gate semantics (deny with reason, `updatedInput` rewrite), but only
  for *tool calls*. In the interactive CLI the user reads plain assistant text, which is not
  a tool call — so PreToolUse can't reach it. (It becomes the interception point the moment
  delivery is a tool call — see channels, §3.)

**Verdict: post-hoc only** for the interactive terminal. Honest answer per
`docs/architecture.md`: audit-only + post-hoc correction. A `MessageDisplay`-based
"display polish" adapter is possible but is a degraded MOP (rewrite-only, agent-blind,
fails open to original) and should be labeled as such if ever built.

Sources:
- https://code.claude.com/docs/en/hooks

## 2. Claude Agent SDK (Python + TypeScript) — the reference case

The SDK host process consumes the message stream programmatically
(`query()` / `ClaudeSDKClient.receive_response()` yielding `AssistantMessage` →
`TextBlock`), so **nothing reaches the user unless the host forwards it**. Even with
`include_partial_messages=True`, partial `StreamEvent`s go to the host, not the user.
The docs are explicit that hosts extract text and "can withhold, transform, or delay before
showing user."

MOP-relevant surfaces:

- `mcp_servers` + `create_sdk_mcp_server` — mount MOP's four tools in-process.
- `system_prompt` — concatenate `mop.protocol_prompt(rules)`.
- `hooks={"Stop": ...}` — register `mop.hooks.stop(mop)` to force at least one
  `submit_message` per turn.
- `can_use_tool` — a coarser alternative gate on tool calls (only invoked when permission
  evaluation falls through to a prompt, so less reliable than owning the delivery tool).

A "MOP middleware" for SDK hosts is therefore: *don't forward plain assistant text at all;
deliver exclusively from inside the `submit_message` tool handler.* This is exactly what
**patchbay-relay's `cc-sdk-mop` harness** does (`patchbay/harness/claude_sdk_mop.py`,
wired in `bridge.py`: in-process MCP + Stop hook, `deliver` closure →
`bot.send_message`, with a fallback path when the model produced plain text but MOP never
delivered). This is the shipped reference implementation; the TS SDK
(`@anthropic-ai/claude-agent-sdk`) has the same shape (`query()` async generator,
`canUseTool`, hook callbacks, in-process MCP servers) and would host the same design.

**Verdict: native.** Already proven live.

Sources:
- https://code.claude.com/docs/en/agent-sdk/python
- https://code.claude.com/docs/en/agent-sdk/overview
- https://github.com/synodic-studio/patchbay-relay

## 3. Claude Code channels (research preview, v2.1.80+)

This is the surprise of the survey. A channel is an MCP server that Claude Code spawns; it
pushes inbound events via `notifications/claude/channel`, and — critically — **outbound
replies are discrete MCP tool calls**: the channel exposes a `reply` tool
(e.g. `reply(chat_id, text)`) and Claude calls it with the **complete message text**. Per the
docs: "The terminal shows the tool call and a confirmation (like 'sent'), and the actual
reply appears on the other platform." There is **no streaming of partial assistant output to
the channel** — the remote user sees only what the reply tool sends.

That means channel delivery already has MOP's shape. Two interception mechanisms:

1. **Inside the channel server's `reply` tool handler** (preferred). The handler receives
   `{chat_id, text}`, runs MOP's eval, and:
   - Accepted/Rewritten → sends (possibly rewritten) text to the platform, returns "sent".
   - Rejected → sends nothing, returns the violations as the tool *result* — the agent sees
     them in-context and can resubmit or justify. This is `submit_message` in all but name;
     the channel's `reply` tool **is** the delivery path, so MOP owns it completely.
2. **A `PreToolUse` hook matching `mcp__<plugin>__reply`** — deny with reason (agent sees
   it, can retry) or rewrite via `updatedInput`. Works without modifying the channel server,
   but splits MOP state between hook process and session; option 1 keeps it in one place.

Caveats: research preview; custom channels need `--dangerously-load-development-channels`
(the approved allowlist is Anthropic-curated); "the `--channels` flag syntax and protocol
contract may change."

**Note for `docs/architecture.md`:** the "Channels compatibility" section assumes "Channels
stream partial output as it arrives." The published channel contract contradicts this —
replies are discrete, complete tool calls. The streaming-incompatibility concern (and the
three fallbacks) still applies to surfaces that truly stream (CLI terminal, claude.ai,
OpenAI streamed runs), but Claude Code channels themselves look **native-fit**, not
incompatible. The `supports_inflight_push` concern — events being injected mid-turn — is
orthogonal to output gating and unaffected: queued channel events arrive between turns.

**Verdict: native** (pending contract stability).

Sources:
- https://code.claude.com/docs/en/channels
- https://code.claude.com/docs/en/channels-reference
- https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins (telegram, discord, imessage, fakechat)

## 4. OpenAI Agents SDK

Has first-class **output guardrails**: functions that "receive the output produced by the
agent," run after the agent completes, and raise `OutputGuardrailTripwireTriggered` on
violation. In **non-streaming** `Runner.run`, the caller receives `final_output` only after
output guardrails complete — so the message is blockable pre-delivery. But guardrails are
**validators, not rewriters**: they can allow or raise, not transform (tool guardrails can
replace tool output; agent-level output guardrails cannot). A MOP adapter would therefore
wrap `Runner.run` in host code: catch the tripwire (→ MOP reject, feed violations back as a
new run input, i.e. a host-built justification loop) or run MOP's full
accept/rewrite/reject eval on `result.final_output` before the host displays it.

In **streaming** (`Runner.run_streamed`), this collapses: "output guardrails are triggered
after the streaming has already occurred" — tokens reach the consumer before the guardrail
fires. Halting the stream on tripwire is an open upstream feature request
([issue #495](https://github.com/openai/openai-agents-python/issues/495)); input guardrails
have the mirror-image problem ([issue #300](https://github.com/openai/openai-agents-python/issues/300)).

**Verdict: middleware** for non-streaming runs (host wrapper, since it consumes the result
programmatically anyway); **post-hoc only** for streamed runs — architecture.md fallback #3
applies verbatim.

Sources:
- https://openai.github.io/openai-agents-python/guardrails/
- https://openai.github.io/openai-agents-python/results/
- https://github.com/openai/openai-agents-python/issues/495
- https://github.com/openai/openai-agents-python/issues/300

## 5. LangGraph / LangChain agents

LangChain's middleware system (LangGraph 1.0 era) exposes `before_agent` / `after_agent`
(and `after_model`) hooks. `after_agent` runs on the final state before it is returned to
the caller and **can mutate/replace the last AI message** — the docs' own example rewrites
an unsafe response in place. `@hook_config(can_jump_to=[...])` gives limited flow control
(e.g. jump to end); a reject→retry loop is buildable by jumping back to the model node with
injected feedback, though it's host-assembled rather than built-in.

Streaming caveat: with `stream`/`astream` in messages/token mode, chunks are yielded to the
consumer as generated — `after_agent` runs too late for anything already forwarded. But as
with the Claude SDK, LangGraph is a library inside a host process: the stream goes to the
*host*, and a host that buffers per-message before forwarding gets full interception at the
cost of streaming UX.

**Verdict: middleware.** Clean fit for non-streaming; buffered-delivery decision required
for streaming hosts.

Sources:
- https://docs.langchain.com/oss/python/langchain/guardrails
- https://docs.nvidia.com/nemo/guardrails/latest/integration/langchain/agent-middleware.html
- https://github.com/langchain-ai/langgraph-guardrails-example

## 6. Pydantic AI

`@agent.output_validator` functions run on the final output before the result is returned to
the caller, and raising `ModelRetry` sends feedback to the model and re-runs — which is
structurally MOP's reject/justify loop, natively. Since MOP's reference Haiku evaluator is
already built on pydantic-ai, an adapter here is unusually cheap: MOP eval as an output
validator, `Rejected` → `ModelRetry(violations)`, `Rewritten` → return the rewritten text.

Streaming is the weak spot: with `run_stream`, "the first matching output is committed
immediately," validators see partials (`RunContext.partial_output=True`), and `ModelRetry`
from an output validator during streaming is unhandled/crashes — open issues
[#1663](https://github.com/pydantic/pydantic-ai/issues/1663) and
[#3393](https://github.com/pydantic/pydantic-ai/issues/3393).

**Verdict: middleware** (near-native for non-streaming runs); post-hoc or buffer for
`run_stream`.

Sources:
- https://ai.pydantic.dev/output/
- https://ai.pydantic.dev/agent/
- https://github.com/pydantic/pydantic-ai/issues/1663
- https://github.com/pydantic/pydantic-ai/issues/3393

## 7. Bot-style relays generally (Telegram, Slack bots, Discord bots, SMS gateways)

The easy case, confirmed: platform bot APIs (`sendMessage`, `chat.postMessage`, …) take a
complete message per call. Any relay process that composes the outbound message before
calling the platform API is a total interception point — MOP's `deliver` closure *is* the
API call. Streaming does not exist at the platform layer (Telegram bots can fake it with
`editMessageText`, but a MOP host simply wouldn't). patchbay-relay is the live instance.
The only design question is upstream: whether the agent side hands the relay a discrete
message (SDK forced-tool: yes) or a token stream (then the relay buffers — trivially fine
here since the platform forces full-message sends anyway).

**Verdict: native.**

## 8. Anthropic-managed surfaces (Claude in Slack / "Claude Tag", claude.ai, Claude Code on the web)

Claude Tag (June 2026) posts task output into Slack threads; Claude Code on the web and
claude.ai render streams directly. Delivery is managed end-to-end by Anthropic with no
user-installable hook between generation and display. No interception surface exists.

**Verdict: impossible.** Nothing to strain for — these are out of scope for MOP unless
Anthropic ships an output-hook API.

Sources:
- https://techstrong.ai/articles/anthropic-launches-claude-tag-to-turn-slack-channels-into-agentic-ai-workspaces/
- https://the-decoder.com/anthropic-turns-claude-code-into-an-always-on-ai-agent-with-new-channels-feature/

---

## What this means for MOP — adapter priority

1. **Claude Code channel server adapter** (build next). Highest leverage, lowest lift: the
   channels contract already routes every user-visible byte through a discrete `reply` tool
   call, so a MOP-wrapped channel server gets full native semantics (reject loop included)
   *without the Agent SDK* — it works with stock interactive Claude Code. It also directly
   competes with/complements patchbay-relay's Telegram path using Anthropic's own transport.
   Risk: research preview — allowlist gating (`--dangerously-load-development-channels` for
   custom channels) and a contract that "may change." Build it thin: MOP eval inside the
   `reply` handler, reusing the existing evaluator/deliver injection points unchanged.
   Also: update `docs/architecture.md`'s Channels section — the streaming-incompatibility
   premise does not match the published contract.

2. **Pydantic AI output-validator adapter.** Near-free given MOP already depends on
   pydantic-ai; `ModelRetry` gives the reject loop natively. Good demo that MOP is
   genuinely harness-agnostic. Non-streaming runs only.

3. **TS Agent SDK port of the patchbay-relay pattern.** Same design as the shipped Python
   reference; broadens reach to the larger TS SDK-host population. Mechanical, not novel.

4. **OpenAI Agents SDK wrapper** (non-streaming). Middleware around `Runner.run`; guardrails
   alone can't rewrite, so MOP adds real capability here. Defer streamed runs until
   upstream ships stream-halting (issue #495).

5. **LangGraph `after_agent` middleware.** Workable but the retry loop is the clunkiest of
   the set; do after 1–4 unless demand appears.

**Not worth building:** a Claude Code interactive-CLI adapter. No pre-display gate exists;
`MessageDisplay` offers only agent-blind, fail-open display rewriting. For the terminal, the
honest posture is architecture.md's fallbacks — audit-only logging (fallback #1, buildable
today from the transcript via Stop) and opt-in post-hoc correction (fallback #3). Same
posture for every truly streaming surface: OpenAI streamed runs, unbuffered LangGraph
streams, and Anthropic-managed UIs.
