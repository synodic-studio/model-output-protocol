# MOP Architecture

> Decisions and their rationale are recorded as ADRs in [`adr/`](adr/). The
> longer design/grilling narratives live in [`superpowers/specs/`](superpowers/specs/).

## System position

MOP makes itself the *only* path to the user: the agent has no other way to reach the human.

```
agent → submit_message (MCP tool) → MOP.eval → deliver(text) → user
                                            ↘ Rejected → submit_justification loop
                                            ↘ FailedOpen → deliver(text, system_note)
```

MOP is not a competing protocol to MCP (Model Context Protocol).
The name is a playful inversion; MOP *may* use MCP to operate (e.g. via
MCP tool definitions in a host harness).

Counterpart on the input side: **HOP** (`human-output-protocol`).

---

## Verdict types

| Verdict | Effect |
|---|---|
| `Accepted` | `deliver(text)` called with the original message; `pending_message` cleared; `sent_message_this_turn = True`. |
| `Rewritten(rewritten)` | `deliver(rewritten)` called; agent sees the rewritten text in the tool result so future references resolve. |
| `Rejected(unresolved)` | `pending_message = source`; agent must call `submit_justification`. No delivery. |
| `AcceptedFailedOpen(system_note)` | After `max_justification_attempts = 4`, MOP delivers the original plus a `system_note` bubble. Burns the budget. This is an **escape hatch** — it is never produced by the LLM evaluator, only by MOP itself. |

The verdict *is* the disposition — there's no separate severity or `on_violation` field.

---

## State

Plain instance state on the in-process `MOP` object — one MOP per CC session. No cross-process coordination.

```python
pending_message: str | None
justification_attempts: int = 0
sent_message_this_turn: bool = False
max_justification_attempts: int = 4
```

The host pins the MOP instance for the lifetime of the SDK client (typically a per-session struct that owns the client) so the Stop-hook closure stays alive.

---

## Tools

Exposed via `mop.build_mcp_server(mop)` — returns an `McpSdkServerConfig` you mount on `ClaudeAgentOptions.mcp_servers`.

| Tool | Behavior |
|---|---|
| `submit_message(text)` | Run the LLM evaluator with regex hints. Apply the verdict. |
| `submit_justification(reason)` | Bounded retry against `pending_message`. Failed-open on exhaustion. |
| `get_rules(regex_filter?)` | Read-only rule listing. |
| `get_status()` | Returns `(pending_message, sent_this_turn, justification_attempts)`. |

---

## Hooks

| Hook | Form | Behavior |
|---|---|---|
| Protocol prompt | `mop.protocol_prompt(rules)` — pure function | Returns the prompt string that teaches the agent the protocol. Host concatenates into `ClaudeAgentOptions.system_prompt`. **Not** a CC `SessionStart` hook — simpler lifecycle. |
| Stop | `mop.hooks.stop(mop)` returning `Allow \| Block(reason)` | Host registers as `ClaudeAgentOptions.hooks={"Stop": [HookMatcher(hooks=[callback])]}`. Callback closes over the MOP instance and calls `mop.hooks.stop` — blocks if `sent_message_this_turn` is False; resets on Allow. |

CC plugin form of the Stop hook is deferred — it's only needed if MOP gets wired into non-SDK harnesses (cc-cli, pi).

---

## Host-injected callables

MOP itself doesn't speak HTTP, doesn't know what an LLM provider is, and doesn't know what Telegram is. The host injects:

```python
MOP(
    rules=rules,
    evaluator=async_callable(text, lint_hints, justification) -> Verdict,
    deliver=async_callable(text, system_note?) -> None,
)
```

This keeps MOP transport-agnostic and LLM-agnostic. MOP provides two adapter surfaces:

- **`mop/mcp.py`** (`build_mcp_server`) — stateful MCP server for agent-in-process usage (the primary path for SDK-based harnesses).
- **`mop/cli.py`** (`main`, `check`) — stateless one-shot adapter for the `mop` CLI command. Supports `mop check` (evaluate text, exit 0/1/2/3) and `mop rules` (inspect the resolved rule set).

