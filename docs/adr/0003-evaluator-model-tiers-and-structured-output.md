# ADR-0003 — Evaluator model tiers & structured-output handling

- **Status:** Accepted — implemented on `develop` (commits `bdfeae5`, `b371181`, 2026-07-06)
- **Date:** 2026-07-06

## Context

MOP calls litellm **in-process** (not via pi or a proxy), so it cannot rely on
any host's `small`/`medium`/`large` config — pi/patchbay-voice resolve those
through pi, not raw litellm. MOP must define its own tier map.

The evaluator also needs machine-readable JSON back. Provider support for
structured output is inconsistent and its self-reported capability metadata is
**not trustworthy** (see the finding below).

## Decision

**MOP owns its tier aliases** (`mop/evaluators.py::MODEL_ALIASES`):

| tier | model | note |
|---|---|---|
| `small` | `deepseek/deepseek-v4-flash` | default; confirmed litellm id |
| `medium` | `deepseek/deepseek-v4-pro` | provisional |
| `large` | `anthropic/claude-sonnet-5` | provisional frontier judge |

`resolve_model` maps a bare `small`/`medium`/`large` (from the `--model` arg or
`MOP_EVALUATOR_MODEL`) through this map; any other string is treated as a raw
litellm `provider/model` and passes through. Precedence: arg > env > legacy
`MOP_EVALUATOR` alias > default (`small`).

**Structured output uses a graduated ladder**, strongest first, stepping down
**only** when a provider rejects the *format itself* (never on a real error):

1. `response_format=EvalLLMResponse` (pydantic → json_schema, schema-enforced)
2. `response_format={"type": "json_object"}` (JSON mode — widely supported)
3. no `response_format` (last resort — prompt already asks for JSON)

Pydantic (`EvalLLMResponse.model_validate_json`) validates the result at **every**
rung, so `json_object` is a real guarantee, not a rounded corner.

## Finding that drove the ladder (recorded so future backends get checked)

Live-testing `deepseek/deepseek-v4-flash` (key: `pass show deepseek-api-key`):
`litellm.supports_response_schema` returns **True**, but the API **400s** the
json_schema form — `{"error":{"message":"This response_format type is
unavailable now"}}` — while **accepting `json_object`**. So the capability flag
lied; only the empirical ladder gets a working request. An earlier fix rounded
the corner by dropping straight to prompt-only prose parsing; this ladder
instead lands DeepSeek on `json_object`.

## Consequences

- Works across providers with inconsistent structured-output support (DeepSeek
  API, Ollama, Anthropic) without per-provider special-casing.
- Pydantic remains the single validation authority.
- `medium`/`large` are provisional and unexercised; only `small` is the tested
  default. Confirm/adjust before relying on the higher tiers.
