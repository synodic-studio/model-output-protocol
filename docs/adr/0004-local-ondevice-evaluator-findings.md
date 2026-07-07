# ADR-0004 — Local / on-device evaluator findings

- **Status:** Informational (research record; no code change) — 2026-07-06
- **Related:** [ADR-0003](0003-evaluator-model-tiers-and-structured-output.md)

## Context

The shipped default evaluator (`small = deepseek/deepseek-v4-flash`) is a hosted
API. We want to know whether a **local** model (via Ollama) or eventually
**Apple on-device intelligence** could back MOP's evaluator — for offline use,
privacy, and cost. This ADR records the first live results so we don't re-run
them from scratch. The bar for any candidate is "good enough to replace the
default *for MOP's gate*," not frontier quality.

## Method

Ran MOP's real `llm` path (`mop check --builtins --model <m>`) against four
representative messages: a fabricated attribution (should fix/flag), clean
status text (should accept), an external reference missing a link, and a
legitimate quote from a user-visible source (must NOT over-strip).

## Findings (2026-07-06)

| Model | Judgement | Rewrite quality | Speed (Mac Mini) | Verdict |
|---|---|---|---|---|
| `deepseek/deepseek-v4-flash` (default, API) | good | cleanly stripped the fabricated attribution | fast | best default |
| `gemma4:e4b` (Gemma 4 **4B**, Ollama) | **good** — flagged fabrication as `unresolved` honestly; did **not** over-strip a legit README quote | mixed — fabrication rewrite still named Einstein; once reworded for politeness and missed a rule's intent | **slow, ~30–40s/call** | plausibly viable **for MOP specifically** |
| `gemma4:e2b` (Gemma 4 **2B**, Ollama) | weak — kept the fabricated attribution | poor | fast-ish | not good enough |

## Decision / guidance

- **Keep `deepseek/deepseek-v4-flash` as the default.** Faster and cleaner
  rewrites than the local options.
- **Gemma 4 4B is a viable local/offline option for MOP** (this gate is not
  latency-sensitive and its judgement is decent), but it is **not** a general
  `small`-tier replacement across the wider stack. Use `--model ollama_chat/gemma4:e4b`.
- **Gemma 4 2B is not viable** for this task.
- Any local/on-device backend must be checked for the structured-output quirk
  in [ADR-0003](0003-evaluator-model-tiers-and-structured-output.md) — the
  graduated ladder already handles it.

## Open follow-ups (not scheduled)

- Try sharper prompting or few-shot on Gemma 4B to clean up rewrites.
- **Apple Foundation Models (on-device):** not a litellm provider, so it needs
  a thin adapter behind the existing `Evaluator` callable (`mop.types.Evaluator`).
  Clean seam; net-new code.
