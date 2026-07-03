# Research: proactive rule-context injection

**Question:** the agent should see the active rules up front and write
compliant messages on the first try, instead of discovering rules by
violating them (or by voluntarily calling `get_rules`). What should be
injected, where, when, in what format, and how does it relate to the
planned `mop rules` CLI subcommand?

## Current state (differs from the framing of the question)

`protocol_prompt(rules)` in `mop/hooks.py` **already injects rule
content**: after the protocol-mechanics section it renders an
`ACTIVE RULES (n):` block, one line per rule, as
`- {name}: {guidance flattened to one line}`. The evaluator prompt in
`mop/evaluators.py` renders the same shape (`- {name}: {guidance}`), as
two duplicated inline expressions in `build_deepseek_evaluator` and
`build_haiku_evaluator`. So injection v0 exists. What is missing:

- **Verbosity is fixed and lossy.** Only `guidance` is rendered. Rules
  with no `guidance` (e.g. `no-fabricated-attribution`, every role-file
  rule, most lints) render as `- name:` followed by nothing — the agent
  learns the rule's name and zero content. There is no
  `description` fallback and no `canonical_example`.
- **The `Rule` dataclass drops the fields we'd want.** `mop/rules.py`
  loads only `name, detector, parameters, guidance, source_file, lint`.
  `description` and `canonical_example` are parsed out of the YAML and
  discarded, so richer injection requires a loader change first.
- **No staleness story.** Rules are baked into the system prompt at
  SDK-init and never refreshed. (See below — this is currently
  consistent with the evaluator, which is also frozen.)
- **Duplicated rendering.** Three near-identical renderings exist
  (protocol_prompt, two evaluator builders), and a fourth is planned
  (`mop rules` default output).

