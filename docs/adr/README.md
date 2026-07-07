# Architecture Decision Records

Numbered, immutable-ish records of the decisions that shaped MOP and *why*.
An ADR captures the context, the decision, and its consequences at a point in
time. Supersede rather than silently rewrite: if a decision changes, add a new
ADR and mark the old one `Superseded by ADR-XXXX`.

Longer design/grilling narratives live in `../superpowers/specs/`; ADRs are the
short canonical "what we decided and why" that outlives any one spec.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-detector-taxonomy-and-composition.md) | Detector taxonomy & rule composition | Accepted |
| [0002](0002-deterministic-authority-and-verdict-shape.md) | Deterministic authority & best-effort-rewrite verdict | Accepted |
| [0003](0003-evaluator-model-tiers-and-structured-output.md) | Evaluator model tiers & structured-output handling | Accepted |
| [0004](0004-local-ondevice-evaluator-findings.md) | Local / on-device evaluator findings | Informational |
| [0005](0005-integration-shapes-and-streaming.md) | Integration shapes & the streaming constraint | Accepted |
