# Merge semantics for style composition

This is the part Vale actually handles poorly, but for MOP it matters
more because rules are evaluated by an LLM and can produce verdicts
that conflict.

## Problem

If core-voice says "no code identifiers in prose" and role-senior-dev
says nothing about it, no conflict. But if role-product-manager says
"skip implementation jargon" which overlaps with "no code identifiers,"
the same text could be flagged by two rules. The evaluator sees both
rules in the prompt and may double-count violations.

Or worse: personal-style has a rule that says "actually I want code
identifiers, they help me reason about the system." That rule is a
direct override of a core rule. How does MOP know?

## Proposal: style layer precedence

When styles are composed, each rule carries a "layer" based on which
style it came from. MOP resolves conflicts before the evaluator sees
them:

Layer 0: core voice (lowest priority — universal defaults)
Layer 1: transitional (temporary patches that should eventually go away)
Layer 2: role (communication profile for the audience)
Layer 3: overlay (task-specific modes like one-thing-at-a-time)
Layer 4: personal (highest priority — user preference wins)

A personal-style rule with the same name as a core-voice rule replaces
it entirely. A role rule and a core rule with different names both
survive. Layer 4 rules can set active: false to silence a lower-layer
rule by name.

## Not proposing this for now

This is speculative. Vale has no equivalent and MOP has not hit this
problem yet. The reason to think about it is that once styles exist,
someone will try to compose core-voice with personal-style and wonder
why their personal no-cheerleading override isnt having the expected
effect. Better to have a design sketch ready than to invent merge
semantics under pressure.
