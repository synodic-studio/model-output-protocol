# Research: rule-composition model for MOP

Status: research note (patchbay). Evaluates the typed 5-layer precedence
sketch in `vale-style-example-4-merge-semantics.md` against a generic
ordered-list-of-rulesets model, grounded in how Vale actually works and
in the CLI spec's already-approved two-layer merge
(`docs/superpowers/specs/2026-07-03-mop-cli-core-design.md`, "Rule
discovery").

Owner feedback this note honors: skepticism about the Vale
add-rules-to-profiles model, and a hard requirement that "roles" not be
a special kind of rule set — if they exist at all, they are ordinary
rule sets that might represent a context, a user, or something else.

---

## 1. What Vale actually does

Sources:
- [.vale.ini reference](https://vale.sh/docs/vale-ini)
- [Styles](https://vale.sh/docs/styles)
- [Packages](https://vale.sh/docs/keys/packages)
- [Vocab](https://vale.sh/docs/keys/vocab)

### Config shape

`.vale.ini` has three tiers: core settings (`StylesPath`, `Packages`,
`Vocab`, `MinAlertLevel`), a `[formats]` extension-mapping section, and
per-glob format sections (`[*]`, `[*.md]`) that carry `BasedOnStyles`
plus ignores. Precedence within the config: "Settings defined under a
more specific section will override those in `[*]`." Multi-valued keys
(`BasedOnStyles`) merge across sources; single-valued keys
(`MinAlertLevel`) are overridden.

### Rules are namespaced, not merged

The detail most secondhand descriptions get wrong: Vale rule identity is
`Style.Rule` (e.g. `Microsoft.Passive`, `Vale.Spelling`). Two styles
that both define a rule named `Passive` do **not** collide — they are
two distinct rules, and both run. Vale never has to answer "which
style's version of this rule wins" because there is no shared name
space to fight over. `BasedOnStyles = Microsoft, Google` just activates
both bags of independent checks.

Conflict resolution is therefore pushed entirely to the **user's
config**, per rule, by fully qualified name:

```ini
BasedOnStyles = Microsoft, HPC
HPC.Rule1 = NO        ; disable
HPC.Rule2 = error     ; re-severity
```

This is Vale's real override mechanism — flat, name-addressed,
user-side. It is the direct analog of MOP's `active: false`
silencing-by-name in the CLI spec.

### Where ordering *does* matter in Vale: Packages

Packages (zip bundles of styles and/or config, installed by
`vale sync`) are the one place Vale has genuine composition semantics,
and they are exactly a generic ordered list:

> "In the case of conflicting configuration, the order in which
> packages are loaded is important" — later packages override earlier
> ones, and "local configuration will override any conflicting
> package."

No types, no layers: left-to-right, later wins, local last. This is the
same shape as the CLI spec's two-layer merge (built-in base, local
override) generalized to N entries.

### Vocab: the user/creator split

Vocabularies (`accept.txt` / `reject.txt`) are terminology lists kept
*outside* any style; accepted terms are "added to every exception list
in all styles listed in `BasedOnStyles`." The stated rationale: "ignore
files are for style *creators* while vocabularies are for style
*users*" — users customize third-party styles without forking them.
MOP's `personal.yml` overlay is the same instinct (user-side patch
plane over shipped rules), just rule-shaped instead of term-shaped.

### What Vale gets right for MOP

1. **Generic ordered composition, later wins, local last** (Packages).
   No typed layers exist anywhere in Vale.
2. **Flat name-addressed overrides** (`Style.Rule = NO`) as the entire
   user-side control plane — no precedence algebra to learn.
3. **A deliberate creator/user split** (Vocab, per-rule overrides) so
   customization never requires forking a shipped set.
4. **Styles are just directories of rule files** — organizational
   units, not semantic types. "Microsoft" and "write-good" sit in the
   same list as peers; nothing in the engine knows one is a vendor
   style guide and the other a prose linter.

### Where Vale diverges fundamentally from MOP

Vale runs every rule as an **independent deterministic check** emitting
its own alerts. Overlap between rules is harmless: two rules flagging
the same span just produce two alerts in an editor gutter, and severity
is per-alert. There is no combined verdict, so there is nothing for
overlapping rules to confuse.

MOP inverts this: all rules and lint hints are batched into **one
evaluator prompt producing one verdict per message**
(`mop/evaluators.py`: "single prompt and asks the LLM for one
structured verdict per submission"). Composition problems therefore
surface as *prompt-level* pathologies — double-counting one fault as
two violations, or contradictory guidance pulling the rewrite in two
directions — rather than as mechanical alert merges. Vale's namespacing
trick (let both rules exist and fire) is exactly the wrong answer for
MOP: two live near-duplicate rules make the evaluator prompt worse, not
just the alert list longer. MOP needs same-name **replacement** at
merge time, which the CLI spec already specifies. Vale's per-rule
`suggestion/warning/error` levels also have no MOP analog; MOP's
verdict is message-level (`accept/rewrite/reject`), which turns out to
be an advantage for overlap tolerance (§3).

---

## 2. Gap analysis: typed 5 layers vs. generic ordered sets

The example-4 sketch: core=0, transitional=1, role=2, overlay=3,
personal=4; same-name replaces across layers, higher layer wins, layer-4
`active: false` silences lower rules by name.

The generic alternative: an ordered list of rule sets; fold left with
the CLI spec's pairwise merge (same-name replaces, else union,
`active: false` silences by name). The 5-layer model is trivially
representable as the ordered list
`[core, transitional, role-x, overlay-y, personal]` — so the generic
model is a strict superset in expressive power. The question is only
what the *types* buy on top.

### What typing buys

- **Self-documentation.** `transitional` announces "this should expire
  when models improve." But expiry is a convention either way — no
  layer number deletes a file. A filename prefix (`transitional-*.yml`,
  which the repo already uses), a comment, or an optional `tags:` field
  carries the same signal without engine semantics.
- **Fixed precedence regardless of config mistakes.** A user can't
  accidentally put personal below core. In the generic model,
  misordering is possible — but it is written once per deployment, and
  `mop rules` (already specced) shows the resolved set, making the
  mistake visible in one command. Provenance output (§4) makes it
  self-diagnosing.
- **Slot semantics** — "there is one role at a time." But that's a
  session-assembly concern: whoever builds the list picks which sets to
  include. The generic model handles "swap the role" as "swap one list
  entry," with no engine knowledge of what a role is. This is precisely
  the owner's requirement: a role, if it exists, is an ordinary set
  someone chose to include, possibly representing a context or a user.
- **Tooling grouping** (Studio could group by layer). Achievable with
  tags or with provenance (which file/set a rule came from), which the
  merge has to track anyway.

Net: the typed model buys taxonomy, not mechanism. And the taxonomy is
the part the owner has explicitly rejected — five blessed categories
hardcode today's guess about how rule sets will be used ("role" as
persona) into engine semantics. If tomorrow a set represents a channel
(Telegram vs. terminal), a project, or a model era, it has to be
shoehorned into one of five slots or the schema grows a sixth layer.
The generic list absorbs all of these as "another entry."

### Where generic ordering could break down (honest look)

- **Ties within a "layer."** Two personal-ish sets both wanting to win.
  But the typed model has the same problem *within* layer 4 and the
  sketch never specifies intra-layer tie-breaking; the generic list at
  least forces a total order, so the answer is always defined.
- **Third-party sets that assume a position.** A shipped set written
  expecting to be "base" could be placed last by a confused user and
  clobber their overrides. Vale's answer (packages doc): document the
  convention — base things first, local/personal last — and let the
  consumer own the order. Acceptable at MOP's scale.
- **No engine-enforced expiry for transitional rules.** True, but the
  typed model doesn't enforce expiry either; layer 1 is just a number.
  If expiry ever needs enforcement, an optional per-rule
  `expires:`/`review_by:` field is the honest mechanism, orthogonal to
  composition.
- **Loss of "layer 4 may silence, layer 2 may not" policy.** The typed
  sketch implies capability differences per layer. The generic model
  gives every later set the same powers over every earlier set. This is
  a real semantic difference — and the simpler rule ("later wins,
  period") is easier to predict and matches Vale, CSS user-origin
  ordering, and the CLI spec. No current MOP scenario needs
  capability-restricted layers.

Conclusion: nothing found that justifies typed layers. The one
mechanically real feature of the sketch — same-name replacement +
`active: false` silencing — survives intact in the generic model and is
already approved in the CLI spec.

---

## 3. Composition × the holistic evaluator

Merge order of operations: discovery → fold-left merge (dedupe by name,
later wins) → **then** prompt assembly. So same-name conflicts never
reach the evaluator; only the resolved set does. That handles the
"personal override isn't taking effect" scenario from example-4
completely.

What merge cannot handle: **overlapping, differently named rules**.
Real example from the repo's own sketches: core's
`no-code-identifiers-in-prose` vs. a PM set's
`skip-implementation-jargon`; or core terseness vs. a junior-dev set's
add-rationale-and-next-steps. Two failure shapes in the one-prompt
model:

1. **Double-counting:** one fault listed as two violations. Mostly
   benign — MOP's verdict is message-level, not a per-rule score sum,
   so listing two overlapping names under one `reject` doesn't reject
   harder. It slightly pollutes guidance shown to the agent, nothing
   more.
2. **Contradictory guidance:** genuinely harmful. "Be terse" and "add
   rationale and next steps" in the same prompt yields incoherent
   rewrite pressure and verdict flapping.

How comparable products handle it: **they don't compose free-text
policies into one prompt at all.** NeMo Guardrails runs rails as
independent (optionally parallel) flows, with per-policy LLM-judge
prompts (`llm_judge_check_single_policy_compliance` — note *single
policy*), combined mechanically (any block → block)
([rail types](https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/rail-types),
[parallel rails](https://docs.nvidia.com/nemo/microservices/latest/guardrails/tutorials/parallel-rails.html)).
OpenAI's moderation endpoint is a fixed category taxonomy with
independent per-category scores and thresholds. Both sidestep overlap
by paying N calls (or a fixed taxonomy) and merging verdicts
mechanically — the design MOP deliberately rejected for cost, latency,
and rewrite coherence. So there is no off-the-shelf answer to import;
MOP's overlap problem is a consequence of its (justified) one-prompt
choice.

The important insight: **no layer scheme fixes contradiction either.**
Typed precedence only resolves *same-name* conflicts, and so does
generic ordering. Contradiction between differently named rules is a
content problem, and the fixes are editorial:

- **Canonical names for contested dimensions.** If a set wants
  different verbosity/tone/detail than core, it should *override by
  reusing the canonical rule name* (e.g. both define
  `message-verbosity`), so replacement — not accumulation — happens.
  This is a documented authoring convention, enforced socially and by
  review, exactly like Vale style guides converging on rule vocabulary.
- **Explicit silencing.** A set that fundamentally disagrees with an
  earlier rule ships `active: false` for it by name (the
  junior-dev set silences core terseness rather than arguing with it in
  the prompt).
- **Eval-corpus regression.** The repo already has
  `evals/counterexamples/`; composed profiles should be run against it
  so double-fires and verdict flapping show up as eval failures, not
  production confusion.
- **Provenance-aware `mop rules` output** to make near-duplicates
  visible to the human assembling a config.

Whether residual mild overlap actually degrades Haiku's verdict is an
empirical question the eval harness can answer cheaply; nothing about
the composition model needs to block on it.

---

## 4. Minimal composition config (strict superset of the CLI spec)

```yaml
# .mop/config.yml — optional. Absent file = exactly the CLI spec's
# behavior: builtin rules + discovered .mop/*.yml as a two-entry list.

rulesets:
  - builtin                 # the packaged base set (may be omitted;
                            #   implicitly first unless listed elsewhere)
  - ./rules/team.yml        # path to a rule file...
  - ./rules/telegram/       # ...or a directory of *.yml
  - ~/.mop/personal.yml     # later = higher precedence
```

Semantics (all already specced for two layers; generalized by folding):

- **Ordered list, fold left** with the CLI spec's merge at each step:
  same-name entry replaces the earlier one wholesale; everything else
  unions; `active: false` in a later set silences an earlier rule by
  name. Builtin lints dedupe by name as a free consequence, exactly as
  the spec notes.
- **Entries are names or paths — no types.** A "role," a channel
  profile, a transitional patch set, and a personal overlay are all
  just entries. What an entry *means* lives in its filename and
  `description`s, not in the engine.
- **Degenerate cases:**
  - No config file, no `.mop/*.yml` → `[builtin]`.
  - No config file, discovered `.mop/*.yml` → `[builtin, local]` — the
    CLI spec's exact two-layer merge, byte-for-byte compatible.
  - `--rules-dir`/`--rules-file` → `[builtin, explicit-path]`,
    unchanged from the spec.
- **Provenance:** the merge records which ruleset each surviving rule
  came from and which entries were replaced/silenced; `mop rules`
  prints it (and a future `--explain` can show the losing versions).
  This replaces the typed model's self-documentation value.
- **Deliberately deferred** (possible later sugar, not needed now):
  per-entry inline disables (Vale's `Style.Rule = NO` analog — already
  expressible today as a one-rule `active: false` overlay file), a
  session-time `--ruleset PATH` append flag, remote/package sources,
  optional `tags:`/`expires:` rule metadata for transitional hygiene.

Nothing being built for the CLI is thrown away: `rulesets:` only
generalizes the already-implemented pairwise merge from exactly two
layers to N, and the discovery path remains the no-config default.

---

## Recommendation

**Adopt the generic ordered list of rule sets; drop the typed 5-layer
model.** Grounds:

1. It is a strict superset: every 5-layer configuration is expressible
   as an ordered list, while the reverse locks future set-kinds into
   five blessed slots.
2. The only mechanically real parts of the typed sketch — same-name
   replacement and silence-by-name — are type-free and already approved
   in the CLI spec's two-layer merge. Types add taxonomy, not
   mechanism.
3. It matches the owner's constraint exactly: roles are ordinary sets.
4. It matches prior art: Vale's Packages ("later overrides earlier,
   local overrides all") is the identical model, and Vale itself has no
   typed layers anywhere.
5. The problem types were supposed to solve — evaluator confusion from
   overlap — isn't solved by precedence of *any* kind for
   differently-named rules. It is addressed editorially (canonical
   names for contested dimensions, explicit silencing, eval-corpus
   regression, provenance output), all of which work identically under
   generic ordering.

The honest caveat: generic ordering trusts the config author to put
personal/local sets last and offers no capability firewall between
sets. At MOP's scale (single-deployment configs, `mop rules` as the
inspection tool) this is the right trade.
