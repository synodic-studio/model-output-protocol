#!/usr/bin/env python3
"""Mine the MOP audit log into candidate counterexamples.

Reads the JSONL flight recorder (`~/.mop/audit/*.jsonl`), runs each recorded
message through one or more `llm` rules via the configured evaluator (DeepSeek by
default), and reports which messages the rule fires on — plus the *disposition*
(rewritten vs rejected), which is the signal for whether a rule is behaving as a
reject-with-reason rule or trying to rewrite.

Fired messages are written as draft counterexample YAML to a triage dir (default:
outside the repo) for human sanitize + label before landing in `evals/`.

Usage:
  MOP_DEEPSEEK_API_KEY=$(pass show deepseek-api-key) \
    python scripts/mine_audit.py --host claude-code \
      --rule no-permission-asking-for-doable-work,no-delegating-doable-work
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mop.evaluators import build_evaluator, resolve_model  # noqa: E402
from mop.rules import load_rules_file  # noqa: E402
from mop.types import Accepted, Rejected, Rewritten  # noqa: E402

AUDIT_GLOB = os.path.expanduser("~/.mop/audit/*.jsonl")
RULES_FILE = REPO / "rules" / "behavior.yml"


def load_candidates(host: str | None, limit: int | None) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for path in sorted(glob.glob(AUDIT_GLOB)):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if host and e.get("host") != host:
                    continue
                text = (e.get("original") or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                out.append(
                    {"text": text, "ts": e.get("ts"), "session": e.get("session")}
                )
    return out[:limit] if limit else out


def rules_by_name(names: list[str]) -> list:
    loaded = {r.name: r for r in load_rules_file(RULES_FILE)}
    missing = [n for n in names if n not in loaded]
    if missing:
        sys.exit(f"unknown rule(s): {missing}. available: {sorted(loaded)}")
    return [loaded[n] for n in names]


def disposition(verdict) -> str:
    if isinstance(verdict, Rewritten):
        return "rewritten"
    if isinstance(verdict, Rejected):
        return "rejected"
    return "accepted"


def slug(text: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", "", text.lower()).split()[:6]
    return "-".join(words) or "untitled"


async def run(args: argparse.Namespace) -> int:
    names = [n.strip() for n in args.rule.split(",") if n.strip()]
    rules = rules_by_name(names)
    candidates = load_candidates(args.host, args.limit)
    model = resolve_model(args.model)
    triage = Path(os.path.expanduser(args.triage_dir))
    triage.mkdir(parents=True, exist_ok=True)

    print(f"model={model}  candidates={len(candidates)}  rules={names}")
    sem = asyncio.Semaphore(args.concurrency)
    evaluators = {r.name: build_evaluator(rules=[r]) for r in rules}

    async def judge(rule, item):
        async with sem:
            try:
                v = await evaluators[rule.name](item["text"], [], None)
            except Exception as exc:  # noqa: BLE001
                return rule, item, None, str(exc)
        return rule, item, v, None

    tasks = [judge(r, it) for r in rules for it in candidates]
    results = await asyncio.gather(*tasks)

    summary: dict = {r.name: {"rewritten": 0, "rejected": 0, "accepted": 0, "error": 0} for r in rules}
    fired: list = []
    for rule, item, verdict, err in results:
        if err is not None:
            summary[rule.name]["error"] += 1
            continue
        d = disposition(verdict)
        summary[rule.name][d] += 1
        if d != "accepted":
            fired.append((rule, item, d))

    print("\n=== summary (disposition split shows reject-vs-rewrite behavior) ===")
    for name, s in summary.items():
        f = s["rewritten"] + s["rejected"]
        print(f"  {name}: fired {f}/{len(candidates)}  (rejected={s['rejected']} rewritten={s['rewritten']}) errors={s['error']}")

    for rule, item, d in fired:
        fp = triage / f"{rule.name}__{slug(item['text'])}.yml"
        body = (
            f"id: {slug(item['text'])}\n"
            f"source: real-sanitized   # TODO sanitize + verify before landing\n"
            f"labels: [real, mined, behavior]\n"
            f"expected_violations:\n  - {rule.name}\n"
            f"expected_clean: []\n"
            f"disposition_observed: {d}\n"
            f"src_ts: {item['ts']}\nsrc_session: {item['session']}\n"
            f"text: |\n"
            + "\n".join("  " + ln for ln in item["text"].splitlines())
            + "\n"
        )
        fp.write_text(body, encoding="utf-8")

    print(f"\ndrafts written: {len(fired)} -> {triage}")
    print("(unsanitized real text; review before copying into evals/)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Mine MOP audit log into candidate counterexamples.")
    p.add_argument("--rule", default="no-permission-asking-for-doable-work,no-delegating-doable-work")
    p.add_argument("--host", default="claude-code")
    p.add_argument("--model", default=None, help="override MOP evaluator model (default: DeepSeek)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--triage-dir", default="~/.mop/triage")
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