The reference evaluator is **`build_litellm_evaluator`** (`mop/evaluators.py`), a litellm-backed, provider-agnostic implementation. Model selection precedence:

  `--model` arg (a `small`/`medium`/`large` tier or a raw `provider/model`)  > `MOP_EVALUATOR_MODEL` env var  > legacy `MOP_EVALUATOR=deepseek|haiku` alias  > default tier `small` = `deepseek/deepseek-v4-flash`. Tiers resolve through MOP's own `MODEL_ALIASES`.

The host supplies its own `deliver` (Telegram, Slack, web socket, …).

Deterministic detectors (`regex`/`script`/`length`) are **authoritative** — a match rejects on its own; the one LLM call judges only `llm` rules. See [`CONTEXT.md`](../CONTEXT.md) and [`adr/`](adr/).

---

## Wire schema

`mop.types.EvalLLMResponse` is a pydantic model that LLM adapters pass to their structured-output layer. `mop.types.verdict_from_eval_response()` **derives** a `Verdict` from it — the model no longer declares an `action`; the label is computed from *(did the text change? is `unresolved` empty?)*, so it can never disagree with the data.

```python
class EvalLLMResponse(BaseModel):
    rewritten: str | None = None   # best-effort corrected text; null = no change
    unresolved: list[str] = []     # rule names the model could not fix
```

---

## Channels compatibility

**Problem:** MOP delivers via discrete tool calls. Channels stream partial output as it arrives. These are mutually exclusive.

**Current behavior:** SDK-based MOP harnesses must declare `supports_inflight_push=False`. Channel mode is incompatible with discrete-tool delivery.

**Intended resolution (in priority order):**

1. **Audit-only in channel mode** — stream through without blocking, log violations. Enforcement only in non-channel turns.
2. **Streaming deterministic eval** — regex/length rules can fire mid-stream; LLM rules remain post-hoc.
3. **Post-hoc correction push** — emit an evaluator-rewritten correction as a follow-up message after a violating stream completes. Weird UX; opt-in only.

---

## Rule format

Every entry is a rule with one of four detectors. Full schema and examples
in [`rules/README.md`](../rules/README.md) and
[`adr/0001`](adr/0001-detector-taxonomy-and-composition.md).

```yaml
name: rule-id
detector: llm            # or: regex | script | length
parameters:
  prompt: "Does this message...?"   # llm
  # patterns: [...]                 # regex
  # command: .mop/scripts/check.sh  # script (stdin in, exit code = verdict)
  # max_words: 200 / max_chars: 3000  # length
guidance: "..."                      # shown to the agent on rejection
rationale: "..."                     # human-facing, never sent to the model
```

`regex`/`script`/`length` are deterministic and authoritative. Rules live in
a flat `rules/` directory; `active: false` stages a draft without enforcing it.

---

## Implementation status

- [x] Verdict union, Gate, NoPendingMessageError types
- [x] Rule / lint loading + hint collection
- [x] `protocol_prompt(rules)` system-prompt builder
- [x] `MOP` class — submit_message, submit_justification, get_rules, get_status
- [x] Failed-open path
- [x] `mop.hooks.stop()` Stop-hook callable
- [x] In-process MCP server builder
- [x] EvalLLMResponse wire schema + verdict_from_eval_response
- [x] Live verified end-to-end via [patchbay-relay](https://github.com/synodic-studio/patchbay-relay) (Telegram text + photo + document paths)
- [x] CLI adapter (`mop check` / `mop rules`, `.mop/` discovery, packaged built-ins)
- [ ] Channels compatibility (audit-only mode)
- [ ] CC plugin form of Stop hook (for non-SDK harnesses)
- [ ] Streaming deterministic eval in channel mode
- [ ] LLM rule support in eval harness (currently only lints are auto-tested)
- [ ] Lint/rule schema formalization in YAML
