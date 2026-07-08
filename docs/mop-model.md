# MOP — the model, its coherence, and the open forks

*An outside read of the architecture as of 2026-07-07 (develop @ c3c5600). Claims cite the file or ADR they rest on; inferences are marked.*

---

## 1. The essential model

MOP is a **pure verdict function over finalized outbound agent text, plus a set of thin placements of that function at delivery chokepoints**. The function takes one string and one ordered-irrelevant flat rule set and returns one of three dispositions — accepted, rewritten, rejected — computed, never declared. Everything else in the repo is either (a) how the rule set gets assembled, (b) how the function gets invoked from a host, or (c) what the host does with the result. There is no agent runtime inside MOP, no transport, no LLM provider knowledge beyond a litellm adapter behind an injectable seam. That smallness is the design.

The core abstractions, and what everything else is an instance of:

- **Rule = name + detector + parameters.** Four detectors: `llm`, `regex`, `script`, `length` (ADR-0001, `mop/rules.py`). Three are deterministic; one is judged. A rule set is two flat layers — opt-in packaged built-ins under a discovered-or-explicit local `.mop/` overlay, same-name replaces, `active: false` silences (`mop/discovery.py`, ADR-0001). No imports, no rulesets, no config file. With zero rules MOP warns and accepts — it imposes nothing by default (`mop/cli.py`).

- **The evaluation pipeline** (`mop/cli.py::check`, ADR-0002): deterministic detectors run first and are **authoritative** — a match is a hard violation no model can wave away. Then exactly **one** LLM call judges the `llm` rules and produces a best-effort rewrite instructed to also repair the deterministic hits (`mop/evaluators.py::_build_query`). Deterministic rules are **re-checked against the rewrite**, so a fix only counts if it cleared the pattern.

- **The verdict is derived** from two facts — did the text change? is `unresolved` empty? — via `verdict_from_eval_response` (`mop/types.py`). The model returns only `{rewritten, unresolved}` (`EvalLLMResponse`); the label can never disagree with the data. `Rewritten` may carry an `unresolved` residual (the partial case). Exit codes 0/1/2/3 mirror this on the CLI.

- **The host seam** is two typed callables: `Evaluator(text, det_hints, justification) -> Verdict` and `Deliver(text, system_note?)` (`mop/types.py`). MOP owns its own model tier aliases and a structured-output degradation ladder validated by pydantic at every rung (ADR-0003, `mop/evaluators.py`), so any provider — hosted, Ollama, eventually Apple on-device (ADR-0004) — plugs in behind the same signature.

- **The gate** (`mop/host.py::gate`) is the out-of-band placement: a synchronous call a Python host makes at its delivery chokepoint. It resolves rules, evaluates, appends a host-tagged JSONL audit record (`mop/audit.py`), and returns a `GateResult` saying what to deliver. Its two **gears**: `log` (evaluate + audit, never alter delivery — the shadow launch) and `enforce` (deliver the rewrite; redact on reject).

- **Integration shape is selected by one property of the host** — "is there a Python seam holding the full response string pre-delivery?" (ADR-0005, `docs/integration.md`). Yes → Shape 1, in-process gate (Hermes `transform_llm_output`, shipped in log mode, `integrations/hermes/`). No → Shape 2, shell out to `mop check` (Pi extension, `integrations/pi/mop.ts`) or wrap the host in a Python relay (patchbay). **Streaming is authoritative over enforcement mode**: a host that streams tokens has already shown the user text, so enforcement degrades to audit-only unless streaming is off (ADR-0005). **One gate per delivery path**, at the outermost boundary (`docs/integration.md`).

- A second, older placement exists: the **stateful MCP gate** (`mop/protocol.py`, `mop/mcp.py`) — `submit_message`/`submit_justification` tools, a bounded justification loop, and a fail-open escape hatch (`AcceptedFailedOpen`) for Agent-SDK hosts where the agent itself drives delivery.

One sentence to hold: *deterministic rules decide, one cheap model repairs, arithmetic labels the result, and hosts choose only where to stand and whether the gears are engaged.*

---

## 2. Load-bearing decisions and their coherence

1. **Derived verdict** (ADR-0002, `types.py`). The keystone. It makes every adapter honest by construction and makes exit codes, audit records, and `GateResult` trivially consistent. Coherent with everything else; the best decision in the repo.

2. **Deterministic authority** (ADR-0002, `cli.py::_deterministic_hits` + re-check). Coherent with the derived verdict and with the "cheap evaluator does the rewrite" economics. But see the enforcement hole below — the *delivery* layer does not fully honor it.

