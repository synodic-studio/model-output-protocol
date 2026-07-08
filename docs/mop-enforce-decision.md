# Enforcement-path decisions

*Resolves the three confirmed issues from docs/mop-model.md §2. Decision doc only — no implementation proposed beyond contract statements. 2026-07-07, develop @ c3c5600.*

---

## Issue 1 — What does `enforce` guarantee about unresolved deterministic violations?

### Current behavior

`mop/cli.py::check` re-checks deterministic rules against the rewrite and, when the text changed but a pattern still fires, returns `Rewritten(final_text, unresolved=[...])` (cli.py lines 90–98). `mop/host.py::_delivery` then delivers the text of **any** `Rewritten` without inspecting `unresolved` (host.py lines 126–134); only `Rejected` redacts. So in `mode=enforce`, a half-fixed secret-leak match goes out the door. This contradicts ADR-0002's motivating scenario at the delivery layer even though the verdict layer closed it.

### (a) The decision

Define the enforce contract: **may `gate(mode="enforce")` ever deliver text on which an active deterministic rule still fires?**

### (b) Options

- **1A — Deterministic residual blocks delivery; llm residual does not.** If the delivered text would still trip a `regex`/`script`/`length` rule, enforce redacts (delivers `REDACTION_NOTICE`) even for a `Rewritten` verdict. A `Rewritten` whose `unresolved` contains only `llm`-rule names still delivers — those are judged, not authoritative. Trade-offs: honors ADR-0002 exactly; costs a partition of `unresolved` by detector at delivery time (`_delivery` currently receives only the verdict — either the verdict grows a deterministic/judged split, or `gate` re-checks the candidate `deliver` text with `_deterministic_hits`, which host.py already imports — inference: either is a few lines). Slightly more redaction notices on partially-fixed messages.
- **1B — Any non-empty `unresolved` blocks delivery.** Treat partial `Rewritten` like `Rejected` at delivery. Trade-offs: simplest (no detector partition); but it promotes a probabilistic judge's "I couldn't fix this llm-rule" into a hard block, erasing the deterministic/judged distinction ADR-0002 is built on. Over-blocks.
- **1C — Deliver best-effort; redefine "authoritative" as "authoritatively labeled."** Docs-only change. Trade-offs: zero code; but it guts the ADR's own rationale — the exact coin-flip-LLM scenario it was written to close reopens on every enforce-mode channel. The audit record would faithfully log the leak it just delivered.

### (c) Recommendation

**1A.** It is the only option under which "deterministic is authoritative" is true where it matters — at delivery — while preserving the two-tier rule model. The contract, stated once: *in enforce mode, MOP never delivers text on which an active deterministic rule fires; judged (`llm`) residuals are best-effort and deliver.* Cost is one small change in one function while no live channel runs enforce (Hermes is log-mode, per the c3c5600 commit message); after a flip it becomes a behavior change on a live channel. 1C is what the code accidentally implements today; nobody chose it, and choosing it on purpose would mean rewriting ADR-0002's premise. Decide 1A and update ADR-0002's Consequences to state the delivery-layer guarantee explicitly.

---

## Issue 2 — One evaluation engine or two: the MCP gate's fate

### Current behavior

`mop/protocol.py::submit_message` calls `collect_lint_hints` (which sees only `lint: True` bundled entries, skipping ordinary deterministic rules), passes them as advisory hints, does no post-rewrite re-check, and lets the evaluator's verdict stand (protocol.py lines 58–61, 75–77). `mop/cli.py::check` / `mop/host.py::gate` is the ADR-0002 two-phase engine. ADR-0002 names the advisory half of this as a "temporary inconsistency" scoped to v1; the lint-only filtering exceeds what it documented. No live integration uses the MCP gate (docs/integration.md; README calls it legacy while ADR-0005 positions it for Agent-SDK hosts — the docs disagree).

### (a) The decision

**Does `protocol.py` get rebuilt on `check()`, explicitly parked, or reconciled piecemeal?**

### (b) Options

