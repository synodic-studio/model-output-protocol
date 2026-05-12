# MOP Architecture

## System position

MOP makes itself the *only* path to the user: the agent has no other way to reach the human.

```
agent → submit_message (MCP tool) → MOP.eval → deliver(text) → user
                                            ↘ Rejected → submit_justification loop
                                            ↘ FailedOpen → deliver(text, system_note)
```

Counterpart on the input side: **HOP** (`human-output-protocol`).

---

## Verdict types

| Verdict | Effect |
|---|---|
| `Accepted` | `deliver(text)` called with the original message; `pending_message` cleared; `sent_message_this_turn = True`. |
| `Rewritten(rewritten)` | `deliver(rewritten)` called; agent sees the rewritten text in the tool result so future references resolve. |
| `Rejected(violations)` | `pending_message = source`; agent must call `submit_justification`. No delivery. |
| `AcceptedFailedOpen(system_note)` | After `max_justification_attempts = 4`, MOP delivers the original plus a `system_note` bubble. Burns the budget. |

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
    evaluator=async_callable(text, regex_hints, justification) -> Verdict,
    deliver=async_callable(text, system_note?) -> None,
)
```

This keeps MOP transport-agnostic and LLM-agnostic. The reference Haiku evaluator lives in `mop.haiku` (`build_haiku_evaluator`); the host supplies its own `deliver` (Telegram, Slack, web socket, …).

---

## Wire schema

`mop.types.EvalLLMResponse` is a pydantic model that LLM adapters pass to their structured-output layer (e.g. pydantic-ai's `output_type=`). `mop.types.verdict_from_eval_response()` converts the structured response back to a `Verdict`. Keeping the schema inside MOP means every adapter speaks the same wire format — no schema drift across hosts.

```python
class EvalLLMResponse(BaseModel):
    action: Literal["accept", "rewrite", "reject"]
    rewritten: str | None = None
    violations: list[str] = []
```

---

## Channels compatibility

**Problem:** MOP delivers via discrete tool calls. Channels stream partial output as it arrives. These are mutually exclusive.

**Current behavior:** SDK-based MOP harnesses must declare `supports_inflight_push=False`. Channel mode is incompatible with discrete-tool delivery.

**Intended resolution (in priority order):**

1. **Audit-only in channel mode** — stream through without blocking, log violations. Enforcement only in non-channel turns.
2. **Streaming deterministic eval** — regex/word-count rules can fire mid-stream; LLM rules remain post-hoc.
3. **Post-hoc correction push** — emit a Haiku-rewritten correction as a follow-up message after a violating stream completes. Weird UX; opt-in only.

---

## Rule format

```yaml
name: rule-id
detector: llm | regex | word_count
parameters:
  prompt: "Does this message...?"   # llm
  patterns: [...]                    # regex
  max: 200                           # word_count
guidance: "..."                      # surfaced to the LLM evaluator as advice
rationale: "..."                     # human-facing
sunset_check: "..."                  # for transitional rules
```

Rules live in a flat `rules/` directory and are loaded at startup. To stage a draft rule without enforcing it, keep it out of `rules/` (e.g. in a notes file outside the directory or behind a feature flag in your fork).

---

## Implementation status

- [x] Verdict union, Gate, NoPendingMessageError types
- [x] Rule loading + regex hint collection
- [x] `protocol_prompt(rules)` system-prompt builder
- [x] `MOP` class — submit_message, submit_justification, get_rules, get_status
- [x] Failed-open path
- [x] `mop.hooks.stop()` Stop-hook callable
- [x] In-process MCP server builder
- [x] EvalLLMResponse wire schema + verdict_from_eval_response
- [x] Live verified end-to-end via [patchbay-relay](https://github.com/synodic-studio/patchbay-relay) (Telegram text + photo + document paths)
- [ ] Channels compatibility (audit-only mode)
- [ ] CC plugin form of Stop hook (for non-SDK harnesses)
- [ ] Streaming deterministic eval in channel mode