3. **One LLM call, best-effort rewrite** (ADR-0002). Coherent: the expensive frontier agent never redoes work the cheap judge can do, and `unresolved` is the clean handoff for what it can't.

4. **Four detectors, two flat layers, opt-in built-ins** (ADR-0001, `discovery.py`). Coherent with the solo, ship-fast posture — the Vale-style composition machinery was dropped, not deferred, and the `length` walk-back is honestly reasoned. "MOP imposes nothing by default" is consistent with the log-first integration stance.

5. **Host-injected evaluator/deliver; MOP owns model tiers** (ADR-0003, `types.py`, `evaluators.py`). Coherent: the seam is what makes ADR-0004's local-model exploration and the Apple-adapter idea zero-refactor futures.

6. **Shape selection by the Python-seam property; streaming authoritative; log/enforce gears; one gate per path** (ADR-0005, `host.py`, `integration.md`). Coherent as a set, and notably honest — the docs refuse to claim enforcement where transient exposure exists.

### Genuine tensions and drift

- **The enforce path can deliver text that still violates an authoritative rule.** In `check()`, a rewrite that clears *some* violations but leaves a deterministic hit yields `Rewritten(unresolved=[det-rule])` — and `host.py::_delivery` delivers any `Rewritten`'s text; only `Rejected` redacts. So under `mode=enforce`, a secret-leak regex the model half-fixed goes out the door, exit-code-1-labeled but delivered. In the MCP gate the agent sees `unresolved` and can react; in the out-of-band gate nobody is left to react. ADR-0002's motivating scenario ("a coin-flip LLM could rationalize a matched secret-leak pattern") is thus closed at the *verdict* layer but reopened at the *delivery* layer. This is the sharpest incoherence I found.

- **Two evaluation engines with different semantics.** The MCP gate (`protocol.py`) does not run the ADR-0002 pipeline: it calls `collect_lint_hints` — which checks **only** `lint: True` bundled entries (`rules.py::collect_lint_hints`), skipping ordinary `regex`/`length`/`script` rules entirely — passes them as *advisory* hints, does no post-rewrite re-check, and lets the evaluator's verdict stand. ADR-0002 names the advisory-vs-authoritative half of this as a "temporary inconsistency"; the lint-only filtering makes it worse than documented. Meanwhile the README calls the MCP gate "legacy" while ADR-0005 positions it as *the* path for Agent-SDK hosts — the docs disagree about whether this code has a future.

- **Log mode doesn't rehearse enforce mode.** `gate(mode="log")` runs judge-only (`allow_rewrite=False`, `host.py::_evaluate`), so its verdict space is only Accepted/Rejected. The shadow launch therefore measures violation *rates* but generates zero evidence about rewrite *quality* — the exact thing you'd want vetted before shifting gears. The stated purpose ("flip to enforce only after rules are vetted", `integration.md` §A) is only half-served by the data log mode produces.

- **Retired vocabulary lives on in code.** CONTEXT.md retires "lint," yet `Rule.lint`, `register_builtin_lint`, `collect_lint_hints`, `collect_regex_hints`, and the CLI's "Kind: lint" display all remain (`rules.py`, `cli.py`). Cosmetic, but it's exactly where the next contributor (future-you) re-learns a dead mental model.

