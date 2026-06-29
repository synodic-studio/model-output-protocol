---
type: proof
title: LLM evaluator harness supporting Haiku and DeepSeek backends
capability: Multi-backend LLM evaluator with factory dispatch
kind: built
tags: [Python, pydantic-ai, API integration, testing]
created: 2026-05-22
confidence: 0.95
sources: [e4561d2e]
---
Built a factory function that returns an evaluator for either Anthropic's Haiku or DeepSeek's API, both using the same evaluation prompt. The factory is controlled by a simple environment variable. Created an eval harness that loads all rules and counterexamples, runs each deterministic rule against each example, and reports false positives and negatives. LLM rules are evaluated by the configured backend with a mismatch report. This makes the evaluation pipeline backend-agnostic and enables cross-model comparison of rule enforcement quality.
