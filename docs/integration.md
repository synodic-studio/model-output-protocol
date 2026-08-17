# Integrating MOP into a host

MOP is an output gate: give it the text an agent is about to send a human, get
back a verdict — **accepted** (deliver as-is), **rewritten** (deliver the
cleaned text), or **rejected** (don't deliver; the agent may justify). This doc
maps that gate onto four real hosts and states, honestly, where enforcement is
possible today and where it is only advisory.

## Two integration shapes

Every host reduces to one question: **is the outgoing-text path Python with a
seam where the finalized response string is available before it reaches the
user?**

- **Shape 1 — in-process gate (preferred).** Host is Python and has (or can
  register) an output-transform seam. MOP is imported as a library; the host
  injects a `deliver` closure and an evaluator. Full accept/rewrite/reject.
  Surfaces: [`mop.protocol.MOP`](../mop/protocol.py),
  [`mop/mcp.py`](../mop/mcp.py) (agent-driven `submit_message` tools),
  [`mop/audit.py`](../mop/audit.py) (`MOP(auditor=…)`).
- **Shape 2 — out-of-band CLI gate / audit-only.** Host is not Python, or has
  no output-rewrite seam. Either shell out to `mop check "<text>"` (exit
  0/1/2/3) on captured output, or run the host *behind* a Shape-1 Python relay
  that gates its stdout. Where the host streams tokens to the user before
  finalizing, enforcement degrades to **audit-only** (log verdicts; can't unsend).

The cross-cutting constraint is **streaming** — see the bottom of this doc and
[ADR 0005](adr/0005-integration-shapes-and-streaming.md).

**One gate per delivery path.** A relay that runs another agent as a subprocess
(patchbay-relay runs Pi as `pi -p --mode json`) sees that agent's final output
as a string it can gate. Installing a MOP gate in *both* the relay and the inner
agent double-evaluates and double-logs the same message. Gate at the **outermost
boundary only**. Every audit record carries a `host` tag so double-counting is at
least detectable, but the rule is: pick one layer per path.

---

## A. Hermes — SHIPPED (log mode), Shape 1, zero core edits

**The highest-leverage integration, now live in log mode.** The plugin lives in
this repo at [`integrations/hermes/`](../integrations/hermes/) and is symlinked
into `~/.hermes/plugins/mop`; it registers `transform_llm_output` and calls
[`mop.host.gate`](../mop/host.py) with `host="hermes"`. Enabled via
`plugins.enabled: [mop]` in Hermes config; audit dir via `MOP_AUDIT_LOG` in
`~/.hermes/.env`. Default `MOP_MODE=log` — every outgoing message is evaluated
and recorded, delivery untouched. Flip to `enforce` only after rules are vetted.

Hermes (`~/.hermes/hermes-agent/`, Python, v0.16.0) is the live personal agent
reachable over ~20 chat platforms — the thing actually talked to from the iPad.
It exposes exactly the seam MOP needs.

- **Chokepoint:** the `transform_llm_output` plugin hook, fired at
  `agent/turn_finalizer.py:272` right before every surface (CLI, gateway, ACP)
  returns `final_response`. Its published contract *already matches* MOP's
  verdicts:
  - return `None`/empty → leave unchanged  ⟷ **accepted**
  - return a string     → replace the response ⟷ **rewritten**
  - return a refusal/placeholder string        ⟷ **rejected** (redacted)
- **Wiring:** register a Hermes plugin (`hermes_cli/plugins.py`,
  `register_hook("transform_llm_output", …)`). No edits to Hermes core. The
  plugin calls MOP in-process (`from mop import …`), passing the rule set and a
  litellm evaluator; on the verdict it returns the right string.
- **Existing precedent to generalize:** `_sanitize_gateway_final_response`
  (`gateway/run.py:343`) is already a final-text filter, but Telegram-only and
  secret-redaction-only. MOP is the general form of that filter.
- **Streaming caveat:** Hermes streams progressively; a `transform_llm_output`
  rewrite reaches the user by *editing the already-streamed message*
  (`gateway/run.py:16268-16297`), so the user briefly saw the pre-rewrite text.
  For a hard reject/redact that transient exposure is a real gap. Run gated
  surfaces with streaming `off` (`gateway/config.py:402`, per-platform
  overridable) or have the plugin force full-response buffering before emit.

---

## B. patchbay-relay — historical reference, currently removed (Shape 1)

> **Shipped (wiring):** [`integrations/patchbay/`](../integrations/patchbay/)
> documents the one-call reactivation — `mop.host.filter_text(response,
> host="patchbay-relay")` in `_send_response`, log mode. Gates whatever harness
> patchbay dispatches (Pi, codex, …) at the delivery boundary.


MOP was **fully integrated here once and then removed** — the Claude-SDK and
`cc-sdk-mop` harnesses were deleted in commit `906b0c8` when the bridge went
`pi`-only, and the README now marks it "alpha, no longer actively developed."
So this is *not* a live integration; it's a paved road if the bridge is ever
revived.

- **Chokepoint (still ideal):** `_send_response` at `patchbay/telegram_send.py:240`,
  immediately after the file-sentinel extraction (`:267`) and alongside two
  existing out-of-band filters — `_is_silence_narration` (`:273`) and
  `_is_noisy_status` (`:285`). MOP slots in as a third, more capable filter on
  the whole `response` string (gate before the `TELEGRAM_MSG_LIMIT` chunking).
- **Language:** Python — in-process import, no CLI shell-out.
- **Paved road left behind:** the `.env.example` MOP block (`:56-74`), real
  captured verdict logs in `mop-audit/*.jsonl`, and design docs under
  `docs/superpowers/plans/`. Config is **stale** (`MOP_LLM_BACKEND=haiku`,
  `MOP_RULES_DIR=…/rules/active`) — it predates the deepseek default and the
  `.mop/` discovery model; don't copy it verbatim.
- Because patchbay runs Pi as `pi -p --mode json` (`patchbay/harness/pi.py:103`)
  and gates the captured string, **gating here also enforces Pi's output** —
  see C.

---

## C. Pi (pi-mono) — TypeScript, no output-rewrite hook (Shape 2)

> **Shipped:** a Pi extension at [`integrations/pi/mop.ts`](../integrations/pi/mop.ts)
> gates on `message_end` and shells to `mop check --host pi`. Log mode
> (observe-only) is verified end-to-end against real `pi -p`; enforce mode
> (in-place mutation) is opt-in and marked fragile. Gates Pi wherever it runs.


Pi is TypeScript/Node end-to-end. Its extension system
(`packages/coding-agent/docs/extensions.md`) gates **inputs and tool traffic**
(`input`, `tool_call`, `before_provider_request`) but the assistant's outgoing
text is emitted only via **observe-only** events (`message_end`,
`packages/coding-agent/src/core/extensions/types.ts:1100-1102`). There is no
designed hook to rewrite or reject the message the user is about to see, and
interactive mode streams tokens before `message_end` fires.

Honest options, best first:

1. **Gate downstream in a Python relay (recommended).** When Pi runs headless
   (`pi -p --mode json`), its output is a captured string. A Shape-1 host
   (patchbay-style, or any Python wrapper) gates that string with full
   enforcement. This is the real enforcement path and needs no Pi changes.
2. **`pi -p` one-shot wrapper.** For scripted/CI use, `pi -p "<prompt>" |
   mop check` — the single stdout emit is `print-mode.ts:140`. Works today;
   covers print mode only, not the interactive TUI.
3. **Audit-only extension.** An extension observes `message_end` and shells to
   `mop check` to *log* verdicts. Cannot suppress/rewrite (text already
   streamed). Useful for building an eval corpus from real Pi traffic.
4. **Upstream a real gate.** Land a result-bearing output event (e.g.
   `message_finalize`) around `packages/agent/src/agent-loop.ts:318` so
   extensions can rewrite/reject in-process. This is the only path to true
   in-Pi enforcement; it's an upstream PR, not a local change.

---

## D. Claude Code — no self-rewrite hook (Shape 1 only via the Agent SDK)

Stock Claude Code (the CLI harness this repo is developed in) has **no hook that
can rewrite or suppress the assistant's final text**. The hook surface
(PreToolUse, PostToolUse, UserPromptSubmit, Stop, SubagentStop, PreCompact,
SessionStart) intercepts tool calls and turn boundaries, not assistant
messages. So MOP cannot gate stock CC from inside CC.

Two real paths:

1. **Agent-SDK harness (Shape 1).** If the CC agent is built on
   `claude_agent_sdk`, MOP's in-process MCP gate is exactly this case: mount
   [`mop.build_mcp_server`](../mop/mcp.py) so the agent delivers via
   `submit_message`, concatenate [`protocol_prompt`](../mop/hooks.py) into the
   system prompt, and register the [`stop`](../mop/hooks.py) hook so a turn
   can't end without a delivered message. This is what `mcp.py` / `hooks.py` /
   `protocol.py` were built for. Full accept/rewrite/reject + justification loop.
2. **Gate at the transport (Shape 2).** Whatever carries CC's output to the
   user (e.g. a Telegram bridge running CC headless) can gate the captured text
   out-of-band, the same way a Python relay gates Pi. **Which transport carries
   CC-over-Telegram today is unconfirmed** — patchbay-relay used to (its
   `cc-sdk-mop` harness) but removed it and went pi-only; `inline-relay` is a
   CC *code-review plugin*, not a transport. Identify the live bridge before
   assuming this path exists.

---

## Cross-cutting: streaming vs. discrete verdicts

MOP delivers discrete verdicts on a *complete* message. Hosts that stream tokens
to the user during generation (Hermes gateway, Pi interactive TUI) have already
shown the user text by the time MOP runs. This is the single constraint that
decides **enforce vs. audit**:

- **Streaming off / full buffering** → MOP is a true pre-send gate
  (accept/rewrite/reject all work).
- **Streaming on** → hard reject/redact leaks a transient pre-verdict view;
  degrade to audit-only, or accept the "edit-the-streamed-message" rewrite UX
  (Hermes supports it) knowing the original was briefly visible.

See [ADR 0005](adr/0005-integration-shapes-and-streaming.md) and the "Channels
compatibility" section of [architecture.md](architecture.md).

---

## Deployment wiring — where audit logging is turned on

Two vars have to be set, and either one missing produces a recorder full of
nothing:

- `MOP_AUDIT_LOG` names the directory, or nothing is written at all
  (`mop/host.py::gate`, guarded by `if audit_dir:`).
- `MOP_RULES_DIR` names the rule set, or **zero rules resolve** and every
  message is accepted unread. This one is quieter and worse: the records land,
  they just all say `accepted` with an empty `rule_names`.

Canonical dirs on this machine: audit at `/Users/bryancostanza/.mop/audit`
(daily-rotated `YYYY-MM-DD.jsonl`, UTC-dated), rules at
`/Users/bryancostanza/.mop/rules` — deterministic detectors only, so a verdict
costs no model call and adds no latency. Per-host wiring:

- **Hermes** — `EnvironmentVariables` in
  `~/Library/LaunchAgents/ai.hermes.gateway.plist` (`MOP_AUDIT_LOG`,
  `MOP_RULES_DIR`, `MOP_MODE=log`). Launched Python directly, so plist env
  reaches the process.
- **patchbay-relay** — `EnvironmentVariables` in
  `~/Library/LaunchAgents/com.synodic.patchbay-relay.plist` (`MOP_AUDIT_LOG`,
  `MOP_RULES_DIR`, `MOP_MODE=log`). Goes through `run.sh`, which inherits (does
  not scrub) the plist env. Log mode is the shadow launch: flip `MOP_MODE` to
  `enforce` once the rule set has been vetted against real traffic, and a
  deterministic hit starts redacting a real Telegram message.
- **Claude Code** — stock CC has no pre-delivery hook, so audit-only via a Stop
  hook: `~/.claude/hooks/mop-audit-stop.py` (registered in `~/.claude/settings.json`).
  It pulls the last assistant message's `text` blocks only (never `thinking`/
  `tool_use`) and shells to
  `mop check --host claude-code --rules-dir ~/.mop/rules --no-rewrite`. The CLI
  reads no `MOP_RULES_DIR`, hence the explicit flag; `--no-rewrite` keeps a
  deterministic-only run from dispatching a model call it would only discard.
- **Pi** — `mop.ts` shells to `mop check` reading `MOP_AUDIT_LOG`; interactive,
  not a launchd service, so wire the vars in Pi's own env when that surface is
  activated.

After editing a plist you must `launchctl bootout` + `bootstrap gui/$(id -u)` —
a plain edit changes nothing. Verify the var reached the **running** process
(`launchctl print gui/$(id -u)/<label> | grep MOP_`), not just the file.

Done-criterion for "logging works": a real host-tagged line lands via the
host's actual path. The single proving command:

```
cat ~/.mop/audit/*.jsonl | python3 -c "import sys,json,collections; \
print(collections.Counter(json.loads(l)['host'] for l in sys.stdin if l.strip()))"
```

should show `hermes`, `patchbay-relay`, and `claude-code`. Add the rule set to
the same check — an empty `rule_names` means the host is logging, but judging
nothing:

```
cat ~/.mop/audit/*.jsonl | python3 -c "import sys,json,collections; \
print(collections.Counter(bool(json.loads(l)['rule_names']) for l in sys.stdin if l.strip()))"
```

---

## Recommendation

Do **Hermes first** — it's live, Python, and its `transform_llm_output` hook
already speaks MOP's verdict shape with zero core edits. Pi and Claude Code are
either audit-only or need an upstream/SDK change; patchbay is a revival, not a
new build. Run gated surfaces with streaming off until channel-mode audit
support lands.
