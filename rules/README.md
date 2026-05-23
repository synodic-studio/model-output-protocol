# Rules

Each `*.yml` file in this directory is loaded at MOP startup. Every entry
contributes either a **rule** (LLM-evaluated, produces a verdict) or a
**lint** (deterministic pattern check, feeds advisory hints to the
evaluator). Entries with `active: false` stay visible in the
[Studio UI](../web/) but are filtered out of the loaded set. Order does
not matter.

See [`CONTEXT.md`](../CONTEXT.md) for the definitions of **rule** vs **lint**.

## Schema

A rule file contains a top-level `rules:` list. Each entry is one of two
shapes.

### LLM Rule

```yaml
rules:
  - name: doable-work-permission
    detector: llm
    description: Don't ask permission for work the agent has the tools to do
    parameters:
      prompt: |
        Is this message asking the user permission to do work the agent
        has the tools and context to do itself? Examples: "Want me to
        search the repo?", "Should I read the file?", "Do you want me to
        fix it?"

        EXCEPTION: genuine ambiguity, irreversible actions (deploy to
        production, delete data), or when the user explicitly opted into
        confirmation.
    guidance: |
      If you have the tools and context to do the work, do it and report
      back. If there's genuine ambiguity, state the ambiguity and your
      proposed resolution — don't ask permission.
```

`parameters.prompt` is a yes/no question handed to the Haiku evaluator.
Haiku decides whether the agent's message trips the rule and returns one
of `accept`, `rewrite`, or `reject`.

`guidance` is shown to the agent on a rejected verdict so it knows how
to revise. `description` is for humans.

### Lint (deterministic check)

```yaml
rules:
  - name: no-commit-hashes
    lint: true
    detector: deterministic
    description: Don't reference SHAs the user has no use for
    parameters:
      type: regex
      patterns:
        - "\\b[a-f0-9]{7,}\\b"
```

Lints are **non-authoritative hints**. If a pattern matches, the lint
name is appended to the hints list passed to the LLM evaluator as
context — the LLM still produces the verdict. A pattern match never on
its own rejects a message. This keeps regexes from misfiring on
legitimate uses (a hex color, a hash in a code block) while still
flagging suspicious shapes to the model.

Lints are distinguished from rules by `lint: true` (or in the future by
their own `lints:` top-level key). Lints without `lint: true` are
treated as legacy deterministic rules and load the same way.

Supported `parameters.type` values:

| Type | Extra fields |
| --- | --- |
| `regex` | `patterns` — list of Python-flavor regexes |
| `word_count` | `max` — integer ceiling on `len(text.split())` |

## Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | kebab-case identifier, unique across the loaded set |
| `detector` | yes | `llm` for rules, `deterministic` for lints |
| `lint` | no | `true` marks this entry as a lint (deterministic, advisory). Omit for LLM rules. |
| `parameters` | yes | shape depends on detector (above) |
| `active` | no | `true` (default) or `false`. Inactive entries are visible in Studio but never reach the evaluator |
| `description` | no | one-line human summary |
| `guidance` | no | rules: shown to the agent on rejection. lints: feeds into evaluator prompt as advisory context |
| `rationale` | no | private commentary, never surfaced to the model |
| `canonical_example` | no | the prototypical message this entry should catch; used by Studio |

Legacy fields like `severity` and `on_violation` are silently ignored.
Older rule files load fine.

## Authoring loop

1. Add the rule file (or append to an existing one).
2. Restart any host using MOP so the new rule loads.
3. Test it against a real or synthetic offending message in the
   [MOP Studio](../web/) playground. Run it against the
   [evals](../evals/) corpus to confirm it doesn't fire on clean text.
4. For rules: if Haiku misjudges, sharpen `parameters.prompt`. Add
   explicit exceptions for the false positives you saw.
   For lints: the regex patterns are the only lever.

## Personal overlays

`personal.yml.example` is a template for per-user style entries that are
too personal to ship in the default set (no em-dashes, no startup-pitch
cadence, etc.). Copy it to `personal.yml` in your deployment and edit.

For ongoing rule-mining notes — patterns observed in feedback corpora,
candidate rules not yet stable — see
[`../docs/rule-mining-notes.md`](../docs/rule-mining-notes.md).
