# Counterexample Schema

Each counterexample is a YAML file under `counterexamples/voice/` or
`counterexamples/behavior/`. One example per file. Filename is the `id`
field with `.yml` extension.

## Fields

```yaml
id: <slug>                    # required, unique, kebab-case
source: synthetic | real-sanitized
labels: [<tag>, ...]          # freeform, helps filter (e.g. mobile, code-soup)
text: |                       # required, the offending message verbatim
  <message body>
expected_violations:          # rules that SHOULD fire (empty list = clean)
  - <rule-name>
expected_clean: []            # rules that should NOT fire (sanity controls)
rationale: |                  # required, why this is in the corpus
  <one paragraph>
ideal_rewrite: |              # optional, what the message should have been
  <preferred form>
notes: |                      # optional
  <anything else>
```

## Example

```yaml
id: nfc-payload-soup
source: real-sanitized
labels: [code-soup, mobile, technical-detail]
text: |
  NFC payload notes (read from repo):
  • App's CheckInURLAction.decide accepts the universal link
    https://link.example.com/checkin with optional ?nfc=<code> and
    ?status=<rawValue>. Empty query → marks .present.
  • Custom scheme example://checkin works as fallback.
  • Single shared promo code is fine — physical possession of the tag
    IS the auth (per the source comment in CheckInURLAction.swift).
expected_violations:
  - no-code-identifiers-in-prose
expected_clean:
  - no-completed-without-findings-pings
rationale: |
  Reads as a code spec dump. "Universal link" alone carries the meaning
  for the user; CheckInURLAction.decide and CheckInURLAction.swift are
  jargon tax for a non-debugging context.
ideal_rewrite: |
  NFC tag check-in:
  • The app accepts a universal link (https://link.example.com/checkin),
    with optional nfc-code and status fields. Empty query = "present".
  • Custom URL scheme works as a fallback.
  • One shared promo code is fine — possession of the tag is the auth.
```

## Conventions

- Keep `text` verbatim — match real drift, including weird whitespace.
- `expected_violations` is the AND-set: every listed rule must fire.
- `expected_clean` is sanity-check coverage: useful when an example
  could plausibly look like another rule's target.
- An empty `expected_violations` list means the message is clean.
  Useful as false-positive ballast.
