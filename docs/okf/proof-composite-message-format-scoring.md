---
type: proof
title: Format Score: quadratic penalties for message shape quality
capability: Composite message format scoring
kind: designed
tags: [Python, algorithmic design, communication quality metrics]
created: 2026-05-03
confidence: 0.9
sources: [590764a2]
---
Designed a composite penalty function (format_score) for outbound message shape with three independent terms: wrap_penalty penalizes source lines that wrap, height_penalty climbs linearly past half a screen budget and quadratically past the full budget, structure_penalty charges one point per plain-prose line beyond a free set. The calibration was deliberate but unverified—the implementation prioritizes ordering of quality over absolute values. This metric became the basis for a built-in lint that fires when the score exceeds a threshold, integrating into the rule evaluation pipeline.
