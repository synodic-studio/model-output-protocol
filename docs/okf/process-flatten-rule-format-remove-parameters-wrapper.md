---
type: process
title: Flatten rule format, remove `parameters` wrapper
kind: decision
tags: [YAML schema design, rule management]
confidence: 0.95
---
Removed the `parameters` wrapper and `active` field from rule definitions. This simplifies the schema, moves activation state externally, and aligns with Vale's approach of keeping rule body type-specific.
