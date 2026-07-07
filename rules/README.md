# Rules

Each `*.yml` file in this directory is loaded at MOP startup. Every entry
is a **rule** with one of three detectors: `llm` (model-judged, produces a
verdict), `regex` (declarative pattern match), or `script` (external
command). Entries with `active: false` stay visible in the
[Studio UI](../web/) but are filtered out of the loaded set. Order does
not matter.

Deterministic detectors (`regex`, `script`) are **authoritative** — a
match rejects on its own; the LLM judges only `llm` rules. See
[`CONTEXT.md`](../CONTEXT.md) for background.

## Schema

A rule file contains a top-level `rules:` list. Each entry uses one of the
three detector shapes below.

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

`parameters.prompt` is a yes/no question handed to the evaluator model.
The evaluator decides whether the agent's message trips the rule and
folds it into the single best-effort-rewrite verdict.

`guidance` is shown to the agent on a rejected verdict so it knows how
to revise. `description` is for humans.

### Regex detector (declarative pattern match)

```yaml
rules:
  - name: no-commit-hashes
    detector: regex
    description: Don't reference SHAs the user has no use for
    parameters:
      patterns:
        - "\\b[a-f0-9]{7,}\\b"
```

`parameters.patterns` is a list of Python-flavor regexes; the rule fires
if any pattern matches. Fully declarative — no code.

### Script detector (anything beyond regex)

```yaml
rules:
  - name: no-internal-hostnames
    detector: script
    description: Don't leak internal hostnames
    parameters:
      command: .mop/scripts/check_hostnames.sh
```

A `script` detector runs an external command: the message under review is
piped to the command's **stdin**, and the exit code is the verdict —
`0` = pass, non-zero = the rule fires. This is how you express checks
regex can't (word/length counts, entropy scans, schema validation). It is
language-agnostic and process-isolated; the trust model is the same as a
git pre-commit hook (it runs a script you placed in your own repo's
`.mop/`). MOP's own bundled deterministic checks use the same `script`
detector via an in-process fast path (no subprocess).

## Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | kebab-case identifier, unique across the loaded set |
| `detector` | yes | `llm` (model-judged), `regex` (declarative patterns), or `script` (external command) |
| `parameters` | yes | shape depends on detector (above): `prompt` for `llm`, `patterns` for `regex`, `command` for `script` |
| `active` | no | `true` (default) or `false`. Inactive entries are visible in Studio but never reach the evaluator |
| `description` | no | one-line human summary |
| `guidance` | no | shown to the agent on rejection; tells it how to revise |
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
4. For `llm` rules: if the evaluator misjudges, sharpen
   `parameters.prompt`. Add explicit exceptions for the false positives
   you saw. For `regex`/`script`: the patterns or the command are the
   only lever.

## Local project rules (`.mop/`)

In addition to the packaged built-in rules (`mop/rules_builtin/`) and the
repository `rules/` directory, MOP discovers a **project-local `.mop/`**
directory by walking up from the current working directory, stopping at
the first ancestor containing `.git` (the repo root). If found, `.mop/`
acts as a third layer merged over the built-ins — same YAML schema,
same merge semantics.

Merge rules (see `mop.rules.merge_rules`):

| Scenario | Result |
|---|---|
| Local rule name matches a built-in | Local replaces built-in entirely. |
| Local rule name is new | Added to the resolved set. |
| Local `active: false` on a built-in name | Built-in is silenced (dropped from the set). |

**Discovery bypass**: pass `--rules-dir PATH` or `--rules-file PATH` to
`mop check` or `mop rules` to skip walk-up discovery entirely and use an
explicit rules source.

**Active built-ins by default** (shipped in `mop/rules_builtin/`):

| Rule | Detector | Active |
|---|---|---|
| `no-fabricated-attribution` | llm | yes |
| `links-for-references` | llm | yes |
| `format-score-too-high` | deterministic (lint) | yes (always active) |

Rules with `active: false` in the packaged files (e.g.
`no-completed-without-findings-pings`, `acknowledgment-without-action`,
`verify-before-asserting`, `no-empty-future-commitments`) are visible
in Studio but do not reach the evaluator unless a local `.mop/` entry
re-enables them.

## Personal overlays

`personal.yml.example` is a template for per-user style entries that are
too personal to ship in the default set (no em-dashes, no startup-pitch
cadence, etc.). Copy it to `personal.yml` in your deployment and edit.

For ongoing rule-mining notes — patterns observed in feedback corpora,
candidate rules not yet stable — see
[`../docs/rule-mining-notes.md`](../docs/rule-mining-notes.md).
