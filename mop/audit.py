"""MOP audit log — flight recorder for every verdict.

The host constructs an `Auditor` and passes it to `MOP(auditor=...)`.
After every verdict is produced — Accepted, Rewritten, Rejected,
AcceptedFailedOpen — MOP calls `auditor.record(...)` with the full
context: the original text submitted, the verdict, the names of every
active rule at that moment, the justification text if any, the attempt
counter (0 = first submission, 1 = first justification, …), and an
optional `host` tag naming which delivery surface emitted the verdict
(so logs from a relay and the agent it wraps stay attributable and
dedupable — see the one-gate-per-path rule in docs/integration.md).

The default `JsonlAuditor` appends one JSON line per verdict to a
daily-rotated file (`<log_dir>/YYYY-MM-DD.jsonl`). The directory is
created lazily on first write. The `Auditor` Protocol is the public
contract — hosts that want a different backend (sqlite, http, in-memory
ring buffer for tests) implement that shape and pass it instead.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .types import Verdict


class Auditor(Protocol):
    """Side-effect callback invoked once per verdict."""

    def record(
        self,
        *,
        original: str,
        verdict: Verdict,
        rule_names: list[str],
        attempt: int,
        justification: str | None = None,
        host: str | None = None,
    ) -> None: ...


def _verdict_payload(verdict: Verdict) -> dict:
    return verdict.serialize()


class JsonlAuditor:
    """Append one JSON line per verdict to `<log_dir>/YYYY-MM-DD.jsonl`."""

    def __init__(self, log_dir: Path | str) -> None:
        self.log_dir = Path(log_dir)

    def _path_for(self, ts: datetime) -> Path:
        return self.log_dir / f"{ts.strftime('%Y-%m-%d')}.jsonl"

    def record(
        self,
        *,
        original: str,
        verdict: Verdict,
        rule_names: list[str],
        attempt: int,
        justification: str | None = None,
        host: str | None = None,
    ) -> None:
        ts = datetime.now(timezone.utc)
        entry: dict = {
            "ts": ts.isoformat(),
            "original": original,
            "rule_names": list(rule_names),
            "attempt": attempt,
        }
        if host is not None:
            entry["host"] = host
        if justification is not None:
            entry["justification"] = justification
        entry.update(_verdict_payload(verdict))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self._path_for(ts).open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
