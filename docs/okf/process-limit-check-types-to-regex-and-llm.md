---
type: process
title: Limit check types to regex and llm
kind: decision
tags: [rule schema design]
confidence: 0.9
---
Word count checks are handled internally via regex; only `regex` and `llm` types are needed. This reduces cognitive overhead and keeps the schema minimal.