- **2A — Park it explicitly.** Mark the MCP gate frozen/experimental (module docstring, README/ADR-0005 alignment), commit in writing that any revival rebuilds `submit_message`/`submit_justification` as a thin state wrapper around `check()` rather than patching the old path. Trade-offs: nearly free; makes "one engine" true by scope (the ADR-0002 engine is the only *live* engine); risk is dormant code rotting further — acceptable because it's quarantined by declaration, not trusted.
- **2B — Rebuild on `check()` now.** The justification loop, attempt budget, and `AcceptedFailedOpen` stay as protocol.py's state machine; the verdict inside comes from `check()`. Trade-offs: one pipeline, one semantics, Issue 1's contract lands everywhere at once; but it is real work spent on a path with zero users, ahead of any Agent-SDK host existing — the definition of premature for a solo project.
- **2C — Keep both, patch piecemeal.** Status quo. Every semantic change (starting with Issue 1) lands twice or silently diverges; the divergence already outran its own documentation. Reject.

### (c) Recommendation

**2A now, 2B as the condition-triggered follow-up.** The trigger is concrete: the first real Agent-SDK host integration. Until then, parking costs a docstring and two doc edits, resolves the README-vs-ADR-0005 status contradiction (resolve it toward "parked pending an Agent-SDK host" — both docs become true), and — critically — means Issue 1's contract only has to be implemented and tested in one engine. The justification-loop *design* (bounded retries, fail-open) is worth keeping and is orthogonal to which engine produces verdicts; 2B preserves it. What 2A refuses is maintaining two verdict semantics while one has no callers.

---

## Issue 3 — Producing the rewrite evidence the log→enforce flip needs

### Current behavior

`gate(mode="log")` sets `allow_rewrite=False` (host.py line 164 via `_evaluate`), so `check()` takes its judge-only branch (cli.py lines 83–88): verdict space is Accepted/Rejected, no rewrite is ever attempted, and the audit record (host.py lines 167–176) captures the original text and which rules fired but nothing about what enforce would have delivered. The shadow launch measures violation rates, not rewrite quality — the evidence the flip decision actually needs.

### (a) The decision

**Where does rewrite-quality evidence come from: the live log-mode path, an opt-in rehearsal mode, or offline replay of the audit log?**

### (b) Options

- **3A — Log mode always runs the full pipeline** (`allow_rewrite=True`, deliver stays original, audit records the would-be rewrite). Trade-offs: evidence is automatic and contemporaneous; but `gate` is synchronous on the host's send path (host.py docstring, lines 22–25), so every flagged message eats an LLM round-trip in *shadow* mode — paying enforce's latency and token cost without enforce's benefit, forever.
- **3B — A rehearsal knob** (third mode or a flag: log delivery + rewrite evaluation). Default log stays cheap; flip it on for an evidence-gathering window before an enforce flip. Trade-offs: evidence on demand, cost bounded to the window; one more mode on a two-gear surface that is currently pleasingly binary, and the window still pays live-path latency.
- **3C — Offline replay.** Audit records already carry `original` (host.py line 171); a batch harness re-runs `mop check` with rewrites over logged originals, off the send path. Trade-offs: zero live-path cost ever; re-runnable as rules evolve (today's rewrite quality against *today's* rules, which is what the flip decision needs — inference: contemporaneous rewrites would actually be stale evidence by flip time); aligns with the existing "audit logs mined into new evals" direction (cli.py `_maybe_audit`, evals/). Downside: evidence isn't automatic — someone must run the replay; and replayed LLM calls may differ from what would have happened live (acceptable: the flip question is distributional, not per-message).

### (c) Recommendation

**3C.** It answers the flip question with the data already being collected, adds nothing to the live path, and produces *fresher* evidence than 3A would (rewrites evaluated against current rules at decision time, not months-old ones). It also composes with Issue 1: the replay should report how often a partial rewrite would redact under the 1A contract — the single number the flip decision most needs. Keep 3B in reserve only if replay-vs-live divergence turns out to matter empirically; do not build it speculatively. 3A is rejected outright — permanent latency tax on the safe mode inverts the point of having gears.

---

## Ordering

Settle **Issue 1 first**: it is the contract every other artifact cites — every rule authored, both docstrings in host.py, ADR-0002's central claim — and it costs one function while enforce has no live callers; after Hermes flips it becomes a semantics change on a live channel. **Issue 2 second, immediately after** (they nearly collapse into one move): parking the MCP gate means the 1A contract is implemented, tested, and documented in exactly one engine, and the docs' engine-status contradiction resolves in the same edit — deciding 2 before 1 would just leave the parked code's divergence undefined against an unpinned contract. **Issue 3 last**: the replay's headline metric (how often partials would redact) is only defined once 1A exists, and nothing on develop blocks on it — the audit log accrues replayable evidence the whole time. Net: 1 unblocks a truthful enforce mode, 2 unblocks single-engine maintenance of it, 3 unblocks the flip decision itself.