- **`gate_from_env` discovery is cwd-relative** (`host.py` → `resolve_rules(rules_dir=None)` → walk-up from the *host process's* cwd, `discovery.py`). A Hermes deployment without `MOP_RULES_DIR` gets whatever `.mop/` happens to be above wherever Hermes was launched — most likely nothing, possibly something surprising. Inferred, not observed in the wild; but it means the shipped Hermes integration evaluates against an empty rule set unless env is configured, silently.

---

## 3. The open high-level decisions

Ranked by cost of deciding late.

**D1 — What does `enforce` guarantee about unresolved deterministic violations?** (the delivery hole above). Options: (a) redact whenever `unresolved` contains a deterministic rule, even on a rewrite; (b) deliver best-effort and accept that "authoritative" means "authoritatively labeled"; (c) split rules into must-block vs. best-effort classes. Stakes: this *is* MOP's promise — every rule authored, every host wired, and every claim in the docs is against this contract. It costs one function (`_delivery`) to fix today; after Hermes flips to enforce and rules are written assuming one semantics, it's a behavior change on a live channel. Cheapest now, most expensive later. Decide first.

**D2 — One evaluation engine or two: the MCP gate's fate.** Options: (a) rebuild `protocol.py`'s justification loop on top of `check()` so there is one pipeline with one semantics; (b) explicitly park/delete the MCP gate until an Agent-SDK host actually exists; (c) keep both and reconcile piecemeal. Stakes: every rule added and every semantic change (like D1) currently has to land twice or silently diverge; the divergence already exceeds what ADR-0002 documented. The README-vs-ADR-0005 status disagreement should resolve with this. Note: option (b) is nearly free — no live integration uses the MCP gate today (`integration.md`).

**D3 — Streaming endgame: is MOP an enforcement gate or an audit/telemetry layer that sometimes enforces?** ADR-0005 settled the *constraint* (streaming wins); the *response* is unbuilt and still forked (architecture.md "Channels compatibility": audit-only, mid-stream deterministic eval, or post-hoc correction push; plus the "streaming off on gated surfaces" doctrine). Stakes: this decides MOP's identity. If real hosts (Hermes gateway, Pi TUI) won't turn streaming off, MOP converges on audit-first, and the enforcement machinery (D1, D2) matters less than the corpus pipeline. Deciding late is survivable — the audit fallback is genuinely useful either way — but each new host integration hard-codes an assumption about which product this is.

**D4 — The log→enforce flip criteria and the eval/corpus loop.** There is a counterexample corpus and harness (`evals/`), audit logs designed to be "mined into new evals" (`cli.py::_maybe_audit`), and a stated gate ("flip after rules are vetted") — but no defined evidence standard, and `llm` rules aren't in the default harness run (`--llm` opt-in, `evals/README.md`), and log mode can't produce the rewrite data the flip decision needs (§2). Options: define per-rule precision thresholds from audit data; or vet ad hoc and accept judgment calls. Stakes: without this, `enforce` either never happens (MOP stays a logger) or happens on vibes. Medium urgency — the corpus accrues value regardless, and the schema already carries host tags.

**D5 — Evaluator locality and the gate's own privacy.** Every outgoing message — the very text whose secret-leaks the deterministic rules exist to catch — is sent to a hosted third-party API (`small` = DeepSeek, ADR-0003) the moment any `llm` rule is active. ADR-0004 shows the local option (Gemma 4 4B) is viable-but-slow and the Apple adapter is a clean seam. Stakes: real but cheap to change late — it's a model string and possibly a thin adapter behind an existing seam. Decide when an `llm` rule goes active on a sensitive channel, not before.

**D6 — Should anything ever ship active-by-default?** Today: built-ins opt-in, most packaged rules `active: false` (`rules/README.md`), zero-rules = warn-and-accept. Options: keep pure opt-in forever; or ship a minimal always-on core once vetted. Stakes: low for a personal deployment (env config is under your control); this only matters if MOP is ever *distributed*. Cheapest to defer.

---

## 4. Coherence verdict and what matters most next

**The architecture is sound, and unusually well-proportioned for a solo project.** The core is a small pure function with a derived, un-lieable verdict; hosts integrate through two typed callables or one CLI; the four surveyed hosts required zero core changes (ADR-0005 "Consequences"), which is the strongest empirical evidence the seams are cut in the right places. The ADRs are honest about walk-backs and inconsistencies rather than papering over them, and the dropped-not-deferred composition machinery (ADR-0001) shows the right instinct for right-sizing. Nothing here is enterprise theater; nothing essential is missing for what it claims to be *today*, which is a log-mode auditor with an enforcement option.

The risk is not the shape — it's that **the system currently has two verdict semantics and an underspecified enforcement promise**, and both get more load-bearing with every rule authored and every host wired. The docs' own confidence ("deterministic guarantees hold regardless of the model", ADR-0002) is ahead of what `host.py` delivers in enforce mode.

Two calls to settle next, in order:

1. **Pin the enforce contract (D1) and make one engine authoritative (D2).** These are really one decision: state what a deterministic rule guarantees *at delivery*, implement it once in `check()`/`_delivery`, and either rebuild the MCP gate on that engine or park it explicitly until an Agent-SDK host exists. This is small work with compounding returns — every future rule, host, and doc statement inherits it.

2. **Let Hermes log-mode data decide MOP's identity (D3/D4) — but instrument the log to actually answer the question.** The strategic fork (enforcement gate vs. audit layer) shouldn't be decided in the abstract; weeks of real Hermes verdicts will decide it. What *should* be decided now is that log mode must produce evidence relevant to the flip — which today it structurally cannot, because it never exercises rewrites. Resolve that mismatch as part of settling D1, then let the corpus speak.

Everything else — local evaluator, defaults, streaming machinery — is behind clean seams and correctly deferred.
