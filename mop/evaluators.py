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

# MOP owns its own small/medium/large tier aliases and populates them here
# rather than relying on any host's model config (pi/patchbay-voice resolve
# these through pi, NOT raw litellm — MOP calls litellm directly, so it must
# define the map itself). `small` is the default evaluator: cheap, fast, good
# enough to judge a rule and rewrite a message. medium/large are provisional
# house defaults for callers who want a stronger judge; adjust as needed.
MODEL_ALIASES = {
    "small": "deepseek/deepseek-v4-flash",   # DeepSeek V4 Flash (confirmed litellm id)
    "medium": "deepseek/deepseek-v4-pro",    # provisional
    "large": "anthropic/claude-sonnet-5",    # provisional frontier judge
}

DEFAULT_MODEL = MODEL_ALIASES["small"]

MODEL_KEY = "MOP_EVALUATOR_MODEL"
LEGACY_EVALUATOR_KEY = "MOP_EVALUATOR"
_LEGACY_ALIASES = {
    "deepseek": "deepseek/deepseek-chat",
    "haiku": "anthropic/claude-haiku-4-5-20251001",
}


def resolve_model(model: str | None = None) -> str:
    """Resolve the litellm model string.

    Precedence: explicit arg > MOP_EVALUATOR_MODEL env > legacy MOP_EVALUATOR
    alias > default (`small`). A `small`/`medium`/`large` tier name (from arg
    or env) resolves through MOP's own alias map; any other string is treated
    as a raw litellm ``provider/model`` and passes through unchanged.
    """
    raw = model or os.environ.get(MODEL_KEY)
    if raw:
        return MODEL_ALIASES.get(raw, raw)
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
    rules: list[Rule],
    text: str,
    deterministic_violations: list[str],
    justification: str | None,
) -> str:
    rule_lines = "\n".join(
        f"  - {r.name} [{r.disposition}]: {(r.guidance or '').strip()}"
        for r in rules
    ) or "  (no active rules)"
    reject_names = [r.name for r in rules if r.disposition == "reject"]
    reject_line = (
        "\nRules tagged [reject] are NOT rewritable: the fix is an action the "
        "agent must take, not a wording change. If the message violates one, DO "
        "NOT rewrite it — set 'rewritten' to null and list that rule in "
        "'unresolved'. A [reject] violation dominates: if any [reject] rule "
        "fires, leave the text unchanged and list every violated rule in "
        f"'unresolved'. Reject-disposition rules: {', '.join(reject_names)}.\n"
        if reject_names
        else ""
    )
    det_line = (
        "These deterministic rules are CONFIRMED violated and MUST be removed "
        f"in your rewrite: {', '.join(deterministic_violations)}"
        if deterministic_violations
        else "No deterministic violations detected."
    )
    just_line = (
        f"\nThe agent has provided this justification for the message:\n"
        f"  {justification}\n"
        "Weigh whether the justification clears the fixable rules."
        if justification
        else ""
    )
    return (
        "You are a message gate. Active rules:\n"
        f"{rule_lines}\n\n"
        f"{det_line}\n\n"
        f"{reject_line}"
        "Message under review:\n"
        f"<message>\n{text}\n</message>\n"
        f"{just_line}\n\n"
        "Produce your BEST-EFFORT rewrite that fixes every REWRITABLE violation "
        "you can (including the confirmed deterministic ones), preserving the "
        "message's intent. NEVER rewrite to satisfy a [reject] rule — if one is "
        "violated, leave the text unchanged (set 'rewritten' to null) and name "
        "it in 'unresolved'. List in 'unresolved' every rule you could not fix, "
        "plus every violated [reject] rule. If the message already passes every "
        "rule, set 'rewritten' to null and 'unresolved' to [].\n\n"
        "Respond ONLY with a JSON object of the shape:\n"
        '  {"rewritten": string or null,\n'
        '   "unresolved": [string, ...]}\n'
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
        text: str, deterministic_violations: list[str], justification: str | None
    ) -> Verdict:
        import litellm

        # We handle provider errors ourselves (retry-without-response_format
        # below); silence litellm's "Give Feedback / Get Help" banner so a
        # caught-and-recovered error doesn't look like a failure.
        litellm.suppress_debug_info = True

        query = _build_query(rules, text, deterministic_violations, justification)
        kwargs: dict = {}
        if api_key:
            kwargs["api_key"] = api_key
        try:
            schema_ok = litellm.supports_response_schema(model=model_id)
        except Exception:
            schema_ok = False
        messages = [{"role": "user", "content": query}]
        # Graduated structured-output ladder: strongest form first, step down
        # ONLY when a provider rejects the format itself (never on a real
        # error). Whatever comes back is validated by pydantic below, so
        # json_object is a genuine guarantee, not a rounded corner. This is
        # necessary because some providers advertise schema support they don't
        # honor — e.g. DeepSeek v4-flash: supports_response_schema is True, but
        # the json_schema form 400s ("response_format type unavailable"), while
        # json_object works.
        formats: list = []
        if schema_ok:
            formats.append(EvalLLMResponse)      # json_schema — schema-enforced
        formats.append({"type": "json_object"})  # JSON mode — widely supported
        formats.append(None)                     # last resort — prompt-only JSON
        response = None
        for response_format in formats:
            call_kwargs = dict(kwargs)
            if response_format is not None:
                call_kwargs["response_format"] = response_format
            try:
                response = await litellm.acompletion(
                    model=model_id, messages=messages, **call_kwargs
                )
                break
            except Exception as exc:
                is_last = response_format is None
                if is_last or "response_format" not in str(exc).lower():
                    raise
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
