# patchbay-relay integration

patchbay-relay is Python and finalizes every outgoing reply as a string in
`_send_response` (`patchbay/telegram_send.py`), alongside two existing filters
(`_is_silence_narration`, `_is_noisy_status`). MOP slots in as a third filter.
MOP owns the logic; patchbay's edit is one call.

## Wiring (in `patchbay/telegram_send.py`, in `_send_response`)

Place the call **after** the silence/noisy-status drop filters and **before**
the MarkdownV2 conversion / chunking, so MOP only sees what is actually
delivered (the audit means "what the user got", not "what the agent emitted"):

```python
if response:
    try:
        from mop.host import filter_text
        response = filter_text(response, host="patchbay-relay")
    except Exception as _mop_err:
        bridge.logger.warning("MOP gate failed, passing response through: %s", _mop_err)
```

`filter_text` returns the string to send: the original in log mode, a rewrite or
redaction notice in enforce mode. It reads the MOP_* env vars and is fail-open —
on any error it returns the original text, never withholds it.

## Reactivation config (log mode — passthrough)

patchbay must be able to `import mop` (add `model-output-protocol` to its deps,
or install it into patchbay's venv). Then in patchbay's environment:

```
MOP_MODE=log
MOP_AUDIT_LOG=<patchbay-repo>/mop-audit
# MOP_RULES_DIR / MOP_BUILTINS / MOP_EVALUATOR_MODEL as desired (unset = no rules)
```

Log mode records every reply's verdict (`host="patchbay-relay"`) and changes
nothing the user sees. Flip to `MOP_MODE=enforce` only after rules are vetted —
and only if the Pi extension is NOT also enforcing on this path (one enforcing
gate per path; see ../README.md).

## Note

patchbay is marked "alpha, no longer actively developed" and it went pi-only
(the earlier in-process MOP harness was removed at that refactor). This wiring
is the lightweight reactivation — a filter call, not the old harness. patchbay
buffers Pi's full output before sending (no live token streaming to Telegram),
so enforcement here is airtight with no pre-verdict leak.
