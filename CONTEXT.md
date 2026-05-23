# Model Output Protocol (MOP)

An output gate that sits between an LLM agent and its user, enforcing
communication discipline — rejecting, rewriting, or accepting messages
before they reach the human. Counterpart to **HOP** (Human Output
Protocol) on the input side.

MOP is not a competing protocol to MCP (Model Context Protocol). The
name is a playful inversion; MOP *may use* MCP to operate (e.g. via
MCP tool definitions in a host harness).

## Language

### Lint
A deterministic, surface-level check on message text (regex patterns or
word count). Lints are advisory only — they feed hints to the evaluator
but never produce a verdict on their own. Named after the `lint` family
of static analyzers that flag patterns without executing or interpreting
the input.
_Avoid_: deterministic rule, rule

### Rule
A directive evaluated by the LLM (Haiku, in the reference adapter) that
produces a verdict: accept, rewrite, or reject. Rules carry a prompt
that asks the evaluator a yes/no question about the message.
_Avoid_: lint (when you mean rule)

### Verdict
The outcome of evaluating a message against the active rules. One of:
- **Accepted** — LLM decided the message passes all rules
- **Rewritten** — LLM found style issues and corrected the text
- **Rejected** — LLM found substantive violations; the agent must justify
- **AcceptedFailedOpen** — MOP's own escape hatch after repeated justification failures; delivers the original message with a warning system note. This is **not** an evaluator output — only MOP itself can produce this.

### Hint
Advisory context fed to the evaluator alongside the message. Currently
produced by lints (regex/word_count hits) and formatted as a flat prose
line in the evaluator prompt. Future: structured hint data so MOP can
optionally bypass the LLM on clear-cut cases.

### Evaluator
The LLM callable that decides accept/rewrite/reject. The reference
implementation wraps Anthropic Haiku via pydantic-ai. MOP is evaluator-
agnostic — the host injects any callable matching the `Evaluator`
signature.

### Failed-open
When the justification budget is exhausted (default: 4 attempts), MOP
delivers the original message with a system note explaining it was
forced through despite violations. The budget is then reset.

### Justification loop
After a Rejected verdict, the agent may call `submit_justification` to
argue why the message should still be delivered. Each attempt re-runs
the evaluator with the justification appended. Repeated failures trigger
failed-open.

## Example dialogue

**Dev:** "I just added a new LLM rule to catch fabricated quotes. How do I
test it?"

**Domain expert:** "Add a counterexample YAML in `evals/counterexamples/voice/`
with the text you want it to catch, list the rule name in
`expected_violations`, then run `python harness.py --llm --rule
no-fabricated-attribution`. The lints (regex checks) run every time; the
LLM rules run only with `--llm`."

**Dev:** "What if my regex lint matches but the message is fine? I don't
want it to reject."

**Domain expert:** "It won't — lints are advisory. They only feed hints to
the evaluator. Only the rule produces a verdict. If the evaluator keeps
getting it wrong, sharpen the rule's `parameters.prompt`."

## Flagged ambiguities

None.
