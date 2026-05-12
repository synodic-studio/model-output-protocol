# Rules

Each `*.yml` file in this directory is loaded at MOP startup. Every rule inside contributes one entry to the rule set the LLM evaluator considers on each `submit_message` call — unless the rule carries `active: false`, in which case it stays visible in the [Studio UI](../web/) but is filtered out of the loaded set. Rule order does not matter.

## Schema

A rule file contains a top-level `rules:` list. Each rule is one of two detector shapes.

### LLM rule

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

The `parameters.prompt` is handed to the Haiku evaluator at runtime. Phrase it as a yes/no question — Haiku decides whether the agent's message trips the rule and returns one of `accept`, `rewrite`, or `reject`.

`guidance` is shown back to the agent on a rejected verdict so it knows how to revise. `description` is for humans.

### Deterministic rule

```yaml
rules:
  - name: no-commit-hashes
    detector: deterministic
    description: Don't reference SHAs the user has no use for
    parameters:
      type: regex
      patterns:
        - "\\b[a-f0-9]{7,}\\b"
```

Deterministic rules are non-authoritative hints. If a pattern matches, the rule name is appended to the `regex_hints` list passed to the LLM evaluator as context — the LLM still produces the verdict. A pattern match never on its own rejects a message. This keeps regexes from misfiring on legitimate uses (a hex color, a hash in a code block) while still flagging suspicious shapes to the model.

Supported `parameters.type` values:

| Type | Extra fields |
| --- | --- |
| `regex` | `patterns` — list of Python-flavor regexes |
| `word_count` | `max` — integer ceiling on `len(text.split())` |

## Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | kebab-case identifier, must be unique across the loaded set |
| `detector` | yes | `llm` or `deterministic` |
| `parameters` | yes | shape depends on detector (above) |
| `active` | no | `true` (default) or `false`. Inactive rules are visible in Studio but never reach the evaluator |
| `description` | no | one-line human summary |
| `guidance` | no | shown to the agent on rejection; explains how to revise |
| `rationale` | no | private commentary, never surfaced to the model |
| `canonical_example` | no | the prototypical message this rule should catch; used by Studio's "Test against canonical example" button |

Legacy fields like `severity` and `on_violation` are silently ignored. Older rule files load fine.

## Authoring loop

1. Add the rule file (or append to an existing one).
2. Restart any host using MOP so the new rule loads.
3. Test it against a real or synthetic offending message in the [MOP Studio](../web/) playground. Click "Test against canonical example" to confirm the rule fires on the case you wrote it for, and run the [evals](../evals/) corpus to confirm it doesn't fire on clean text.
4. If Haiku misjudges, the fix is almost always to sharpen the `parameters.prompt`. Add explicit exceptions for the false positives you saw.

## Personal overlays

`personal.yml.example` is a template for per-user style rules that are too personal to ship in the default set (no em-dashes, no startup-pitch cadence, etc.). Copy it to `personal.yml` in your deployment and edit. Personal rule files load the same way as core rules.

For ongoing rule-mining notes — patterns observed in feedback corpora, candidate rules not yet stable — see [`../docs/rule-mining-notes.md`](../docs/rule-mining-notes.md).
