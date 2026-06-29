---
type: process
title: Active state lives outside rule files
kind: decision
tags: [architecture, rule management]
confidence: 0.85
---
Whether a rule is active should be managed in a separate configuration or database, not within the rule definition itself. This allows sharing rule definitions while varying activation by profile or session.
