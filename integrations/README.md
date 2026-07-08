# Host integrations

MOP owns its host adapters. Each subdirectory here is an installable shim that
plugs MOP's gate into one host's outgoing-message chokepoint. The shims are
thin — the real work lives in [`mop/host.py`](../mop/host.py) (`gate()`), so
every host shares one evaluation + audit path. Design rationale and per-host
chokepoints are in [`docs/integration.md`](../docs/integration.md).

**One _enforcing_ gate per delivery path.** These shims cover different axes:
the Pi extension gates Pi wherever Pi runs (standalone or inside any relay); a
relay filter gates whatever harness the relay dispatches (Pi, codex, …) at the
delivery boundary. They overlap only for Pi-inside-a-relay. Double **logging**
there is harmless — each record carries a `host` tag, so you just see the same
message from two surfaces. The thing to avoid is two **enforcing** gates on one
path: that stacks two rewrites / a redact-then-rewrite on the same message. So
the rule is precise — at most one gate in `enforce` mode per path; any number
may run in `log` mode.

## Gears: log vs. enforce

Both shims default to **log mode** — evaluate every message, write the verdict
to the audit log, but never change what the user sees. Flip to enforcement only
after rules are activated and vetted, via env (no code edit):

```
MOP_MODE=log            # default: passthrough, audit only
MOP_MODE=enforce        # apply rewrites; redact rejections
MOP_RULES_DIR=<dir>     # a .mop-style rules dir (unset = no local rules)
MOP_AUDIT_LOG=<dir>     # JSONL flight recorder (unset = no audit)
MOP_BUILTINS=1          # opt in to packaged built-in rules
MOP_EVALUATOR_MODEL=<m> # litellm model/tier; only used once llm rules are active
```

With no active rules, the gate short-circuits to `accepted` without importing
litellm or touching an event loop — logging is effectively free.

## hermes/

Symlink into Hermes' user-plugin dir (`~/.hermes/plugins/<name>/`). MOP stays
in place in this repo; the symlink resolves back to it, so no MOP install into
Hermes' venv is needed.

```bash
ln -s "$PWD/integrations/hermes" ~/.hermes/plugins/mop
# set env for the Hermes process, e.g. in its launchd plist / .env:
#   MOP_AUDIT_LOG=~/.hermes/mop-audit
```

Registers the `transform_llm_output` hook (`agent/turn_finalizer.py`). Fail-open:
any error passes the message through unchanged.

## pi/

`mop.ts` — a Pi extension (TypeScript). Pi has no output-rewrite hook, so this
gates on `message_end` and shells out to the `mop` CLI. Load it per invocation
or install it globally:

```bash
pi -p -e "$PWD/integrations/pi/mop.ts" "<prompt>"      # one-off
ln -s "$PWD/integrations/pi/mop.ts" ~/.pi/agent/extensions/mop.ts   # global
```

Log mode (default) is observe-only — it reads the final assistant text, calls
`mop check --host pi` to evaluate + audit, and does not touch the message.
Enforce mode mutates the final message in place; that leans on Pi-internal
by-reference behavior (verified for `pi -p` and the `agent_end` payload relays
read), so it's opt-in and should be re-verified per Pi version. Gates Pi
*wherever it runs*, including inside a relay — so if you enforce here, don't also
enforce at the relay for the Pi path.

## patchbay/

Wiring for the patchbay-relay send path (Python, in-process). MOP owns the
interface — `mop.host.filter_text(response, host="patchbay-relay")` — so
patchbay's edit is a single call added alongside its existing `_send_response`
filters (`patchbay/telegram_send.py`). See `patchbay/README.md` here for the
exact snippet and the reactivation config. Gates whatever harness patchbay
dispatches (Pi, codex, …) at the delivery boundary.
