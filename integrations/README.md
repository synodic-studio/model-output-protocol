# Host integrations

MOP owns its host adapters. Each subdirectory here is an installable shim that
plugs MOP's gate into one host's outgoing-message chokepoint. The shims are
thin — the real work lives in [`mop/host.py`](../mop/host.py) (`gate()`), so
every host shares one evaluation + audit path. Design rationale and per-host
chokepoints are in [`docs/integration.md`](../docs/integration.md).

**One gate per delivery path.** A relay that runs another agent as a subprocess
(patchbay-relay runs Pi) must gate at the *outermost* boundary only. Installing
a MOP gate in both the relay and the inner agent double-evaluates and
double-logs the same message. Every audit record carries a `host` tag so
double-counting is at least detectable, but the rule is: pick one layer.

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

## patchbay/

(Reserved — the patchbay-relay filter shim. Gate at `_send_response`
[`patchbay/telegram_send.py`], logging passthrough. Because patchbay runs Pi,
this is the single gate for the Pi delivery path; do not also gate inside Pi.)
