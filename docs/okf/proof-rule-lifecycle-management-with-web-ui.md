---
type: proof
title: Studio: active/inactive toggle for rules with per-rule save
capability: Rule lifecycle management with web UI
kind: built
tags: [FastAPI, YAML, UX design]
created: 2026-05-11
confidence: 0.95
sources: [9ef0103d]
---
Designed and built a rule management UI (MOP Studio) that adds an active/inactive notion to the rule loader. Each rule entry carries an optional boolean flag; the loader filters to only active rules unless the UI requests all. The Studio displays inactive rules with a strike-through name and faded row, and provides a per-rule toggle switch wired to a dedicated API endpoint. The save-rule path preserves the active flag so editing an inactive rule doesn't accidentally re-activate it. This enables gradual rollout of new rules without file-level gymnastics.
