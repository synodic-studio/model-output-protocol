"""LLM evaluator for MOP — litellm-backed, provider-agnostic.

`build_evaluator(rules, model=None)` is the main entrypoint. Model
selection precedence:

  explicit ``model=`` arg
  > MOP_EVALUATOR_MODEL env var (litellm "provider/model" string)
  > legacy MOP_EVALUATOR alias (deepseek | haiku — kept for existing
    hosts like patchbay-relay)
  > DEFAULT_MODEL

The builder returns an `Evaluator`-shaped callable suitable for passing
into `MOP(evaluator=...)`. The callable batches every active rule into a
single prompt and asks the LLM for one structured verdict per submission.

The structured-output schema (`mop.types.EvalLLMResponse`) is owned by
the protocol. Where the provider supports response schemas, litellm is
handed the pydantic class directly; otherwise we fall back to JSON mode
and parse the content manually. Either way the raw content is validated
through `EvalLLMResponse.model_validate_json()`.

API keys: litellm resolves keys via its standard per-provider env vars
(ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, ...). Additionally, an isolated
``MOP_<PROVIDER>_API_KEY`` (e.g. MOP_ANTHROPIC_API_KEY) is honored by
passing it explicitly as ``api_key=`` — it is never exported, so it
stays out of env inherited by spawned agent subprocesses.
"""

from __future__ import annotations

import os
from typing import Awaitable, Callable

from .rules import Rule
from .types import EvalLLMResponse, Verdict, verdict_from_eval_response

DEFAULT_MODEL = "deepseek/deepseek-chat"

MODEL_KEY = "MOP_EVALUATOR_MODEL"
LEGACY_EVALUATOR_KEY = "MOP_EVALUATOR"
_LEGACY_ALIASES = {
    "deepseek": "deepseek/deepseek-chat",
    "haiku": "anthropic/claude-haiku-4-5-20251001",
}


def resolve_model(model: str | None = None) -> str:
    """Resolve the litellm model string from arg > env > legacy alias > default."""
    if model:
        return model
    env_model = os.environ.get(MODEL_KEY)
    if env_model:
        return env_model
    legacy = os.environ.get(LEGACY_EVALUATOR_KEY, "").strip().lower()
    if legacy:
        if legacy not in _LEGACY_ALIASES:
            msg = (
                f"Unknown MOP_EVALUATOR={legacy!r}. Expected 'deepseek' or "
                f"'haiku', or set {MODEL_KEY} to a litellm model string."
            )
            raise ValueError(msg)
        return _LEGACY_ALIASES[legacy]
    return DEFAULT_MODEL


def _api_key_for(model: str) -> str | None:
    """Isolated key lookup: MOP_<PROVIDER>_API_KEY for 'provider/model' strings."""
    if "/" not in model:
        return None
    provider = model.split("/", 1)[0]
    return os.environ.get(f"MOP_{provider.upper()}_API_KEY")


def _build_query(
    rules: list[Rule], text: str, regex_hints: list[str], justification: str | None
) -> str:
    rule_lines = "\n".join(
        f"  - {r.name}: {(r.guidance or '').strip()}" for r in rules
    ) or "  (no active rules)"
    hint_line = (
        f"Regex prelim hits (advisory, not authoritative): {', '.join(regex_hints)}"
        if regex_hints
        else "Regex prelim: clean."
    )
    just_line = (
        f"\nThe agent has provided this justification for the message:\n"
        f"  {justification}\n"
        "Decide whether the justification clears the rule violation."
        if justification
        else ""
    )
    return (
        "You are a message gate. Active rules:\n"
        f"{rule_lines}\n\n"
        f"{hint_line}\n\n"
        "Message under review:\n"
        f"<message>\n{text}\n</message>\n"
        f"{just_line}\n\n"
        "Decide one of three actions:\n"
        "  - accept: message passes all rules, deliver as-is\n"
        "  - rewrite: message violates style but is fixable; provide the corrected text in 'rewritten'\n"
        "  - reject: message violates substantive rules; list the violated rule names in 'violations'\n\n"
        "Respond ONLY with a JSON object of the shape:\n"
        '  {"action": "accept" | "rewrite" | "reject",\n'
        '   "rewritten": string or null,\n'
        '   "violations": [string, ...]}\n'
    )


def _strip_fences(content: str) -> str:
    """Strip a single wrapping ```/```json code fence, if present."""
    stripped = content.strip()
    if stripped.startswith("```"):
        idx = stripped.index("\n") if "\n" in stripped else -1
        if idx >= 0:
            stripped = stripped[idx + 1 :]
            if stripped.endswith("```"):
                stripped = stripped[: -3]
        # No newline after opening fence — can't determine where fence ends.
        # Pass the stripped content through as-is.
    return stripped.strip()


def build_litellm_evaluator(
    *, rules: list[Rule], model: str | None = None
) -> Callable[[str, list[str], str | None], Awaitable[Verdict]]:
    """Return an Evaluator-shaped callable backed by litellm."""
    model_id = resolve_model(model)
    api_key = _api_key_for(model_id)

    async def evaluate(
        text: str, regex_hints: list[str], justification: str | None
    ) -> Verdict:
        import litellm

        query = _build_query(rules, text, regex_hints, justification)
        kwargs: dict = {}
        if api_key:
            kwargs["api_key"] = api_key
        try:
            schema_ok = litellm.supports_response_schema(model=model_id)
        except Exception:
            schema_ok = False
        kwargs["response_format"] = (
            EvalLLMResponse if schema_ok else {"type": "json_object"}
        )
        response = await litellm.acompletion(
            model=model_id,
            messages=[{"role": "user", "content": query}],
            **kwargs,
        )
        content = response.choices[0].message.content or ""
        try:
            parsed = EvalLLMResponse.model_validate_json(_strip_fences(content))
        except Exception as exc:
            raise RuntimeError(
                f"Evaluator returned unparseable output: {content[:200]!r}"
            ) from exc
        return verdict_from_eval_response(parsed, original_text=text)

    return evaluate


def build_evaluator(
    *, rules: list[Rule], model: str | None = None
) -> Callable[[str, list[str], str | None], Awaitable[Verdict]]:
    """Build the default evaluator. Backward-compatible entrypoint.

    Existing hosts call ``build_evaluator(rules=...)`` with no model and
    configure via env (MOP_EVALUATOR_MODEL, or legacy MOP_EVALUATOR).
    """
    return build_litellm_evaluator(rules=rules, model=model)
