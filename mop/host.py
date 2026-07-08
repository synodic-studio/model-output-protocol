"""Out-of-band host glue — embed MOP at a delivery chokepoint.

A host (a Hermes plugin, a patchbay filter, any Python send-path) calls
``gate(text, host=...)`` at the point its agent's outgoing text is finalized.
MOP resolves the rule set, evaluates the text, audits the verdict, and returns
a :class:`GateResult` saying what to deliver.

Two modes — the "gears" the caller shifts between:

* ``mode="log"`` (default, gears **disengaged**): evaluate + audit, but never
  alter delivery. ``GateResult.deliver`` is always the original text. This is
  the safe shadow launch — you get real verdicts in the audit log without any
  behaviour change the user can see.
* ``mode="enforce"`` (gears **engaged**): ``deliver`` reflects the verdict —
  the rewrite on Rewritten, a redaction notice on Rejected.

Every audit record is tagged with ``host`` so logs from multiple surfaces stay
attributable and dedupable. Because a relay (patchbay) wraps the agent it runs
(Pi), gating in BOTH double-logs the same message — install ONE gate per
delivery path, at the outermost boundary. See docs/integration.md.

The callback surface a host registers must be **synchronous** (Hermes'
``invoke_hook`` calls ``cb(**kwargs)`` without awaiting), so ``gate`` is sync.
It only spins a worker thread for the LLM call when an ``llm`` rule is actually
active; with no active rules it never touches litellm or an event loop.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path

from .cli import _deterministic_hits, check
from .discovery import resolve_rules
from .types import Accepted, Evaluator, Rejected, Rewritten, Verdict

REDACTION_NOTICE = "[message withheld by MOP]"


@dataclass
class GateResult:
    """Outcome of gating one outgoing message."""

    deliver: str  # text the host should send
    verdict: Verdict  # the raw MOP verdict (for logging / branching)
    changed: bool  # did enforce alter the original text?
    mode: str  # "log" | "enforce"

    def replacement(self) -> str | None:
        """The string a transform-hook should return, or None to leave as-is.

        In ``log`` mode this is always None (passthrough). In ``enforce`` mode
        it is the delivered text when it differs from the original, else None.
        """
        return self.deliver if self.changed else None


def _run_coro(coro):
    """Run *coro* to completion whether or not a loop is already running here.

    Hermes calls hook callbacks synchronously from inside its async loop, so
    ``asyncio.run`` would raise "loop already running". When that's the case we
    execute the coroutine in a short-lived worker thread with its own loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}

    def runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # re-raised on the caller thread below
            box["error"] = exc

    worker = threading.Thread(target=runner, name="mop-gate")
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


def _evaluate(
    text: str,
    *,
    rules_dir: Path | None,
    use_builtins: bool,
    model: str | None,
    allow_rewrite: bool,
    evaluator: Evaluator | None,
):
    """Return (verdict, rules). Skips litellm entirely when no LLM call is needed.

    ``allow_rewrite`` False (log mode) is judge-only: a deterministic-only rule
    set rejects without any LLM call. The evaluator (litellm) is built lazily —
    only when an ``llm`` rule is active, or a deterministic hit needs a rewrite
    attempt (``allow_rewrite`` True). A caller-supplied ``evaluator`` overrides
    the built one (injection for hosts and tests).
    """
    rules = resolve_rules(rules_dir=rules_dir, use_builtins=use_builtins)
    det = _deterministic_hits(text, rules)
    has_llm = any(r.detector == "llm" for r in rules)
    needs_llm = has_llm or (bool(det) and allow_rewrite)
    if not det and not has_llm:
        return Accepted(), rules
    if evaluator is None and needs_llm:
        from .evaluators import build_evaluator

        evaluator = build_evaluator(rules=rules, model=model)
    verdict = _run_coro(
        check(text, rules, evaluator or _unused_evaluator, allow_rewrite=allow_rewrite)
    )
    return verdict, rules


async def _unused_evaluator(text, hints, justification):  # pragma: no cover
    """Placeholder passed when check() provably won't call the evaluator."""
    raise AssertionError("evaluator called unexpectedly")


def _delivery(text: str, verdict: Verdict, *, mode: str) -> tuple[str, bool]:
    """Map a verdict to (text_to_deliver, changed) under the active mode."""
    if mode == "log":
        return text, False
    if isinstance(verdict, Rewritten):
        return verdict.rewritten, verdict.rewritten.strip() != text.strip()
    if isinstance(verdict, Rejected):
        return REDACTION_NOTICE, True
    return text, False


def gate(
    text: str,
    *,
    host: str,
    mode: str = "log",
    rules_dir: Path | str | None = None,
    use_builtins: bool = False,
    model: str | None = None,
    audit_dir: Path | str | None = None,
    evaluator: Evaluator | None = None,
) -> GateResult:
    """Evaluate *text*, audit the verdict, and decide what to deliver.

    ``host`` tags the audit record. ``mode`` selects log (passthrough, judge-only)
    vs. enforce (apply rewrites, redact rejections). ``rules_dir`` /
    ``use_builtins`` / ``model`` feed rule resolution and the evaluator;
    ``audit_dir`` (falsy = no audit) is where the JSONL flight recorder is
    written; ``evaluator`` overrides the built-in litellm evaluator.
    """
    if mode not in ("log", "enforce"):
        raise ValueError(f"mode must be 'log' or 'enforce', got {mode!r}")
    rd = Path(rules_dir) if rules_dir else None
    verdict, rules = _evaluate(
        text,
        rules_dir=rd,
        use_builtins=use_builtins,
        model=model,
        allow_rewrite=(mode == "enforce"),
        evaluator=evaluator,
    )
    if audit_dir:
        from .audit import JsonlAuditor

        JsonlAuditor(audit_dir).record(
            original=text,
            verdict=verdict,
            rule_names=[r.name for r in rules],
            attempt=0,
            host=host,
        )
    deliver, changed = _delivery(text, verdict, mode=mode)
    return GateResult(deliver=deliver, verdict=verdict, changed=changed, mode=mode)
