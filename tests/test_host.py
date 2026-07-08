"""mop.host.gate — the out-of-band host glue used by the integration shims."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mop import gate
from mop.host import REDACTION_NOTICE
from mop.types import Rejected


async def _reject_evaluator(text, hints, justification):
    """Fake evaluator: the LLM couldn't fix the deterministic hits."""
    return Rejected(list(hints))

# A minimal deterministic rules dir so we never need a live LLM in tests.
_REGEX_RULE = """
rules:
- name: no-lgtm
  detector: regex
  description: ban LGTM
  parameters:
    patterns:
      - "(?i)\\\\bLGTM\\\\b"
  rationale: test
"""


@pytest.fixture
def rules_dir(tmp_path: Path) -> Path:
    d = tmp_path / "rules"
    d.mkdir()
    (d / "r.yml").write_text(_REGEX_RULE)
    return d


# ---------------------------------------------------------------------------
# No active rules: free passthrough, no litellm, still audits.
# ---------------------------------------------------------------------------


def test_no_rules_accepts_and_passes_through(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    result = gate("anything at all", host="test", audit_dir=audit)
    assert result.verdict.__class__.__name__ == "Accepted"
    assert result.deliver == "anything at all"
    assert result.replacement() is None  # nothing to change
    entry = json.loads(next(audit.glob("*.jsonl")).read_text().strip())
    assert entry["host"] == "test"
    assert entry["verdict"] == "accepted"


def test_audit_optional(tmp_path: Path) -> None:
    # No audit_dir → no files written, no crash.
    gate("hello", host="test")
    assert not list(tmp_path.glob("**/*.jsonl"))


# ---------------------------------------------------------------------------
# Log mode: deterministic violation is recorded but delivery is untouched.
# ---------------------------------------------------------------------------


def test_log_mode_passthrough_despite_violation(rules_dir: Path, tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    result = gate("LGTM ship it", host="relay", rules_dir=rules_dir, audit_dir=audit)
    assert result.mode == "log"
    assert result.deliver == "LGTM ship it"  # gears disengaged
    assert result.replacement() is None
    # ...but the verdict was still evaluated and logged.
    assert result.verdict.__class__.__name__ == "Rejected"
    entry = json.loads(next(audit.glob("*.jsonl")).read_text().strip())
    assert entry["verdict"] == "rejected"
    assert entry["host"] == "relay"


# ---------------------------------------------------------------------------
# Enforce mode: rejection is redacted.
# ---------------------------------------------------------------------------


def test_enforce_mode_redacts_rejection(rules_dir: Path) -> None:
    result = gate(
        "LGTM ship it",
        host="relay",
        mode="enforce",
        rules_dir=rules_dir,
        evaluator=_reject_evaluator,
    )
    assert result.deliver == REDACTION_NOTICE
    assert result.changed is True
    assert result.replacement() == REDACTION_NOTICE


def test_bad_mode_raises(rules_dir: Path) -> None:
    with pytest.raises(ValueError):
        gate("x", host="t", mode="audit", rules_dir=rules_dir)


# ---------------------------------------------------------------------------
# The sync bridge must work from inside a running event loop (Hermes' case).
# ---------------------------------------------------------------------------


def test_gate_callable_from_running_loop(rules_dir: Path) -> None:
    async def driver() -> str:
        # gate() is sync but internally may run a coroutine; calling it from an
        # already-running loop must not raise "loop already running".
        return gate(
            "LGTM here",
            host="h",
            mode="enforce",
            rules_dir=rules_dir,
            evaluator=_reject_evaluator,
        ).deliver

    assert asyncio.run(driver()) == REDACTION_NOTICE