A meta-note: the one active rule in the corpus,
`doable-work-permission`, carries a `canonical_example` whose text is
*about this exact gap* ("a running MOP host bakes the rule list into
its system prompt at session spawn, so toggles on the site won't reach
an already-running session until it restarts").

## 1. What to inject — token cost against the real corpus

Measured with tiktoken `cl100k_base` on the actual `rules/` corpus
(2026-07-03; Claude's tokenizer will land within ~10%). Levels:

- **L0** — names only
- **L1** — `name: description` one-liners
- **L2** — `name: guidance` one-liners (current rendering, with a
  description fallback added for guidance-less rules)
- **L3** — L2 + indented `canonical_example` block where present
- **L4** — name + description + guidance + canonical_example

| Scenario | entries | L0 | L1 | L2 | L3 | L4 |
|---|---|---|---|---|---|---|
| Active today (`doable-work-permission` only) | 1 | 7 | 21 | 45 | 141 | 161 |
| Realistic full set (core + behavior + transitional files) | 14 | 122 | 305 | 439 | 535 | 679 |
| Entire corpus, everything flipped active | 33 | 271 | 691 | 825 | 921 | 1,122 |

Reference points: the protocol-mechanics preamble in `protocol_prompt`
is **252 tokens**; the full current prompt (mechanics + active rules +
builtin lint) is **336 tokens**. A typical agent system prompt is
already thousands of tokens, and the fragment sits in the cached prefix.

**Conclusion: token cost is not a real constraint.** The gap between
the stingiest useful level (L1, ~300 tokens realistic) and the richest
(L4, ~680 tokens realistic) is under 400 tokens. Even the
everything-active worst case at full verbosity is ~1.1k tokens.

**Recommendation: inject L3 — guidance (falling back to description)
plus `canonical_example` where present.**

- `guidance` is the imperative "how to write compliantly" text — it is
  exactly what a first-try-compliant agent needs, and it's already what
  the evaluator judges against.
- `description` fallback fixes the silent empty-line bug for
  guidance-less rules.
- `canonical_example` earns its tokens: few-shot negative examples are
  the strongest lever for style compliance, and the corpus only carries
  them on rules where a real-world violation was worth preserving
  (currently one). Cost scales with curation, not rule count.
- Do **not** inject `rationale` (`rules/README.md` explicitly marks it
  "never surfaced to the model") and do **not** inject
  `parameters.prompt` — that is the detection question, and handing the
  agent the detector invites writing to the test rather than to the
  guidance. `get_rules` remains the pull path for anything deeper.
- Include lints in the same block (they already are — `protocol_prompt`
  receives the full loaded list including builtin lints). A lint's
  guidance/description is exactly the kind of thing the agent should
  know before writing ("don't paste commit hashes"), even though a lint
  alone never produces a verdict.

## 2. Where and when — and the staleness question

**Where: keep `protocol_prompt` as the single injection point**, baked
into `ClaudeAgentOptions.system_prompt` at SDK-init. System prompt
placement means (a) it's in context every turn, (b) it lands in the
prompt-cache prefix so its per-turn marginal cost is near zero, (c) no
new lifecycle machinery. The rules block should stay at the **end** of
the fragment (it already does) — recency within the fragment, and it
keeps the mechanics text stable for caching if rules churn between
sessions.

**Staleness: accept it for now, deliberately.** The key observation is
that staleness is currently *symmetric*: `MOP.__init__` stores the rule
list once, and `build_*_evaluator(rules=...)` closes over that same
list at construction. A Studio toggle mid-session reaches **neither**
the agent's prompt **nor** the evaluator until the host restarts. The
agent's injected rules and the enforcer's rules cannot diverge, so a
stale prompt never produces a "complied with the prompt, rejected by
the evaluator" contradiction. Since the evaluator is the authority and
the injection is only an optimization (fewer rejection round-trips),
symmetric staleness is safe — the worst case is the pre-injection
status quo.

If/when live rule reload is built (a separate transport problem — the
Studio toggle currently only edits YAML for the *next* session), the
refresh strategy should be:

1. Reload rules into `MOP.rules` and rebuild the evaluator together —
   never one without the other.
2. Notify the agent via a **note attached to the next verdict** (and/or
   the `get_status` payload): `"note: rule set changed this session —
   call get_rules for the current set"`. Verdicts are the one channel
   MOP already owns into the agent's context every turn.
3. Do **not** attempt mid-session system-prompt re-injection. The SDK
   doesn't support editing an active session's system prompt, and even
   where a host could fake it (e.g. injected user-turn preamble), it
   busts the prompt cache and duplicates the rules block in context.

So: session-start injection, symmetric-staleness accepted, verdict-note
as the future refresh signal.

## 3. Format — phrasing for compliance, shared with the evaluator

Two findings shape this:

- The evaluator's rendering (`- name: guidance`) and the agent's
  rendering are already the same shape, maintained in three copies.
- The framing *around* the list should differ by audience: the
  evaluator gets "You are a message gate. Active rules:", while the
  agent needs a reason to treat the list as binding rather than
  ambient style advice.

**Agent-facing framing recommendation** — precede the shared list with
stakes and an instruction to self-check, e.g.:

```
ACTIVE RULES (n)
Every submit_message call is evaluated against these rules by a
separate gate model. Messages that violate them are rewritten or
rejected before the user sees them — a rejection costs you a
justification round-trip. Draft each message to comply on the first
submission; re-read your draft against this list before submitting.

  - doable-work-permission: If you have the tools and context to do
    the work, do it and report back. [...]
    Example violation:
      [...canonical_example...]
```

The load-bearing phrasing choices: name the enforcement mechanism (an
external gate, not an honor system), name the cost of violation (lost
round-trips), and give one concrete behavior ("re-read your draft
against this list") rather than "please follow these rules." Guidance
text in the corpus is already imperative ("do it and report back"),
which is the right voice — inject it verbatim, one rule per bullet,
no prose paragraphs that invite skimming.

**Should agent and evaluator share one rendering function? Yes, for
the list body; no, for the framing.** The rule *content* rendering must
be identical — the agent should comply with exactly the text the
evaluator judges by, and today's triplicated expression is how they
drift apart. The framing headers stay with each caller.

One caveat surfaced while reading `mop/evaluators.py`: the batched
evaluators never use `parameters.prompt` — the per-rule yes/no
detection question that `rules/README.md` describes as "handed to the
Haiku evaluator" is currently dead weight; the evaluator judges from
`guidance` alone. That's a separate latent issue, but it strengthens
the shared-renderer case: right now guidance *is* the entire shared
contract between author, agent, and evaluator, so all three should read
the same rendering of it.

### Refactor sketch (described, not implemented)

New function in `mop/rules.py` (it's a pure function of `list[Rule]`,
and rules.py is the module both hooks.py and evaluators.py already
import from — no new module needed):

```
def render_rules_for_prompt(
    rules: list[Rule],
    *,
    examples: bool = False,   # include canonical_example blocks
) -> str
```

- Renders the bullet list only (no header): one `- name: guidance`
  line per rule, `guidance` falling back to `description`, guidance
  flattened to a single line; when `examples=True`, an indented
  `Example violation:` block follows rules that carry a
  `canonical_example`.
- Callers:
  - `hooks.protocol_prompt(rules)` → header with the compliance framing
    above + `render_rules_for_prompt(rules, examples=True)`.
  - `evaluators.build_deepseek_evaluator` / `build_haiku_evaluator` →
    `"You are a message gate. Active rules:\n" +
    render_rules_for_prompt(rules)` (examples optional there; they
    would also serve the evaluator as few-shot positives for
    detection, at the same negligible cost — reasonable to enable).
  - `mop/cli.py` `mop rules` (planned) → default human output is
    `render_rules_for_prompt(rules, examples=True)`; `--json` stays
    full-object.
- **Prerequisite loader change:** extend the `Rule` dataclass and
  `load_rules()` with `description: str = ""` and
  `canonical_example: str = ""` (both already in the YAML schema and
  currently discarded). Backward-compatible; Studio and eval harness
  unaffected.
- Existing empty-state handling ("(no active rules)") stays with the
  callers, or the function returns a sentinel line for an empty list —
  either is fine, pick one and test it.

## 4. Evidence check — does up-front rule statement beat correction loops?

Short version: direct A/B studies of "rules in system prompt" vs
"discover-by-rejection" are scarce; adjacent evidence consistently
favors stating rules up front *and* keeping the correction loop.

- [Rule-Guided Feedback (arXiv 2503.11336)](https://arxiv.org/pdf/2503.11336)
  is the closest match: explicitly enforcing stated rules improved
  adherence ~26.5% over direct prompting, but also found models
  sometimes fail to integrate correction feedback and repeat the same
  violation — i.e., the rejection loop alone is an unreliable teacher,
  which is the argument for front-loading.
- [Meeseeks (arXiv 2504.21625)](https://arxiv.org/pdf/2504.21625)
  benchmarks iterative self-correction against stated constraints:
  compliance improves over feedback turns but does not converge to
  full compliance — correction loops help and saturate.
- [IFEval (arXiv 2311.07911)](https://llm-stats.com/benchmarks/ifeval)
  and successors ([FollowBench, ComplexBench, AgentIF](https://arxiv.org/html/2505.16944))
  establish that compliance with explicitly stated constraints is high
  for frontier models on small constraint sets and degrades as
  constraint count and composition grow — which supports keeping the
  injected list curated/small-ish rather than flipping the whole
  corpus active.
- Constitutional AI (Bai et al. 2022) is about training, not
  inference-time prompting, but its core mechanic — models can apply
  explicitly stated principles to critique and revise output — is the
  premise both of MOP's evaluator and of expecting the agent to
  self-check against an injected list.
- Guardrails frameworks (NeMo Guardrails, Guardrails AI) uniformly pair
  prompt-time instruction with output-side validation rather than
  relying on either alone — the same architecture MOP lands on with
  injection + evaluator.

Nothing found that argues *against* injection (e.g. that stating rules
degrades compliance); the known failure mode is dilution at large
constraint counts, which the token table shows we're nowhere near.

## 5. Relationship to the `mop rules` CLI

The CLI spec (`docs/superpowers/specs/2026-07-03-mop-cli-core-design.md`)
already positions `mop rules` as the exposure half of this feature: a
non-MOP-native harness shells out and pastes the output into its own
system prompt. **That output and `protocol_prompt`'s rules block should
be the same function** (`render_rules_for_prompt`), for three reasons:

1. **Equivalence of surfaces.** A harness that pastes `mop rules`
   output should give its agent *exactly* what an SDK-native MOP agent
   sees — same fallbacks, same example blocks, same flattening. Two
   renderings means two compliance behaviors to debug.
2. **Evaluator alignment.** Both feed agents whose output will be
   judged by an evaluator rendering the same rules. One function makes
   "the agent was told X, the evaluator enforced X" true by
   construction and auditable in one place.
3. **Spec convergence.** The CLI spec already defines `mop rules`
   default output as "name + guidance per rule" — that *is* the shared
   rendering. Implementing it as a fourth inline copy would be
   creating the drift problem on the day the tool ships.

The only CLI-specific delta is packaging (the spec's `--json` mode
emits full rule objects, which is a different serialization, not a
different prompt rendering) and possibly an `--examples/--no-examples`
flag mirroring the function's parameter.

## Summary of recommendations

- **What:** guidance (fallback: description) + canonical_example for
  every active entry, rules and lints alike; never rationale or
  `parameters.prompt`. (~535 tokens for a realistic 14-entry active
  set; ~921 worst-case whole-corpus.)
- **Where/when:** system prompt via `protocol_prompt` at SDK-init,
  rules block last in the fragment. No mid-session re-injection.
- **Staleness:** accept it; it is symmetric with the evaluator today,
  so prompt and enforcement cannot diverge. When live reload exists,
  reload prompt-snapshot and evaluator together and signal the agent
  via a note on the next verdict.
- **Format:** imperative bullet list identical to the evaluator's,
  wrapped in agent-facing framing that names the gate, the cost of
  rejection, and a self-check instruction.
- **Refactor:** `render_rules_for_prompt(rules, *, examples=False)` in
  `mop/rules.py`; callers: `protocol_prompt`, both evaluator builders,
  and the planned `mop rules` default output. Prerequisite: `Rule`
  gains `description` and `canonical_example` fields in `load_rules()`.
