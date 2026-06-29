---
type: proof
title: JsonlAuditor: daily-rotated audit log of every verdict
capability: Flight recorder for LLM filtering decisions
kind: built
tags: [Python, Protocol classes, logging]
created: 2026-05-11
confidence: 0.95
sources: [ea81086e]
---
Built a flight recorder that makes the MOP runtime transparent: after every evaluation verdict, an Auditor protocol implementation records the original text, active rule names, verdict payload, attempt counter, and justification. The default JsonlAuditor writes one JSON line per verdict to daily-rotated files under a host-specified log directory. By snapshotting rule names at evaluation time, the audit log provides a stable record that survives rule set changes and enables post-hoc analysis of agent behavior over time.
