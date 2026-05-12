# MOP Evals

Corpus-driven evaluation for MOP rules. Each rule has a population of
example messages it should (or should not) flag. The harness runs every
rule against every example and reports mismatches.

## Structure

```
evals/
├── README.md              # this file
├── SCHEMA.md              # counterexample YAML schema
├── harness.py             # rule-vs-corpus runner
└── counterexamples/
    ├── voice/             # voice-rule examples
    └── behavior/          # behavior-rule examples
```

## What's a counterexample?

A counterexample is a message that *should* trigger one or more rules,
plus the list of rules expected to fire. Positive controls (messages
that should NOT trigger anything) are also valuable — they catch
false positives. Both are stored in the same corpus, distinguished by
the `expected_violations` field (empty list = clean message).

## Privacy

Real-mined examples are sanitized before commit:
- Personal names → generic placeholders (`Alex`, `the user`)
- Emails → `user@example.com`
- Profanity → toned down or `[***]`
- Internal URLs/repos → generic equivalents
- API keys / secrets → `<redacted>`

When in doubt, synthesize. Synthetic examples are equally valid for
rule-fire checks.

## Running

```bash
cd evals
python harness.py                    # all rules vs all counterexamples
python harness.py --rule no-code-identifiers-in-prose   # one rule
python harness.py --verbose          # show full text of mismatches
```

The harness only evaluates **deterministic** rules at present. LLM-based
rules are listed but skipped (they require Haiku in the loop). A
`--with-llm` flag is reserved for when the filter pipeline lands.

## Adding new examples

1. Pick a category (`voice/` or `behavior/`).
2. Drop a YAML file in that directory matching the schema in
   [SCHEMA.md](SCHEMA.md).
3. Run `python harness.py` to confirm the example is correctly
   classified.

## Adding new rules

When you add a rule to `rules/*.yml`, also add at least:
- One counterexample that should trigger it (positive case)
- One clean example that should NOT trigger it (negative case, control)

Rules without corpus coverage are not landed.
