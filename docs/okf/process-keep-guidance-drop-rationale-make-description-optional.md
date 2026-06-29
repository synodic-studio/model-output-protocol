---
type: process
title: Keep `guidance`, drop `rationale`, make `description` optional
kind: decision
tags: [rule schema design]
confidence: 0.8
---
`guidance` provides functional feedback to the LLM on how to fix violations. `rationale` is redundant (use comments). `description` is optional since rule names can be self-explanatory.
