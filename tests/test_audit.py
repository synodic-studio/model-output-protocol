"""JsonlAuditor + MOP integration: every verdict produces one record."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mop import MOP, JsonlAuditor
from mop.audit import _verdict_payload
from mop.rules import Rule
from mop.types import Accepted, AcceptedFailedOpen, Rejected, Rewritten


# ---------------------------------------------------------------------------
# JsonlAuditor — file format
# ---------------------------------------------------------------------------


def test_jsonl_auditor_creates_daily_file_with_record(tmp_path: Path) -> None:
    auditor = JsonlAuditor(tmp_path / "audit")
    auditor.record(
        original="hello",
        verdict=Accepted(),
        rule_names=["rule-a", "rule-b"],
        attempt=0,
    )
    files = sorted((tmp_path / "audit").glob("*.jsonl"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text().strip())
    assert entry["original"] == "hello"
    assert entry["verdict"] == "accepted"
    assert entry["rule_names"] == ["rule-a", "rule-b"]
    assert entry["attempt"] == 0
    assert "justification" not in entry
    # ISO-8601 timestamp
    assert "T" in entry["ts"] and entry["ts"].endswith("+00:00")


def test_jsonl_auditor_appends_multiple_records(tmp_path: Path) -> None:
    auditor = JsonlAuditor(tmp_path)
    auditor.record(original="one", verdict=Accepted(), rule_names=[], attempt=0)
    auditor.record(
        original="two",
        verdict=Rewritten(rewritten="two (cleaned)"),
        rule_names=["r1"],
        attempt=0,
    )
    auditor.record(
        original="three",
        verdict=Rejected(violations=("bad-thing",)),
        rule_names=["r1"],
        attempt=0,
    )
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["verdict"] == "accepted"
    assert parsed[1]["verdict"] == "rewritten"
    assert parsed[1]["rewritten"] == "two (cleaned)"
    assert parsed[2]["verdict"] == "rejected"
    assert parsed[2]["violations"] == ["bad-thing"]


def test_jsonl_auditor_includes_justification_when_present(tmp_path: Path) -> None:
    auditor = JsonlAuditor(tmp_path)
    auditor.record(
        original="contested",
        verdict=Accepted(),
        rule_names=[],
        attempt=1,
        justification="because reasons",
    )
    entry = json.loads(next(tmp_path.glob("*.jsonl")).read_text().strip())
    assert entry["justification"] == "because reasons"
    assert entry["attempt"] == 1


def test_verdict_payload_handles_accepted_failed_open() -> None:
    payload = _verdict_payload(AcceptedFailedOpen(system_note="ran out of attempts"))
    assert payload == {
        "verdict": "accepted_failed_open",
        "system_note": "ran out of attempts",
    }


# ---------------------------------------------------------------------------
# MOP integration — auditor sees every verdict path
# ---------------------------------------------------------------------------


class _Recorder:
    """In-memory Auditor stub for assertions."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, **kw: object) -> None:
        self.records.append(kw)


def _rule(name: str) -> Rule:
    return Rule(
        name=name,
        detector="llm",
        parameters={"prompt": "irrelevant"},
        guidance="",
        source_file="test.yml",
    )


@pytest.mark.asyncio
async def test_mop_audits_accepted(tmp_path: Path) -> None:
    rec = _Recorder()

    async def evaluator(text, hints, just):
        return Accepted()

    async def deliver(text, note):
        return None

    mop = MOP(
        rules=[_rule("rule-x")],
        evaluator=evaluator,
        deliver=deliver,
        auditor=rec,
    )
    await mop.submit_message("hi")
    assert len(rec.records) == 1
    assert isinstance(rec.records[0]["verdict"], Accepted)
    assert rec.records[0]["original"] == "hi"
    assert rec.records[0]["rule_names"] == ["rule-x"]
    assert rec.records[0]["attempt"] == 0
    assert rec.records[0]["justification"] is None


@pytest.mark.asyncio
async def test_mop_audits_rewritten_with_attempt_zero() -> None:
    rec = _Recorder()

    async def evaluator(text, hints, just):
        return Rewritten(rewritten="cleaned")

    async def deliver(text, note):
        return None

    mop = MOP(rules=[], evaluator=evaluator, deliver=deliver, auditor=rec)
    await mop.submit_message("dirty")
    assert len(rec.records) == 1
    verdict = rec.records[0]["verdict"]
    assert isinstance(verdict, Rewritten)
    assert verdict.rewritten == "cleaned"
    assert rec.records[0]["original"] == "dirty"


@pytest.mark.asyncio
async def test_mop_audits_rejected_then_justification_round_trip() -> None:
    rec = _Recorder()
    verdicts = iter([Rejected(violations=("bad",)), Accepted()])

    async def evaluator(text, hints, just):
        return next(verdicts)

    async def deliver(text, note):
        return None

    mop = MOP(rules=[_rule("r1")], evaluator=evaluator, deliver=deliver, auditor=rec)
    await mop.submit_message("borderline")
    await mop.submit_justification("here's why")
    assert len(rec.records) == 2
    first, second = rec.records
    assert isinstance(first["verdict"], Rejected)
    assert first["attempt"] == 0
    assert first["justification"] is None
    assert isinstance(second["verdict"], Accepted)
    assert second["attempt"] == 1
    assert second["justification"] == "here's why"


@pytest.mark.asyncio
async def test_mop_audits_failed_open_after_budget_exhausted() -> None:
    rec = _Recorder()

    async def evaluator(text, hints, just):
        return Rejected(violations=("bad",))

    async def deliver(text, note):
        return None

    mop = MOP(
        rules=[],
        evaluator=evaluator,
        deliver=deliver,
        auditor=rec,
        max_justification_attempts=2,
    )
    await mop.submit_message("first")
    await mop.submit_justification("try 1")
    await mop.submit_justification("try 2")
    await mop.submit_justification("try 3 — budget exhausted")
    # 4 records: initial reject, 2 reject retries, then 1 failed-open
    assert len(rec.records) == 4
    assert isinstance(rec.records[0]["verdict"], Rejected)
    assert isinstance(rec.records[1]["verdict"], Rejected)
    assert isinstance(rec.records[2]["verdict"], Rejected)
    final = rec.records[3]
    assert isinstance(final["verdict"], AcceptedFailedOpen)
    assert final["attempt"] == 3
    assert final["justification"] == "try 3 — budget exhausted"


@pytest.mark.asyncio
async def test_mop_without_auditor_does_not_break(tmp_path: Path) -> None:
    """No auditor wired — every verdict path still works."""

    async def evaluator(text, hints, just):
        return Accepted()

    async def deliver(text, note):
        return None

    mop = MOP(rules=[], evaluator=evaluator, deliver=deliver)  # no auditor
    verdict = await mop.submit_message("hi")
    assert isinstance(verdict, Accepted)


@pytest.mark.asyncio
async def test_mop_with_jsonl_auditor_writes_file(tmp_path: Path) -> None:
    """End-to-end: MOP + JsonlAuditor produces an on-disk record per verdict."""
    auditor = JsonlAuditor(tmp_path / "audit")

    async def evaluator(text, hints, just):
        return Accepted()

    async def deliver(text, note):
        return None

    mop = MOP(
        rules=[_rule("rule-1")],
        evaluator=evaluator,
        deliver=deliver,
        auditor=auditor,
    )
    await mop.submit_message("first message")
    await mop.submit_message("second message")
    files = list((tmp_path / "audit").glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["original"] == "first message"
    assert first["verdict"] == "accepted"
    assert first["rule_names"] == ["rule-1"]
